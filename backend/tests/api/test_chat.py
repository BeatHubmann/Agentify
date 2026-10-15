"""The chat path (A§2, A§8.5, brief §12.5B "disconnect"): chat turns stream through the AI SDK protocol,
history is server-authoritative, a disconnect or a killed gateway stops the run like a cancel, a
reconnect within the grace period does not, and events and chat pass the secret gate."""

import asyncio
import contextlib
import json
import os
import signal
import socket
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from agentify import chat
from agentify.api.app import create_app
from agentify.api.server import uvicorn_config
from agentify.domain.edits import merge
from agentify.harness import execute, runs, sweeper
from agentify.harness.worker import WorkerConfig, run_worker
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace
from tests.api.conftest import (
    AUDIENCE,
    FAST_STREAM,
    ISSUER,
    TokenFactory,
    api_context,
    engagement_with,
    subject,
)
from tests.conftest import KNOWN_SECRET, REGISTRY_ROOT, Infra
from tests.orchestration.test_run_exit_paths import commit_golden, head_flow, history, leftovers
from tests.orchestration.test_user_edits import edit_layer
from tests.support.scripted import Script, ScriptedSource, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
BACKEND = Path(__file__).resolve().parents[2]
GRACE = 1.0


def slow_modeler() -> Script:
    """Stages two nodes, then keeps working in short steps, so stop checks run between them."""

    async def pause() -> None:
        await asyncio.sleep(0.2)

    nodes = [
        Step(calls=[("add_node", {"id": i, "label": i, "kind": "step", "execution": "human"})])
        for i in "ab"
    ]
    return Script([*nodes, *(Step(calls=[("read_flow", {})], before=pause) for _ in range(30))])


def finishing_modeler() -> Script:
    """Outlasts a disconnect gap, so the run is still active when the client reconnects."""

    async def pause() -> None:
        await asyncio.sleep(0.3)

    ids = ["a", "b", "c"]
    steps = [
        Step(
            calls=[("add_node", {"id": i, "label": i, "kind": "step", "execution": "human"})],
            before=pause,
        )
        for i in ids
    ]
    steps += [
        Step(calls=[("add_edge", {"id": f"e-{x}-{y}", "source": x, "target": y})])
        for x, y in (("a", "b"), ("b", "c"))
    ]
    steps += [Step(calls=[("read_flow", {})], before=pause) for _ in range(8)]
    return Script([*steps, Step(text="done")])


def qa_router() -> Script:
    return Script(
        [
            Step(calls=[("load_skill", {"name": "general-qa"})]),
            Step(text="Agentify redesigns processes for AI execution."),
        ]
    )


# AWS's documented example key, built at runtime: pattern-only, so it may go to the model (A§18.2).
EXAMPLE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def leaky_router() -> Script:
    """Puts a pattern-only key in a tool argument (the next request carries it back to the model,
    which A§18.2 allows) and the user's known secret in its final reply, which no request carries."""
    return Script(
        [
            Step(calls=[("load_skill", {"name": f"skill for {EXAMPLE_KEY}"})]),
            Step(text=f"The key you sent was {KNOWN_SECRET}."),
        ]
    )


@contextlib.asynccontextmanager
async def worker(
    ws: Workspace, registry: Registry, plan: dict[str, Callable[[], Script]]
) -> AsyncIterator[None]:
    def model_for(kind: AgentKind) -> object:
        return ScriptedSource(plan[kind.id]())

    stop = asyncio.Event()
    config = WorkerConfig(
        owner="chat-worker",
        queues=tuple(plan),
        poll_interval=0.05,
        sweep_interval=60,
        harness=execute.HarnessConfig(
            lease_seconds=5, renew_every=1, attachment_grace_seconds=GRACE
        ),
    )
    task = asyncio.create_task(run_worker(ws, registry, model_for, config, stop))  # type: ignore[arg-type]
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.asynccontextmanager
async def live_server(infra: Infra, registry: Registry, jwks_url: str) -> AsyncIterator[str]:
    """The real app on a real socket, so a client can drop the connection mid-stream."""
    ctx = api_context(infra, registry, jwks_url)
    port = free_port()
    server = uvicorn.Server(uvicorn_config(create_app(ctx), host="127.0.0.1", port=port))
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes no started event
        await asyncio.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


