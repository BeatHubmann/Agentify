"""Authored-level flow schema (A§6.1). Derived data (layout, provenance, findings) never lives here."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentify.domain.ids import ItemId
from agentify.domain.text import normalize_text


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FlowMode(StrEnum):
    CURRENT = "current"
    REDESIGN = "redesign"


class NodeKind(StrEnum):
    TASK = "task"
    ORCHESTRATOR = "orchestrator"
    ACTOR = "actor"
    STEP = "step"


class Execution(StrEnum):
    DETERMINISTIC = "deterministic"
    AI = "ai"
    HUMAN = "human"
    EXTERNAL = "external"


class NodeRole(StrEnum):
    CHECKPOINT = "checkpoint"


AGENT_ARCHETYPES = frozenset({NodeKind.TASK, NodeKind.ORCHESTRATOR, NodeKind.ACTOR})
CONTAINER_KINDS = frozenset({NodeKind.ORCHESTRATOR, NodeKind.ACTOR})


NormalizedText = Annotated[str, Field(max_length=20_000)]


class OutputRef(_Model):
    node: ItemId
    output: str


class Output(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str
    schema_ref: str | None = None

    _norm = field_validator("name")(normalize_text)


class InputBinding(_Model):
    name: str
    source: OutputRef


class ConditionAtom(_Model):
    output: OutputRef
    op: Literal["eq", "ne", "in", "lt", "le", "gt", "ge", "present", "absent"]
    value: str | int | float | bool | list[str | int | float | bool] | None = None


class Precondition(_Model):
    combinator: Literal["all_of", "any_of", "none_of"]
    atoms: tuple[ConditionAtom, ...]


class Node(_Model):
    id: ItemId
    label: NormalizedText
    description: NormalizedText = ""
    kind: NodeKind
    execution: Execution
    parent: ItemId | None = None
    roles: frozenset[NodeRole] = frozenset()
    outputs: tuple[Output, ...] = ()
    inputs: tuple[InputBinding, ...] = ()
    preconditions: Precondition | None = None

    _norm = field_validator("label", "description")(normalize_text)


class Edge(_Model):
    id: ItemId
    source: ItemId
    target: ItemId
    condition: NormalizedText | None = None

    @field_validator("condition")
    @classmethod
    def _norm_condition(cls, value: str | None) -> str | None:
        return None if value is None else normalize_text(value) or None


class CoverageEntry(_Model):
    redesign_node: ItemId
    covers: tuple[ItemId, ...]


class Flow(_Model):
    mode: FlowMode
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    coverage: tuple[CoverageEntry, ...] = ()

    def node_map(self) -> dict[str, Node]:
        return {node.id: node for node in self.nodes}


def children(flow: Flow, container_id: str) -> list[Node]:
    """Direct children of a container: the one definition used by the gate and its messages."""
    return [node for node in flow.nodes if node.parent == container_id]
