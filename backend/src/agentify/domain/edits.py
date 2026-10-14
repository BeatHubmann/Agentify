"""User-edit layer and the pure, total merge (A§6.2, brief §6.4).

User edits never touch agent artifacts. The merge applies an anchored patch only to the content it
was made against; anything else is reported as orphaned, never applied and never dropped.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agentify.domain.canonical import content_hash
from agentify.domain.flow import Edge, Flow, Node
from agentify.domain.ids import is_agent_id, is_user_id

PatchOp = Literal[
    "add_node",
    "modify_node",
    "remove_node",
    "add_edge",
    "modify_edge",
    "remove_edge",
    "profile_override",
    "contract_edit",
]
FLOW_OPS = frozenset(
    {"add_node", "modify_node", "remove_node", "add_edge", "modify_edge", "remove_edge"}
)
MODIFIABLE_NODE_FIELDS = frozenset(
    {
        "label",
        "description",
        "kind",
        "execution",
        "parent",
        "roles",
        "outputs",
        "inputs",
        "preconditions",
    }
)
MODIFIABLE_EDGE_FIELDS = frozenset({"condition"})


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Patch(_Model):
    id: str
    op: PatchOp
    target: str | None = None
    anchor: str | None = None
    ref_anchors: dict[str, str] = {}
    payload: dict[str, Any] = {}
    author_id: str
    created_at: datetime


class Lock(_Model):
    target: str
    anchor: str
    author_id: str


class EditLayer(_Model):
    patches: tuple[Patch, ...] = ()
    locks: tuple[Lock, ...] = ()


@dataclass(frozen=True)
class EditLayerUnreadable:
    """Passed instead of a layer when the stored edit layer cannot be parsed (fail closed)."""

    reason: str


class Origin(StrEnum):
    AGENT = "agent"
    AGENT_EDITED_BY_USER = "agent_edited_by_user"
    USER = "user"


@dataclass(frozen=True)
class OrphanedPatch:
    patch_id: str
    reason: str


@dataclass(frozen=True)
class ConflictReport:
    orphaned: tuple[OrphanedPatch, ...] = ()
    ghost_locks: tuple[str, ...] = ()
    cascaded_removals: tuple[str, ...] = ()
    edit_layer_unreadable: str | None = None


@dataclass(frozen=True)
class MergeResult:
    flow: Flow
    origins: dict[str, Origin] = field(default_factory=dict)
    active_locks: frozenset[str] = frozenset()
    report: ConflictReport = field(default_factory=ConflictReport)


def item_hash(item: Node | Edge) -> str:
    """Anchor for patches and locks: hash of the item's canonical authored content."""
    return content_hash(item.model_dump(mode="json"))


def lockable(authored: Flow, item_id: str) -> bool:
    """The one definition of lockability (A§6.2): agent-authored nodes and edges."""
    return is_agent_id(item_id) and (
        item_id in authored.node_map() or item_id in {edge.id for edge in authored.edges}
    )


def _authored_item(authored: Flow, item_id: str) -> Node | Edge | None:
    nodes = authored.node_map()
    if item_id in nodes:
        return nodes[item_id]
    return next((edge for edge in authored.edges if edge.id == item_id), None)


class _State:
    def __init__(self, flow: Flow) -> None:
        self.nodes: dict[str, Node] = {node.id: node for node in flow.nodes}
        self.edges: dict[str, Edge] = {edge.id: edge for edge in flow.edges}
        self.origins: dict[str, Origin] = dict.fromkeys([*self.nodes, *self.edges], Origin.AGENT)
        self.orphaned: list[OrphanedPatch] = []
        self.cascaded: list[str] = []

    def item(self, item_id: str) -> Node | Edge | None:
        return self.nodes.get(item_id) or self.edges.get(item_id)

    def anchored(self, item_id: str, anchor: str | None) -> bool:
        """User items need no anchor (agents cannot change them); agent items must match it."""
        current = self.item(item_id)
        if current is None:
            return False
        if is_user_id(item_id):
            return True
        return anchor is not None and item_hash(current) == anchor

    def refs_anchored(self, patch: Patch, refs: list[str]) -> bool:
        for ref in refs:
            if ref not in self.nodes:
                return False
            if is_agent_id(ref) and patch.ref_anchors.get(ref) != item_hash(self.nodes[ref]):
                return False
        return True

    def orphan(self, patch: Patch, reason: str) -> None:
        self.orphaned.append(OrphanedPatch(patch.id, reason))

    def remove_node(self, node_id: str) -> None:
        doomed = {node_id}
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            for candidate in self.nodes.values():
                if candidate.parent == current and candidate.id not in doomed:
                    doomed.add(candidate.id)
                    frontier.append(candidate.id)
        for item_id in sorted(doomed):
            self.nodes.pop(item_id, None)
            self.origins.pop(item_id, None)
            if item_id != node_id:
                self.cascaded.append(item_id)
        for edge_id in [
            e.id for e in self.edges.values() if e.source in doomed or e.target in doomed
        ]:
            self.edges.pop(edge_id)
            self.origins.pop(edge_id, None)
            self.cascaded.append(edge_id)