def chunks(lines: list[str]) -> list[Any]:
    out: list[Any] = []
    for line in lines:
        if line.startswith("data: "):
            body = line.removeprefix("data: ")
            out.append("[DONE]" if body == "[DONE]" else json.loads(body))
    return out


def event_ids(lines: list[str]) -> list[int]:
    return [int(line.removeprefix("id: ")) for line in lines if line.startswith("id: ")]


async def consultant_process(
    api: httpx.AsyncClient, token: TokenFactory
) -> tuple[str, dict[str, str]]:
    who = subject()
    setup = await engagement_with(api, token, {who: "consultant"})
    headers = setup["users"][who][1]
    created = await api.post(
        f"/engagements/{setup['engagement_id']}/processes", json={"name": "p"}, headers=headers
    )
    return str(created.json()["process_id"]), headers


async def wait_state(
    ws: Workspace, run_id: str, states: set[str], within: float = 30
) -> dict[str, Any]:
    async with asyncio.timeout(within):
        while True:
            state = await runs.run_state(ws, run_id)
            if state["state"] in states:
                return state
            await asyncio.sleep(0.1)


async def test_a_chat_turn_streams_the_routers_reply_and_keeps_the_history(
    api: httpx.AsyncClient, token: TokenFactory, workspace: Workspace, registry: Registry
) -> None:
    pid, headers = await consultant_process(api, token)
    async with (
        worker(workspace, registry, {"router": qa_router}),
        api.stream(
            "POST", f"/processes/{pid}/chat", json={"message": "What do you do?"}, headers=headers
        ) as response,
    ):
        lines = [line async for line in response.aiter_lines()]
        stream_headers = dict(response.headers)
    parts = chunks(lines)
    types = [p if p == "[DONE]" else p["type"] for p in parts]
    text = "".join(p["delta"] for p in parts if isinstance(p, dict) and p["type"] == "text-delta")
    saved = (await api.get(f"/processes/{pid}/chat", headers=headers)).json()
    assert (
        stream_headers["x-vercel-ai-ui-message-stream"],
        types[0],
        types[-2:],
        "tool-input-available" in types,
        "tool-output-available" in types,
        text,
        [(m["author_kind"], m["content"]) for m in saved],
    ) == (
        "v1",
        "start",
        ["finish", "[DONE]"],
        True,
        True,
        "Agentify redesigns processes for AI execution.",
        [
            ("user", "What do you do?"),
            ("agent_run", "Agentify redesigns processes for AI execution."),
        ],
    )


async def test_a_client_disconnect_stops_the_run_like_a_cancel(
    *,
    infra: Infra,
    api: httpx.AsyncClient,
    token: TokenFactory,
    workspace: Workspace,
    registry: Registry,
    jwks_url: str,
) -> None:
    pid, headers = await consultant_process(api, token)
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": "current-state-modeler", "mode": "from_scratch"},
            headers=headers,
        )
    ).json()["run_id"]
    async with (
        live_server(infra, registry, jwks_url) as base,
        httpx.AsyncClient(base_url=base) as client,
        worker(workspace, registry, {"current-state-modeler": slow_modeler}),
    ):
        async with client.stream("GET", f"/runs/{run_id}/stream", headers=headers) as response:
            async for line in response.aiter_lines():
                if '"tool-output-available"' in line:
                    break  # the client goes away mid-run
        state = await wait_state(workspace, run_id, {"rolled_back", "committed"})
    again = await api.post(
        f"/processes/{pid}/runs",
        json={"kind": "current-state-modeler", "mode": "from_scratch"},
        headers=headers,
    )
    assert (
        state["state"],
        state["outcome"],
        state["outcome_detail_scrubbed"],
        await history(workspace, pid),
        await leftovers(workspace, run_id),
        again.status_code,
    ) == ("rolled_back", "cancelled", "client disconnected", [], (0, 0), 201)


