"""Identity (A§19, OD-8): OIDC bearer tokens from the firm's identity provider, verified against the
issuer's JWKS; users created on first sign-in; roles held per engagement by Agentify.

The API never trusts a client-supplied user id: every request's identity comes from a verified token."""

import uuid
from dataclasses import dataclass, field

import anyio
import jwt

from agentify.store.db import Database

ROLES = frozenset({"consultant", "waiver_approver", "reviewer"})


class Unauthenticated(Exception):
    pass


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...] = ("RS256", "ES256")
    leeway_seconds: int = 30
    admin_subjects: frozenset[str] = frozenset()
    """Subjects that become deployment admins on first sign-in (operator configuration)."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    is_admin: bool
    roles: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def engagements(self) -> tuple[str, ...]:
        return tuple(sorted(self.roles))

    def has(self, engagement_id: str, role: str) -> bool:
        return role in self.roles.get(engagement_id, frozenset())


class TokenVerifier:
    def __init__(self, settings: OIDCSettings) -> None:
        self.settings = settings
        self._jwks = jwt.PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=300)

    def _verify(self, token: str) -> dict[str, object]:
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims: dict[str, object] = jwt.decode(
                token,
                key.key,
                algorithms=list(self.settings.algorithms),
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=self.settings.leeway_seconds,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise Unauthenticated(type(exc).__name__) from exc
        return claims

    async def verify(self, token: str) -> dict[str, object]:
        return await anyio.to_thread.run_sync(self._verify, token)


async def principal_for(
    db: Database, settings: OIDCSettings, claims: dict[str, object]
) -> Principal:
    issuer, subject = str(claims["iss"]), str(claims["sub"])
    name = str(claims.get("name") or claims.get("email") or "")
    async with db.tx() as conn:
        await conn.execute(
            """INSERT INTO app_user (id, issuer, subject, display_name, is_admin)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (issuer, subject) DO NOTHING""",
            (f"usr_{uuid.uuid4().hex}", issuer, subject, name, subject in settings.admin_subjects),
        )
        user = await (
            await conn.execute(
                "SELECT id, is_admin FROM app_user WHERE issuer = %s AND subject = %s",
                (issuer, subject),
            )
        ).fetchone()
        if user is None:
            raise Unauthenticated("user record vanished during sign-in")
        rows = await (
            await conn.execute(
                "SELECT engagement_id, role FROM membership WHERE user_id = %s", (user["id"],)
            )
        ).fetchall()
    roles: dict[str, set[str]] = {}
    for row in rows:
        roles.setdefault(str(row["engagement_id"]), set()).add(str(row["role"]))
    return Principal(
        str(user["id"]), bool(user["is_admin"]), {k: frozenset(v) for k, v in roles.items()}
    )


async def grant(
    db: Database, *, engagement_id: str, user_id: str, role: str, granted_by: str
) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    async with db.tx() as conn:
        await conn.execute(
            """INSERT INTO membership (engagement_id, user_id, role, granted_by) VALUES (%s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (engagement_id, user_id, role, granted_by),
        )
