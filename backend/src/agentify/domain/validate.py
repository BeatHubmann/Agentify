"""Flow validation: integrity (every write) and flow invariants (gate and commit), A§6.1."""

from collections import Counter
from dataclasses import dataclass

import networkx as nx

from agentify.domain.flow import AGENT_ARCHETYPES, CONTAINER_KINDS, Execution, Flow, children
from agentify.domain.ids import is_agent_id
from agentify.domain.semantics import (
    end_nodes,
    execution_graph,
    nodes_off_start_end_paths,
    start_nodes,
)

CONTAINER_MIN_CHILDREN = 2


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    location: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateThresholds:
    min_nodes: int
    max_shrink: float


def _duplicates(ids: list[str]) -> list[str]:
    return sorted(item for item, count in Counter(ids).items() if count > 1)


def _containment_violations(flow: Flow) -> list[Violation]:
    nodes = flow.node_map()
    found: list[Violation] = []
    for node in flow.nodes:
        if node.parent is None:
            continue
        parent = nodes.get(node.parent)
        if parent is None:
            found.append(
                Violation("unknown_parent", f"Parent {node.parent!r} does not exist.", (node.id,))
            )
        elif parent.kind not in CONTAINER_KINDS:
            found.append(
                Violation(
                    "parent_not_container",
                    f"Parent {node.parent!r} is a {parent.kind}; only orchestrators and actors contain nodes.",
                    (node.id,),
                )
            )
    containment = nx.DiGraph(
        (node.parent, node.id)
        for node in flow.nodes
        if node.parent is not None and node.parent in nodes
    )
    for cycle in nx.simple_cycles(containment):
        found.append(
            Violation("containment_cycle", "Containment must be acyclic.", tuple(sorted(cycle)))
        )
    return found


def _edge_violations(flow: Flow) -> list[Violation]:
    nodes = flow.node_map()
    found = [
        Violation("duplicate_edge_id", f"Edge id {edge_id!r} is used more than once.", (edge_id,))
        for edge_id in _duplicates([edge.id for edge in flow.edges])
    ]
    pairs = Counter((edge.source, edge.target) for edge in flow.edges)
    for (source, target), count in pairs.items():
        if count > 1:
            found.append(
                Violation(
                    "duplicate_edge_pair",
                    "At most one edge per ordered node pair.",
                    (source, target),
                )
            )
    for edge in flow.edges:
        if edge.source not in nodes or edge.target not in nodes:
            found.append(
                Violation(
                    "unknown_edge_endpoint",
                    "Edge endpoints must exist.",
                    (edge.id, edge.source, edge.target),
                )
            )
            continue
        if edge.source == edge.target:
            found.append(Violation("self_loop", "Self-loops are not allowed.", (edge.id,)))
        if nodes[edge.source].parent != nodes[edge.target].parent:
            found.append(
                Violation(
                    "edge_crosses_container",
                    "Edges may only connect nodes with the same parent.",
                    (edge.id,),
                )
            )
    return found


def _split_violations(flow: Flow) -> list[Violation]:
    outgoing: dict[str, list[bool]] = {}
    for edge in flow.edges:
        outgoing.setdefault(edge.source, []).append(edge.condition is not None)
    return [
        Violation(
            "mixed_split",
            "Outgoing edges must be all conditional (exclusive) or all unconditional (parallel).",
            (source,),
        )
        for source, flags in outgoing.items()
        if any(flags) and not all(flags)
    ]


def _reference_violations(flow: Flow) -> list[Violation]:
    nodes = flow.node_map()
    found: list[Violation] = []
    graph = execution_graph(flow)
    for node in flow.nodes:
        refs = [binding.source for binding in node.inputs]
        if node.preconditions is not None:
            refs.extend(atom.output for atom in node.preconditions.atoms)
        for ref in refs:
            producer = nodes.get(ref.node)
            if producer is None or ref.output not in {output.id for output in producer.outputs}:
                found.append(
                    Violation(
                        "unknown_output_ref", f"{ref.node}.{ref.output} does not exist.", (node.id,)
                    )
                )
            elif ref.node != node.id and not nx.has_path(graph, ref.node, node.id):
                found.append(
                    Violation(
                        "input_not_upstream",
                        f"{ref.node}.{ref.output} is not produced upstream of {node.id}.",
                        (node.id, ref.node),
                    )
                )
    return found


def integrity(flow: Flow, *, authored: bool) -> list[Violation]:
    """Level-2 checks; run on every write path."""
    found = [
        Violation("duplicate_node_id", f"Node id {node_id!r} is used more than once.", (node_id,))
        for node_id in _duplicates([node.id for node in flow.nodes])
    ]
    for node in flow.nodes:
        if node.kind in AGENT_ARCHETYPES and node.execution is not Execution.AI:
            found.append(
                Violation(
                    "archetype_requires_ai", f"A {node.kind} must have execution 'ai'.", (node.id,)
                )
            )
        if authored and not is_agent_id(node.id):
            found.append(
                Violation(
                    "user_id_in_authored_flow",
                    "Ids starting with 'u_' are reserved for user items.",
                    (node.id,),
                )
            )
    if authored:
        found.extend(
            Violation(
                "user_id_in_authored_flow",
                "Ids starting with 'u_' are reserved for user items.",
                (edge.id,),
            )
            for edge in flow.edges
            if not is_agent_id(edge.id)
        )
    found += _containment_violations(flow) + _edge_violations(flow) + _split_violations(flow)
    if found:
        return found
    return _reference_violations(flow)


def flow_invariants(
    flow: Flow, thresholds: GateThresholds, *, base: Flow | None
) -> list[Violation]:
    """Level-3 checks (A§8.6): integrity, structural soundness, non-degeneracy, arity, shrink."""
    found = integrity(flow, authored=False)
    if found:
        return found
    if len(flow.nodes) < thresholds.min_nodes:
        found.append(
            Violation(
                "too_few_nodes",
                f"The flow has {len(flow.nodes)} nodes; at least {thresholds.min_nodes} required.",
            )
        )
    if flow.nodes and not start_nodes(flow):
        found.append(Violation("no_start_node", "The flow needs at least one start node."))
    if flow.nodes and not end_nodes(flow):
        found.append(Violation("no_end_node", "The flow needs at least one end node."))
    found.extend(
        Violation(
            "node_off_start_end_path",
            "Every node must lie on a path from a start to an end.",
            (node_id,),
        )
        for node_id in nodes_off_start_end_paths(flow)
    )
    for node in flow.nodes:
        if node.kind in CONTAINER_KINDS:
            count = len(children(flow, node.id))
            if count < CONTAINER_MIN_CHILDREN:
                found.append(
                    Violation(
                        "container_too_few_children",
                        f"{node.id} has {count} direct children; a {node.kind} needs at least "
                        f"{CONTAINER_MIN_CHILDREN}.",
                        (node.id,),
                    )
                )
    if base is not None and base.nodes:
        removed = {n.id for n in base.nodes} - {n.id for n in flow.nodes}
        share = len(removed) / len(base.nodes)
        if share > thresholds.max_shrink:
            found.append(
                Violation(
                    "shrink_exceeds_bound",
                    f"The run removed {share:.0%} of the base flow's nodes; at most "
                    f"{thresholds.max_shrink:.0%} is allowed in incremental mode.",
                    tuple(sorted(removed)),
                )
            )
    return found
