"""Self-validating graph-mutation tools for flow-authoring agents (A§6.12, brief §6.3 item 1).

Every tool reads the run's staged flow, applies one small change, validates it and stages it, or
returns a structured, correctable error through `ModelRetry`. Counting happens in the harness loop.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext

from agentify.domain.edits import EditLayer, item_hash
from agentify.domain.envelope import FLOW_SCHEMA_VERSION, Corrupt, decode_flow, encode
from agentify.domain.flow import Edge, Flow, FlowMode, Node
from agentify.domain.validate import Violation, integrity
from agentify.harness.ports import ArtifactLocks, StagingPort

FRAMEWORK_RETRIES = 1_000
"""Set far above Agentify's own error budgets so the harness's cumulative counter trips first."""


@dataclass
class FlowToolDeps:
    staging: StagingPort
    output_path: str
    flow_mode: FlowMode
    output_language: str
    locked: frozenset[str] = frozenset()
    """Agent items the user locked (incremental mode); changing them is a correctable error."""
    lock_anchors: dict[str, str] = field(default_factory=dict)
    locks: ArtifactLocks = field(default_factory=ArtifactLocks)
    _cache: dict[str, Flow] = field(default_factory=dict)


def correctable(
    error: str, message: str, location: tuple[str, ...] = (), hint: str = ""
) -> ModelRetry:
    return ModelRetry(
        json.dumps(
            {"error": error, "message": message, "location": list(location), "hint": hint},
            sort_keys=True,
        )
    )


def _violations_error(violations: list[Violation]) -> ModelRetry:
    first = violations[0]
    return correctable(
        first.code,
        "; ".join(v.message for v in violations[:5]),
        first.location,
        hint="Fix the listed problem with another small edit.",
    )


async def _load(deps: FlowToolDeps) -> Flow:
    cached = deps._cache.get(deps.output_path)
    if cached is not None:
        return cached
    data = await deps.staging.read(deps.output_path)
    if data is None:
        flow = Flow(mode=deps.flow_mode)
    else:
        decoded = decode_flow(data)
        if isinstance(decoded, Corrupt):
            raise correctable("read_failure", f"The staged flow is unreadable: {decoded.reason}")
        flow = decoded
    deps._cache[deps.output_path] = flow
    return flow


async def _store(deps: FlowToolDeps, flow: Flow) -> None:
    violations = integrity(flow, authored=True)
    if violations:
        raise _violations_error(violations)
    data = encode("flow", FLOW_SCHEMA_VERSION, flow, output_language=deps.output_language)
    await deps.staging.write(deps.output_path, data)
    deps._cache[deps.output_path] = flow


def _guard_lock(deps: FlowToolDeps, flow: Flow, item_id: str) -> None:
    if item_id not in deps.locked:
        return
    current = flow.node_map().get(item_id) or next((e for e in flow.edges if e.id == item_id), None)
    if current is not None and item_hash(current) == deps.lock_anchors.get(item_id):
        raise correctable(
            "locked_item", f"{item_id} is locked by the user and cannot change.", (item_id,)
        )


async def _mutate(ctx: RunContext[FlowToolDeps], change: Any, *args: Any) -> str:
    deps = ctx.deps
    async with deps.locks.for_path(deps.output_path):
        flow = await _load(deps)
        try:
            updated = change(deps, flow, *args)
        except ValidationError as exc:
            raise correctable(
                "invalid_arguments", f"{exc.error_count()} invalid fields: {exc.errors()[0]['msg']}"
            ) from exc
        await _store(deps, updated)
    return "ok"


def _add_node(_deps: FlowToolDeps, flow: Flow, fields: dict[str, Any]) -> Flow:
    node = Node.model_validate(fields)
    return flow.model_copy(update={"nodes": (*flow.nodes, node)})


def _edit_node(deps: FlowToolDeps, flow: Flow, node_id: str, fields: dict[str, Any]) -> Flow:
    current = flow.node_map().get(node_id)
    if current is None:
        raise correctable(
            "unknown_node", f"No node {node_id!r}.", (node_id,), hint="Call read_flow."
        )
    _guard_lock(deps, flow, node_id)
    updated = Node.model_validate({**current.model_dump(), **fields, "id": node_id})
    return flow.model_copy(
        update={"nodes": tuple(updated if n.id == node_id else n for n in flow.nodes)}
    )


def _delete_node(deps: FlowToolDeps, flow: Flow, node_id: str) -> Flow:
    if node_id not in flow.node_map():
        raise correctable(
            "unknown_node", f"No node {node_id!r}.", (node_id,), hint="Call read_flow."
        )
    _guard_lock(deps, flow, node_id)
    touching = [e for e in flow.edges if node_id in (e.source, e.target)]
    for edge in touching:
        _guard_lock(deps, flow, edge.id)
    return flow.model_copy(
        update={
            "nodes": tuple(n for n in flow.nodes if n.id != node_id),
            "edges": tuple(e for e in flow.edges if e not in touching),
        }
    )


def _add_edge(_deps: FlowToolDeps, flow: Flow, fields: dict[str, Any]) -> Flow:
    edge = Edge.model_validate(fields)
    return flow.model_copy(update={"edges": (*flow.edges, edge)})


def _delete_edge(deps: FlowToolDeps, flow: Flow, edge_id: str) -> Flow:
    if edge_id not in {e.id for e in flow.edges}:
        raise correctable(
            "unknown_edge", f"No edge {edge_id!r}.", (edge_id,), hint="Call read_flow."
        )
    _guard_lock(deps, flow, edge_id)
    return flow.model_copy(update={"edges": tuple(e for e in flow.edges if e.id != edge_id)})


def register_flow_tools(agent: Agent[FlowToolDeps, str]) -> None:
    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def add_node(  # noqa: PLR0917 - the signature is the tool schema the model sees
        ctx: RunContext[FlowToolDeps],
        id: str,
        label: str,
        kind: str,
        execution: str,
        parent: str | None = None,
        description: str = "",
    ) -> str:
        """Add one node. kind: task|orchestrator|actor|step; execution: deterministic|ai|human|external."""
        fields = {
            "id": id,
            "label": label,
            "kind": kind,
            "execution": execution,
            "parent": parent,
            "description": description,
        }
        return await _mutate(ctx, _add_node, fields)

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def edit_node(  # noqa: PLR0917 - the signature is the tool schema the model sees
        ctx: RunContext[FlowToolDeps],
        id: str,
        label: str | None = None,
        kind: str | None = None,
        execution: str | None = None,
        description: str | None = None,
    ) -> str:
        """Change fields of one existing node."""
        fields = {
            k: v
            for k, v in {
                "label": label,
                "kind": kind,
                "execution": execution,
                "description": description,
            }.items()
            if v is not None
        }
        return await _mutate(ctx, _edit_node, id, fields)

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def delete_node(ctx: RunContext[FlowToolDeps], id: str) -> str:
        """Delete one node and the edges touching it."""
        return await _mutate(ctx, _delete_node, id)

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def add_edge(
        ctx: RunContext[FlowToolDeps],
        id: str,
        source: str,
        target: str,
        condition: str | None = None,
    ) -> str:
        """Add one edge between two sibling nodes; give a condition for exclusive branches."""
        return await _mutate(
            ctx, _add_edge, {"id": id, "source": source, "target": target, "condition": condition}
        )

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def delete_edge(ctx: RunContext[FlowToolDeps], id: str) -> str:
        """Delete one edge."""
        return await _mutate(ctx, _delete_edge, id)

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def read_flow(ctx: RunContext[FlowToolDeps]) -> str:
        """Return the flow as staged so far."""
        flow = await _load(ctx.deps)
        return json.dumps(flow.model_dump(mode="json"), sort_keys=True)


def build_modeler(instructions: str) -> Agent[FlowToolDeps, str]:
    agent: Agent[FlowToolDeps, str] = Agent(
        deps_type=FlowToolDeps, output_type=str, instructions=instructions
    )
    register_flow_tools(agent)
    return agent


def lock_view(layer: EditLayer) -> tuple[frozenset[str], dict[str, str]]:
    return frozenset(lock.target for lock in layer.locks), {
        lock.target: lock.anchor for lock in layer.locks
    }
