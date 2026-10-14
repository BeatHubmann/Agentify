"""Run records: admission, claiming, leases and cancellation (A§8.2 to A§8.5)."""

import uuid
from dataclasses import dataclass
from typing import Literal

from psycopg import errors

from agentify.domain.edits import EditLayer, merge
from agentify.domain.envelope import Corrupt, decode_edit_layer, decode_flow
from agentify.domain.flow import Flow
from agentify.registry import AgentKind
from agentify.store.workspace import Conn, Fence, Workspace

Mode = Literal["from_scratch", "incremental"]
INTENT_WINDOW_MINUTES = 15
TERMINAL_STATES = frozenset({"committed", "unchanged", "rolled_back", "rollback_failed"})


class AdmissionRefused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ClaimedRun:
    run_id: str
    process_id: str
    kind: str
    mode: Mode
    base_revision_id: str | None
    initiated_by: str
    fence: Fence


async def _read_at(
    ws: Workspace, conn: Conn, process_id: str, revision_id: str | None, path: str
) -> bytes | None:
    sha = (await ws.path_map(conn, revision_id)).get(path)
    if sha is None:
        return None
    return await ws.blobs.get(await ws.engagement_of(conn, process_id), sha)


async def _has_something_to_lose(flow_data: bytes | None, layer: EditLayer | Corrupt) -> bool:
    if isinstance(layer, Corrupt) or layer.locks:
        return True
    if flow_data is None:
        return False
    decoded = decode_flow(flow_data)
    return isinstance(decoded, Corrupt) or bool(decoded.nodes)


async def admit(
    ws: Workspace,
    *,
    process_id: str,
    kind: AgentKind,
    mode: Mode,
    initiated_by: str,
    entry_point: str,
    intent_confirmation_id: str | None = None,
) -> str:
    """The only function that creates runs (A§8.2). Every entry point calls it."""
    if mode not in kind.modes:
        raise AdmissionRefused("mode_not_supported", mode)
    run_id = f"run_{uuid.uuid4().hex}"
    async with ws.db.tx() as conn:
        process = await (
            await conn.execute("SELECT quarantined_at FROM process WHERE id = %s", (process_id,))
        ).fetchone()
        if process is None:
            raise AdmissionRefused("unknown_process", process_id)
        if process["quarantined_at"] is not None:
            raise AdmissionRefused("process_quarantined")
        base = await ws.head_revision(conn, process_id)
        flow_data = await _read_at(ws, conn, process_id, base, kind.output_path)
        layer_data = await _read_at(ws, conn, process_id, base, kind.edit_layer_path)
        layer: EditLayer | Corrupt = (
            EditLayer() if layer_data is None else decode_edit_layer(layer_data)
        )
        if mode == "incremental":
            if isinstance(layer, Corrupt):
                raise AdmissionRefused("edit_layer_unreadable", layer.reason)
            flow = Flow(mode=kind.flow_mode) if flow_data is None else decode_flow(flow_data)
            if isinstance(flow, Corrupt):
                raise AdmissionRefused("flow_corrupt", flow.reason)
            ghosts = merge(flow, layer).report.ghost_locks
            if ghosts:
                raise AdmissionRefused("ghost_locks", ", ".join(ghosts))
        elif await _has_something_to_lose(flow_data, layer):
            await _consume_intent(
                conn,
                confirmation_id=intent_confirmation_id,
                process_id=process_id,
                kind=kind.id,
                head=base,
                run_id=run_id,
            )
        try:
            await conn.execute(
                """INSERT INTO run (id, process_id, kind, mode, state, base_revision_id, initiated_by,
                                    entry_point, intent_confirmation_id)
                   VALUES (%s, %s, %s, %s, 'admitted', %s, %s, %s, %s)""",
                (
                    run_id,
                    process_id,
                    kind.id,
                    mode,
                    base,
                    initiated_by,
                    entry_point,
                    intent_confirmation_id,
                ),
            )
        except errors.UniqueViolation as exc:
            raise AdmissionRefused("already_running", kind.id) from exc
        await conn.execute(
            "INSERT INTO job (id, queue, run_id) VALUES (%s, %s, %s)",
            (f"job_{uuid.uuid4().hex}", kind.id, run_id),
        )
    return run_id


async def _consume_intent(
    conn: Conn,
    *,
    confirmation_id: str | None,
    process_id: str,
    kind: str,
    head: str | None,
    run_id: str,
) -> None:
    """From-scratch over something to lose needs attributed, single-use, fresh human intent."""
    if confirmation_id is None:
        raise AdmissionRefused("needs_confirmation", "from-scratch would discard the current flow")
    consumed = await conn.execute(
        """UPDATE intent_confirmation SET consumed_by_run = %s
           WHERE id = %s AND process_id = %s AND run_kind = %s AND consumed_by_run IS NULL
             AND head_revision_id IS NOT DISTINCT FROM %s
             AND created_at > now() - make_interval(mins => %s)""",
        (run_id, confirmation_id, process_id, kind, head, INTENT_WINDOW_MINUTES),
    )
    if consumed.rowcount == 0:
        raise AdmissionRefused(
            "confirmation_invalid", "missing, used, expired or for another state"
        )


