"""Structured logs (A§21) with the secret gate as a processor: no log line leaves unscrubbed (A§18.2)."""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from agentify.secretgate import SecretGate


def _scrubber(gate: SecretGate) -> Any:
    def scrub(
        _logger: object, _method: str, event: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        return {
            key: gate.redact_text(value).value if isinstance(value, str) else value
            for key, value in event.items()
        }

    return scrub


class _RedactFilter(logging.Filter):
    """Standard-library log records (libraries, uvicorn) pass the same gate."""

    def __init__(self, gate: SecretGate) -> None:
        super().__init__()
        self._gate = gate

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._gate.redact_text(record.getMessage()).value
        record.args = ()
        if record.exc_info:
            record.exc_text = self._gate.redact_text(
                logging.Formatter().formatException(record.exc_info)
            ).value
            record.exc_info = None
        return True


def configure_logging(gate: SecretGate, stream: Any = sys.stdout) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            _scrubber(gate),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(stream),
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(stream)
    handler.addFilter(_RedactFilter(gate))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def logger() -> Any:
    return structlog.get_logger("agentify")
