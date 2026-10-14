"""Security events and the per-engagement allow-list (A§18.3). Neither ever holds a secret: a hit
is stored by rule and keyed fingerprint, and its location passes the gate first.

Events are written in their own transaction, so they survive the rollback of the write that
caused them."""

from collections.abc import Sequence
from typing import Literal

from agentify.secretgate import Hit, SecretGate
from agentify.store.db import Database

Exit = Literal["staged_write", "blob", "inbox", "chat", "run_event", "run_record", "http_body"]
AllowAction = Literal["allow", "revoke"]


async def record(
    db: Database,
    gate: SecretGate,
    *,
    exit: Exit,
    hits: Sequence[Hit],
    location: str,
    process_id: str | None = None,
    run_id: str | None = None,
) -> None:
    if not hits:
        return
    where = gate.redact_text(location).value
    async with db.tx() as conn:
        for hit in hits:
            await conn.execute(
                """WITH p AS (SELECT COALESCE(%s, (SELECT process_id FROM run WHERE id = %s)) AS id)
                   INSERT INTO security_event
                     (engagement_id, process_id, run_id, exit, rule, fingerprint, location)
                   SELECT (SELECT engagement_id FROM process WHERE id = p.id), p.id, %s, %s, %s, %s, %s
                   FROM p""",
                (process_id, run_id, run_id, exit, hit.rule, hit.fingerprint, where),
            )


async def hits_in_run(db: Database, run_id: str, hit: Hit) -> int:
    async with db.tx() as conn:
        row = await (
            await conn.execute(
                """SELECT count(*) AS n FROM security_event
                   WHERE run_id = %s AND rule = %s AND fingerprint = %s""",
                (run_id, hit.rule, hit.fingerprint),
            )
        ).fetchone()
    return int(str(row["n"])) if row else 0


async def allowed(db: Database, engagement_id: str) -> frozenset[Hit]:
    """The engagement's active entries: for each fingerprint, the latest entry decides."""
    async with db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT DISTINCT ON (rule, fingerprint) rule, fingerprint, action
                   FROM secret_allowlist_entry WHERE engagement_id = %s
                   ORDER BY rule, fingerprint, seq DESC""",
                (engagement_id,),
            )
        ).fetchall()
    return frozenset(
        Hit(str(r["rule"]), str(r["fingerprint"])) for r in rows if r["action"] == "allow"
    )


async def add_allowlist_entry(
    db: Database,
    *,
    engagement_id: str,
    hit: Hit,
    action: AllowAction,
    justification: str,
    admin_id: str,
) -> None:
    async with db.tx() as conn:
        await conn.execute(
            """INSERT INTO secret_allowlist_entry
                 (engagement_id, rule, fingerprint, action, justification, admin_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (engagement_id, hit.rule, hit.fingerprint, action, justification, admin_id),
        )