async def test_a_user_edit_made_before_a_disconnect_survives_the_rollback(
    *,
    infra: Infra,
    api: httpx.AsyncClient,
    token: TokenFactory,
    workspace: Workspace,
    registry: Registry,
    jwks_url: str,
) -> None:
    """INV-2.ME on the CD exit: the disconnect rolls the run back, never the user's revision."""
    pid, headers = await consultant_process(api, token)
    await commit_golden(workspace, registry, pid)
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": "current-state-modeler", "mode": "incremental"},
            headers=headers,
        )
    ).json()["run_id"]
    relabel = {"op": "modify_node", "target": "approve", "payload": {"label": "edited mid-run"}}
    async with (
        live_server(infra, registry, jwks_url) as base,
        # Earlier tests leave admitted runs queued; the worker may finish one before this run.
        httpx.AsyncClient(base_url=base, timeout=30) as client,
        worker(workspace, registry, {"current-state-modeler": slow_modeler}),
    ):
        async with client.stream("GET", f"/runs/{run_id}/stream", headers=headers) as response:
            async for line in response.aiter_lines():
                if '"tool-output-available"' in line:
                    head = (await api.get(f"/processes/{pid}", headers=headers)).json()
                    body = {
                        "kind": "current-state-modeler",
                        "base_revision_id": head["head_revision_id"],
                        "patches": [relabel],
                    }
                    edited = await api.post(f"/processes/{pid}/edits", json=body, headers=headers)
                    break  # the client goes away mid-run, right after the user's edit
        state = await wait_state(workspace, run_id, {"rolled_back", "committed"})
    flow = await head_flow(workspace, pid)
    assert flow is not None
    merged = merge(flow, await edit_layer(workspace, pid)).flow.node_map()
    assert (
        edited.status_code,
        state["state"],
        state["outcome_detail_scrubbed"],
        [r["author_kind"] for r in await history(workspace, pid)],
        merged["approve"].label,
        sorted(merged),
    ) == (
        201,
        "rolled_back",
        "client disconnected",
        ["agent_run", "user"],
        "edited mid-run",
        ["approve", "classify", "compute-fee", "receive"],
    )


async def test_a_reader_who_streams_a_run_and_leaves_never_stops_it(
    *,
    infra: Infra,
    api: httpx.AsyncClient,
    token: TokenFactory,
    workspace: Workspace,
    registry: Registry,
    jwks_url: str,
) -> None:
    """Only a caller who may cancel a run attaches to it (A§8.5): a reviewer can watch, not stop."""
    consultant, reviewer = subject(), subject()
    setup = await engagement_with(api, token, {consultant: "consultant", reviewer: "reviewer"})
    starts, watches = setup["users"][consultant][1], setup["users"][reviewer][1]
    pid = (
        await api.post(
            f"/engagements/{setup['engagement_id']}/processes", json={"name": "p"}, headers=starts
        )
    ).json()["process_id"]
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": "current-state-modeler", "mode": "from_scratch"},
            headers=starts,
        )
    ).json()["run_id"]
    async with (
        live_server(infra, registry, jwks_url) as base,
        httpx.AsyncClient(base_url=base, timeout=30) as client,
        worker(workspace, registry, {"current-state-modeler": finishing_modeler}),
    ):
        async with client.stream("GET", f"/runs/{run_id}/stream", headers=watches) as response:
            async for line in response.aiter_lines():
                if '"tool-output-available"' in line:
                    break  # the reviewer closes the tab mid-run
        state = await wait_state(workspace, run_id, {"rolled_back", "committed"})
    assert (response.status_code, state["state"], state["stream_attached_until"]) == (
        200,
        "committed",
        None,
    )


async def test_a_stream_ends_when_the_sweeper_finishes_a_lost_run(
    api: httpx.AsyncClient, token: TokenFactory, workspace: Workspace, registry: Registry
) -> None:
    """INV-1.PK on the stream: a run whose executor died ends with `finished`, not a hung reply."""
    pid, headers = await consultant_process(api, token)
    kind = registry.agents["current-state-modeler"]
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": kind.id, "mode": "from_scratch"},
            headers=headers,
        )
    ).json()["run_id"]
    lost = await runs.claim(workspace, queue=kind.id, owner="dies", lease_seconds=30, run_id=run_id)
    assert lost is not None  # the executor that claimed it never comes back

    async def read_stream() -> list[str]:
        async with api.stream("GET", f"/runs/{run_id}/stream", headers=headers) as response:
            return [line async for line in response.aiter_lines()]

    reader = asyncio.create_task(read_stream())
    await asyncio.sleep(0.3)
    async with workspace.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (run_id,)
        )
    swept = await sweeper.sweep(
        workspace, owner="sweeper", lease_seconds=30, admission_timeout_seconds=600
    )
    lines = await asyncio.wait_for(reader, timeout=10)
    assert ([s.final_state for s in swept if s.run_id == run_id], chunks(lines)[-1]) == (
        ["rolled_back"],
        "[DONE]",
    )


