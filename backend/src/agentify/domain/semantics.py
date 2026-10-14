"""Execution semantics (A§6.1.1): one definition shared by validation, governance and simulation."""

from collections.abc import Iterable

import networkx as nx

from agentify.domain.flow import CONTAINER_KINDS, Flow


def _sibling_edges_out(flow: Flow) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {node.id: [] for node in flow.nodes}
    for edge in flow.edges:
        out.setdefault(edge.source, []).append(edge.target)
    return out


def _sibling_edges_in(flow: Flow) -> dict[str, list[str]]:
    into: dict[str, list[str]] = {node.id: [] for node in flow.nodes}
    for edge in flow.edges:
        into.setdefault(edge.target, []).append(edge.source)
    return into


def start_nodes(flow: Flow) -> list[str]:
    """Top-level nodes without incoming edges."""
    into = _sibling_edges_in(flow)
    return [node.id for node in flow.nodes if node.parent is None and not into[node.id]]


def end_nodes(flow: Flow) -> list[str]:
    """Top-level nodes without outgoing edges."""
    out = _sibling_edges_out(flow)
    return [node.id for node in flow.nodes if node.parent is None and not out[node.id]]


def execution_graph(flow: Flow) -> nx.DiGraph:
    """The flow with every container expanded into its children.

    An edge into a container enters the container node, which leads to its children's start
    nodes; the container is left from its children's end nodes. Requires integrity (A§6.1 level 2).
    """
    nodes = flow.node_map()
    kids: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for node in flow.nodes:
        if node.parent is not None and node.parent in kids:
            kids[node.parent].append(node.id)
    out = _sibling_edges_out(flow)
    into = _sibling_edges_in(flow)

    def exits(node_id: str, seen: frozenset[str] = frozenset()) -> set[str]:
        node = nodes[node_id]
        members = kids[node_id]
        if node.kind not in CONTAINER_KINDS or not members or node_id in seen:
            return {node_id}
        inner_ends = [kid for kid in members if not out[kid]]
        if not inner_ends:
            return {node_id}
        result: set[str] = set()
        for kid in inner_ends:
            result |= exits(kid, seen | {node_id})
        return result

    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    for node_id, members in kids.items():
        if nodes[node_id].kind in CONTAINER_KINDS:
            for kid in members:
                if not into[kid]:
                    graph.add_edge(node_id, kid)
    for edge in flow.edges:
        for exit_id in exits(edge.source):
            graph.add_edge(exit_id, edge.target)
    graph.graph["terminal"] = (
        set().union(*(exits(end) for end in end_nodes(flow))) if flow.nodes else set()
    )
    return graph


def nodes_off_start_end_paths(flow: Flow) -> list[str]:
    graph = execution_graph(flow)
    starts = start_nodes(flow)
    terminal: Iterable[str] = graph.graph["terminal"]
    reachable: set[str] = set(starts)
    for start in starts:
        reachable |= nx.descendants(graph, start)
    coreachable: set[str] = set(terminal)
    for end in terminal:
        coreachable |= nx.ancestors(graph, end)
    return [
        node.id for node in flow.nodes if node.id not in reachable or node.id not in coreachable
    ]
