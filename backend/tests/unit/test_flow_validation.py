import pytest

from agentify.domain.flow import Edge, Execution, Flow, FlowMode, Node, NodeKind
from agentify.domain.validate import GateThresholds, flow_invariants, integrity
from tests.support.flows import edge, linear, minimal_golden, node

THRESHOLDS = GateThresholds(min_nodes=3, max_shrink=0.3)


def codes(violations: list) -> set[str]:
    return {v.code for v in violations}


def test_minimal_golden_flow_is_valid() -> None:
    flow = minimal_golden()
    assert integrity(flow, authored=True) == []
    assert flow_invariants(flow, THRESHOLDS, base=None) == []


@pytest.mark.parametrize(
    ("flow", "expected_code"),
    [
        (Flow(mode=FlowMode.CURRENT, nodes=(node("a"), node("a"))), "duplicate_node_id"),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(node("a"), node("b")),
                edges=(edge("a", "b"), Edge(id="e-a-b", source="b", target="a")),
            ),
            "duplicate_edge_id",
        ),
        (
            Flow(mode=FlowMode.CURRENT, nodes=(node("a"),), edges=(edge("a", "ghost"),)),
            "unknown_edge_endpoint",
        ),
        (Flow(mode=FlowMode.CURRENT, nodes=(node("a"),), edges=(edge("a", "a"),)), "self_loop"),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(node("a"), node("b")),
                edges=(edge("a", "b"), Edge(id="e2", source="a", target="b")),
            ),
            "duplicate_edge_pair",
        ),
        (
            Flow(mode=FlowMode.CURRENT, nodes=(node("a", NodeKind.TASK, Execution.HUMAN),)),
            "archetype_requires_ai",
        ),
        (Flow(mode=FlowMode.CURRENT, nodes=(node("a", parent="ghost"),)), "unknown_parent"),
        (
            Flow(mode=FlowMode.CURRENT, nodes=(node("s"), node("a", parent="s"))),
            "parent_not_container",
        ),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(
                    node("o1", NodeKind.ORCHESTRATOR, Execution.AI, parent="o2"),
                    node("o2", NodeKind.ORCHESTRATOR, Execution.AI, parent="o1"),
                ),
            ),
            "containment_cycle",
        ),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(
                    node("o", NodeKind.ORCHESTRATOR, Execution.AI),
                    node("c", NodeKind.TASK, Execution.AI, parent="o"),
                    node("x"),
                ),
                edges=(edge("c", "x"),),
            ),
            "edge_crosses_container",
        ),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(node("a"), node("b"), node("c")),
                edges=(edge("a", "b", "yes"), edge("a", "c")),
            ),
            "mixed_split",
        ),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(
                    Node(
                        id="u_0f8e8d3c-1b2a-4c5d-9e6f-7a8b9c0d1e2f",
                        label="x",
                        kind=NodeKind.STEP,
                        execution=Execution.HUMAN,
                    ),
                ),
            ),
            "user_id_in_authored_flow",
        ),
    ],
)
def test_integrity_reports_each_violation(flow: Flow, expected_code: str) -> None:
    assert expected_code in codes(integrity(flow, authored=True))


def test_data_dependency_must_follow_edge_order() -> None:
    flow = Flow.model_validate(
        {
            "mode": "current",
            "nodes": [
                {
                    "id": "a",
                    "label": "a",
                    "kind": "step",
                    "execution": "human",
                    "outputs": [{"id": "o", "name": "o"}],
                },
                {
                    "id": "b",
                    "label": "b",
                    "kind": "step",
                    "execution": "human",
                    "inputs": [{"name": "i", "source": {"node": "a", "output": "o"}}],
                },
            ],
            "edges": [{"id": "e", "source": "b", "target": "a"}],
        }
    )
    assert "input_not_upstream" in codes(integrity(flow, authored=True))


@pytest.mark.parametrize(
    ("flow", "expected_code"),
    [
        (Flow(mode=FlowMode.CURRENT), "too_few_nodes"),
        (linear("a", "b"), "too_few_nodes"),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(node("a"), node("b"), node("c"), node("d")),
                edges=(edge("a", "b"), edge("b", "a"), edge("c", "d")),
            ),
            "node_off_start_end_path",
        ),
        (
            Flow(
                mode=FlowMode.CURRENT,
                nodes=(
                    node("in"),
                    node("o", NodeKind.ORCHESTRATOR, Execution.AI),
                    node("t", NodeKind.TASK, Execution.AI, parent="o"),
                    node("out"),
                ),
                edges=(edge("in", "o"), edge("o", "out")),
            ),
            "container_too_few_children",
        ),
    ],
)
def test_flow_invariants_report_each_violation(flow: Flow, expected_code: str) -> None:
    assert expected_code in codes(flow_invariants(flow, THRESHOLDS, base=None))


def test_container_children_are_on_paths_through_the_container() -> None:
    flow = Flow(
        mode=FlowMode.CURRENT,
        nodes=(
            node("in"),
            node("a", NodeKind.ACTOR, Execution.AI),
            node("t1", NodeKind.TASK, Execution.AI, parent="a"),
            node("t2", NodeKind.TASK, Execution.AI, parent="a"),
            node("out"),
        ),
        edges=(edge("in", "a"), edge("t1", "t2"), edge("a", "out")),
    )
    assert flow_invariants(flow, THRESHOLDS, base=None) == []


def test_incremental_shrink_beyond_bound_is_rejected() -> None:
    base = linear("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")
    shrunk = linear("a", "b", "c", "d", "e", "f")
    assert codes(flow_invariants(shrunk, THRESHOLDS, base=base)) == {"shrink_exceeds_bound"}


def test_incremental_shrink_within_bound_is_accepted() -> None:
    base = linear("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")
    assert flow_invariants(linear("a", "b", "c", "d", "e", "f", "g"), THRESHOLDS, base=base) == []
