"""Run prompts: the admission instructions plus a workspace snapshot of the kind's inputs (§7.10).

The snapshot saves read turns for parallel section agents. It is bounded; when it is cut short the
prompt says so, and the agent still has its read tools."""

from agentify.registry import AgentKind
from agentify.store.workspace import Workspace

SNAPSHOT_LIMIT_CHARS = 200_000


async def compose_prompt(
    ws: Workspace,
    *,
    process_id: str,
    base_revision_id: str | None,
    kind: AgentKind,
    instructions: str,
    output_language: str,
) -> str:
    parts = [instructions or f"Run {kind.id}.", f"Write all output in language: {output_language}."]
    paths: list[str] = []
    for entry in kind.input_paths:
        if entry.endswith("/"):
            paths.extend(await ws.list_paths(process_id, entry, base_revision_id))
        else:
            paths.append(entry)
    budget = SNAPSHOT_LIMIT_CHARS
    snapshot: list[str] = []
    for path in paths:
        data = await ws.read(process_id, path, base_revision_id)
        if data is None:
            continue
        text = data.decode("utf-8", errors="replace")
        if len(text) > budget:
            snapshot.append(
                f"--- {path} (not included: snapshot limit reached; use read_artifact) ---"
            )
            continue
        budget -= len(text)
        snapshot.append(f"--- {path} ---\n{text}")
    if snapshot:
        parts.append("Workspace snapshot at your base revision:\n" + "\n".join(snapshot))
    return "\n\n".join(parts)
