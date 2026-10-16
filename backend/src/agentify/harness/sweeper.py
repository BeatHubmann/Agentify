"""Startup and periodic recovery of runs whose executor is gone (A§8.5, HZ-13).

The sweeper takes a run over by bumping its fencing epoch, so a stalled executor that wakes up
can no longer write or commit; then it finishes the run: committed if a revision carries its id,
otherwise rolled back and verified. It also completes terminal runs whose completion effects were
never written, because the process that ended them died first (`runs.complete`).
"""

from dataclasses import dataclass

from agentify.harness import runs
from agentify.store.workspace import Fence, Workspace

ACTIVE = ("running", "gating", "committing", "rolling_back")


@dataclass(frozen=True)
class SweepResult:
    run_id: str
    final_state: str


async def sweep(
    ws: Workspace, *, owner: str, lease_seconds: float, admission_timeout_seconds: float
) -> list[SweepResult]:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT id, lease_epoch FROM run
               WHERE (state = ANY(%s) AND lease_expires_at < now())
                  OR (state = 'admitted' AND created_at < now() - make_interval(secs => %s))
               ORDER BY created_at""",
                (list(ACTIVE), admission_timeout_seconds),
            )
        ).fetchall()
    results: list[SweepResult] = []
    for row in rows:
        fence = await _take_over(
            ws, str(row["id"]), int(str(row["lease_epoch"])), owner, lease_seconds
        )
        if fence is None:
            continue
        final = await _finish_lost_run(ws, fence)
        await runs.complete(ws, fence.run_id)
        results.append(SweepResult(fence.run_id, final))
    for run_id in await _incomplete(ws):
        state = await runs.complete(ws, run_id)
        if state is not None:
            results.append(SweepResult(run_id, state))
    return results


async def _incomplete(ws: Workspace) -> list[str]:
    """Terminal runs without their completion effects, once the lease of whoever ended them is
    over: a live executor completes its own run, after its rollback verification has settled the
    final state. The states are spelled as in the partial index `run_incomplete` (migration 0009),
    so a prepared statement can still use it."""
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT id FROM run WHERE completed_at IS NULL
                     AND state IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed')
                     AND COALESCE(lease_expires_at, finished_at) < now() ORDER BY finished_at"""
            )
        ).fetchall()
    return [str(r["id"]) for r in rows]


async def _take_over(
    ws: Workspace, run_id: str, epoch: int, owner: str, lease_seconds: float
) -> Fence | None:
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                """UPDATE run SET lease_owner = %s, lease_epoch = lease_epoch + 1,
                 lease_expires_at = now() + make_interval(secs => %s)
               WHERE id = %s AND lease_epoch = %s
                 AND state NOT IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed')
               RETURNING lease_epoch""",
                (owner, lease_seconds, run_id, epoch),
            )
        ).fetchone()
    return None if row is None else Fence(run_id, owner, int(str(row["lease_epoch"])))


async def _finish_lost_run(ws: Workspace, fence: Fence) -> str:
    async with ws.db.tx() as conn:
        committed = await (
            await conn.execute("SELECT 1 FROM revision WHERE run_id = %s", (fence.run_id,))
        ).fetchone()
        if committed is not None:
            await conn.execute(
                """UPDATE run SET state = 'committed', outcome = 'committed', finished_at = now()
                   WHERE id = %s AND lease_owner = %s AND lease_epoch = %s""",
                (fence.run_id, fence.owner, fence.epoch),
            )
    if committed is not None:
        return "committed"
    verified = await ws.rollback(fence, outcome="executor_lost")
    return "rolled_back" if verified else "rollback_failed"
