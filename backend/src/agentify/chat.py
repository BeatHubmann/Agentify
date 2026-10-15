"""Chat history (A§20, brief §5): server-authoritative and append-only.

The client sends only the new message; the server keeps the history. Concurrent sessions on one
process append under a per-process lock, so neither overwrites the other. Content is redacted by
the secret gate before it is stored (chat is an exit, A§18.2)."""

from dataclasses import dataclass
from typing import Literal

from agentify.store import security_events
from agentify.store.workspace import Workspace

AuthorKind = Literal["user", "agent_run"]


@dataclass(frozen=True)
class ChatMessage:
    seq: int
    author_kind: str
    author_id: str
    run_id: str | None
    content: str


async def append(
    ws: Workspace,
    *,
    process_id: str,
    author_kind: AuthorKind,
    author_id: str,
    content: str,
    run_id: str | None = None,
) -> int:
    redacted = ws.gate.redact_text(content)
    await security_events.record(
        ws.db,
        ws.gate,
        exit="chat",
        hits=redacted.hits,
        location="chat",
        process_id=process_id,
        run_id=run_id,
    )
    scrubbed = redacted.value
    async with ws.db.tx() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('chat:' || %s))", (process_id,))
        row = await (
            await conn.execute(
                "SELECT COALESCE(max(seq), 0) + 1 AS next FROM chat_message WHERE process_id = %s",
                (process_id,),
            )
        ).fetchone()
        seq = int(str(row["next"])) if row else 1
        await conn.execute(
            """INSERT INTO chat_message (process_id, seq, author_kind, author_id, run_id, content_scrubbed)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (process_id, seq, author_kind, author_id, run_id, scrubbed),
        )
    return seq


async def history(ws: Workspace, process_id: str, *, last: int = 50) -> list[ChatMessage]:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT seq, author_kind, author_id, run_id, content_scrubbed FROM chat_message
               WHERE process_id = %s ORDER BY seq DESC LIMIT %s""",
                (process_id, last),
            )
        ).fetchall()
    return [
        ChatMessage(
            int(str(r["seq"])),
            str(r["author_kind"]),
            str(r["author_id"]),
            None if r["run_id"] is None else str(r["run_id"]),
            str(r["content_scrubbed"]),
        )
        for r in reversed(rows)
    ]


def as_instructions(messages: list[ChatMessage], new_message: str) -> str:
    """The router's instructions for a chat turn: recent history, then the new message."""
    lines = [f"[{m.author_kind}] {m.content}" for m in messages]
    context = "Conversation so far:\n" + "\n".join(lines) + "\n\n" if lines else ""
    return f"{context}User: {new_message}"
