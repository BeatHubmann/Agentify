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
