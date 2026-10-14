"""The worker loop (A§8.4, A§8.5): periodic sweep, claim, execute; graceful shutdown.

A stop request (SIGTERM in the process entry point) cancels the run in progress, which ends in a
shielded rollback like any runtime-level cancellation, then the loop exits.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai.models import Model

from agentify.agents.catalog import build_agent
from agentify.harness import execute, runs, sweeper
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace

ModelFactory = Callable[[AgentKind], Model]


@dataclass(frozen=True)
class WorkerConfig:
    owner: str
    queues: tuple[str, ...]
    poll_interval: float = 0.5
    sweep_interval: float = 5.0
    admission_timeout: float = 300.0
    harness: execute.HarnessConfig = field(default_factory=execute.HarnessConfig)


async def _work_once(
    ws: Workspace, registry: Registry, model_for: ModelFactory, config: WorkerConfig
) -> bool:
    for queue in config.queues:
        claimed = await runs.claim(
            ws, queue=queue, owner=config.owner, lease_seconds=config.harness.lease_seconds
        )
        if claimed is None:
            continue
        kind = registry.agents[claimed.kind]
        await execute.execute(
            ws,
            claimed,
            kind,
            agent=build_agent(kind, registry),
            model=model_for(kind),
            prompt=claimed.instructions or "Model the process from the workspace inputs.",
            config=config.harness,
        )
        return True
    return False


async def run_worker(
    ws: Workspace,
    registry: Registry,
    model_for: ModelFactory,
    config: WorkerConfig,
    stop: asyncio.Event,
) -> None:
    next_sweep = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_sweep:
            await sweeper.sweep(
                ws,
                owner=config.owner,
                lease_seconds=config.harness.lease_seconds,
                admission_timeout_seconds=config.admission_timeout,
            )
            next_sweep = time.monotonic() + config.sweep_interval
        work = asyncio.create_task(_work_once(ws, registry, model_for, config))
        stopping = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({work, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if stopping in done and work not in done:
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await work
            return
        stopping.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopping
        if not work.result():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=config.poll_interval)
