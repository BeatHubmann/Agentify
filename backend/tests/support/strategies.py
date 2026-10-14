"""Hypothesis strategies for flows. Generated flows are valid by construction (forward-only edges,
uniform split kinds, archetypes executed by AI), so tests can plant one violation at a time."""

from datetime import UTC, datetime

from hypothesis import strategies as st

from agentify.domain.flow import Edge, Execution, Flow, FlowMode, Node, NodeKind

_STEP_EXECUTIONS = [Execution.HUMAN, Execution.DETERMINISTIC, Execution.EXTERNAL, Execution.AI]


@st.composite
def nodes_for(draw: st.DrawFn, node_id: str) -> Node:
    if draw(st.booleans()):
        return Node(id=node_id, label=node_id, kind=NodeKind.TASK, execution=Execution.AI)
    return Node(
        id=node_id,
        label=node_id,
        kind=NodeKind.STEP,
        execution=draw(st.sampled_from(_STEP_EXECUTIONS)),
    )


@st.composite
def valid_flows(draw: st.DrawFn, min_nodes: int = 3, max_nodes: int = 12) -> Flow:
    count = draw(st.integers(min_nodes, max_nodes))
    ids = [f"n{i}" for i in range(count)]
    nodes = [draw(nodes_for(node_id)) for node_id in ids]
    targets: dict[int, set[int]] = {i: {i + 1} for i in range(count - 1)}
    for source in range(count - 2):
        extra = draw(st.sets(st.integers(source + 2, count - 1), max_size=2))
        targets[source] |= extra
    edges: list[Edge] = []
    for source, outs in targets.items():
        conditional = len(outs) > 1 and draw(st.booleans())
        for target in sorted(outs):
            edges.append(
                Edge(
                    id=f"e{source}-{target}",
                    source=ids[source],
                    target=ids[target],
                    condition=f"case {target}" if conditional else None,
                )
            )
    return Flow(mode=FlowMode.CURRENT, nodes=tuple(nodes), edges=tuple(edges))


USER_IDS = st.uuids(version=4).map(lambda u: f"u_{u}")
FIXED_TIME = datetime(2026, 10, 14, tzinfo=UTC)


def regenerated(flow: Flow) -> st.SearchStrategy[Flow]:
    """A regenerated agent flow that reuses some of the old ids with possibly different content,
    drops others, and adds new ones: the collisions the merge must survive (brief §12.5A)."""

    @st.composite
    def build(draw: st.DrawFn) -> Flow:
        fresh = draw(valid_flows())
        keep = draw(st.sets(st.sampled_from([n.id for n in flow.nodes]), max_size=len(flow.nodes)))
        relabel = draw(st.booleans())
        reused = tuple(
            n.model_copy(update={"label": n.label + " v2"}) if relabel else n
            for n in flow.nodes
            if n.id in keep
        )
        reused_ids = {n.id for n in reused}
        nodes = reused + tuple(n for n in fresh.nodes if n.id not in reused_ids)
        ids = {n.id for n in nodes}
        edges = tuple(e for e in flow.edges + fresh.edges if e.source in ids and e.target in ids)
        unique: dict[str, Edge] = {}
        for e in edges:
            unique.setdefault(e.id, e)
        return Flow(mode=flow.mode, nodes=nodes, edges=tuple(unique.values()))

    return build()
