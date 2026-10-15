"""Run records: admission, claiming, leases and cancellation (A§8.2 to A§8.5)."""

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from psycopg import errors

from agentify.domain.edits import EditLayer, merge
from agentify.domain.envelope import Corrupt, decode_edit_layer, decode_flow
from agentify.domain.flow import Flow
from agentify.registry import AgentKind, ArtifactAgentKind, FlowAgentKind
from agentify.secretgate import SecretFound
from agentify.store import security_events
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
    instructions: str = ""
    parent_run_id: str | None = None
    entry_point: str = ""


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
    instructions: str = "",
    parent_run_id: str | None = None,
) -> str:
    """The only function that creates runs (A§8.2). Every entry point calls it."""
    async with ws.db.tx() as conn:
        return await admit_in(
            ws,
            conn,
            process_id=process_id,
            kind=kind,
            mode=mode,
            initiated_by=initiated_by,
            entry_point=entry_point,
            intent_confirmation_id=intent_confirmation_id,
            instructions=instructions,
            parent_run_id=parent_run_id,
        )


async def _check_flow_admission(
    ws: Workspace, conn: Conn, *, process_id: str, kind: FlowAgentKind, mode: Mode, base: str | None
) -> bool:
    """Fail-closed checks for flow kinds. Returns whether a from-scratch run has something to lose."""
    flow_data = await _read_at(ws, conn, process_id, base, kind.output_path)
    layer_data = await _read_at(ws, conn, process_id, base, kind.edit_layer_path)
    layer: EditLayer | Corrupt = (
        EditLayer() if layer_data is None else decode_edit_layer(layer_data)
    )
    if mode == "from_scratch":
        return await _has_something_to_lose(flow_data, layer)
    if isinstance(layer, Corrupt):
        raise AdmissionRefused("edit_layer_unreadable", layer.reason)
    flow = Flow(mode=kind.flow_mode) if flow_data is None else decode_flow(flow_data)
    if isinstance(flow, Corrupt):
        raise AdmissionRefused("flow_corrupt", flow.reason)
    ghosts = merge(flow, layer).report.ghost_locks
    if ghosts:
        raise AdmissionRefused("ghost_locks", ", ".join(ghosts))
    return False


async def _check_artifact_admission(
    ws: Workspace,
    conn: Conn,
    *,
    process_id: str,
    kind: ArtifactAgentKind,
    mode: Mode,
    base: str | None,
) -> bool:
    existing = {
        path: await _read_at(ws, conn, process_id, base, path) for path in kind.output_paths
    }
    if mode == "from_scratch":
        return any(data is not None for data in existing.values())
    for path, data in existing.items():
        if data is not None:
            try:
                json.loads(data)
            except ValueError as exc:
                raise AdmissionRefused("output_corrupt", path) from exc
    return False


