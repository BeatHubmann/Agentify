"""The chat stream relay (A§2): stored run events encoded as the AI SDK UI message stream.

The run executes in a worker, not in the request; this relay only reads the run-event log and
keeps the durable attachment heartbeat fresh while the client is connected (A§8.5). Encoding uses
Pydantic AI's AI SDK chunk types, so the wire format is the published protocol, not a copy of it.
Each run event's sequence number is the SSE `id`, and `after` resumes the stream from it.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from pydantic_ai.ui.vercel_ai.response_types import (
    AbortChunk,
    BaseChunk,
    DoneChunk,
    FinishChunk,
    StartChunk,
    TextDeltaChunk,
    TextEndChunk,
    TextStartChunk,
    ToolInputAvailableChunk,
    ToolOutputAvailableChunk,
    ToolOutputErrorChunk,
)

from agentify.harness import events, runs
from agentify.store.workspace import Workspace

STREAM_HEADERS = {"x-vercel-ai-ui-message-stream": "v1", "cache-control": "no-cache"}
SDK_VERSION = 6
DONE_STATES = frozenset({"committed", "unchanged"})


@dataclass(frozen=True)
class StreamSettings:
    heartbeat_interval: float = 5.0
    attach_seconds: float = 10.0
    poll_interval: float = 0.2


def sse(chunk: BaseChunk, event_id: int | None = None) -> str:
    """One SSE message; `event_id` marks the run event it completes, so a client resumes after it."""
    prefix = "" if event_id is None else f"id: {event_id}\n"
    return f"{prefix}data: {chunk.encode(SDK_VERSION)}\n\n"


def chunks_for(seq: int, kind: str, payload: dict[str, Any]) -> Iterator[BaseChunk]:
    match kind:
        case "text":
            part_id = f"text-{seq}"
            yield TextStartChunk(id=part_id)
            yield TextDeltaChunk(id=part_id, delta=str(payload["text"]))
            yield TextEndChunk(id=part_id)
        case "tool_call":
            yield ToolInputAvailableChunk(
                tool_call_id=str(payload["tool_call_id"]),
                tool_name=str(payload["tool_name"]),
                input=payload.get("args", {}),
            )
        case "tool_result":
            yield ToolOutputAvailableChunk(tool_call_id=str(payload["tool_call_id"]), output="ok")
        case "tool_error":
            yield ToolOutputErrorChunk(
                tool_call_id=str(payload["tool_call_id"]), error_text="corrected by the agent"
            )
        case "finished":
            state = str(payload.get("state"))
            yield (
                FinishChunk(finish_reason="stop")
                if state in DONE_STATES
                else AbortChunk(reason=state)
            )


async def relay(
    ws: Workspace,
    run_id: str,
    *,
    after: int,
    settings: StreamSettings,
    scope: AbstractContextManager[None],
    attached: bool,
) -> AsyncIterator[str]:
    """Stream a run's events from `after`. An `attached` client (one who may cancel the run) renews
    the attachment while it listens, so leaving stops the run like a cancel (A§8.5)."""
    with scope:

        async def heartbeat() -> None:
            """Renew the attachment periodically; stops once the run is terminal."""
            while True:
                if not await runs.attach(ws, run_id, seconds=settings.attach_seconds):
                    return
                await asyncio.sleep(settings.heartbeat_interval)

        beating = asyncio.create_task(heartbeat()) if attached else None
        try:
            yield sse(StartChunk(message_id=run_id))
            cursor, finished = after, False
            final = await events.final(ws, run_id)
            if final is not None and final[0] <= after:  # resumed after the end: repeat the end
                for chunk in chunks_for(final[0], "finished", final[1]):
                    yield sse(chunk)
                finished = True
            while not finished:
                for seq, kind, payload in await events.read(ws, run_id, after=cursor):
                    cursor = seq
                    # The id rides on an event's last chunk: a client cut off mid-event gets it again.
                    encoded = list(chunks_for(seq, kind, payload))
                    for i, chunk in enumerate(encoded, start=1):
                        yield sse(chunk, seq if i == len(encoded) else None)
                    finished = finished or kind == "finished"
                if not finished:
                    await asyncio.sleep(settings.poll_interval)
            yield sse(DoneChunk())
        finally:
            if beating is not None:
                beating.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beating
