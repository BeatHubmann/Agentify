"""Brief §12.5A: property tests for the user-edit merge, with collisions across regeneration."""

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from agentify.domain.edits import (
    EditLayer,
    EditLayerUnreadable,
    Lock,
    Origin,
    Patch,
    item_hash,
    merge,
)
from agentify.domain.flow import Flow
from tests.support.strategies import FIXED_TIME, USER_IDS, regenerated, valid_flows


def patch(patch_id: str, op: str, **fields: Any) -> Patch:
    return Patch(id=patch_id, op=op, author_id="user-1", created_at=FIXED_TIME, **fields)  # type: ignore[arg-type]


def user_node(node_id: str) -> dict[str, Any]:
    return {"id": node_id, "label": "added by user", "kind": "step", "execution": "human"}


@given(valid_flows())
def test_empty_layer_is_the_identity(flow: Flow) -> None:
    result = merge(flow, EditLayer())
    assert (result.flow, set(result.origins.values()), result.active_locks) == (
        flow,
        {Origin.AGENT},
        frozenset(),
    )


@given(valid_flows(), USER_IDS, st.data())
def test_user_created_node_survives_regeneration_with_user_origin(
    flow: Flow, user_id: str, data: st.DataObject
) -> None:
    layer = EditLayer(patches=(patch("p1", "add_node", payload=user_node(user_id)),))
    regen = data.draw(regenerated(flow))
    result = merge(regen, layer)
    assert (user_id in result.flow.node_map(), result.origins.get(user_id)) == (True, Origin.USER)


@given(valid_flows(), st.data())
def test_patch_applies_only_to_the_content_it_was_made_against(
    flow: Flow, data: st.DataObject
) -> None:
    target = data.draw(st.sampled_from(flow.nodes))
    layer = EditLayer(
        patches=(
            patch(
                "p1",
                "modify_node",
                target=target.id,
                anchor=item_hash(target),
                payload={"label": "edited"},
            ),
        )
    )
    regen = data.draw(regenerated(flow))
    result = merge(regen, layer)
    regen_target = regen.node_map().get(target.id)
    if regen_target is not None and item_hash(regen_target) == item_hash(target):
        assert (result.flow.node_map()[target.id].label, result.report.orphaned) == ("edited", ())
    else:
        merged_label = result.flow.node_map().get(target.id)
        assert merged_label is None or merged_label.label != "edited"
        assert [o.patch_id for o in result.report.orphaned] == ["p1"]


@given(valid_flows(), st.data())
def test_removal_cascades_to_touching_edges(flow: Flow, data: st.DataObject) -> None:
    target = data.draw(st.sampled_from(flow.nodes))
    layer = EditLayer(
        patches=(patch("p1", "remove_node", target=target.id, anchor=item_hash(target)),)
    )
    result = merge(flow, layer)
    touching = [e.id for e in result.flow.edges if target.id in (e.source, e.target)]
    assert (target.id in result.flow.node_map(), touching) == (False, [])


@given(valid_flows(), USER_IDS, st.data())
def test_child_added_under_a_vanished_parent_is_orphaned_not_reparented(
    flow: Flow, user_id: str, data: st.DataObject
) -> None:
    parent = data.draw(st.sampled_from(flow.nodes))
    payload = {**user_node(user_id), "parent": parent.id}
    layer = EditLayer(
        patches=(
            patch("p1", "add_node", payload=payload, ref_anchors={parent.id: item_hash(parent)}),
        )
    )
    without_parent = flow.model_copy(
        update={
            "nodes": tuple(n for n in flow.nodes if n.id != parent.id),
            "edges": tuple(e for e in flow.edges if parent.id not in (e.source, e.target)),
        }
    )
    result = merge(without_parent, layer)
    assert (user_id in result.flow.node_map(), [o.patch_id for o in result.report.orphaned]) == (
        False,
        ["p1"],
    )


@given(valid_flows(), st.data())
def test_locks_never_reference_ghosts(flow: Flow, data: st.DataObject) -> None:
    locked = data.draw(st.lists(st.sampled_from(flow.nodes), unique_by=lambda n: n.id, max_size=4))
    layer = EditLayer(
        locks=tuple(Lock(target=n.id, anchor=item_hash(n), author_id="user-1") for n in locked)
    )
    regen = data.draw(regenerated(flow))
    result = merge(regen, layer)
    regen_nodes = regen.node_map()
    expected_active = {
        n.id for n in locked if n.id in regen_nodes and item_hash(regen_nodes[n.id]) == item_hash(n)
    }
    assert (set(result.active_locks), set(result.report.ghost_locks)) == (
        expected_active,
        {n.id for n in locked} - expected_active,
    )


_ANY_PAYLOAD = st.dictionaries(
    st.sampled_from(["id", "label", "kind", "parent", "source", "target", "x"]),
    st.none() | st.text(max_size=8) | st.integers(),
    max_size=5,
)


@given(
    valid_flows(),
    st.lists(
        st.builds(
            Patch,
            id=st.text(min_size=1, max_size=4),
            op=st.sampled_from(
                [
                    "add_node",
                    "modify_node",
                    "remove_node",
                    "add_edge",
                    "modify_edge",
                    "remove_edge",
                    "profile_override",
                ]
            ),
            target=st.none() | st.text(max_size=6),
            anchor=st.none() | st.text(max_size=6),
            payload=_ANY_PAYLOAD,
            author_id=st.just("user-1"),
            created_at=st.just(FIXED_TIME),
        ),
        max_size=8,
    ),
)
def test_merge_never_raises_and_is_deterministic(flow: Flow, patches: list[Patch]) -> None:
    layer = EditLayer(patches=tuple(patches))
    first, second = merge(flow, layer), merge(flow, layer)
    assert (first.flow, first.origins, first.report) == (second.flow, second.origins, second.report)


@given(valid_flows())
def test_unreadable_layer_renders_the_authored_flow_and_reports_it(flow: Flow) -> None:
    result = merge(flow, EditLayerUnreadable("bad json"))
    assert (result.flow, result.report.edit_layer_unreadable, result.active_locks) == (
        flow,
        "bad json",
        frozenset(),
    )
