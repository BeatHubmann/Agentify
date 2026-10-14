"""Flow builders shared by tests. Expected results in tests are written independently of these."""

import itertools

from agentify.domain.flow import Edge, Execution, Flow, FlowMode, Node, NodeKind


def node(
    node_id: str,
    kind: NodeKind = NodeKind.STEP,
    execution: Execution = Execution.HUMAN,
    parent: str | None = None,
    label: str | None = None,
) -> Node:
    return Node(id=node_id, label=label or node_id, kind=kind, execution=execution, parent=parent)


def edge(source: str, target: str, condition: str | None = None) -> Edge:
    return Edge(id=f"e-{source}-{target}", source=source, target=target, condition=condition)


def linear(*ids: str, mode: FlowMode = FlowMode.CURRENT) -> Flow:
    nodes = tuple(node(i) for i in ids)
    edges = tuple(edge(a, b) for a, b in itertools.pairwise(ids))
    return Flow(mode=mode, nodes=nodes, edges=edges)


def minimal_golden() -> Flow:
    """The canonical four-step case: one step per execution type (evals/synthetic)."""
    return Flow(
        mode=FlowMode.CURRENT,
        nodes=(
            node("receive", NodeKind.STEP, Execution.EXTERNAL),
            node("classify", NodeKind.TASK, Execution.AI),
            node("compute-fee", NodeKind.STEP, Execution.DETERMINISTIC),
            node("approve", NodeKind.STEP, Execution.HUMAN),
        ),
        edges=(
            edge("receive", "classify"),
            edge("classify", "compute-fee"),
            edge("compute-fee", "approve"),
        ),
    )
