"""One problem-details shape for every error (A§6.12); every body passes the secret gate (A§18.2)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agentify import inbox
from agentify.editing import EditConflict, EditRejected
from agentify.harness import runs
from agentify.identity import Unauthenticated
from agentify.observability import logger
from agentify.secretgate import SecretFound, SecretGate
from agentify.store import security_events
from agentify.store.db import Database


class NotFound(Exception):
    """Unknown, or not visible to this user: the API does not distinguish (no existence disclosure)."""


class Forbidden(Exception):
    def __init__(self, role: str) -> None:
        super().__init__(f"requires the {role} role")
        self.role = role


class BadRequest(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


_INBOX_STATUS = {"human_answer_required": 403, "unknown_item": 404}


def install(app: FastAPI, gate: SecretGate, db: Database) -> None:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        status, code, detail = _problem(exc)
        scrubbed = gate.redact_text(detail)
        await security_events.record(
            db, gate, exit="http_body", hits=scrubbed.hits, location=request.url.path
        )
        body = {
            "type": "about:blank",
            "status": status,
            "code": code,
            "title": f"error.{code}",
            "detail": scrubbed.value,
        }
        return JSONResponse(body, status_code=status, media_type="application/problem+json")

    def _problem(exc: Exception) -> tuple[int, str, str]:  # noqa: PLR0911 - one mapping per kind
        match exc:
            case Unauthenticated():
                return (401, "unauthenticated", "A valid bearer token is required.")
            case Forbidden(role=role):
                return (403, "forbidden", f"This action requires the {role} role.")
            case NotFound():
                return (404, "not_found", "Not found.")
            case BadRequest(code=code):
                return (400, code, str(exc))
            case runs.AdmissionRefused(code=code, detail=detail):
                return (409, code, detail or code)
            case EditConflict(paths=paths):
                return (
                    409,
                    "edit_conflict",
                    f"Changed since your base revision: {', '.join(paths)}",
                )
            case EditRejected(violations=violations):
                return (422, violations[0].code if violations else "edit_rejected", str(exc))
            case inbox.InboxRejected(code=code):
                return (_INBOX_STATUS.get(code, 409), code, str(exc))
            case SecretFound(rule=rule, where=where):
                return (
                    422,
                    "secret_detected",
                    f"The content at {where} resembles a credential ({rule}).",
                )
            case RequestValidationError():
                return (422, "invalid_request", str(exc.errors()[:3]))
            case _:
                logger().error("unhandled_error", error=type(exc).__name__)
                return (500, "internal_error", "Internal error.")

    for kind in (
        Unauthenticated,
        Forbidden,
        NotFound,
        BadRequest,
        runs.AdmissionRefused,
        EditConflict,
        EditRejected,
        inbox.InboxRejected,
        SecretFound,
        RequestValidationError,
        Exception,
    ):
        app.add_exception_handler(kind, handle)
