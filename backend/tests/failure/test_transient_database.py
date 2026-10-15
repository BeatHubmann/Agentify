"""A transient database error is not a lost run (A§8.4, A§8.5): lease renewal and the worker loop
survive one, and a run in flight still commits."""

import asyncio

import pytest

from agentify.harness import execute, runs
from agentify.harness.worker import WorkerConfig, run_worker
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace
from tests.conftest import Infra
from tests.orchestration.test_run_exit_paths import history
from tests.support.agents import run_kind
from tests.support.faults import FlakyDatabase
from tests.support.scripted import Script, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def flaky_workspace(infra: Infra, workspace: Workspace) -> tuple[Workspace, FlakyDatabase]:
    db = FlakyDatabase(infra.app_conninfo)
    await db.open()
    return Workspace(db, workspace.blobs, workspace.gate), db


async def test_one_failed_lease_renewal_does_not_lose_the_run(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    ws, db = await flaky_workspace(infra, workspace)

    async def blip() -> None:
        """Only the lease keeper uses the database while the model 'thinks'."""
        db.fail(1)
        while db.failures:  # noqa: ASYNC110 - waits for the renewal task to hit the fault
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)  # several successful renewals follow

    steps = golden_calls()
    steps[2] = Step(calls=steps[2].calls, before=blip)
    config = execute.HarnessConfig(lease_seconds=2, renew_every=0.05)
    try:
        run_id, final = await run_kind(
            ws, registry, process_id, "current-state-modeler", Script(steps), config=config
        )
    finally:
        await db.close()
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], len(await history(workspace, process_id))) == (
        "committed",
        "committed",
        1,
    )


@pytest.mark.parametrize(
    "skip",
    [1, 2],
    ids=["in-the-sweep", "in-an-iteration"],  # after the startup log-settings check (1 transaction)
)
async def test_the_worker_loop_survives_a_database_error_and_runs_the_next_job(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str, skip: int
) -> None:
    ws, db = await flaky_workspace(infra, workspace)
    kind = registry.agents["current-state-modeler"]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    db.fail(1, skip=skip)

    def model_for(_kind: AgentKind) -> object:
        return Script(golden_calls()).model()

    stop = asyncio.Event()
    config = WorkerConfig(
        owner="survivor", queues=(kind.id,), poll_interval=0.05, sweep_interval=60
    )
    worker = asyncio.create_task(run_worker(ws, registry, model_for, config, stop))  # type: ignore[arg-type]
    try:
        async with asyncio.timeout(60):
            while (await runs.run_state(workspace, run_id))["state"] not in {"committed"}:
                assert not worker.done(), worker.exception() if worker.done() else None
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=30)
        await db.close()
    assert (db.failures, (await runs.run_state(workspace, run_id))["state"]) == (0, "committed")