async def admit_in(
    ws: Workspace,
    conn: Conn,
    *,
    process_id: str,
    kind: AgentKind,
    mode: Mode,
    initiated_by: str,
    entry_point: str,
    intent_confirmation_id: str | None = None,
    instructions: str = "",
    parent_run_id: str | None = None,
) -> str:
    """Admission inside a caller's transaction (follow-up turns consume their notices atomically)."""
    if mode not in kind.modes:
        raise AdmissionRefused("mode_not_supported", mode)
    run_id = f"run_{uuid.uuid4().hex}"
    process = await (
        await conn.execute("SELECT quarantined_at FROM process WHERE id = %s", (process_id,))
    ).fetchone()
    if process is None:
        raise AdmissionRefused("unknown_process", process_id)
    if process["quarantined_at"] is not None:
        raise AdmissionRefused("process_quarantined")
    base = await ws.head_revision(conn, process_id)
    if isinstance(kind, FlowAgentKind):
        something_to_lose = await _check_flow_admission(
            ws, conn, process_id=process_id, kind=kind, mode=mode, base=base
        )
    elif isinstance(kind, ArtifactAgentKind):
        something_to_lose = await _check_artifact_admission(
            ws, conn, process_id=process_id, kind=kind, mode=mode, base=base
        )
    else:
        something_to_lose = False
    if something_to_lose:
        await _consume_intent(
            conn,
            confirmation_id=intent_confirmation_id,
            process_id=process_id,
            kind=kind.id,
            head=base,
            run_id=run_id,
        )
    try:
        async with (
            conn.transaction()
        ):  # a savepoint, so a refusal leaves the caller's transaction usable
            await conn.execute(
                """INSERT INTO run (id, process_id, kind, mode, state, base_revision_id, initiated_by,
                                    entry_point, intent_confirmation_id, instructions, parent_run_id)
                   VALUES (%s, %s, %s, %s, 'admitted', %s, %s, %s, %s, %s, %s)""",
                (
                    run_id,
                    process_id,
                    kind.id,
                    mode,
                    base,
                    initiated_by,
                    entry_point,
                    intent_confirmation_id if something_to_lose else None,
                    instructions,
                    parent_run_id,
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
               RETURNING id, process_id, kind, mode, base_revision_id, initiated_by, lease_epoch,
                         instructions, parent_run_id, entry_point""",
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
        instructions=str(run["instructions"]),
        parent_run_id=None if run["parent_run_id"] is None else str(run["parent_run_id"]),
        entry_point=str(run["entry_point"]),
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


async def latest_confirmation(ws: Workspace, *, process_id: str, run_kind: str) -> str | None:
    """The newest unconsumed, fresh confirmation a human gave for this process and kind, if any."""
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                """SELECT id FROM intent_confirmation
               WHERE process_id = %s AND run_kind = %s AND consumed_by_run IS NULL
                 AND created_at > now() - make_interval(mins => %s)
               ORDER BY created_at DESC LIMIT 1""",
                (process_id, run_kind, INTENT_WINDOW_MINUTES),
            )
        ).fetchone()
    return None if row is None else str(row["id"])


async def record_result(ws: Workspace, fence: Fence, text: str) -> None:
    """Store a run's final text (router turns). Text that the secret gate refuses is withheld."""
    try:
        ws.gate.check_bytes(text.encode("utf-8"), where="run.result_text")
    except SecretFound as exc:
        text = "[withheld: the secret gate refused this text]"
        await security_events.record(
            ws.db,
            ws.gate,
            exit="run_record",
            hits=[exc.hit],
            location="run.result_text",
            run_id=fence.run_id,
        )
    async with ws.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET result_text_scrubbed = %s WHERE id = %s AND lease_owner = %s AND lease_epoch = %s",
            (text, fence.run_id, fence.owner, fence.epoch),
        )


async def record_continuation(ws: Workspace, run_id: str) -> None:
    """When a router-dispatched run ends, leave a durable notice for a router follow-up turn."""
    async with ws.db.tx() as conn:
        await conn.execute(
            """INSERT INTO router_continuation (id, process_id, child_run_id)
               SELECT %s, process_id, id FROM run
               WHERE id = %s AND parent_run_id IS NOT NULL
                 AND state IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed')
               ON CONFLICT (child_run_id) DO NOTHING""",
            (f"cont_{uuid.uuid4().hex}", run_id),
        )


async def admit_router_followups(ws: Workspace, router: AgentKind) -> list[str]:
    """Admit one router turn per process with unconsumed notices, consuming them atomically."""
    admitted: list[str] = []
    async with ws.db.tx() as conn:
        processes = await (
            await conn.execute(
                "SELECT DISTINCT process_id FROM router_continuation WHERE consumed_by_run IS NULL"
            )
        ).fetchall()
    for row in processes:
        process_id = str(row["process_id"])
        try:
            async with ws.db.tx() as conn:
                notices = await (
                    await conn.execute(
                        """SELECT c.id, r.kind, r.id AS child, r.state, r.outcome FROM router_continuation c
                       JOIN run r ON r.id = c.child_run_id
                       WHERE c.process_id = %s AND c.consumed_by_run IS NULL
                       ORDER BY c.created_at FOR UPDATE OF c SKIP LOCKED""",
                        (process_id,),
                    )
                ).fetchall()
                if not notices:
                    continue
                summary = "; ".join(
                    f"{n['kind']} {n['child']}: {n['state']} ({n['outcome']})" for n in notices
                )
                run_id = await admit_in(
                    ws,
                    conn,
                    process_id=process_id,
                    kind=router,
                    mode="incremental",
                    initiated_by="system",
                    entry_point="router_followup",
                    instructions=f"Dispatched runs ended: {summary}.",
                )
                await conn.execute(
                    "UPDATE router_continuation SET consumed_by_run = %s WHERE id = ANY(%s)",
                    (run_id, [str(n["id"]) for n in notices]),
                )
                admitted.append(run_id)
        except AdmissionRefused:
            continue  # a router turn is active; the notices wait for the next pass
    return admitted


async def stop_requested(
    ws: Workspace, run_id: str, *, attachment_grace_seconds: float
) -> str | None:
    """`cancelled`, `disconnected`, or None (A§8.5).

    A run whose stream was attached and whose attachment is older than the grace period is stopped
    like a cancel: the check records the cancel, so every later check (including the commit's) sees it."""
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                """SELECT cancel_requested_at IS NOT NULL AS cancelled,
                      stream_attached_until IS NOT NULL
                        AND stream_attached_until < now() - make_interval(secs => %s) AS gone
               FROM run WHERE id = %s""",
                (attachment_grace_seconds, run_id),
            )
        ).fetchone()
        if row is None:
            return None
        if row["cancelled"]:
            return "cancelled"
        if row["gone"]:
            await conn.execute(
                "UPDATE run SET cancel_requested_at = now() WHERE id = %s", (run_id,)
            )
            return "disconnected"
    return None


async def attach(ws: Workspace, run_id: str, *, seconds: float) -> bool:
    """A client is attached to this run's stream for the next `seconds` (durable heartbeat)."""
    async with ws.db.tx() as conn:
        moved = await conn.execute(
            """UPDATE run SET stream_attached_until = now() + make_interval(secs => %s)
               WHERE id = %s AND state NOT IN ('committed', 'unchanged', 'rolled_back', 'rollback_failed')""",
            (seconds, run_id),
        )
    return moved.rowcount == 1
