"""Invariants 1 and 2 where a user edit meets a run's exit: the commit re-validates the merged result
(INV-1.UE-REVAL), and every failure exit leaves the user's revision intact (INV-2.ME, A§7.3, A§7.4)."""

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from agentify.agents.flow_tools import build_modeler
from agentify.domain.edits import merge
from agentify.domain.flow import Execution, Flow, FlowMode, NodeKind
from agentify.domain.validate import GateThresholds, flow_invariants
from agentify.editing import EditBatch, PatchRequest
from agentify.harness import execute, runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import (
    commit_golden,
    head_flow,
    history,
    leftovers,
    modeler,
    run_once,
)
from tests.orchestration.test_user_edits import edit, edit_layer, relabel
from tests.support.flows import edge, node
from tests.support.scripted import Script, ScriptedSource, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
USER_LABEL = "edited during the run"
LEAD = ("in", "p1", "p2", "p3", "p4")  # enough base nodes that removing two stays under max_shrink


def add(i: str, kind: str = "step", execution: str = "human", parent: str | None = None) -> Step:
    fields: dict[str, Any] = {"id": i, "label": i, "kind": kind, "execution": execution}
    return Step(calls=[("add_node", fields if parent is None else {**fields, "parent": parent})])


def link(a: str, b: str) -> Step:
    return Step(calls=[("add_edge", {"id": f"e-{a}-{b}", "source": a, "target": b})])


def container_build() -> list[Step]:
    """in -> p1 .. p4 -> decide -> out, where the actor `decide` holds c1 -> c2 -> c3."""
    chain = [*LEAD, "decide", "out"]
    return [
        *(add(i) for i in LEAD),
        add("decide", "actor", "ai"),
        *(add(c, "task", "ai", "decide") for c in ("c1", "c2", "c3")),
        add("out"),
        *(link(a, b) for a, b in itertools.pairwise(chain)),
        link("c1", "c2"),
        link("c2", "c3"),
        Step(text="built"),
    ]


def staged_after_deleting_c3() -> Flow:
    """What the run stages, written independently of the harness: the base flow without c3."""
    actor, task = (NodeKind.ACTOR, Execution.AI), (NodeKind.TASK, Execution.AI)
    return Flow(
        mode=FlowMode.CURRENT,
        nodes=(
            *(node(i) for i in LEAD),
            node("decide", *actor),
            node("c1", *task, parent="decide"),
            node("c2", *task, parent="decide"),
            node("out"),
        ),
        edges=(
            *(edge(a, b) for a, b in itertools.pairwise([*LEAD, "decide", "out"])),
            edge("c1", "c2"),
        ),
    )


async def test_a_user_edit_between_gate_and_commit_is_revalidated_with_the_staged_flow(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    built = await run_once(workspace, registry, process_id, Script(container_build()))
    assert built[1] == "committed"
    remove_c2 = EditBatch(patches=(PatchRequest("remove_node", "c2"),))

    class EditAtCommit(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            if kwargs["fence"].run_id == holder["run_id"]:
                await edit(workspace, registry, process_id, remove_c2)
            return await super().commit(**kwargs)

    holder: dict[str, str] = {"run_id": ""}

    async def note_run() -> None:
        holder["run_id"] = await running_run(workspace, process_id)

    ws = EditAtCommit(workspace.db, workspace.blobs, workspace.gate)
    script = Script(
        [Step(calls=[("delete_node", {"id": "c3"})], before=note_run), Step(text="c3 removed")]
    )
    run_id, final = await run_once(ws, registry, process_id, script, mode="incremental")
    state = await runs.run_state(workspace, run_id)
    layer = await edit_layer(workspace, process_id)
    merged_staged = merge(staged_after_deleting_c3(), layer).flow
    oracle = [
        (v.code, v.location)
        for v in flow_invariants(
            merged_staged, GateThresholds(min_nodes=1, max_shrink=1.0), base=None
        )
    ]
    head = await head_flow(workspace, process_id)
    assert head is not None
    assert (
        oracle,
        final,
        state["outcome"],
        state["outcome_detail_scrubbed"],
        [r["author_kind"] for r in await history(workspace, process_id)],
        sorted(n.id for n in merge(head, layer).flow.nodes),
        await leftovers(workspace, run_id),
    ) == (
        [("container_too_few_children", ("decide",))],
        "rolled_back",
        "commit_failed",
        "CommitValidationFailed: container_too_few_children: decide has 1 direct children; a actor "
        "needs at least 2.",
        ["agent_run", "user"],
        sorted(["c1", "c3", "decide", "out", *LEAD]),
        (0, 0),
    )


Exit = Callable[[Workspace, Registry, str, Step], Awaitable[None]]


async def running_run(ws: Workspace, process_id: str) -> str:
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                "SELECT id FROM run WHERE process_id = %s AND state = 'running'", (process_id,)
            )
        ).fetchone()
    assert row is not None
    return str(row["id"])


