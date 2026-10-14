"""The Agentify HTTP API (A§2 gateway role, A§6.12, A§19).

Every request is authenticated from a verified OIDC token. Every handler runs inside the caller's
engagement scope, so row-level security is the second wall behind the membership checks here.
Unknown and not-visible resources both answer 404."""

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agentify import inbox, intake
from agentify.api import errors
from agentify.api.errors import BadRequest, Forbidden, NotFound
from agentify.editing import EditBatch, PatchRequest, apply_edit_batch
from agentify.harness import runs
from agentify.identity import (
    OIDCSettings,
    Principal,
    TokenVerifier,
    Unauthenticated,
    grant,
    principal_for,
)
from agentify.observability import logger
from agentify.registry import FlowAgentKind, Registry
from agentify.secretgate import SecretGate
from agentify.store.db import Database, engagement_scope
from agentify.store.workspace import Workspace

MAX_INPUT_BYTES = 1_000_000


@dataclass(frozen=True)
class ApiContext:
    db: Database
    ws: Workspace
    registry: Registry
    verifier: TokenVerifier
    oidc: OIDCSettings
    gate: SecretGate


class EngagementIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberIn(BaseModel):
    user_id: str
    role: Literal["consultant", "waiver_approver", "reviewer"]


class ProcessIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class InputIn(BaseModel):
    name: str
    text: str


class RunIn(BaseModel):
    kind: str
    mode: Literal["from_scratch", "incremental"]
    instructions: str = ""
    intent_confirmation_id: str | None = None


class PatchIn(BaseModel):
    op: Literal["add_node", "modify_node", "remove_node", "add_edge", "modify_edge", "remove_edge"]
    target: str | None = None
    payload: dict[str, Any] = {}


class EditsIn(BaseModel):
    kind: str
    base_revision_id: str | None
    patches: list[PatchIn] = []
    lock: list[str] = []
    unlock: list[str] = []


class AnswerIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ConfirmationIn(BaseModel):
    run_kind: str


@contextmanager
def scoped(principal: Principal, *extra: str) -> Iterator[None]:
    with engagement_scope(tuple(sorted({*principal.engagements, *extra}))):
        yield


class Access:
    """Authentication and the service wall: who is calling, and may they touch this process."""

    def __init__(self, ctx: ApiContext) -> None:
        self.ctx = ctx

    async def principal(self, request: Request) -> Principal:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise Unauthenticated("missing bearer token")
        claims = await self.ctx.verifier.verify(header[7:].strip())
        return await principal_for(self.ctx.db, self.ctx.oidc, claims)

    async def require(self, who: Principal, process_id: str, role: str | None = None) -> str:
        """The process's engagement, if the caller is a member (and holds `role`); 404 otherwise."""
        with scoped(who):
            async with self.ctx.db.tx() as conn:
                row = await (
                    await conn.execute(
                        "SELECT engagement_id FROM process WHERE id = %s", (process_id,)
                    )
                ).fetchone()
        engagement = None if row is None else str(row["engagement_id"])
        if engagement is None or engagement not in who.roles:  # independent of row-level security
            raise NotFound(process_id)
        if role is not None and not who.has(engagement, role):
            raise Forbidden(role)
        return engagement

    def flow_kind(self, kind_id: str) -> FlowAgentKind:
        kind = self.ctx.registry.agents.get(kind_id)
        if not isinstance(kind, FlowAgentKind):
            raise BadRequest("unknown_flow_kind", f"{kind_id} is not a flow-authoring kind")
        return kind


