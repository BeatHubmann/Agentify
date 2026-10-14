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


def install(app: FastAPI, gate: SecretGate) -> None:
    def problem(status: int, code: str, detail: str) -> JSONResponse:
        body = {
            "type": "about:blank",
            "status": status,
            "code": code,
            "title": f"error.{code}",
            "detail": gate.redact_text(detail).value,
        }
        return JSONResponse(body, status_code=status, media_type="application/problem+json")

    async def handle(_request: Request, exc: Exception) -> JSONResponse:  # noqa: PLR0911 - one mapping per kind
        match exc:
            case Unauthenticated():
                return problem(401, "unauthenticated", "A valid bearer token is required.")
            case Forbidden(role=role):
                return problem(403, "forbidden", f"This action requires the {role} role.")
            case NotFound():
                return problem(404, "not_found", "Not found.")
            case BadRequest(code=code):
                return problem(400, code, str(exc))
            case runs.AdmissionRefused(code=code, detail=detail):
                return problem(409, code, detail or code)
            case EditConflict(paths=paths):
                return problem(
                    409, "edit_conflict", f"Changed since your base revision: {', '.join(paths)}"
                )
            case EditRejected(violations=violations):
                return problem(422, violations[0].code if violations else "edit_rejected", str(exc))
            case inbox.InboxRejected(code=code):
                return problem(_INBOX_STATUS.get(code, 409), code, str(exc))
            case SecretFound(rule=rule):
                return problem(
                    422, "secret_detected", f"The content resembles a credential ({rule})."
                )
            case RequestValidationError():
                return problem(422, "invalid_request", str(exc.errors()[:3]))
            case _:
                logger().error("unhandled_error", error=type(exc).__name__)
                return problem(500, "internal_error", "Internal error.")

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
