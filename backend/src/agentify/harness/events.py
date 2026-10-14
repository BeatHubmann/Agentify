"""Run events (A§2, A§8.4): what the agent said and did, step by step, for live previews and the
chat stream. Events pass the secret gate (redacted) before storage, and are fenced by the run's
lease so a stale executor cannot add to a run another process now owns."""

import json
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from agentify.store import security_events
from agentify.store.workspace import Fence, Workspace


async def publish(ws: Workspace, fence: Fence, kind: str, payload: dict[str, Any]) -> None:
    redacted = ws.gate.redact_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    await security_events.record(
        ws.db,
        ws.gate,
        exit="run_event",
        hits=redacted.hits,
        location=f"run_event.{kind}",
        run_id=fence.run_id,
    )
    scrubbed = redacted.value
    async with ws.db.tx() as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('events:' || %s))", (fence.run_id,)
        )
        await conn.execute(
            """INSERT INTO run_event (run_id, seq, kind, payload_scrubbed)
               SELECT %s, COALESCE((SELECT max(seq) FROM run_event WHERE run_id = %s), 0) + 1, %s, %s
               WHERE EXISTS (SELECT 1 FROM run WHERE id = %s AND lease_owner = %s AND lease_epoch = %s)""",
            (fence.run_id, fence.run_id, kind, scrubbed, fence.run_id, fence.owner, fence.epoch),
        )


def from_node(node: object) -> list[tuple[str, dict[str, Any]]]:
    """Translate one agent-graph step into events (text, tool calls, tool results, tool errors)."""
    found: list[tuple[str, dict[str, Any]]] = []
    response = getattr(node, "model_response", None)
    if isinstance(response, ModelResponse):
        for part in response.parts:
            if isinstance(part, TextPart) and part.content:
                found.append(("text", {"text": part.content}))
            elif isinstance(part, ToolCallPart):
                found.append(
                    (
                        "tool_call",
                        {
                            "tool_call_id": part.tool_call_id,
                            "tool_name": part.tool_name,
                            "args": part.args_as_dict(),
                        },
                    )
                )
    request = getattr(node, "request", None)
    if isinstance(request, ModelRequest):
        for req_part in request.parts:
            if isinstance(req_part, ToolReturnPart):
                found.append(
                    (
                        "tool_result",
                        {"tool_call_id": req_part.tool_call_id, "tool_name": req_part.tool_name},
                    )
                )
            elif isinstance(req_part, RetryPromptPart) and req_part.tool_name:
                found.append(
                    (
                        "tool_error",
                        {"tool_call_id": req_part.tool_call_id, "tool_name": req_part.tool_name},
                    )
                )
    return found


async def read(
    ws: Workspace, run_id: str, *, after: int = 0
) -> list[tuple[int, str, dict[str, Any]]]:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                "SELECT seq, kind, payload_scrubbed FROM run_event WHERE run_id = %s AND seq > %s ORDER BY seq",
                (run_id, after),
            )
        ).fetchall()
    return [(int(str(r["seq"])), str(r["kind"]), dict(r["payload_scrubbed"])) for r in rows]  # type: ignore[call-overload]
