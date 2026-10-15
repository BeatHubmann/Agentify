"""Artifact envelope and tolerant reads (A§6, A§22.3)."""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from agentify.domain.canonical import canonical_json
from agentify.domain.edits import EditLayer
from agentify.domain.flow import Flow

FLOW_SCHEMA_VERSION = 1
EDIT_LAYER_SCHEMA_VERSION = 1


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int
    artifact_type: str
    output_language: str
    inputs: dict[str, Any] = {}
    body: dict[str, Any]


@dataclass(frozen=True)
class Corrupt:
    """A stored artifact that could not be read. Shown to the user, never silently replaced."""

    reason: str


def encode(
    artifact_type: str,
    schema_version: int,
    body: BaseModel,
    *,
    output_language: str,
    inputs: dict[str, Any] | None = None,
) -> bytes:
    envelope = Envelope(
        schema_version=schema_version,
        artifact_type=artifact_type,
        output_language=output_language,
        inputs=inputs or {},
        body=body.model_dump(mode="json"),
    )
    return canonical_json(envelope.model_dump(mode="json"))


INPUTS_PREFIX = "inputs/"  # uploaded documents: raw text, not enveloped artifacts


def decode_artifact(data: bytes) -> Envelope | Corrupt:
    """Any stored artifact's envelope (every path outside `INPUTS_PREFIX`)."""
    try:
        return Envelope.model_validate(json.loads(data))
    except (ValueError, ValidationError) as exc:
        return Corrupt(f"unreadable envelope: {type(exc).__name__}")


def _envelope(data: bytes, artifact_type: str) -> Envelope | Corrupt:
    envelope = decode_artifact(data)
    if isinstance(envelope, Corrupt):
        return envelope
    if envelope.artifact_type != artifact_type:
        return Corrupt(f"expected {artifact_type}, found {envelope.artifact_type}")
    return envelope


def decode_flow(data: bytes) -> Flow | Corrupt:
    envelope = _envelope(data, "flow")
    if isinstance(envelope, Corrupt):
        return envelope
    try:
        return Flow.model_validate(envelope.body)
    except ValidationError as exc:
        return Corrupt(f"flow schema violation: {exc.error_count()} errors")


def decode_edit_layer(data: bytes) -> EditLayer | Corrupt:
    envelope = _envelope(data, "edit_layer")
    if isinstance(envelope, Corrupt):
        return envelope
    try:
        return EditLayer.model_validate(envelope.body)
    except ValidationError as exc:
        return Corrupt(f"edit layer schema violation: {exc.error_count()} errors")
