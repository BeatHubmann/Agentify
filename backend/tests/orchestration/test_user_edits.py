"""Invariants 2 and 3, brief §12.5B "user edit during a run": edits are their own revisions, never lost,
never applied to changed content; locks hold or the run rolls back."""

import pytest
from pydantic_ai.messages import RetryPromptPart

from agentify.domain.edits import EditLayer, Origin, merge
from agentify.domain.envelope import decode_edit_layer
from agentify.editing import EditBatch, EditConflict, EditRejected, PatchRequest, apply_edit_batch
from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_admission import _commit_raw
from tests.orchestration.test_run_exit_paths import (
    FLOW_PATH,
    commit_golden,
    head_flow,
    history,
    run_once,
)
from tests.support.flows import minimal_golden
from tests.support.scripted import Script, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"
EDITS = "flows/current/edits.json"
USER_NODE = "u_0f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f"


async def head(ws: Workspace, process_id: str) -> str | None:
    async with ws.db.tx() as conn:
        return await ws.head_revision(conn, process_id)


async def edit_layer(ws: Workspace, process_id: str) -> EditLayer:
    data = await ws.read(process_id, EDITS)
    if data is None:
        return EditLayer()
    layer = decode_edit_layer(data)
    assert isinstance(layer, EditLayer)
    return layer


def relabel(node_id: str, label: str) -> EditBatch:
    return EditBatch(patches=(PatchRequest("modify_node", node_id, {"label": label}),))


async def edit(
    ws: Workspace, registry: Registry, process_id: str, batch: EditBatch, base: str | None = "head"
) -> str:
    base_revision = await head(ws, process_id) if base == "head" else base
    return await apply_edit_batch(
        ws,
        process_id=process_id,
        kind=registry.agents[KIND],
        base_revision_id=base_revision,
        user_id="consultant-2",
        batch=batch,
    )


