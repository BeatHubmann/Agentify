"""Canonical JSON: the one serializer every artifact write path uses (A§6)."""

import hashlib
import json
from typing import Any

from agentify.domain.text import normalize_text


def normalize_leaves(value: Any) -> Any:
    """Return a copy of a JSON-like tree with every string leaf normalized; keys are kept as-is."""
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list | tuple):
        return [normalize_leaves(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_leaves(item) for key, item in value.items()}
    return value


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no insignificant whitespace, UTF-8, normalized strings, no NaN."""
    return json.dumps(
        normalize_leaves(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(value: Any) -> str:
    """SHA-256 of the canonical JSON encoding: stable across key order and text encoding."""
    return sha256_hex(canonical_json(value))
