"""The router (A§9.1): dispatch returns at once, follow-up turns continue the playbook, only skills'
kinds can be dispatched, there are no write tools, and human intent cannot come from an agent."""

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import Callable

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agentify import inbox
from agentify.agents.catalog import build_agent
from agentify.harness import runs
from agentify.harness.worker import WorkerConfig, run_worker
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import commit_golden
from tests.support.agents import add_process_input, run_kind, section_steps, synthesis_steps
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
SECTION_KINDS = ("spec-section-current", "spec-section-desired", "spec-section-constraints")


class Scripts:
    """A model factory handing out the next script for each kind, so a worker can run them."""

    def __init__(self, plan: dict[str, list[Callable[[], Script]]]) -> None:
        self.plan = {kind: deque(makers) for kind, makers in plan.items()}
        self.used: dict[str, list[Script]] = defaultdict(list)

    def __call__(self, kind: AgentKind) -> object:
        script = self.plan[kind.id].popleft()()
        self.used[kind.id].append(script)
        return script.model()


async def run_worker_until(
    ws: Workspace,
    registry: Registry,
    scripts: Scripts,
    done: Callable[[], object],
    within: float = 60,
) -> None:
    stop = asyncio.Event()
    config = WorkerConfig(
        owner="worker-r",
        queues=tuple(
            scripts.plan
        ),  # only the kinds this test scripts; other tests leave runs queued poll_interval=0.05, sweep_interval=60
    )
    task = asyncio.create_task(run_worker(ws, registry, scripts, config, stop))  # type: ignore[arg-type]
    try:
        async with asyncio.timeout(within):
            while not await done():  # type: ignore[misc]  # noqa: ASYNC110 - polls the database, no event exists
                await asyncio.sleep(0.1)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=30)


def router_turn(*calls: tuple[str, dict[str, object]]) -> Callable[[], Script]:
    return lambda: Script(
        [Step(calls=list(calls)), Step(text="Dispatched. I will continue when they end.")]
    )


def follow_up_that_checks_the_workspace() -> Script:
    """A follow-up turn doing what the playbook says: synthesize once all three sections exist."""
    script = Script([])

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        script.seen.append(list(messages))
        returns = [
            p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)
        ]
        listed = [p for p in returns if p.tool_name == "list_workspace"]
        if not listed:
            return ModelResponse(parts=[ToolCallPart("list_workspace", {"prefix": "spec/"})])
        sections = {f"spec/{s}.json" for s in ("current", "desired", "constraints")}
        dispatched = any(p.tool_name == "dispatch_subagent" for p in returns)
        if sections <= set(listed[-1].content) and not dispatched:
            return ModelResponse(
                parts=[
                    ToolCallPart("load_skill", {"name": "specification"}),
                    ToolCallPart(
                        "dispatch_subagent",
                        {
                            "kind": "spec-synthesis",
                            "instructions": "assemble",
                            "mode": "from_scratch",
                        },
                    ),
                ]
            )
        return ModelResponse(
            parts=[TextPart("Synthesis dispatched." if dispatched else "Waiting for sections.")]
        )

    script.model = lambda: FunctionModel(respond)  # type: ignore[method-assign]
    return script


async def test_router_dispatches_parallel_sections_then_a_follow_up_turn_dispatches_synthesis(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    first = router_turn(
        ("load_skill", {"name": "specification"}),
        *(
            (
                "dispatch_subagent",
                {"kind": k, "instructions": "write your section", "mode": "from_scratch"},
            )
            for k in SECTION_KINDS
        ),
    )
    scripts = Scripts(
        {
            "router": [first, *[follow_up_that_checks_the_workspace] * 10],
            **{
                k: [lambda k=k: Script(section_steps(k.removeprefix("spec-section-")))]
                for k in SECTION_KINDS
            },
            "spec-synthesis": [lambda: Script(synthesis_steps())],
        }
    )
    await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents["router"],
        mode="incremental",
        initiated_by="consultant-1",
        entry_point="chat",
        instructions="Build the specification.",
    )

    async def specification_committed() -> bool:
        return await workspace.read(process_id, "spec/specification.json") is not None

    await run_worker_until(workspace, registry, scripts, specification_committed)
    async with workspace.db.tx() as conn:
        rows = await (
            await conn.execute(
                "SELECT kind, state, parent_run_id IS NOT NULL AS child, entry_point FROM run WHERE process_id = %s",
                (process_id,),
            )
        ).fetchall()
    states = {(str(r["kind"]), str(r["state"]), bool(r["child"])) for r in rows}
    entry_points = {str(r["entry_point"]) for r in rows if r["kind"] == "router"}
    assert (
        {(k, "committed", True) for k in (*SECTION_KINDS, "spec-synthesis")} <= states,
        ("router", "unchanged", False) in states,
        {"chat", "router_followup"} <= entry_points,
    ) == (True, True, True)