async def test_an_edit_batch_is_one_user_revision_holding_only_the_edit_layer(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    await edit(workspace, registry, process_id, relabel("approve", "Approve invoice and fee"))
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    merged = merge(flow, await edit_layer(workspace, process_id))
    revisions = await history(workspace, process_id)
    assert (
        merged.flow.node_map()["approve"].label,
        merged.origins["approve"],
        revisions[-1]["author_kind"],
        flow == minimal_golden(),
    ) == ("Approve invoice and fee", Origin.AGENT_EDITED_BY_USER, "user", True)


async def test_an_edit_against_a_stale_base_is_a_conflict_not_a_silent_overwrite(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    stale = await head(workspace, process_id)
    await edit(workspace, registry, process_id, relabel("approve", "first session"))
    with pytest.raises(EditConflict):
        await edit(
            workspace, registry, process_id, relabel("classify", "second session"), base=stale
        )
    layer = await edit_layer(workspace, process_id)
    assert [p.payload for p in layer.patches] == [{"label": "first session"}]


async def test_an_edit_that_would_break_an_invariant_is_rejected_atomically(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    before = await history(workspace, process_id)
    batch = EditBatch(
        patches=(
            PatchRequest("remove_node", "classify"),
            PatchRequest("remove_node", "compute-fee"),
        )
    )
    with pytest.raises(EditRejected) as rejected:
        await edit(workspace, registry, process_id, batch)
    assert (
        await history(workspace, process_id),
        {v.code for v in rejected.value.violations} & {"too_few_nodes", "node_off_start_end_path"}
        != set(),
    ) == (before, True)


async def test_only_agent_items_can_be_locked(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    with pytest.raises(EditRejected):
        await edit(workspace, registry, process_id, EditBatch(lock=("ghost",)))


async def test_a_user_edit_during_a_run_is_committed_alone_and_survives_the_runs_rollback(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)

    async def user_edits_now() -> None:
        await edit(workspace, registry, process_id, relabel("approve", "edited during the run"))

    async def provider_down() -> None:
        raise ConnectionError("model endpoint unavailable")

    script = Script(
        [
            Step(
                calls=[("edit_node", {"id": "receive", "label": "Receive invoice"})],
                before=user_edits_now,
            ),
            Step(before=provider_down),
        ]
    )
    _run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    revisions = await history(workspace, process_id)
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    merged = merge(flow, await edit_layer(workspace, process_id)).flow.node_map()
    assert (
        final,
        [r["author_kind"] for r in revisions],
        merged["approve"].label,
        merged["receive"].label,
    ) == ("rolled_back", ["agent_run", "user"], "edited during the run", "receive")


async def test_a_run_commit_rebases_onto_a_concurrent_user_edit(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)

    async def user_edits_now() -> None:
        await edit(workspace, registry, process_id, relabel("approve", "edited during the run"))

    script = Script(
        [
            Step(
                calls=[("edit_node", {"id": "receive", "label": "Receive invoice"})],
                before=user_edits_now,
            ),
            Step(text="done"),
        ]
    )
    run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    revisions = await history(workspace, process_id)
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    merged = merge(flow, await edit_layer(workspace, process_id)).flow.node_map()
    assert (
        final,
        [(r["author_kind"], r["paths"] if r["run_id"] == run_id else None) for r in revisions][1:],
        merged["approve"].label,
        merged["receive"].label,
    ) == (
        "committed",
        [("user", None), ("agent_run", [EDITS, FLOW_PATH])],
        "edited during the run",
        "Receive invoice",
    )


async def test_a_locked_item_cannot_be_changed_by_an_incremental_run(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    await edit(workspace, registry, process_id, EditBatch(lock=("approve",)))
    script = Script(
        [
            Step(calls=[("edit_node", {"id": "approve", "label": "changed by agent"})]),
            Step(text="done"),
        ]
    )
    _run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    retries = [
        p
        for messages in script.seen
        for msg in messages
        for p in getattr(msg, "parts", [])
        if isinstance(p, RetryPromptPart)
    ]
    assert (
        final,
        flow.node_map()["approve"].label,
        ['"locked_item"' in str(p.content) for p in retries],
    ) == ("unchanged", "approve", [True])


async def test_a_lock_added_during_the_run_is_honoured_at_the_gate(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)

    async def user_locks_now() -> None:
        await edit(workspace, registry, process_id, EditBatch(lock=("approve",)))

    script = Script(
        [
            Step(calls=[("read_flow", {})], before=user_locks_now),
            Step(calls=[("edit_node", {"id": "approve", "label": "changed by agent"})]),
            Step(text="done"),
        ]
    )
    run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    state = await runs.run_state(workspace, run_id)
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    assert (final, state["outcome"], flow.node_map()["approve"].label) == (
        "rolled_back",
        "gate_failed",
        "approve",
    )
    assert "locked_item_changed" in str(state["outcome_detail_scrubbed"])


async def test_ghost_locks_refuse_incremental_admission(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    await edit(workspace, registry, process_id, EditBatch(lock=("approve",)))
    confirmation = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind=KIND, confirmed_by="c"
    )
    steps = golden_calls()
    steps[3] = Step(
        calls=[
            (
                "add_node",
                {"id": "approve", "label": "Approve (new)", "kind": "step", "execution": "human"},
            )
        ]
    )
    _run_id, final = await run_once(
        workspace, registry, process_id, Script(steps), confirmation=confirmation
    )
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            workspace,
            process_id=process_id,
            kind=registry.agents[KIND],
            mode="incremental",
            initiated_by="c",
            entry_point="test",
        )
    assert (final, refused.value.code, refused.value.detail) == (
        "committed",
        "ghost_locks",
        "approve",
    )


async def test_user_created_node_survives_a_from_scratch_regeneration(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    batch = EditBatch(
        patches=(
            PatchRequest(
                "add_node",
                None,
                {"id": USER_NODE, "label": "Archive", "kind": "step", "execution": "human"},
            ),
            PatchRequest(
                "add_edge",
                None,
                {
                    "id": "u_1f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                    "source": "approve",
                    "target": USER_NODE,
                },
            ),
        )
    )
    await edit(workspace, registry, process_id, batch)
    layer_before = await edit_layer(workspace, process_id)
    confirmation = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind=KIND, confirmed_by="c"
    )
    _run_id, final = await run_once(
        workspace, registry, process_id, Script(golden_calls()), confirmation=confirmation
    )
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    result = merge(flow, await edit_layer(workspace, process_id))
    assert (
        final,
        await edit_layer(workspace, process_id) == layer_before,
        result.origins.get(USER_NODE),
        result.report.orphaned,
    ) == ("committed", True, Origin.USER, ())


async def test_a_concurrent_commit_to_the_runs_own_path_is_a_conflict(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    from agentify.domain.envelope import FLOW_SCHEMA_VERSION, encode

    async def other_writer_commits() -> None:
        data = encode("flow", FLOW_SCHEMA_VERSION, minimal_golden(), output_language="en")
        await _commit_raw(workspace, process_id, FLOW_PATH, data)

    steps = golden_calls()
    steps[-1] = Step(text="done", before=other_writer_commits)
    run_id, final = await run_once(workspace, registry, process_id, Script(steps))
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        state["outcome"],
        [r["author_kind"] for r in await history(workspace, process_id)],
    ) == ("rolled_back", "commit_failed", ["system"])
    assert "BaseMovedConflict" in str(state["outcome_detail_scrubbed"])


async def test_an_incremental_run_commits_its_other_changes_and_leaves_locked_items_identical(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    await edit(
        workspace, registry, process_id, EditBatch(lock=("approve", "e-compute-fee-approve"))
    )
    before = await head_flow(workspace, process_id)
    assert before is not None
    script = Script(
        [
            Step(calls=[("edit_node", {"id": "approve", "label": "changed by agent"})]),
            Step(calls=[("delete_edge", {"id": "e-compute-fee-approve"})]),
            Step(calls=[("edit_node", {"id": "receive", "label": "Receive invoice"})]),
            Step(text="done"),
        ]
    )
    _run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    after = await head_flow(workspace, process_id)
    assert after is not None
    locked_edge = next(e for e in after.edges if e.id == "e-compute-fee-approve")
    assert (
        final,
        after.node_map()["approve"] == before.node_map()["approve"],
        locked_edge == next(e for e in before.edges if e.id == "e-compute-fee-approve"),
        after.node_map()["receive"].label,
    ) == ("committed", True, True, "Receive invoice")


async def test_a_ghost_lock_is_resolved_by_release_or_by_relocking(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    await commit_golden(workspace, registry, process_id)
    await edit(workspace, registry, process_id, EditBatch(lock=("approve", "classify")))
    confirmation = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind=KIND, confirmed_by="c"
    )
    steps = golden_calls()
    steps[1] = Step(
        calls=[
            (
                "add_node",
                {"id": "classify", "label": "Classify (new)", "kind": "task", "execution": "ai"},
            )
        ]
    )
    steps[3] = Step(
        calls=[
            (
                "add_node",
                {"id": "approve", "label": "Approve (new)", "kind": "step", "execution": "human"},
            )
        ]
    )
    await run_once(workspace, registry, process_id, Script(steps), confirmation=confirmation)
    await edit(workspace, registry, process_id, EditBatch(unlock=("approve",)))
    still_ghost = await _refusal(workspace, kind, process_id)
    await edit(workspace, registry, process_id, EditBatch(unlock=("classify",), lock=("classify",)))
    admitted = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="incremental",
        initiated_by="c",
        entry_point="test",
    )
    assert (still_ghost, admitted.startswith("run_")) == ("ghost_locks", True)


async def _refusal(ws: Workspace, kind: object, process_id: str) -> str:
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            ws,
            process_id=process_id,
            kind=kind,
            mode="incremental",  # type: ignore[arg-type]
            initiated_by="c",
            entry_point="test",
        )
    return refused.value.code


@pytest.mark.parametrize(
    ("batch", "expected_code"),
    [
        (
            EditBatch(
                patches=(
                    PatchRequest(
                        "add_edge",
                        None,
                        {
                            "id": "u_2f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "source": "approve",
                            "target": "ghost",
                        },
                    ),
                )
            ),
            "patch_not_applicable",
        ),
        (
            EditBatch(patches=(PatchRequest("modify_node", "approve", {"kind": "task"}),)),
            "archetype_requires_ai",
        ),
        (
            EditBatch(
                patches=(
                    PatchRequest("modify_edge", "e-classify-compute-fee", {"condition": "if fee"}),
                    PatchRequest(
                        "add_edge",
                        None,
                        {
                            "id": "u_3f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "source": "classify",
                            "target": "approve",
                        },
                    ),
                )
            ),
            "mixed_split",
        ),
        (
            EditBatch(
                patches=(
                    PatchRequest(
                        "add_node",
                        None,
                        {
                            "id": "u_4f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "label": "x",
                            "kind": "step",
                            "execution": "human",
                        },
                    ),
                    PatchRequest(
                        "add_node",
                        None,
                        {
                            "id": "u_6f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "label": "y",
                            "kind": "step",
                            "execution": "human",
                        },
                    ),
                    PatchRequest(
                        "add_edge",
                        None,
                        {
                            "id": "u_5f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "source": "u_4f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "target": "u_6f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                        },
                    ),
                    PatchRequest(
                        "add_edge",
                        None,
                        {
                            "id": "u_7f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "source": "u_6f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                            "target": "u_4f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                        },
                    ),
                )
            ),
            "node_off_start_end_path",
        ),
        (
            EditBatch(
                patches=(
                    PatchRequest(
                        "modify_node", "classify", {"kind": "orchestrator", "execution": "ai"}
                    ),
                    PatchRequest("modify_node", "compute-fee", {"parent": "classify"}),
                )
            ),
            "edge_crosses_container",
        ),
    ],
    ids=[
        "dangling-edge",
        "archetype-contradiction",
        "mixed-split",
        "unreachable-node",
        "containment-breaks-edges",
    ],
)
async def test_invalid_edit_batches_are_rejected_with_the_violation(
    workspace: Workspace, registry: Registry, process_id: str, batch: EditBatch, expected_code: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    before = await history(workspace, process_id)
    with pytest.raises(EditRejected) as rejected:
        await edit(workspace, registry, process_id, batch)
    assert (
        expected_code in {v.code for v in rejected.value.violations},
        await history(workspace, process_id),
    ) == (True, before)


async def test_a_refused_session_rereads_and_resubmits(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    shared_base = await head(workspace, process_id)
    await edit(
        workspace, registry, process_id, relabel("approve", "first session"), base=shared_base
    )
    with pytest.raises(EditConflict):
        await edit(
            workspace, registry, process_id, relabel("classify", "second session"), base=shared_base
        )
    await edit(workspace, registry, process_id, relabel("classify", "second session"))
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    merged = merge(flow, await edit_layer(workspace, process_id)).flow.node_map()
    assert (merged["approve"].label, merged["classify"].label) == (
        "first session",
        "second session",
    )


def test_no_agent_kind_can_write_the_user_layer(registry: Registry) -> None:
    user_paths = {kind.edit_layer_path for kind in registry.agents.values()}
    assert [kind.id for kind in registry.agents.values() if kind.output_path in user_paths] == []