@pytest.mark.parametrize("before_final", [1, 0], ids=["before-the-final-event", "at-its-cursor"])
async def test_resuming_a_finished_run_ends_with_its_final_chunk(
    api: httpx.AsyncClient,
    token: TokenFactory,
    workspace: Workspace,
    registry: Registry,
    before_final: int,
) -> None:
    """External review R2: a client resuming at the `finished` event's own id must not wait forever."""
    pid, headers = await consultant_process(api, token)
    kind = registry.agents["current-state-modeler"]
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": kind.id, "mode": "from_scratch"},
            headers=headers,
        )
    ).json()["run_id"]
    assert await runs.claim(workspace, queue=kind.id, owner="dies", lease_seconds=30, run_id=run_id)
    async with workspace.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (run_id,)
        )
    await sweeper.sweep(workspace, owner="sweeper", lease_seconds=30, admission_timeout_seconds=600)
    async with workspace.db.tx() as conn:
        final = await (
            await conn.execute(
                "SELECT seq FROM run_event WHERE run_id = %s AND kind = 'finished'", (run_id,)
            )
        ).fetchone()
    assert final is not None

    async def resume() -> list[str]:
        after = int(str(final["seq"])) - before_final
        async with api.stream(
            "GET", f"/runs/{run_id}/stream", params={"after": after}, headers=headers
        ) as response:
            return [line async for line in response.aiter_lines()]

    lines = await asyncio.wait_for(resume(), timeout=10)
    assert [p if p == "[DONE]" else p["type"] for p in chunks(lines)] == [
        "start",
        "abort",
        "[DONE]",
    ]


async def test_a_reconnect_within_the_grace_period_keeps_the_run_going(
    *,
    infra: Infra,
    api: httpx.AsyncClient,
    token: TokenFactory,
    workspace: Workspace,
    registry: Registry,
    jwks_url: str,
) -> None:
    pid, headers = await consultant_process(api, token)
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": "current-state-modeler", "mode": "from_scratch"},
            headers=headers,
        )
    ).json()["run_id"]
    async with (
        live_server(infra, registry, jwks_url) as base,
        httpx.AsyncClient(base_url=base) as client,
        worker(workspace, registry, {"current-state-modeler": finishing_modeler}),
    ):
        delivered: list[int] = []
        async with client.stream("GET", f"/runs/{run_id}/stream", headers=headers) as response:
            async for line in response.aiter_lines():
                delivered += event_ids([line])
                if '"tool-input-available"' in line:
                    break
        await asyncio.sleep(FAST_STREAM.attach_seconds + GRACE / 2)  # stale, but within grace
        async with client.stream(
            "GET", f"/runs/{run_id}/stream", params={"after": delivered[-1]}, headers=headers
        ) as again:
            lines = [line async for line in again.aiter_lines()]
        state = await wait_state(workspace, run_id, {"rolled_back", "committed"})
    async with workspace.db.tx() as conn:
        row = await (
            await conn.execute("SELECT count(*) AS n FROM run_event WHERE run_id = %s", (run_id,))
        ).fetchone()
    logged = int(str(row["n"])) if row else 0
    types = [p if p == "[DONE]" else p["type"] for p in chunks(lines)]
    assert (state["state"], delivered + event_ids(lines), types[0], types[-2:]) == (
        "committed",
        list(range(1, logged + 1)),
        "start",
        ["finish", "[DONE]"],
    )


