"""User edits (A§6.2): one validated, anchored, atomic batch per call, committed as a user revision.

Anchors are computed here from the state the client edited against (`base_revision_id`), so a
patch can only ever apply to that content. If the flow or the edit layer moved since, the batch is
refused with a conflict (HTTP 409 later) instead of being applied to different content.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agentify.domain.edits import EditLayer, Lock, Patch, PatchOp, item_hash, lockable, merge
from agentify.domain.envelope import (
    EDIT_LAYER_SCHEMA_VERSION,
    Corrupt,
    decode_edit_layer,
    decode_flow,
    encode,
)
from agentify.domain.flow import Edge, Flow, Node
from agentify.domain.ids import is_agent_id
from agentify.domain.validate import Violation
from agentify.harness.gate import validate_flow_state
from agentify.registry import FlowAgentKind
from agentify.store.workspace import (
    BaseMovedConflict,
    CommitValidationFailed,
    PathReader,
    UserWriter,
    Workspace,
)


class EditConflict(Exception):
    """The flow or edit layer changed since the client's base revision (409)."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"changed since your base revision: {paths}")
        self.paths = paths


class EditRejected(Exception):
    """The batch is invalid or would break a flow invariant (422)."""

    def __init__(self, violations: list[Violation]) -> None:
        super().__init__("; ".join(f"{v.code}: {v.message}" for v in violations))
        self.violations = violations


@dataclass(frozen=True)
class PatchRequest:
    op: PatchOp
    target: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EditBatch:
    patches: tuple[PatchRequest, ...] = ()
    lock: tuple[str, ...] = ()
    unlock: tuple[str, ...] = ()


def _anchored_patch(merged: Flow, request: PatchRequest, user_id: str, now: datetime) -> Patch:
    items: dict[str, Node | Edge] = {
        **merged.node_map(),
        **{edge.id: edge for edge in merged.edges},
    }
    anchor = None
    if request.target is not None and request.target in items:
        anchor = item_hash(items[request.target])
    refs = [
        request.payload.get("parent"),
        request.payload.get("source"),
        request.payload.get("target"),
    ]
    ref_anchors = {
        str(ref): item_hash(items[str(ref)])
        for ref in refs
        if ref is not None and is_agent_id(str(ref)) and str(ref) in items
    }
    return Patch(
        id=f"p_{uuid.uuid4().hex}",
        op=request.op,
        target=request.target,
        anchor=anchor,
        ref_anchors=ref_anchors,
        payload=dict(request.payload),
        author_id=user_id,
        created_at=now,
    )


def build_layer(
    authored: Flow, layer: EditLayer, batch: EditBatch, user_id: str, now: datetime
) -> EditLayer:
    """Append the batch to the layer, anchoring each patch to the merged state it is applied to."""
    patches = list(layer.patches)
    for request in batch.patches:
        merged = merge(authored, EditLayer(patches=tuple(patches), locks=layer.locks))
        patch = _anchored_patch(merged.flow, request, user_id, now)
        after = merge(authored, EditLayer(patches=(*patches, patch), locks=layer.locks))
        if any(o.patch_id == patch.id for o in after.report.orphaned):
            raise EditRejected(
                [
                    Violation(
                        "patch_not_applicable", f"{request.op} on {request.target} cannot apply"
                    )
                ]
            )
        patches.append(patch)
    locks = [lock for lock in layer.locks if lock.target not in set(batch.unlock)]
    nodes_and_edges: dict[str, Node | Edge] = {
        **authored.node_map(),
        **{edge.id: edge for edge in authored.edges},
    }
    for target in batch.lock:
        if not lockable(authored, target):
            raise EditRejected(
                [Violation("not_lockable", f"{target} is not an agent-authored item")]
            )
        if target not in {lock.target for lock in locks}:
            locks.append(
                Lock(target=target, anchor=item_hash(nodes_and_edges[target]), author_id=user_id)
            )
    return EditLayer(patches=tuple(patches), locks=tuple(locks))


async def apply_edit_batch(
    ws: Workspace,
    *,
    process_id: str,
    kind: FlowAgentKind,
    base_revision_id: str | None,
    user_id: str,
    batch: EditBatch,
    output_language: str = "en",
) -> str:
    """Validate and commit one batch as a user-authored revision. Returns the revision id."""
    flow_data = await ws.read(process_id, kind.output_path, base_revision_id)
    flow = None if flow_data is None else decode_flow(flow_data)
    if flow is None or isinstance(flow, Corrupt):
        raise EditRejected([Violation("flow_unavailable", "There is no readable flow to edit.")])
    layer_data = await ws.read(process_id, kind.edit_layer_path, base_revision_id)
    layer = EditLayer() if layer_data is None else decode_edit_layer(layer_data)
    if isinstance(layer, Corrupt):
        raise EditRejected([Violation("edit_layer_unreadable", layer.reason)])
    new_layer = build_layer(flow, layer, batch, user_id, datetime.now(UTC))
    data = encode(
        "edit_layer", EDIT_LAYER_SCHEMA_VERSION, new_layer, output_language=output_language
    )
    async with ws.db.tx() as conn:
        engagement_id = await ws.engagement_of(conn, process_id)
    sha = await ws.blobs.put(engagement_id, ws.gate.check_bytes(data, where=kind.edit_layer_path))

    async def validate(reader: PathReader, _changed: frozenset[str]) -> list[Violation]:
        return await validate_flow_state(reader, kind, incremental=False, base_flow=None)

    try:
        return await ws.commit(
            process_id=process_id,
            writer=UserWriter(user_id),
            base_revision_id=base_revision_id,
            staged={kind.edit_layer_path: sha},
            semantic_inputs=frozenset({kind.output_path}),
            validate=validate,
            message=f"user {user_id}: edit {kind.edit_layer_path}",
        )
    except BaseMovedConflict as exc:
        raise EditConflict(exc.paths) from exc
    except CommitValidationFailed as exc:
        raise EditRejected(exc.violations) from exc