async def test_the_router_can_only_dispatch_kinds_named_by_a_loaded_skill(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    script = Script(
        [
            Step(
                calls=[("dispatch_subagent", {"kind": "spec-section-current", "instructions": "x"})]
            ),
            Step(text="done"),
        ]
    )
    _run_id, final = await run_kind(
        workspace, registry, process_id, "router", script, mode="incremental"
    )
    retries = [
        json.loads(str(p.content))["error"]
        for m in script.seen[-1]
        for p in getattr(m, "parts", [])
        if type(p).__name__ == "RetryPromptPart"
    ]
    async with workspace.db.tx() as conn:
        children = await (
            await conn.execute(
                "SELECT count(*) AS n FROM run WHERE process_id = %s AND kind <> 'router'",
                (process_id,),
            )
        ).fetchone()
    assert (final, retries, children and children["n"]) == (
        "unchanged",
        ["kind_not_in_loaded_skills"],
        0,
    )


def test_the_router_has_no_write_tools(registry: Registry) -> None:
    router = build_agent(registry.agents["router"], registry)
    names = set(router._function_toolset.tools)  # the tool schemas the model is offered
    assert (
        names
        & {"patch_artifact", "add_node", "edit_node", "delete_node", "add_edge", "delete_edge"},
        {"dispatch_subagent", "load_skill", "inbox_ask"} <= names,
    ) == (set(), True)


async def test_from_scratch_dispatch_over_a_flow_needs_a_human_and_an_agent_cannot_confirm(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    dispatch = (
        "dispatch_subagent",
        {"kind": "current-state-modeler", "instructions": "regenerate", "mode": "from_scratch"},
    )
    first = Script(
        [Step(calls=[("load_skill", {"name": "current-state"}), dispatch]), Step(text="asked")]
    )
    router_run, _ = await run_kind(
        workspace, registry, process_id, "router", first, mode="incremental"
    )
    outcomes = [
        json.loads(str(p.content))["status"]
        for m in first.seen[-1]
        for p in getattr(m, "parts", [])
        if type(p).__name__ == "ToolReturnPart"
        and getattr(p, "tool_name", "") == "dispatch_subagent"
    ]
    [confirmation] = [
        i for i in await inbox.items(workspace, process_id) if i.kind == "confirmation"
    ]
    with pytest.raises(inbox.InboxRejected) as by_agent:
        await inbox.answer(
            workspace,
            registry,
            process_id=process_id,
            item_id=confirmation.item_id,
            author=inbox.AgentAuthor(router_run),
            text="confirm",
        )
    await inbox.answer(
        workspace,
        registry,
        process_id=process_id,
        item_id=confirmation.item_id,
        author=inbox.UserAuthor("consultant-1"),
        text="confirm",
    )
    second = Script(
        [Step(calls=[("load_skill", {"name": "current-state"}), dispatch]), Step(text="dispatched")]
    )
    await run_kind(workspace, registry, process_id, "router", second, mode="incremental")
    second_outcomes = [
        json.loads(str(p.content))["status"]
        for m in second.seen[-1]
        for p in getattr(m, "parts", [])
        if type(p).__name__ == "ToolReturnPart"
        and getattr(p, "tool_name", "") == "dispatch_subagent"
    ]
    async with workspace.db.tx() as conn:
        admitted = await (
            await conn.execute(
                """SELECT r.mode, c.confirmed_by FROM run r JOIN intent_confirmation c ON c.consumed_by_run = r.id
               WHERE r.process_id = %s AND r.kind = 'current-state-modeler' AND r.state = 'admitted'""",
                (process_id,),
            )
        ).fetchall()
    assert (
        outcomes,
        by_agent.value.code,
        second_outcomes,
        [(str(a["mode"]), str(a["confirmed_by"])) for a in admitted],
    ) == (
        ["needs_confirmation"],
        "human_answer_required",
        ["admitted"],
        [("from_scratch", "consultant-1")],
    )
