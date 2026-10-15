"""The inbox (A§6.6, A§20): the only channel through which agents ask humans anything.

Items are immutable versions keyed `(item_id, version)`; a status change is a new version. Answers
in human-only categories are accepted only from people, and answering a confirmation item is the
only way to create the human intent a from-scratch run needs (A§8.2)."""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store import security_events
from agentify.store.workspace import Workspace


@dataclass(frozen=True)
class UserAuthor:
    user_id: str


@dataclass(frozen=True)
class AgentAuthor:
    run_id: str


Author = UserAuthor | AgentAuthor


class InboxRejected(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class InboxItem:
    item_id: str
    version: int
    kind: str
    category: str
    status: str
    body: str
    options: list[str]
    metadata: dict[str, Any]
    author_kind: str
    author_id: str
    parent_item_id: str | None


def _as_list(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def _body(ws: Workspace, process_id: str, text: str, run_id: str | None = None) -> str:
    """Inbox items fail on a gate hit, never stored redacted (A§18.2)."""
    await security_events.checked(
        ws.db,
        ws.gate,
        text.encode("utf-8"),
        exit="inbox",
        location="inbox",
        process_id=process_id,
        run_id=run_id,
    )
    return text


async def ask(
    ws: Workspace,
    registry: Registry,
    *,
    process_id: str,
    category: str,
    question: str,
    options: list[str],
    run_id: str | None,
    kind: Literal["agent_question", "confirmation"] = "agent_question",
    metadata: dict[str, Any] | None = None,
) -> str:
    if category not in registry.inbox_categories:
        raise InboxRejected("unknown_category", category)
    item_id = f"ibx_{uuid.uuid4().hex}"
    body = await _body(ws, process_id, question, run_id)
    async with ws.db.tx() as conn:
        await conn.execute(
            """INSERT INTO inbox_item (item_id, version, process_id, kind, category, status, options,
                                       body_scrubbed, metadata, author_kind, author_id, run_id)
               VALUES (%s, 1, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)""",
            (
                item_id,
                process_id,
                kind,
                category,
                json.dumps(options),
                body,
                json.dumps(metadata or {}),
                "agent_run" if run_id else "system",
                run_id or "system",
                run_id,
            ),
        )
    return item_id


async def items(ws: Workspace, process_id: str, *, status: str | None = None) -> list[InboxItem]:
    """The latest version of every item, oldest first."""
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT DISTINCT ON (item_id) * FROM inbox_item WHERE process_id = %s
               ORDER BY item_id, version DESC""",
                (process_id,),
            )
        ).fetchall()
    found = [
        InboxItem(
            str(r["item_id"]),
            int(str(r["version"])),
            str(r["kind"]),
            str(r["category"]),
            str(r["status"]),
            str(r["body_scrubbed"]),
            _as_list(r["options"]),
            _as_dict(r["metadata"]),
            str(r["author_kind"]),
            str(r["author_id"]),
            None if r["parent_item_id"] is None else str(r["parent_item_id"]),
        )
        for r in rows
    ]
    found.sort(key=lambda item: item.item_id)
    return [item for item in found if status is None or item.status == status]


async def answer(
    ws: Workspace, registry: Registry, *, process_id: str, item_id: str, author: Author, text: str
) -> str:
    """Record an answer as a new item and a new version of the question. Returns the answer's id."""
    question = next((i for i in await items(ws, process_id) if i.item_id == item_id), None)
    if question is None:
        raise InboxRejected("unknown_item", item_id)
    category = registry.inbox_categories[question.category]
    if not isinstance(author, UserAuthor) and (
        category.human_only_answers or question.kind == "confirmation"
    ):
        raise InboxRejected(
            "human_answer_required", f"{question.category} answers come only from people"
        )
    if question.status != "open":
        raise InboxRejected("already_answered", item_id)
    if question.options and text not in question.options:
        raise InboxRejected("not_an_option", text)
    body = await _body(ws, process_id, text)  # before any side effect
    metadata: dict[str, Any] = {}
    if question.kind == "confirmation" and isinstance(author, UserAuthor) and text == "confirm":
        metadata["intent_confirmation_id"] = await runs.confirm_intent(
            ws,
            process_id=process_id,
            run_kind=str(question.metadata["run_kind"]),
            confirmed_by=author.user_id,
        )
    answer_id = f"ibx_{uuid.uuid4().hex}"
    author_kind, author_id = (
        ("user", author.user_id) if isinstance(author, UserAuthor) else ("agent_run", author.run_id)
    )
    async with ws.db.tx() as conn:
        await conn.execute(
            """INSERT INTO inbox_item (item_id, version, process_id, parent_item_id, kind, category, status,
                                       body_scrubbed, metadata, author_kind, author_id)
               VALUES (%s, 1, %s, %s, 'user_answer', %s, 'answered', %s, %s, %s, %s)""",
            (
                answer_id,
                process_id,
                item_id,
                question.category,
                body,
                json.dumps(metadata),
                author_kind,
                author_id,
            ),
        )
        inserted = await conn.execute(
            """INSERT INTO inbox_item (item_id, version, process_id, parent_item_id, kind, category, status,
                                       options, body_scrubbed, metadata, author_kind, author_id, run_id)
               SELECT item_id, version + 1, process_id, parent_item_id, kind, category, 'answered', options,
                      body_scrubbed, metadata, author_kind, author_id, run_id
               FROM inbox_item WHERE item_id = %s AND version = %s
               ON CONFLICT DO NOTHING""",
            (item_id, question.version),
        )
        if inserted.rowcount == 0:
            raise InboxRejected("already_answered", item_id)
    return answer_id
