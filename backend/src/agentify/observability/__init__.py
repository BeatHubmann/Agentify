"""Structured logs (A§21) with the secret gate as a processor: no log line leaves unscrubbed (A§18.2).

Every key and value is scrubbed, nested ones included; an object is scrubbed as the text it would
render to. A redaction is recorded on the line itself as `security_event` (A§18.3, change C-7). If
the primary stream fails, the same scrubbed line goes to the fallback stream, else it is dropped."""

import json
import logging
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO

import structlog

from agentify.secretgate import Hit, SecretGate

_WRITE_ERRORS = (OSError, ValueError)


def _scrubbed(gate: SecretGate, value: object, hits: list[Hit]) -> object:
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, dict):
        return {str(_scrubbed(gate, k, hits)): _scrubbed(gate, v, hits) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_scrubbed(gate, v, hits) for v in value]
    redacted = gate.redact_text(value if isinstance(value, str) else str(value))
    hits.extend(redacted.hits)
    return redacted.value


def _security_event(hits: list[Hit]) -> list[dict[str, str]]:
    return [
        {"exit": "logs", "rule": hit.rule, "fingerprint": hit.fingerprint}
        for hit in dict.fromkeys(hits)
    ]


def _scrubber(gate: SecretGate) -> Any:
    def scrub(
        _logger: object, _method: str, event: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        hits: list[Hit] = []
        clean = {str(_scrubbed(gate, k, hits)): _scrubbed(gate, v, hits) for k, v in event.items()}
        if hits:
            clean["security_event"] = _security_event(hits)
        return clean

    return scrub


class _FallbackPrinter:
    """structlog's output: the rendered, scrubbed line to the primary stream, else the fallback."""

    def __init__(self, primary: TextIO, fallback: TextIO) -> None:
        self._streams = (primary, fallback)

    def msg(self, message: str) -> None:
        for stream in self._streams:
            try:
                stream.write(message + "\n")
                stream.flush()
            except _WRITE_ERRORS:
                continue
            return

    debug = info = warning = warn = error = critical = exception = fatal = log = msg


class _RedactFilter(logging.Filter):
    """Standard-library log records (libraries, uvicorn) pass the same gate."""

    def __init__(self, gate: SecretGate) -> None:
        super().__init__()
        self._gate = gate

    def filter(self, record: logging.LogRecord) -> bool:
        message = self._gate.redact_text(record.getMessage())
        hits = list(message.hits)
        record.msg = message.value
        record.args = ()
        if record.exc_info:
            trace = self._gate.redact_text(logging.Formatter().formatException(record.exc_info))
            hits.extend(trace.hits)
            record.exc_text = trace.value
            record.exc_info = None
        if hits:
            record.msg += f" security_event={json.dumps(_security_event(hits))}"
        return True


class _FallbackHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """A failed write goes to the fallback stream as the same scrubbed line, else it is dropped."""

    def __init__(self, primary: TextIO, fallback: TextIO) -> None:
        super().__init__(primary)
        self._fallback = fallback

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - the stdlib's name
        try:
            self._fallback.write(self.format(record) + self.terminator)
            self._fallback.flush()
        except _WRITE_ERRORS:
            pass


def configure_logging(
    gate: SecretGate, stream: TextIO | None = None, fallback: TextIO | None = None
) -> None:
    primary, secondary = stream or sys.stdout, fallback or sys.stderr
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            _scrubber(gate),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=lambda *_args: _FallbackPrinter(primary, secondary),
        cache_logger_on_first_use=False,
    )
    handler = _FallbackHandler(primary, secondary)
    handler.addFilter(_RedactFilter(gate))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def logger() -> Any:
    return structlog.get_logger("agentify")
