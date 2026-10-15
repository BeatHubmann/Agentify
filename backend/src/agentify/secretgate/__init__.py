"""The single secret gate on every exit (A§18). Known values and core credential patterns; the
vendored full rule set comes later (VERIFICATION INV-8 stays pending until then).

A hit is reported by rule and keyed fingerprint (A§18.3), never by value. Known values live in one
shared registry, so every sink matches the values of every live context (A§17.2)."""

import base64
import hashlib
import hmac
import json
import re
import threading
import urllib.parse
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_PATTERNS: dict[str, re.Pattern[bytes]] = {
    "aws-access-key-id": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github-token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "anthropic-key": re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "slack-token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}
KNOWN_VALUE = "known-value"
_MIN_KNOWN_VALUE_LENGTH = 8
_MIN_FINGERPRINT_KEY_LENGTH = 32
_GATE_TOKEN = object()


@dataclass(frozen=True)
class Hit:
    """One match: which rule, and a keyed fingerprint of the matched value (never the value)."""

    rule: str
    fingerprint: str


class SecretFound(Exception):
    """A gated exit refused content. Carries where, which rule and the fingerprint, never the value."""

    def __init__(self, where: str, hit: Hit) -> None:
        super().__init__(f"secret-gate hit at {where}: {hit.rule}")
        self.where = where
        self.hit = hit
        self.rule = hit.rule
        self.fingerprint = hit.fingerprint


@dataclass(frozen=True)
class Scrubbed[T]:
    """A value that has passed the gate. Only `SecretGate` can construct one."""

    value: T
    token: object
    hits: tuple[Hit, ...] = ()
    """What the gate redacted on the way (redacting exits only)."""

    def __post_init__(self) -> None:
        if self.token is not _GATE_TOKEN:
            raise TypeError("Scrubbed values are created only by SecretGate")


def _forms(value: bytes) -> tuple[bytes, ...]:
    """Every form a value takes in a sink: plain, base64, URL-encoded, and JSON-escaped (model
    requests, run events and HTTP bodies are JSON, where a quote or backslash is escaped)."""
    escaped = json.dumps(value.decode("utf-8", "surrogateescape"))[1:-1].encode(
        "utf-8", "surrogateescape"
    )
    forms = (value, base64.b64encode(value), urllib.parse.quote_from_bytes(value, safe="").encode())
    return forms if escaped == value else (*forms, escaped)


class SecretGate:
    def __init__(self, known_values: Iterable[str] = (), *, fingerprint_key: str) -> None:
        if len(fingerprint_key) < _MIN_FINGERPRINT_KEY_LENGTH:
            raise ValueError("the fingerprint key needs at least 32 characters")
        self._key = fingerprint_key.encode()
        self._lock = threading.Lock()
        self._holders: Counter[bytes] = Counter()
        self._known: tuple[tuple[bytes, bytes], ...] = ()
        """(form, raw value) pairs, longest form first; replaced whole, never mutated."""
        self.register(fingerprint_key)
        for value in known_values:
            self.register(value)

    def fingerprint(self, matched: bytes) -> str:
        return hmac.new(self._key, matched, hashlib.sha256).hexdigest()

    def is_known_fingerprint(self, fingerprint: str) -> bool:
        return any(
            hmac.compare_digest(self.fingerprint(raw), fingerprint) for _, raw in self._known
        )

    def register(self, value: str) -> None:
        """Register a credential for the process lifetime (configured credentials, tokens in use)."""
        self._change([value], +1)

    @contextmanager
    def registered(self, values: Iterable[str]) -> Iterator[None]:
        """Register credentials while a context lives (per-request credentials, A§17.2)."""
        held = list(values)
        self._change(held, +1)
        try:
            yield
        finally:
            self._change(held, -1)

    def _change(self, values: list[str], delta: int) -> None:
        for value in values:
            if len(value) < _MIN_KNOWN_VALUE_LENGTH:
                raise ValueError("known values shorter than 8 characters cannot be matched safely")
        with self._lock:
            for value in values:
                self._holders[value.encode()] += delta
            self._holders = +self._holders  # drops values no context holds any more
            pairs = {(form, raw) for raw in self._holders for form in _forms(raw)}
            self._known = tuple(sorted(pairs, key=lambda pair: len(pair[0]), reverse=True))

    def _scan(self, data: bytes) -> list[Hit]:
        hits = [
            Hit(KNOWN_VALUE, self.fingerprint(raw)) for form, raw in self._known if form in data
        ]
        for rule, pattern in _PATTERNS.items():
            hits += [Hit(rule, self.fingerprint(m.group(0))) for m in pattern.finditer(data)]
        return list(dict.fromkeys(hits))

    def known_hits(self, data: bytes) -> list[Hit]:
        """Registered values only, in every encoding: what may never be sent to a model. Pattern
        matches are left out, because client content is the model's input (A§18.2)."""
        return [hit for hit in self._scan(data) if hit.rule == KNOWN_VALUE]

    def redact_text(self, text: str) -> Scrubbed[str]:
        """For logs, events, chat and HTTP bodies: matches are replaced, the rest kept (A§18.2)."""
        data = text.encode("utf-8", "surrogatepass")  # lone surrogates come out as U+FFFD
        hits = self._scan(data)
        for form, _ in self._known:
            data = data.replace(form, f"[REDACTED:{KNOWN_VALUE}]".encode())
        for rule, pattern in _PATTERNS.items():
            data = pattern.sub(f"[REDACTED:{rule}]".encode(), data)
        return Scrubbed(data.decode("utf-8", errors="replace"), _GATE_TOKEN, tuple(hits))

    def check_bytes(
        self, data: bytes, where: str, *, allowed: frozenset[Hit] = frozenset()
    ) -> Scrubbed[bytes]:
        """For artifacts: a hit fails the write (content must not be silently altered). An allowed
        fingerprint (an engagement's allow-list, A§18.3) passes; a known value never does."""
        for hit in self._scan(data):
            if hit.rule == KNOWN_VALUE or hit not in allowed:
                raise SecretFound(where, hit)
        return Scrubbed(data, _GATE_TOKEN)