def _apply(state: _State, patch: Patch) -> None:  # noqa: PLR0911, PLR0912 - one branch per op
    match patch.op:
        case "add_node":
            node_id = str(patch.payload.get("id", ""))
            if not is_user_id(node_id) or node_id in state.nodes:
                return state.orphan(patch, "added node needs a fresh user id")
            parent = patch.payload.get("parent")
            if parent is not None and not state.refs_anchored(patch, [str(parent)]):
                return state.orphan(patch, "parent vanished or changed")
            try:
                state.nodes[node_id] = Node.model_validate(patch.payload)
            except ValidationError:
                return state.orphan(patch, "invalid node payload")
            state.origins[node_id] = Origin.USER
        case "modify_node":
            if patch.target is None or patch.target not in state.nodes:
                return state.orphan(patch, "target vanished")
            if not state.anchored(patch.target, patch.anchor):
                return state.orphan(patch, "target changed since the edit")
            if not set(patch.payload) <= MODIFIABLE_NODE_FIELDS:
                return state.orphan(patch, "unsupported fields")
            current = state.nodes[patch.target]
            try:
                state.nodes[patch.target] = Node.model_validate(
                    {**current.model_dump(), **patch.payload}
                )
            except ValidationError:
                return state.orphan(patch, "invalid node payload")
            if state.origins[patch.target] is Origin.AGENT:
                state.origins[patch.target] = Origin.AGENT_EDITED_BY_USER
        case "remove_node":
            if patch.target is None or not state.anchored(patch.target, patch.anchor):
                return state.orphan(patch, "target vanished or changed since the edit")
            state.remove_node(patch.target)
        case "add_edge":
            edge_id = str(patch.payload.get("id", ""))
            if not is_user_id(edge_id) or edge_id in state.edges:
                return state.orphan(patch, "added edge needs a fresh user id")
            endpoints = [str(patch.payload.get("source", "")), str(patch.payload.get("target", ""))]
            if not state.refs_anchored(patch, endpoints):
                return state.orphan(patch, "endpoint vanished or changed")
            try:
                state.edges[edge_id] = Edge.model_validate(patch.payload)
            except ValidationError:
                return state.orphan(patch, "invalid edge payload")
            state.origins[edge_id] = Origin.USER
        case "modify_edge" | "remove_edge":
            if patch.target is None or patch.target not in state.edges:
                return state.orphan(patch, "target vanished")
            if not state.anchored(patch.target, patch.anchor):
                return state.orphan(patch, "target changed since the edit")
            if patch.op == "remove_edge":
                state.edges.pop(patch.target)
                state.origins.pop(patch.target, None)
                return None
            if not set(patch.payload) <= MODIFIABLE_EDGE_FIELDS:
                return state.orphan(patch, "unsupported fields")
            current_edge = state.edges[patch.target]
            state.edges[patch.target] = Edge.model_validate(
                {**current_edge.model_dump(), **patch.payload}
            )
            if state.origins[patch.target] is Origin.AGENT:
                state.origins[patch.target] = Origin.AGENT_EDITED_BY_USER
        case "profile_override" | "contract_edit":
            return None  # merged by the profile and contract layers, not into the flow
    return None


def merge(authored: Flow, layer: EditLayer | EditLayerUnreadable) -> MergeResult:
    """Pure, deterministic, total: returns a schema-valid flow and a conflict report, never raises."""
    if isinstance(layer, EditLayerUnreadable):
        return MergeResult(
            flow=authored,
            origins=dict.fromkeys(
                [*(n.id for n in authored.nodes), *(e.id for e in authored.edges)], Origin.AGENT
            ),
            report=ConflictReport(edit_layer_unreadable=layer.reason),
        )
    state = _State(authored)
    for patch in layer.patches:
        try:
            _apply(state, patch)
        except (
            ValidationError,
            ValueError,
            TypeError,
        ):  # defensive: a malformed payload is an orphan
            state.orphan(patch, "malformed patch")
    active: set[str] = set()
    ghosts: list[str] = []
    for lock in layer.locks:
        authored_item = _authored_item(authored, lock.target)
        if (
            lockable(authored, lock.target)
            and authored_item is not None
            and item_hash(authored_item) == lock.anchor
        ):
            active.add(lock.target)
        else:
            ghosts.append(lock.target)
    merged = Flow(
        mode=authored.mode,
        nodes=tuple(state.nodes.values()),
        edges=tuple(state.edges.values()),
        coverage=authored.coverage,
    )
    return MergeResult(
        flow=merged,
        origins=state.origins,
        active_locks=frozenset(active),
        report=ConflictReport(
            orphaned=tuple(state.orphaned),
            ghost_locks=tuple(ghosts),
            cascaded_removals=tuple(state.cascaded),
        ),
    )