def _admin_routes(app: FastAPI, ctx: ApiContext, access: Access) -> None:
    Caller = Annotated[Principal, Depends(access.principal)]  # noqa: N806 - a type alias

    @app.get("/health")
    async def health() -> dict[str, str]:
        async with ctx.db.tx() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok", "db": "ok"}

    @app.get("/me")
    async def me(who: Caller) -> dict[str, Any]:
        return {
            "user_id": who.user_id,
            "is_admin": who.is_admin,
            "roles": {e: sorted(r) for e, r in who.roles.items()},
        }

    @app.post("/engagements", status_code=201)
    async def create_engagement(body: EngagementIn, who: Caller) -> dict[str, str]:
        if not who.is_admin:
            raise Forbidden("admin")
        engagement_id = f"eng_{uuid.uuid4().hex[:16]}"
        with scoped(who, engagement_id):
            async with ctx.db.tx() as conn:
                await conn.execute(
                    "INSERT INTO engagement (id, name) VALUES (%s, %s)", (engagement_id, body.name)
                )
        return {"engagement_id": engagement_id}

    @app.post("/engagements/{engagement_id}/members", status_code=201)
    async def add_member(engagement_id: str, body: MemberIn, who: Caller) -> dict[str, str]:
        if not who.is_admin:
            raise Forbidden("admin")
        await grant(
            ctx.db,
            engagement_id=engagement_id,
            user_id=body.user_id,
            role=body.role,
            granted_by=who.user_id,
        )
        return {"engagement_id": engagement_id, "user_id": body.user_id, "role": body.role}

    @app.post("/engagements/{engagement_id}/processes", status_code=201)
    async def create_process(engagement_id: str, body: ProcessIn, who: Caller) -> dict[str, str]:
        if not who.has(engagement_id, "consultant"):
            raise (
                NotFound(engagement_id)
                if engagement_id not in who.roles
                else Forbidden("consultant")
            )
        process_id = f"prc_{uuid.uuid4().hex[:16]}"
        with scoped(who):
            await ctx.ws.create_process(engagement_id, process_id)
        return {"process_id": process_id}


def _process_read_routes(app: FastAPI, ctx: ApiContext, access: Access) -> None:
    Caller = Annotated[Principal, Depends(access.principal)]  # noqa: N806 - a type alias

    @app.get("/processes/{process_id}")
    async def get_process(process_id: str, who: Caller) -> dict[str, Any]:
        engagement = await access.require(who, process_id)
        with scoped(who):
            async with ctx.db.tx() as conn:
                head = await ctx.ws.head_revision(conn, process_id)
                paths = sorted(await ctx.ws.path_map(conn, head))
        return {
            "process_id": process_id,
            "engagement_id": engagement,
            "head_revision_id": head,
            "paths": paths,
        }

    @app.get("/processes/{process_id}/revisions")
    async def revisions(process_id: str, who: Caller) -> list[dict[str, Any]]:
        await access.require(who, process_id)
        with scoped(who):
            async with ctx.db.tx() as conn:
                rows = await (
                    await conn.execute(
                        """SELECT id, parent_id, run_id, author_kind, author_id, message, prompt_version, created_at
                       FROM revision WHERE process_id = %s ORDER BY created_at""",
                        (process_id,),
                    )
                ).fetchall()
        return [{**dict(r), "created_at": str(r["created_at"])} for r in rows]

    @app.get("/processes/{process_id}/artifact")
    async def artifact(
        process_id: str, path: str, who: Caller, revision: str | None = None
    ) -> Response:
        await access.require(who, process_id)
        with scoped(who):
            data = await ctx.ws.read(process_id, path, revision)
        if data is None:
            raise NotFound(path)
        media = "application/json" if path.endswith(".json") else "text/plain; charset=utf-8"
        return Response(content=data, media_type=media)


