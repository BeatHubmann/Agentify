"""INV-1.PK, INV-1.SF and HZ-13 after the run is terminal (external review R2): a process that dies
between ending a run and delivering its completion leaves no stream waiting, no router follow-up
missing and no reservation open. Recovery delivers each effect exactly once."""

import asyncio
import os
import signal
import sys
import uuid
from typing import Any

import pytest

from agentify.harness import runs, sweeper
from agentify.registry import Registry
from agentify.store import budget
from agentify.store.workspace import Workspace
from tests.conftest import REGISTRY_ROOT, Infra
from tests.failure.test_kill_and_stall import BACKEND
from tests.orchestration.test_run_exit_paths import history

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"


class DiesAfterRollback(Workspace):
    """Fault injection: the rollback completes, then the process fails before anything else."""

    async def rollback(self, fence: Any, *, outcome: str, detail: str | None = None) -> bool:
        await super().rollback(fence, outcome=outcome, detail=detail)
        raise OSError("process died after the run ended")


async def completion(ws: Workspace, run_id: str) -> tuple[list[str], str, int, int]:
    """`finished` states, the last event's kind, continuations and open reservations."""
    async with ws.db.tx() as conn:
        events = await (
            await conn.execute(
                "SELECT kind, payload_scrubbed FROM run_event WHERE run_id = %s ORDER BY seq",
                (run_id,),
            )
        ).fetchall()
        counts = await (
            await conn.execute(
                """SELECT (SELECT count(*) FROM router_continuation WHERE child_run_id = %s) AS c,
                          (SELECT count(*) FROM budget_reservation
                           WHERE run_id = %s AND state = 'open') AS r""",
                (run_id, run_id),
            )
        ).fetchone()
    assert counts is not None
    finished = [str(e["payload_scrubbed"]["state"]) for e in events if e["kind"] == "finished"]  # type: ignore[index]
    last = str(events[-1]["kind"]) if events else ""
    return finished, last, int(str(counts["c"])), int(str(counts["r"]))


async def child_run(ws: Workspace, registry: Registry, process_id: str) -> str:
    """A router-dispatched run with one open reservation, as a model call in flight leaves it."""
    kind = registry.agents[KIND]
    router_process = f"proc-{uuid.uuid4().hex[:12]}"
    async with ws.db.tx() as conn:
        engagement = await ws.engagement_of(conn, process_id)
    await ws.create_process(engagement, router_process)
    parent = await runs.admit(
        ws,
        process_id=router_process,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="router",
        parent_run_id=parent,
    )
    await budget.reserve(
        ws.db,
        budget.Scopes(run_id, process_id, engagement),
        deployment="local",
        model_id="m",
        amount=500_000,
        run_ceiling=2_000_000,
    )
    return run_id


async def test_an_executor_killed_after_its_commit_has_its_completion_delivered_once(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    run_id = await child_run(workspace, registry, process_id)
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
        "after_commit",
        cwd=BACKEND,
        stdout=asyncio.subprocess.PIPE,
    )
    assert worker.stdout is not None
    try:
        line = (await asyncio.wait_for(worker.stdout.readline(), timeout=60)).decode()
        os.kill(worker.pid, signal.SIGKILL)
        await asyncio.wait_for(worker.wait(), timeout=10)
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.wait()
    left = await completion(workspace, run_id)
    await asyncio.sleep(2.5)  # the dead executor's lease (2 s) runs out
    for owner in ("sweeper-1", "sweeper-2"):
        await sweeper.sweep(workspace, owner=owner, lease_seconds=30, admission_timeout_seconds=600)
    assert (
        line.strip(),
        (await runs.run_state(workspace, run_id))["state"],
        left,
        await completion(workspace, run_id),
        len(await history(workspace, process_id)),
    ) == (
        "COMMITTED",
        "committed",
        ([], "text", 0, 1),
        (["committed"], "finished", 1, 0),
        1,
    )


async def test_a_sweeper_that_fails_after_ending_a_lost_run_is_completed_by_the_next_sweep(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    run_id = await child_run(workspace, registry, process_id)
    lost = await runs.claim(workspace, queue=KIND, owner="dies", lease_seconds=30, run_id=run_id)
    assert lost is not None
    async with workspace.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (run_id,)
        )
    failing = DiesAfterRollback(workspace.db, workspace.blobs, workspace.gate)
    with pytest.raises(OSError, match="process died"):
        await sweeper.sweep(
            failing, owner="sweeper-1", lease_seconds=1, admission_timeout_seconds=600
        )
    left = await completion(workspace, run_id)
    await asyncio.sleep(1.5)  # the failed sweeper's lease runs out
    for owner in ("sweeper-2", "sweeper-3"):
        await sweeper.sweep(workspace, owner=owner, lease_seconds=30, admission_timeout_seconds=600)
    assert (
        (await runs.run_state(workspace, run_id))["state"],
        left,
        await completion(workspace, run_id),
        await history(workspace, process_id),
    ) == ("rolled_back", ([], "", 0, 1), (["rolled_back"], "finished", 1, 0), [])
