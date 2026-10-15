"""Who is calling and what they may touch: the API's context, engagement scope and service wall
(A§19). Route modules share these; row-level security is the second wall behind them."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from fastapi import Request

from agentify.api.errors import BadRequest, Forbidden, NotFound
from agentify.api.stream import StreamSettings
from agentify.identity import OIDCSettings, Principal, TokenVerifier, Unauthenticated, principal_for
from agentify.registry import FlowAgentKind, Registry
from agentify.secretgate import SecretGate
from agentify.store.db import Database, engagement_scope
from agentify.store.workspace import Workspace


@dataclass(frozen=True)
class ApiContext:
    db: Database
    ws: Workspace
    registry: Registry
    verifier: TokenVerifier
    oidc: OIDCSettings
    gate: SecretGate
    stream: StreamSettings = field(default_factory=StreamSettings)


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