async def test_a_killed_gateway_stops_its_clients_runs(
    *,
    infra: Infra,
    api: httpx.AsyncClient,
    token: TokenFactory,
    workspace: Workspace,
    registry: Registry,
    jwks_url: str,
) -> None:
    pid, headers = await consultant_process(api, token)
    run_id = (
        await api.post(
            f"/processes/{pid}/runs",
            json={"kind": "current-state-modeler", "mode": "from_scratch"},
            headers=headers,
        )
    ).json()["run_id"]
    port = free_port()
    config = {
        "conninfo": infra.api_conninfo,
        "s3_endpoint": infra.s3_endpoint,
        "s3_key": infra.s3_access_key,
        "s3_secret": infra.s3_secret_key,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_url": jwks_url,
        "registry": str(REGISTRY_ROOT),
        "port": str(port),
    }
    gateway = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.api_proc",
        json.dumps(config),
        cwd=BACKEND,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(100):
                with contextlib.suppress(httpx.TransportError):
                    if (await client.get("/health")).status_code == 200:
                        break
                await asyncio.sleep(0.1)
            async with worker(workspace, registry, {"current-state-modeler": slow_modeler}):
                with contextlib.suppress(httpx.TransportError):
                    async with client.stream(
                        "GET", f"/runs/{run_id}/stream", headers=headers
                    ) as response:
                        async for line in response.aiter_lines():
                            if '"tool-output-available"' in line:
                                os.kill(gateway.pid, signal.SIGKILL)
                                break
                state = await wait_state(workspace, run_id, {"rolled_back", "committed"})
    finally:
        if gateway.returncode is None:
            gateway.kill()
        await gateway.wait()
    async with workspace.db.tx() as conn:
        row = await (
            await conn.execute(
                """SELECT extract(epoch FROM finished_at - stream_attached_until) AS stale
                   FROM run WHERE id = %s""",
                (run_id,),
            )
        ).fetchone()
    stale_seconds = float(str(row["stale"])) if row else -1.0
    assert (
        state["state"],
        state["outcome_detail_scrubbed"],
        await history(workspace, pid),
        GRACE < stale_seconds < GRACE + 3,  # stopped once the last heartbeat aged past grace
    ) == ("rolled_back", "client disconnected", [], True)


async def test_chat_messages_and_run_events_pass_the_secret_gate(
    api: httpx.AsyncClient, token: TokenFactory, workspace: Workspace, registry: Registry
) -> None:
    pid, headers = await consultant_process(api, token)
    async with (
        worker(workspace, registry, {"router": leaky_router}),
        api.stream(
            "POST",
            f"/processes/{pid}/chat",
            json={"message": f"my key is {KNOWN_SECRET}"},
            headers=headers,
        ) as response,
    ):
        streamed = "".join([line async for line in response.aiter_lines()])
    saved = (await api.get(f"/processes/{pid}/chat", headers=headers)).json()
    async with workspace.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT e.kind, e.payload_scrubbed::text AS p FROM run_event e
                   JOIN run r ON r.id = e.run_id WHERE r.process_id = %s""",
                (pid,),
            )
        ).fetchall()
    sinks = [(str(r["kind"]), str(r["p"])) for r in rows]
    sinks += [(str(m["author_kind"]), str(m["content"])) for m in saved]
    async with workspace.db.tx() as conn:
        instructions = await (
            await conn.execute("SELECT instructions FROM run WHERE process_id = %s", (pid,))
        ).fetchall()
    sinks += [("instructions", str(r["instructions"])) for r in instructions]
    async with workspace.db.tx() as conn:
        events = await (
            await conn.execute(
                "SELECT exit, rule, location FROM security_event WHERE process_id = %s", (pid,)
            )
        ).fetchall()
    assert (
        [KNOWN_SECRET in streamed, EXAMPLE_KEY in streamed],
        [name for name, text in sinks if KNOWN_SECRET in text or EXAMPLE_KEY in text],
        {name for name, text in sinks if "[REDACTED:known-value]" in text},
        {name for name, text in sinks if "[REDACTED:aws-access-key-id]" in text},
        sorted({(str(e["exit"]), str(e["rule"]), str(e["location"])) for e in events}),
    ) == (
        [False, False],
        [],
        {"user", "agent_run", "text", "instructions"},
        {"tool_call"},
        [
            ("chat", "known-value", "chat"),
            ("run_event", "aws-access-key-id", "run_event.tool_call"),
            ("run_event", "known-value", "run_event.text"),
            ("run_record", "known-value", "run.instructions"),
            ("run_record", "known-value", "run.result_text"),
        ],
    )


async def test_concurrent_chat_sessions_never_overwrite_each_other(
    workspace: Workspace, process_id: str
) -> None:
    await asyncio.gather(
        *(
            chat.append(
                workspace,
                process_id=process_id,
                author_kind="user",
                author_id=f"u{i}",
                content=f"message {i}",
            )
            for i in range(12)
        )
    )
    messages = await chat.history(workspace, process_id)
    assert ([m.seq for m in messages], sorted(m.content for m in messages)) == (
        list(range(1, 13)),
        sorted(f"message {i}" for i in range(12)),
    )