async def latest_run(ws: Workspace, process_id: str) -> tuple[str, str]:
    async with ws.db.tx() as conn:
        row = await (
            await conn.execute(
                "SELECT state, outcome FROM run WHERE process_id = %s ORDER BY created_at DESC "
                "LIMIT 1",
                (process_id,),
            )
        ).fetchone()
    assert row is not None
    return str(row["state"]), str(row["outcome"])


async def gate_failure(ws: Workspace, registry: Registry, process_id: str, edit_step: Step) -> None:
    """VF: the run appends an actor with a single child, which the gate refuses."""
    solo = [add("solo", "actor", "ai"), add("only", "task", "ai", "solo"), link("approve", "solo")]
    await run_once(ws, registry, process_id, Script([edit_step, *solo]), mode="incremental")


async def user_cancel(ws: Workspace, registry: Registry, process_id: str, edit_step: Step) -> None:
    async def cancel_now() -> None:
        await runs.request_cancel(ws, await running_run(ws, process_id))

    steps = [edit_step, Step(calls=[("edit_node", {"id": "approve", "label": "x"})])]
    steps.append(Step(before=cancel_now, text="cancelled"))
    await run_once(ws, registry, process_id, Script(steps), mode="incremental")


async def runtime_cancel(
    ws: Workspace, registry: Registry, process_id: str, edit_step: Step
) -> None:
    stuck = asyncio.Event()

    async def hang() -> None:
        stuck.set()
        await asyncio.sleep(3600)

    kind = modeler(registry)
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=kind,
        mode="incremental",
        initiated_by="consultant-1",
        entry_point="test",
    )
    claimed = await runs.claim(ws, queue=kind.id, owner="w-rc", lease_seconds=30, run_id=run_id)
    assert claimed is not None
    script = Script([edit_step, Step(before=hang)])
    task = asyncio.create_task(
        execute.execute(
            ws,
            claimed,
            kind,
            agent=build_modeler(registry.prompt_text(kind)),
            source=ScriptedSource(script),
            prompt="Model the process.",
        )
    )
    await asyncio.wait_for(stuck.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def storage_failure(
    ws: Workspace, registry: Registry, process_id: str, edit_step: Step
) -> None:
    class FailingCommit(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            raise OSError("database connection lost during commit")

    failing = FailingCommit(ws.db, ws.blobs, ws.gate)
    steps = [edit_step, Step(calls=[("edit_node", {"id": "approve", "label": "x"})])]
    await run_once(failing, registry, process_id, Script(steps), mode="incremental")


@pytest.mark.parametrize(
    ("exit_path", "outcome"),
    [
        (gate_failure, "gate_failed"),
        (user_cancel, "cancelled"),
        (runtime_cancel, "cancelled"),
        (storage_failure, "commit_failed"),
    ],
    ids=["VF", "UC", "RC", "SF"],
)
async def test_every_failure_exit_keeps_a_user_edit_made_during_the_run(
    workspace: Workspace, registry: Registry, process_id: str, exit_path: Exit, outcome: str
) -> None:
    await commit_golden(workspace, registry, process_id)

    async def user_edits_now() -> None:
        await edit(workspace, registry, process_id, relabel("approve", USER_LABEL))

    edit_step = Step(calls=[("edit_node", {"id": "receive", "label": "R"})], before=user_edits_now)
    await exit_path(workspace, registry, process_id, edit_step)
    head = await head_flow(workspace, process_id)
    assert head is not None
    merged = merge(head, await edit_layer(workspace, process_id)).flow.node_map()
    assert (
        await latest_run(workspace, process_id),
        [r["author_kind"] for r in await history(workspace, process_id)],
        merged["approve"].label,
        merged["receive"].label,
    ) == (("rolled_back", outcome), ["agent_run", "user"], USER_LABEL, "receive")
