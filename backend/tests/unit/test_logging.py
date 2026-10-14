"""INV-8.LOG: structlog and standard-library log lines both pass the secret gate."""

import io
import logging

import structlog

from agentify.observability import configure_logging
from tests.support.gates import make_gate

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
