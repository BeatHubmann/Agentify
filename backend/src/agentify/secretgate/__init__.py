"""The single secret gate on every exit (A§18). Slice 1 scope: known values and core credential
patterns, applied to version-history blobs. The vendored full rule set comes later (VERIFICATION
INV-8 rows stay pending until then)."""

import base64
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass

_PATTERNS: dict[str, re.Pattern[bytes]] = {
    "aws-access-key-id": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github-token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "anthropic-key": re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "slack-token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
_MIN_KNOWN_VALUE_LENGTH = 8
_GATE_TOKEN = object()


class SecretFound(Exception):
    """A gated exit refused content. Carries where and which rule, never the value."""

    def __init__(self, where: str, rule: str) -> None:
        super().__init__(f"secret-gate hit at {where}: {rule}")
        self.where = where
        self.rule = rule


@dataclass(frozen=True)
class Scrubbed[T]:
    """A value that has passed the gate. Only `SecretGate` can construct one."""

    value: T
    token: object

    def __post_init__(self) -> None:
        if self.token is not _GATE_TOKEN:
            raise TypeError("Scrubbed values are created only by SecretGate")


class SecretGate:
    def __init__(self, known_values: Iterable[str] = ()) -> None:
        self._known: dict[bytes, str] = {}
        for value in known_values:
            self.register(value)

    def register(self, value: str) -> None:
        """Register a credential value; its literal, base64 and URL-encoded forms are matched."""
        if len(value) < _MIN_KNOWN_VALUE_LENGTH:
            raise ValueError("known values shorter than 8 characters cannot be matched safely")
        raw = value.encode()
        for form in (raw, base64.b64encode(raw), urllib.parse.quote(value, safe="").encode()):
            self._known[form] = "known-value"

    def _find(self, data: bytes) -> str | None:
        for form, rule in self._known.items():
            if form in data:
                return rule
        for rule, pattern in _PATTERNS.items():
            if pattern.search(data):
                return rule
        return None

    def redact_text(self, text: str) -> Scrubbed[str]:
        """For logs, events, chat and HTTP bodies: matches are replaced, the rest kept (A§18.2)."""
        data = text.encode("utf-8")
        for form in sorted(self._known, key=len, reverse=True):
            data = data.replace(form, b"[REDACTED:known-value]")
        for rule, pattern in _PATTERNS.items():
            data = pattern.sub(f"[REDACTED:{rule}]".encode(), data)
        return Scrubbed(data.decode("utf-8", errors="replace"), _GATE_TOKEN)

    def check_bytes(self, data: bytes, where: str) -> Scrubbed[bytes]:
        """For artifacts: a hit fails the write (content must not be silently altered)."""
        rule = self._find(data)
        if rule is not None:
            raise SecretFound(where, rule)
        return Scrubbed(data, _GATE_TOKEN)
