"""Security events and the secret-gate allow-list, for deployment admins (A§18.3). Admins see rules,
fingerprints and locations, never content; an allow-list entry is attributed and append-only."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from agentify.api.access import Access, ApiContext, scoped
from agentify.api.errors import Forbidden, NotFound, Unprocessable
from agentify.identity import Principal
from agentify.secretgate import KNOWN_VALUE, Hit, SecretFound
from agentify.store import security_events


class AllowlistEntryIn(BaseModel):
    rule: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["allow", "revoke"]
    justification: str = Field(min_length=1, max_length=2000)


def security_routes(app: FastAPI, ctx: ApiContext, access: Access) -> None:
    Caller = Annotated[Principal, Depends(access.principal)]  # noqa: N806 - a type alias

    async def require_admin(who: Principal, engagement_id: str) -> None:
        """Admin only; 404 for an unknown engagement."""
        if not who.is_admin:
            raise Forbidden("admin")
        with scoped(who, engagement_id):
            async with ctx.db.tx() as conn:
                known = await (
                    await conn.execute("SELECT 1 FROM engagement WHERE id = %s", (engagement_id,))
                ).fetchone()
        if known is None:
            raise NotFound(engagement_id)

    async def rows(who: Principal, engagement_id: str, query: str) -> list[dict[str, Any]]:
        await require_admin(who, engagement_id)
        with scoped(who, engagement_id):
            async with ctx.db.tx() as conn:
                found = await (await conn.execute(query, (engagement_id,))).fetchall()
        return [
            {k: v.isoformat() if isinstance(v, datetime) else v for k, v in r.items()}
            for r in found
        ]

    @app.get("/engagements/{engagement_id}/security-events")
    async def list_security_events(engagement_id: str, who: Caller) -> list[dict[str, Any]]:
        return await rows(
            who,
            engagement_id,
            """SELECT seq, at, exit, rule, fingerprint, location, process_id, run_id
               FROM security_event WHERE engagement_id = %s ORDER BY seq""",
        )

    @app.get("/engagements/{engagement_id}/secret-allowlist")
    async def list_allowlist(engagement_id: str, who: Caller) -> list[dict[str, Any]]:
        return await rows(
            who,
            engagement_id,
            """SELECT seq, at, rule, fingerprint, action, justification, admin_id
               FROM secret_allowlist_entry WHERE engagement_id = %s ORDER BY seq""",
        )

    @app.post("/engagements/{engagement_id}/secret-allowlist", status_code=201)
    async def add_allowlist_entry(
        engagement_id: str, body: AllowlistEntryIn, who: Caller
    ) -> dict[str, str]:
        await require_admin(who, engagement_id)
        if body.rule == KNOWN_VALUE or ctx.gate.is_known_fingerprint(body.fingerprint):
            raise Unprocessable(
                "known_value_not_allowable", "Known credential values can never be allow-listed."
            )
        try:
            ctx.gate.check_bytes(body.justification.encode("utf-8"), where="justification")
        except SecretFound as exc:
            with scoped(who, engagement_id):
                await security_events.record(
                    ctx.db,
                    ctx.gate,
                    exit="decision",
                    hits=[exc.hit],
                    location="secret_allowlist.justification",
                    engagement_id=engagement_id,
                )
            raise
        with scoped(who, engagement_id):
            await security_events.add_allowlist_entry(
                ctx.db,
                engagement_id=engagement_id,
                hit=Hit(body.rule, body.fingerprint),
                action=body.action,
                justification=body.justification,
                admin_id=who.user_id,
            )
        return {"engagement_id": engagement_id, "rule": body.rule, "action": body.action}
