"""The harness-owned flow gate (A§8.6) and the commit-time validator (A§7.3 step 4).

Neither depends on anything the model did or did not call: they re-read stored bytes.
"""

from dataclasses import dataclass, field

from agentify.domain.edits import EditLayer, item_hash, merge
from agentify.domain.envelope import Corrupt, decode_edit_layer, decode_flow
from agentify.domain.flow import Edge, Flow, Node
from agentify.domain.validate import Violation, flow_invariants
from agentify.registry import FlowAgentKind
from agentify.store.workspace import PathReader, Validator, Workspace

TRUNCATED_FINISH_REASONS = frozenset({"length"})


@dataclass(frozen=True)
class GateResult:
    passed: bool
    unchanged: bool = False
    violations: tuple[Violation, ...] = field(default_factory=tuple)


async def _edit_layer(reader: PathReader, path: str) -> EditLayer | Corrupt:
    data = await reader.read(path)
    return EditLayer() if data is None else decode_edit_layer(data)


def lock_violations(staged: Flow, layer: EditLayer) -> list[Violation]:
    """Every lock must find its item unchanged in the staged authored flow (invariant 3)."""
    items: dict[str, Node | Edge] = {
        **staged.node_map(),
        **{edge.id: edge for edge in staged.edges},
    }
    return [
        Violation(
            "locked_item_changed",
            f"Locked item {lock.target} was changed or removed.",
            (lock.target,),
        )
        for lock in layer.locks
        if lock.target not in items or item_hash(items[lock.target]) != lock.anchor
    ]


async def validate_flow_state(
    reader: PathReader, kind: FlowAgentKind, *, incremental: bool, base_flow: Flow | None
) -> list[Violation]:
    """Invariants of the merged flow in a path map; shared by the gate and the commit."""
    data = await reader.read(kind.output_path)
    if data is None:
        return [Violation("nothing_staged", "No flow was produced.")]
    flow = decode_flow(data)
    if isinstance(flow, Corrupt):
        return [Violation("staged_flow_corrupt", flow.reason)]
    layer = await _edit_layer(reader, kind.edit_layer_path)
    if isinstance(layer, Corrupt):
        if incremental:
            return [Violation("edit_layer_unreadable", layer.reason)]
        layer = EditLayer()
    merged = merge(flow, layer).flow
    found = flow_invariants(merged, kind.gate.thresholds(), base=base_flow if incremental else None)
    if incremental:
        found += lock_violations(flow, layer)
    return found


async def check(
    ws: Workspace,
    *,
    run_id: str,
    process_id: str,
    kind: FlowAgentKind,
    incremental: bool,
    base_revision_id: str | None,
    finish_reason: str | None,
) -> GateResult:
    if finish_reason in TRUNCATED_FINISH_REASONS:
        return GateResult(
            False,
            violations=(Violation("truncated_finish", "The model's last answer was cut off."),),
        )
    staged = await ws.staged(run_id)
    if not staged:
        if incremental:
            return GateResult(True, unchanged=True)
        return GateResult(False, violations=(Violation("nothing_staged", "No flow was produced."),))
    async with ws.db.tx() as conn:
        engagement_id = await ws.engagement_of(conn, process_id)
        head = await ws.head_revision(conn, process_id)
        head_map = await ws.path_map(conn, head)
        base_map = await ws.path_map(conn, base_revision_id)
    reader = PathReader(ws.blobs, engagement_id, {**head_map, **staged})
    base_flow = await _base_flow(PathReader(ws.blobs, engagement_id, base_map), kind)
    violations = await validate_flow_state(
        reader, kind, incremental=incremental, base_flow=base_flow
    )
    return GateResult(not violations, violations=tuple(violations))


async def _base_flow(reader: PathReader, kind: FlowAgentKind) -> Flow | None:
    data = await reader.read(kind.output_path)
    if data is None:
        return None
    decoded = decode_flow(data)
    return None if isinstance(decoded, Corrupt) else decoded


def commit_validator(
    kind: FlowAgentKind, *, incremental: bool, base_flow: Flow | None
) -> Validator:
    async def validate(reader: PathReader, _changed: frozenset[str]) -> list[Violation]:
        return await validate_flow_state(reader, kind, incremental=incremental, base_flow=base_flow)

    return validate


async def base_flow_of(
    ws: Workspace, process_id: str, base_revision_id: str | None, kind: FlowAgentKind
) -> Flow | None:
    async with ws.db.tx() as conn:
        engagement_id = await ws.engagement_of(conn, process_id)
        base_map = await ws.path_map(conn, base_revision_id)
    return await _base_flow(PathReader(ws.blobs, engagement_id, base_map), kind)
