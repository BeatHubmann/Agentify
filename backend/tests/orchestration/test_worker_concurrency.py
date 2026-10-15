"""A worker runs several claimed runs at once (checkpoint audit finding 7, A§8.5): a router's
fan-out of parallel runs must not queue behind one another past the admission timeout."""

import asyncio
import uuid

import pytest

from agentify.harness import runs, sweeper
from agentify.harness.worker import WorkerConfig, run_worker
from agentify.llm import ModelSource
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace
from tests.support.scripted import Script, ScriptedSource, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
FAN_OUT = 3


async def test_a_fan_out_runs_at_the_same_time_on_one_worker(
    workspace: Workspace, registry: Registry
) -> None:
    kind = registry.agents["current-state-modeler"]
    together = asyncio.Barrier(FAN_OUT)

    async def wait_for_the_others() -> None:
        async with asyncio.timeout(20):  # deadlocks unless all runs are in flight at once
            await together.wait()

    # Earlier tests leave runs queued for this kind; finish them so only this fan-out is claimed.
    await sweeper.sweep(workspace, owner="clear", lease_seconds=30, admission_timeout_seconds=0)
    run_ids = []
    for _ in range(FAN_OUT):
        process_id = f"proc-{uuid.uuid4().hex[:12]}"
        await workspace.create_process(f"eng-{uuid.uuid4().hex[:8]}", process_id)
        run_ids.append(
            await runs.admit(
                workspace,
                process_id=process_id,
                kind=kind,
                mode="from_scratch",
                initiated_by="c",
                entry_point="test",
            )
        )

    def source_for(_kind: AgentKind) -> ModelSource:
        first, *rest = golden_calls()
        return ScriptedSource(Script([Step(calls=first.calls, before=wait_for_the_others), *rest]))

    stop = asyncio.Event()
    config = WorkerConfig(
        owner="fan-out",
        queues=(kind.id,),
        poll_interval=0.05,
        sweep_interval=60,
        max_concurrent_runs=FAN_OUT,
    )
    worker = asyncio.create_task(run_worker(workspace, registry, source_for, config, stop))
    try:
        async with asyncio.timeout(60):
            while True:
                states = [str((await runs.run_state(workspace, r))["state"]) for r in run_ids]
                if all(s in runs.TERMINAL_STATES for s in states):
                    break
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=30)
    assert states == ["committed"] * FAN_OUT
