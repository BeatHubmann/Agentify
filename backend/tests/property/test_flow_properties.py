import itertools

from hypothesis import given
from hypothesis import strategies as st

from agentify.domain.flow import Edge, Execution, Flow, Node, NodeKind
from agentify.domain.validate import GateThresholds, flow_invariants, integrity
from tests.support.strategies import valid_flows

THRESHOLDS = GateThresholds(min_nodes=3, max_shrink=0.3)


def located(violations: list, code: str) -> set[tuple[str, ...]]:
    return {v.location for v in violations if v.code == code}


@given(valid_flows())
def test_generated_flows_satisfy_every_invariant(flow: Flow) -> None:
    assert flow_invariants(flow, THRESHOLDS, base=None) == []


@given(valid_flows(), st.data())
def test_planted_duplicate_node_id_is_found_with_its_id(flow: Flow, data: st.DataObject) -> None:
    victim = data.draw(st.sampled_from(flow.nodes))
    planted = flow.model_copy(update={"nodes": (*flow.nodes, victim)})
    assert located(integrity(planted, authored=True), "duplicate_node_id") == {(victim.id,)}


@given(valid_flows(), st.data())
def test_planted_self_loop_is_found_with_its_edge(flow: Flow, data: st.DataObject) -> None:
    victim = data.draw(st.sampled_from(flow.nodes))
    loop = Edge(id="planted", source=victim.id, target=victim.id)
    planted = flow.model_copy(update={"edges": (*flow.edges, loop)})
    assert ("planted",) in located(integrity(planted, authored=True), "self_loop")


@given(valid_flows(), st.data())
def test_planted_archetype_contradiction_is_found(flow: Flow, data: st.DataObject) -> None:
    index = data.draw(st.integers(0, len(flow.nodes) - 1))
    nodes = list(flow.nodes)
    nodes[index] = nodes[index].model_copy(
        update={"kind": NodeKind.TASK, "execution": Execution.HUMAN}
    )
    planted = flow.model_copy(update={"nodes": tuple(nodes)})
    assert located(integrity(planted, authored=True), "archetype_requires_ai") == {
        (nodes[index].id,)
    }


@given(valid_flows())
def test_planted_unreachable_cycle_is_off_every_path(flow: Flow) -> None:
    island = (
        Node(id="iso-a", label="a", kind=NodeKind.STEP, execution=Execution.HUMAN),
        Node(id="iso-b", label="b", kind=NodeKind.STEP, execution=Execution.HUMAN),
    )
    ring = (
        Edge(id="iso-ab", source="iso-a", target="iso-b"),
        Edge(id="iso-ba", source="iso-b", target="iso-a"),
    )
    planted = flow.model_copy(update={"nodes": flow.nodes + island, "edges": flow.edges + ring})
    assert located(flow_invariants(planted, THRESHOLDS, base=None), "node_off_start_end_path") == {
        ("iso-a",),
        ("iso-b",),
    }


@given(valid_flows(min_nodes=10, max_nodes=12), st.integers(0, 12))
def test_shrink_bound_matches_the_share_of_removed_nodes(flow: Flow, removed: int) -> None:
    removed = min(removed, len(flow.nodes) - 3)
    kept_ids = {n.id for n in flow.nodes[: len(flow.nodes) - removed]}
    kept = flow.model_copy(
        update={
            "nodes": tuple(n for n in flow.nodes if n.id in kept_ids),
            "edges": tuple(e for e in flow.edges if e.source in kept_ids and e.target in kept_ids),
        }
    )
    found = {v.code for v in flow_invariants(kept, THRESHOLDS, base=flow)}
    assert ("shrink_exceeds_bound" in found) == (removed / len(flow.nodes) > 0.3)


@st.composite
def flows_with_containers(draw: st.DrawFn) -> tuple[Flow, dict[str, tuple[NodeKind, int]]]:
    """A valid flow extended by `... -> box -> after`: box holds `direct` children chained in order;
    optionally its first child is itself an orchestrator holding `inner` children. Returns the flow
    and, per container, its kind and its direct-child count computed here, not by the code."""
    flow = draw(valid_flows())
    kind = draw(st.sampled_from([NodeKind.ORCHESTRATOR, NodeKind.ACTOR]))
    direct, nested = draw(st.integers(0, 4)), draw(st.booleans())
    inner = draw(st.integers(0, 3)) if nested and direct > 0 else None

    def ai(node_id: str, node_kind: NodeKind = NodeKind.TASK, parent: str | None = None) -> Node:
        return Node(
            id=node_id, label=node_id, kind=node_kind, execution=Execution.AI, parent=parent
        )

    child_ids = [f"c{i}" for i in range(direct)]
    nodes = [ai("box", kind), ai("after")]
    nodes += [
        ai(c, NodeKind.ORCHESTRATOR if inner is not None and i == 0 else NodeKind.TASK, "box")
        for i, c in enumerate(child_ids)
    ]
    inner_ids = [f"d{i}" for i in range(inner or 0)]
    nodes += [ai(d, parent="c0") for d in inner_ids]
    chain = [
        *itertools.pairwise(child_ids),
        *itertools.pairwise(inner_ids),
    ]
    last = flow.nodes[-1].id
    edges = [
        Edge(id="e-in-box", source=last, target="box"),
        Edge(id="e-box-after", source="box", target="after"),
        *(Edge(id=f"e-{a}-{b}", source=a, target=b) for a, b in chain),
    ]
    counts = {"box": (kind, direct)}
    if inner is not None:
        counts["c0"] = (NodeKind.ORCHESTRATOR, inner)
    extended = flow.model_copy(
        update={"nodes": (*flow.nodes, *nodes), "edges": (*flow.edges, *edges)}
    )
    return extended, counts


@given(flows_with_containers())
def test_a_container_needs_two_direct_children_whatever_its_descendants_and_successors(
    case: tuple[Flow, dict[str, tuple[NodeKind, int]]],
) -> None:
    """INV-5: grandchildren and edge successors are not children; the message uses the same count."""
    flow, counts = case
    found = {
        (v.location, v.message)
        for v in flow_invariants(flow, THRESHOLDS, base=None)
        if v.code == "container_too_few_children"
    }
    expected = {
        ((cid,), f"{cid} has {n} direct children; a {kind} needs at least 2.")
        for cid, (kind, n) in counts.items()
        if n < 2
    }
    assert found == expected
