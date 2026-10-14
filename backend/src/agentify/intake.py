"""Intake: uploaded documents become `inputs/<name>` in the workspace, as user revisions (A§6).

Slice 2b scope: text documents. No UI action deletes inputs; there is no delete function."""

import re

from agentify.store import security_events
from agentify.store.workspace import UserWriter, Workspace

_NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


async def add_input(ws: Workspace, *, process_id: str, user_id: str, name: str, text: str) -> str:
    if not _NAME.fullmatch(name) or name.startswith("."):
        raise ValueError(f"invalid input name {name!r}")
    path = f"inputs/{name}"
    async with ws.db.tx() as conn:
        engagement_id = await ws.engagement_of(conn, process_id)
        head = await ws.head_revision(conn, process_id)
        if path in await ws.path_map(conn, head):
            raise ValueError(f"{path} already exists; inputs are never replaced")
    scrubbed = await security_events.checked(
        ws.db, ws.gate, text.encode("utf-8"), exit="blob", location=path, process_id=process_id
    )
    sha = await ws.blobs.put(engagement_id, scrubbed)
    return await ws.commit(
        process_id=process_id,
        writer=UserWriter(user_id),
        base_revision_id=head,
        staged={path: sha},
        message=f"user {user_id}: add {path}",
    )
