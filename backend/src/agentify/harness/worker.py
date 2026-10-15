"""The worker loop (A§8.4, A§8.5): periodic sweep, router follow-ups, claim, execute; graceful shutdown.

A stop request (SIGTERM in the process entry point) cancels the run in progress, which ends in a
shielded rollback like any runtime-level cancellation, then the loop exits.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from agentify.agents.catalog import build_agent
from agentify.harness import execute, prompt_version, runs, sweeper
from agentify.harness.prompts import compose_prompt
from agentify.llm import ModelSource
from agentify.observability import logger
from agentify.registry import AgentKind, Registry
from agentify.store.db import check_log_settings
from agentify.store.workspace import Workspace

SourceFactory = Callable[[AgentKind], ModelSource]
"""Where each kind's base model comes from: Bedrock in deployment, scripted models in tests."""


@dataclass(frozen=True)
class WorkerConfig:
    owner: str
    queues: tuple[str, ...]
    poll_interval: float = 0.5
    sweep_interval: float = 5.0
    admission_timeout: float = 300.0
    max_concurrent_runs: int = 4
    """Claimed runs in flight at once, so a router's fan-out does not queue (audit finding 7)."""
    output_language: str = "en"
    harness: execute.HarnessConfig = field(default_factory=execute.HarnessConfig)


async def _claim_next(
    ws: Workspace, registry: Registry, config: WorkerConfig
) -> runs.ClaimedRun | None:
    if "router" in registry.agents:
        await runs.admit_router_followups(ws, registry.agents["router"])
    for queue in config.queues:
        claimed = await runs.claim(
            ws, queue=queue, owner=config.owner, lease_seconds=config.harness.lease_seconds
        )
        if claimed is not None:
            return claimed
    return None


async def _run_claimed(
    ws: Workspace,
    registry: Registry,
    source_for: SourceFactory,
    config: WorkerConfig,
    claimed: runs.ClaimedRun,
) -> None:
    kind = registry.agents[claimed.kind]
    prompt = await compose_prompt(
        ws,
        process_id=claimed.process_id,
        base_revision_id=claimed.base_revision_id,
        kind=kind,
        instructions=claimed.instructions,
        output_language=config.output_language,
    )
    await execute.execute(
        ws,
        claimed,
        kind,
        agent=build_agent(kind, registry),
        source=source_for(kind),
        prompt=prompt,
        output_language=config.output_language,
        config=config.harness,
        registry=registry,
    )


async def run_worker(
    ws: Workspace,
    registry: Registry,
    source_for: SourceFactory,
    config: WorkerConfig,
    stop: asyncio.Event,
) -> None:
    if config.harness.prompt_version is None:
        config = replace(
            config, harness=replace(config.harness, prompt_version=prompt_version.default())
        )
    await check_log_settings(ws.db)
    config.harness.policy.require({kind.model_role for kind in registry.agents.values()})
    next_sweep = 0.0
    running: set[asyncio.Task[None]] = set()
    try:
        while not stop.is_set():
            next_sweep = await _sweep_if_due(ws, config, next_sweep)
            claimed = None
            if len(running) < config.max_concurrent_runs:
                try:
                    claimed = await _claim_next(ws, registry, config)
                except Exception as exc:  # noqa: BLE001 - a transient error never stops the worker
                    logger().warning(
                        "worker_claim_failed", owner=config.owner, error=type(exc).__name__
                    )
            if claimed is not None:
                running.add(
                    asyncio.create_task(_run_claimed(ws, registry, source_for, config, claimed))
                )
                continue
            await _wait(stop, running, config)
            for task in [t for t in running if t.done()]:
                running.discard(task)
                if not task.cancelled() and task.exception() is not None:
                    # A claimed run left behind ends through the sweeper (A§8.5).
                    logger().warning(
                        "worker_run_failed",
                        owner=config.owner,
                        error=type(task.exception()).__name__,
                    )
    finally:
        for task in running:
            task.cancel()
        for task in running:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _sweep_if_due(ws: Workspace, config: WorkerConfig, next_sweep: float) -> float:
    if time.monotonic() < next_sweep:
        return next_sweep
    try:
        await sweeper.sweep(
            ws,
            owner=config.owner,
            lease_seconds=config.harness.lease_seconds,
            admission_timeout_seconds=config.admission_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - a transient error never stops the worker
        logger().warning("worker_sweep_failed", owner=config.owner, error=type(exc).__name__)
    return time.monotonic() + config.sweep_interval


async def _wait(
    stop: asyncio.Event, running: set[asyncio.Task[None]], config: WorkerConfig
) -> None:
    """Until the stop signal, a run finishing, or the poll interval."""
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait(
            {stopping, *running}, timeout=config.poll_interval, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        stopping.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopping
