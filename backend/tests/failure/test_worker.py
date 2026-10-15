"""HZ-13 and A§8.4 and A§8.5: the worker loop runs queued runs, sweeps periodically, shuts down gracefully,
and a stalled (SIGSTOPped) executor cannot write after the sweeper took its run over."""

import asyncio
import os
import signal
import sys
import uuid

import pytest

from agentify.harness import runs
from agentify.harness.worker import WorkerConfig, run_worker
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace
from tests.conftest import REGISTRY_ROOT, Infra
from tests.failure.test_kill_and_stall import BACKEND
from tests.orchestration.test_run_exit_paths import history, leftovers
from tests.support.scripted import Script, ScriptedSource, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"


async def wait_for_state(ws: Workspace, run_id: str, states: set[str], within: float = 30) -> str:
    async def poll() -> str:
        while True:
            state = str((await runs.run_state(ws, run_id))["state"])
            if state in states:
                return state
            await asyncio.sleep(0.1)

    return await asyncio.wait_for(poll(), timeout=within)


async def spawn(infra: Infra, run_id: str, mode: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.worker_proc",
        infra.app_conninfo,
        infra.s3_endpoint,
        infra.s3_access_key,
        infra.s3_secret_key,
        run_id,
        str(REGISTRY_ROOT),
        mode,
        cwd=BACKEND,
        stdout=asyncio.subprocess.PIPE,
    )


async def read_line(process: asyncio.subprocess.Process, within: float = 60) -> str:
    assert process.stdout is not None
    return (await asyncio.wait_for(process.stdout.readline(), timeout=within)).decode().strip()


async def test_the_worker_loop_runs_queued_runs_and_sweeps_lost_ones_periodically(
    workspace: Workspace, registry: Registry
) -> None:
    kind = registry.agents[KIND]
    engagement = f"eng-{uuid.uuid4().hex[:8]}"
    processes = [f"proc-{uuid.uuid4().hex[:10]}" for _ in range(3)]
    for pid in processes:
        await workspace.create_process(engagement, pid)
    lost = await runs.admit(
        workspace,
        process_id=processes[2],
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    assert await runs.claim(
        workspace, queue=kind.id, owner="dead-worker", lease_seconds=30, run_id=lost
    )
    async with workspace.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (lost,)
        )
    queued = [
        await runs.admit(
            workspace,
            process_id=pid,
            kind=kind,
            mode="from_scratch",
            initiated_by="c",
            entry_point="test",
        )
        for pid in processes[:2]
    ]
    stop = asyncio.Event()

    def model_for(_kind: AgentKind) -> object:
        return ScriptedSource(Script(golden_calls()))

    config = WorkerConfig(owner="worker-1", queues=(KIND,), poll_interval=0.1, sweep_interval=0.5)
    task = asyncio.create_task(run_worker(workspace, registry, model_for, config, stop))  # type: ignore[arg-type]
    try:
        finals = [
            await wait_for_state(workspace, run_id, {"committed", "rolled_back"})
            for run_id in [*queued, lost]
        ]
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)
    assert finals == ["committed", "committed", "rolled_back"]


async def test_sigterm_mid_run_rolls_back_and_the_worker_exits(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents[KIND],
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    worker = await spawn(infra, run_id, "worker")
    try:
        staged = await read_line(worker)
        worker.send_signal(signal.SIGTERM)
        stopped = await read_line(worker)
        code = await asyncio.wait_for(worker.wait(), timeout=30)
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.wait()
    state = await runs.run_state(workspace, run_id)
    assert (
        staged,
        stopped,
        code,
        state["state"],
        state["outcome"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
        await workspace.verify_rollback(run_id),
    ) == ("STAGED", "STOPPED", 0, "rolled_back", "cancelled", [], (0, 0), [])


async def test_a_sigstopped_executor_cannot_write_after_the_sweeper_took_over(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    from agentify.harness import sweeper

    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents[KIND],
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    stalled = await spawn(infra, run_id, "stall")
    try:
        staged = await read_line(stalled)
        os.kill(stalled.pid, signal.SIGSTOP)
        await asyncio.sleep(2.5)  # its 2 s lease runs out while it is frozen
        swept = await sweeper.sweep(
            workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=600
        )
        os.kill(stalled.pid, signal.SIGCONT)
        final = await read_line(stalled, within=60)
        await asyncio.wait_for(stalled.wait(), timeout=30)
    finally:
        if stalled.returncode is None:
            stalled.kill()
            await stalled.wait()
    state = await runs.run_state(workspace, run_id)
    assert (
        staged,
        [s.final_state for s in swept if s.run_id == run_id],
        final,
        state["state"],
        state["lease_owner"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
    ) == ("STAGED", ["rolled_back"], "FINAL abandoned", "rolled_back", "sweeper-1", [], (0, 0))