async def confirm_intent(
    ws: Workspace, *, process_id: str, run_kind: str, confirmed_by: str
) -> str:
    """Record a human's explicit intent (API action or inbox answer) for one from-scratch run."""
    confirmation_id = f"conf_{uuid.uuid4().hex}"
    async with ws.db.tx() as conn:
        head = await ws.head_revision(conn, process_id)
        await conn.execute(
            """INSERT INTO intent_confirmation (id, process_id, run_kind, head_revision_id, confirmed_by)
               VALUES (%s, %s, %s, %s, %s)""",
            (confirmation_id, process_id, run_kind, head, confirmed_by),
        )
    return confirmation_id


async def claim(
    ws: Workspace, *, queue: str, owner: str, lease_seconds: float, run_id: str | None = None
) -> ClaimedRun | None:
    """Claim one admitted run (optionally a specific one, for an interactive executor); take its
    lease and a new fencing epoch in the same transaction."""
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                """SELECT j.id AS job_id, r.id AS run_id FROM job j JOIN run r ON r.id = j.run_id
               WHERE j.queue = %s AND j.state = 'queued' AND r.state = 'admitted'
                 AND (%s::text IS NULL OR r.id = %s)
               ORDER BY j.created_at FOR UPDATE OF j, r SKIP LOCKED LIMIT 1""",
                (queue, run_id, run_id),
            )
        ).fetchone()
        if row is None:
            return None
        run = await (
            await conn.execute(
                """UPDATE run SET state = 'running', lease_owner = %s, lease_epoch = lease_epoch + 1,
                 lease_expires_at = now() + make_interval(secs => %s)
               WHERE id = %s AND state = 'admitted'
               RETURNING id, process_id, kind, mode, base_revision_id, initiated_by, lease_epoch""",
                (owner, lease_seconds, row["run_id"]),
            )
        ).fetchone()
        await conn.execute(
            "UPDATE job SET state = 'claimed', attempts = attempts + 1 WHERE id = %s",
            (row["job_id"],),
        )
    if run is None:
        return None
    return ClaimedRun(
        run_id=str(run["id"]),
        process_id=str(run["process_id"]),
        kind=str(run["kind"]),
        mode=run["mode"],  # type: ignore[arg-type]
        base_revision_id=None if run["base_revision_id"] is None else str(run["base_revision_id"]),
        initiated_by=str(run["initiated_by"]),
        fence=Fence(str(run["id"]), owner, int(str(run["lease_epoch"]))),
    )


async def renew_lease(ws: Workspace, fence: Fence, lease_seconds: float) -> bool:
    async with ws.db.tx() as conn:
        renewed = await conn.execute(
            """UPDATE run SET lease_expires_at = now() + make_interval(secs => %s)
               WHERE id = %s AND lease_owner = %s AND lease_epoch = %s AND state IN ('running', 'gating')""",
            (lease_seconds, fence.run_id, fence.owner, fence.epoch),
        )
    return renewed.rowcount == 1


async def move_to_gating(ws: Workspace, fence: Fence) -> bool:
    async with ws.db.tx() as conn:
        moved = await conn.execute(
            """UPDATE run SET state = 'gating' WHERE id = %s AND state = 'running'
                 AND lease_owner = %s AND lease_epoch = %s""",
            (fence.run_id, fence.owner, fence.epoch),
        )
    return moved.rowcount == 1


async def mark_unchanged(ws: Workspace, fence: Fence) -> bool:
    async with ws.db.tx() as conn:
        moved = await conn.execute(
            """UPDATE run SET state = 'unchanged', outcome = 'unchanged', finished_at = now()
               WHERE id = %s AND state = 'gating' AND lease_owner = %s AND lease_epoch = %s
                 AND cancel_requested_at IS NULL
                 AND NOT EXISTS (SELECT 1 FROM run_staging WHERE run_id = %s)""",
            (fence.run_id, fence.owner, fence.epoch, fence.run_id),
        )
    return moved.rowcount == 1


async def request_cancel(ws: Workspace, run_id: str) -> None:
    async with ws.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET cancel_requested_at = now() WHERE id = %s AND cancel_requested_at IS NULL "
            "AND state NOT IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed')",
            (run_id,),
        )


async def cancel_requested(ws: Workspace, run_id: str) -> bool:
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                "SELECT cancel_requested_at IS NOT NULL AS c FROM run WHERE id = %s", (run_id,)
            )
        ).fetchone()
    return bool(row and row["c"])


async def run_state(ws: Workspace, run_id: str) -> dict[str, object]:
    async with ws.db.tx() as conn:
        row = await (await conn.execute("SELECT * FROM run WHERE id = %s", (run_id,))).fetchone()
    if row is None:
        raise KeyError(run_id)
    return dict(row)