def _process_write_routes(app: FastAPI, ctx: ApiContext, access: Access) -> None:
    Caller = Annotated[Principal, Depends(access.principal)]  # noqa: N806 - a type alias

    @app.post("/processes/{process_id}/inputs", status_code=201)
    async def add_input(process_id: str, body: InputIn, who: Caller) -> dict[str, str]:
        await access.require(who, process_id, "consultant")
        if len(body.text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise BadRequest("input_too_large", f"inputs are limited to {MAX_INPUT_BYTES} bytes")
        try:
            with scoped(who):
                revision = await intake.add_input(
                    ctx.ws,
                    process_id=process_id,
                    user_id=who.user_id,
                    name=body.name,
                    text=body.text,
                )
        except ValueError as exc:
            raise BadRequest("invalid_input", str(exc)) from exc
        return {"revision_id": revision, "path": f"inputs/{body.name}"}

    @app.post("/processes/{process_id}/edits", status_code=201)
    async def edits(process_id: str, body: EditsIn, who: Caller) -> dict[str, str]:
        await access.require(who, process_id, "consultant")
        batch = EditBatch(
            patches=tuple(PatchRequest(p.op, p.target, p.payload) for p in body.patches),
            lock=tuple(body.lock),
            unlock=tuple(body.unlock),
        )
        with scoped(who):
            revision = await apply_edit_batch(
                ctx.ws,
                process_id=process_id,
                kind=access.flow_kind(body.kind),
                base_revision_id=body.base_revision_id,
                user_id=who.user_id,
                batch=batch,
            )
        return {"revision_id": revision}

    @app.post("/processes/{process_id}/runs", status_code=201)
    async def admit_run(process_id: str, body: RunIn, who: Caller) -> dict[str, str]:
        await access.require(who, process_id, "consultant")
        kind = ctx.registry.agents.get(body.kind)
        if kind is None:
            raise BadRequest("unknown_kind", body.kind)
        with scoped(who):
            run_id = await runs.admit(
                ctx.ws,
                process_id=process_id,
                kind=kind,
                mode=body.mode,
                initiated_by=who.user_id,
                entry_point="api",
                instructions=body.instructions,
                intent_confirmation_id=body.intent_confirmation_id,
            )
        return {"run_id": run_id}


def _run_and_inbox_routes(app: FastAPI, ctx: ApiContext, access: Access) -> None:
    Caller = Annotated[Principal, Depends(access.principal)]  # noqa: N806 - a type alias

    async def run_process(who: Principal, run_id: str) -> str:
        with scoped(who):
            async with ctx.db.tx() as conn:
                row = await (
                    await conn.execute("SELECT process_id FROM run WHERE id = %s", (run_id,))
                ).fetchone()
        if row is None:
            raise NotFound(run_id)
        return str(row["process_id"])

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str, who: Caller) -> dict[str, Any]:
        await access.require(who, await run_process(who, run_id))
        with scoped(who):
            state = await runs.run_state(ctx.ws, run_id)
        keys = (
            "id",
            "process_id",
            "kind",
            "mode",
            "state",
            "outcome",
            "outcome_detail_scrubbed",
            "result_text_scrubbed",
            "initiated_by",
            "entry_point",
            "parent_run_id",
        )
        return {k: state.get(k) for k in keys}

    @app.post("/runs/{run_id}/cancel", status_code=202)
    async def cancel_run(run_id: str, who: Caller) -> dict[str, str]:
        await access.require(who, await run_process(who, run_id), "consultant")
        with scoped(who):
            await runs.request_cancel(ctx.ws, run_id)
        return {"run_id": run_id, "status": "cancel_requested"}

    @app.get("/processes/{process_id}/inbox")
    async def list_inbox(
        process_id: str, who: Caller, status: str | None = None
    ) -> list[dict[str, Any]]:
        await access.require(who, process_id)
        with scoped(who):
            return [asdict(item) for item in await inbox.items(ctx.ws, process_id, status=status)]

    @app.post("/processes/{process_id}/inbox/{item_id}/answer", status_code=201)
    async def answer(process_id: str, item_id: str, body: AnswerIn, who: Caller) -> dict[str, str]:
        await access.require(who, process_id, "consultant")
        with scoped(who):
            answer_id = await inbox.answer(
                ctx.ws,
                ctx.registry,
                process_id=process_id,
                item_id=item_id,
                author=inbox.UserAuthor(who.user_id),
                text=body.text,
            )
        return {"answer_id": answer_id}

    @app.post("/processes/{process_id}/confirmations", status_code=201)
    async def confirm(process_id: str, body: ConfirmationIn, who: Caller) -> dict[str, str]:
        await access.require(who, process_id, "consultant")
        if body.run_kind not in ctx.registry.agents:
            raise BadRequest("unknown_kind", body.run_kind)
        with scoped(who):
            confirmation = await runs.confirm_intent(
                ctx.ws, process_id=process_id, run_kind=body.run_kind, confirmed_by=who.user_id
            )
        return {"intent_confirmation_id": confirmation}


def create_app(ctx: ApiContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await ctx.db.open()
        yield
        await ctx.db.close()

    app = FastAPI(title="Agentify", lifespan=lifespan)
    app.state.ctx = ctx
    errors.install(app, ctx.gate)

    @app.middleware("http")
    async def access_log(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        logger().info(
            "request", method=request.method, path=request.url.path, status=response.status_code
        )
        return response

    access = Access(ctx)
    _admin_routes(app, ctx, access)
    _process_read_routes(app, ctx, access)
    _process_write_routes(app, ctx, access)
    _run_and_inbox_routes(app, ctx, access)
    return app
