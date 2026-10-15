"""Gate and history rows: INV-5.VF (arity on containers the run never touched), INV-10.VF (corrupt
staged artifacts fail the gate for the reason they are corrupt), INV-7.CR (each run its own revision)."""

import asyncio

import pytest

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_agent_layer import changed_paths
from tests.orchestration.test_harness_completion import fence_of
from tests.orchestration.test_run_exit_paths import commit_golden, history, run_once
from tests.orchestration.test_user_edits_on_exits import container_build, running_run
from tests.support.agents import add_process_input, run_kind, section_steps
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
TRUNCATED_FLOW = b'{"schema_version": 1, "artifact_type": "flow", "body": {"nodes": [{"id": "rec'


async def test_removing_a_child_fails_the_gate_on_the_container_the_run_never_touched(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    assert (await run_once(workspace, registry, process_id, Script(container_build())))[1] == (
        "committed"
    )
    deletes = [Step(calls=[("delete_node", {"id": c})]) for c in ("c2", "c3")]
    run_id, final = await run_once(
        workspace,
        registry,
        process_id,
        Script([*deletes, Step(text="trimmed")]),
        mode="incremental",
    )
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], state["outcome_detail_scrubbed"]) == (
        "rolled_back",
        "gate_failed",
        "container_too_few_children: decide has 1 direct children; a actor needs at least 2.",
    )


async def stage_raw(ws: Workspace, process_id: str, path: str, data: bytes) -> None:
    run_id = await running_run(ws, process_id)
    await ws.stage(await fence_of(ws, run_id), process_id, path, data)


GARBLED_FLOW = (
    b'{"schema_version": 1, "artifact_type": "flow", "output_language": "en", "inputs": {}, '
    b'"body": {"mode": "current", "nodes": [{"id": 7}], "edges": []}}'
)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (TRUNCATED_FLOW, "unreadable envelope: JSONDecodeError"),
        (GARBLED_FLOW, "flow schema violation: 4 errors"),
    ],
    ids=["truncated", "garbled"],
)
async def test_a_corrupt_staged_flow_fails_the_gate_as_corrupt(
    workspace: Workspace, registry: Registry, process_id: str, data: bytes, reason: str
) -> None:
    await commit_golden(workspace, registry, process_id)

    async def garble() -> None:
        await stage_raw(workspace, process_id, "flows/current/flow.json", data)

    edit = Step(calls=[("edit_node", {"id": "receive", "label": "R"})])
    script = Script([edit, Step(before=garble, text="done")])
    run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    state = await runs.run_state(workspace, run_id)
    revisions = len(await history(workspace, process_id))
    assert (final, state["outcome"], state["outcome_detail_scrubbed"], revisions) == (
        "rolled_back",
        "gate_failed",
        f"staged_flow_corrupt: {reason}",
        1,
    )


async def test_a_corrupt_staged_section_fails_the_artifact_gate_as_corrupt(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)

    async def garble() -> None:
        await stage_raw(workspace, process_id, "spec/current.json", b'{"schema_version": 1, "bo')

    write, _done = section_steps("current")
    script = Script([write, Step(before=garble, text="done")])
    run_id, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], state["outcome_detail_scrubbed"]) == (
        "rolled_back",
        "gate_failed",
        "output_corrupt: spec/current.json is unreadable.",
    )


async def test_two_runs_of_different_kinds_each_commit_their_own_revision_of_their_own_paths(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    await commit_golden(workspace, registry, process_id)
    relabel = Script(
        [Step(calls=[("edit_node", {"id": "receive", "label": "Receive"})]), Step(text="ok")]
    )
    (section_run, section), (modeler_run, modeler) = await asyncio.gather(
        run_kind(
            workspace,
            registry,
            process_id,
            "spec-section-desired",
            Script(section_steps("desired")),
        ),
        run_kind(
            workspace, registry, process_id, "current-state-modeler", relabel, mode="incremental"
        ),
    )
    by_run = dict((await changed_paths(workspace, process_id))[-2:])
    assert ((section, modeler), by_run) == (
        ("committed", "committed"),
        {section_run: ["spec/desired.json"], modeler_run: ["flows/current/flow.json"]},
    )
