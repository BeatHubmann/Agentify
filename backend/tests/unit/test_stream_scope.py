"""The chat relay never holds the engagement scope across a `yield`. A client that leaves while the
relay waits at a `yield` leaves the generator to the event loop, which closes it in another task;
a scope reset there raises "created in a different Context" (CI run 38810154323)."""

import asyncio
import gc
from typing import Any

import pytest

from agentify.api.stream import StreamSettings, relay
from agentify.store.db import engagement_scope

pytestmark = pytest.mark.anyio


async def test_a_stream_dropped_at_a_yield_is_closed_without_a_scope_error() -> None:
    errors: list[str] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(repr(context.get("exception"))))
    ws: Any = None  # the relay reaches its first yield before touching storage

    async def leave_after_the_first_chunk() -> None:
        stream = relay(
            ws,
            "run_dropped",
            after=0,
            settings=StreamSettings(),
            scope=lambda: engagement_scope(("eng_a",)),
            attached=False,
        )
        await anext(stream)

    try:
        await asyncio.create_task(leave_after_the_first_chunk())
        gc.collect()
        for _ in range(5):
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)
    assert errors == []
