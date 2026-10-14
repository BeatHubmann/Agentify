"""INV-1.PK / INV-7.PK: a kill inside the commit transaction; INV-1.SF: a database outage while
a run finishes. Both must end in a verified base state once the sweeper runs."""

import asyncio
import os
import signal
import subprocess
import sys
from typing import Any

import pytest

from agentify.agents.flow_tools import build_modeler
from agentify.harness import execute, runs, sweeper
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.conftest import REGISTRY_ROOT, Infra
from tests.failure.test_kill_and_stall import BACKEND
from tests.orchestration.test_run_exit_paths import history, leftovers
from tests.support.scripted import Script, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"


async def test_sigkill_inside_the_commit_transaction_leaves_no_revision(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.worker_proc",
        infra.app_conninfo,
        infra.s3_endpoint,
        infra.s3_access_key,
        infra.s3_secret_key,
        run_id,
        str(REGISTRY_ROOT),
        "in_commit",
        cwd=BACKEND,
        stdout=asyncio.subprocess.PIPE,
    )
    assert worker.stdout is not None
    try:
        line = (await asyncio.wait_for(worker.stdout.readline(), timeout=60)).decode()
        state_in_commit = (await runs.run_state(workspace, run_id))["state"]
        os.kill(worker.pid, signal.SIGKILL)
        await asyncio.wait_for(worker.wait(), timeout=10)
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.wait()
    await asyncio.sleep(2.5)
    swept = await sweeper.sweep(
        workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=600
    )
    state = await runs.run_state(workspace, run_id)
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    assert (
        line.strip(),
        state_in_commit,
        [s.final_state for s in swept if s.run_id == run_id],
        state["state"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
        again.startswith("run_"),
    ) == ("IN_COMMIT", "gating", ["rolled_back"], "rolled_back", [], (0, 0), True)


def docker(*args: str) -> None:
    subprocess.run(["docker", *args], check=True, capture_output=True)  # noqa: S603, S607 - test-only


async def test_database_outage_while_finishing_is_finished_by_the_sweeper(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class OutageDuringCommit(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            docker("pause", infra.postgres_container_id)
            raise OSError("database unreachable")

    ws = OutageDuringCommit(workspace.db, workspace.blobs, workspace.gate)
    kind = registry.agents[KIND]
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    claimed = await runs.claim(ws, queue=kind.id, owner="w1", lease_seconds=3, run_id=run_id)
    assert claimed is not None
    try:
        final = await execute.execute(
            ws,
            claimed,
            kind,
            agent=build_modeler("m"),
            model=Script(golden_calls()).model(),
            prompt="go",
            config=execute.HarnessConfig(lease_seconds=3, renew_every=1, storage_timeout=3),
        )
    finally:
        docker("unpause", infra.postgres_container_id)
    await asyncio.sleep(3.5)
    swept = await sweeper.sweep(
        workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=600
    )
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        [s.final_state for s in swept if s.run_id == run_id],
        state["state"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
    ) == ("left_for_sweeper", ["rolled_back"], "rolled_back", [], (0, 0))
