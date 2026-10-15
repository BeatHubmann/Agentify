"""INV-8.LOG and INV-8.SF: structlog and standard-library log lines pass the secret gate, nested
values included, and a fallback write carries only the scrubbed line (A§18.3)."""

import hashlib
import hmac
import io
import json
import logging
from typing import Any

import structlog

from agentify.observability import configure_logging
from tests.support.gates import FINGERPRINT_KEY, make_gate

CANARY = "canary-credential-0123456789"


def _fail() -> None:
    raise ValueError(f"failure holding {CANARY}")


def test_structlog_and_stdlib_logs_are_redacted() -> None:
    out = io.StringIO()
    configure_logging(make_gate(CANARY), stream=out)
    structlog.get_logger("agentify").info("event", detail=f"token={CANARY}")
    logging.getLogger("some.library").warning("library says %s", CANARY)
    try:
        _fail()
    except ValueError:
        logging.getLogger("some.library").exception("crashed")
    text = out.getvalue()
    assert (CANARY in text, text.count("[REDACTED:known-value]") >= 3) == (False, True)


def test_uvicorn_records_reach_the_scrubbing_root_handler() -> None:
    from fastapi import FastAPI

    from agentify.api.server import uvicorn_config

    out = io.StringIO()
    configure_logging(make_gate(CANARY), stream=out)
    uvicorn_config(FastAPI(), host="127.0.0.1", port=0).load()
    logging.getLogger("uvicorn.error").error("Exception in ASGI application: %s", CANARY)
    assert (CANARY in out.getvalue(), "[REDACTED:known-value]" in out.getvalue()) == (False, True)


class Opaque:
    """An object whose rendering carries a secret: the JSON renderer would call str() on it."""

    def __str__(self) -> str:
        return f"Opaque({CANARY})"

    __repr__ = __str__


class BrokenStream(io.StringIO):
    def write(self, s: str) -> int:
        raise OSError("stream closed")


def keyed(value: str) -> str:
    return hmac.new(FINGERPRINT_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def test_nested_values_keys_and_objects_are_redacted_and_recorded_on_the_line() -> None:
    out = io.StringIO()
    configure_logging(make_gate(CANARY), stream=out)
    nested: dict[str, Any] = {"k": CANARY, "items": [CANARY, 1]}
    structlog.get_logger("agentify").info("event", payload=nested, obj=Opaque(), **{CANARY: "v"})
    [line] = [json.loads(raw) for raw in out.getvalue().splitlines()]
    assert (
        CANARY in out.getvalue(),
        line["payload"],
        line["obj"],
        line["security_event"],
    ) == (
        False,
        {"k": "[REDACTED:known-value]", "items": ["[REDACTED:known-value]", 1]},
        "Opaque([REDACTED:known-value])",
        [{"exit": "logs", "rule": "known-value", "fingerprint": keyed(CANARY)}],
    )


def test_a_failed_log_write_falls_back_to_stderr_with_the_scrubbed_line() -> None:
    fallback = io.StringIO()
    configure_logging(make_gate(CANARY), stream=BrokenStream(), fallback=fallback)
    structlog.get_logger("agentify").info("event", detail=CANARY)
    logging.getLogger("some.library").warning("library says %s", CANARY)
    text = fallback.getvalue()
    assert (CANARY in text, text.count("[REDACTED:known-value]"), len(text.splitlines())) == (
        False,
        2,
        2,
    )


def test_a_failed_fallback_drops_the_line_instead_of_raising_into_the_caller() -> None:
    configure_logging(make_gate(CANARY), stream=BrokenStream(), fallback=BrokenStream())
    structlog.get_logger("agentify").info("event", detail=CANARY)
    logging.getLogger("some.library").warning("library says %s", CANARY)
