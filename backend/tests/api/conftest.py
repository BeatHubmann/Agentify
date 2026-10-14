"""API fixtures: a local OIDC issuer with a real RSA key and a JWKS endpoint, and the real app."""

import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from agentify.api.app import ApiContext, create_app
from agentify.identity import OIDCSettings, TokenVerifier
from agentify.registry import Registry
from agentify.secretgate import SecretGate
from agentify.store.blobs import BlobStore
from agentify.store.db import Database
from agentify.store.workspace import Workspace
from tests.conftest import BUCKET, KNOWN_SECRET, Infra, s3_client

ISSUER = "https://idp.test.invalid/"
AUDIENCE = "agentify-api"
ADMIN_SUBJECT = "admin-sub"


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks_url(signing_key: rsa.RSAPrivateKey) -> Iterator[str]:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    body = json.dumps(
        {"keys": [{**public, "kid": "test-key", "use": "sig", "alg": "RS256"}]}
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/jwks"
    server.shutdown()
    server.server_close()


TokenFactory = Callable[..., str]


@pytest.fixture
def token(signing_key: rsa.RSAPrivateKey) -> TokenFactory:
    def make(
        subject: str,
        *,
        audience: str = AUDIENCE,
        issuer: str = ISSUER,
        expires_in: int = 300,
        key: rsa.RSAPrivateKey | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": audience,
            "sub": subject,
            "iat": now,
            "exp": now + expires_in,
            "name": subject,
        }
        return jwt.encode(
            claims, key or signing_key, algorithm="RS256", headers={"kid": "test-key"}
        )

    return make


@pytest.fixture
async def api(infra: Infra, registry: Registry, jwks_url: str) -> AsyncIterator[httpx.AsyncClient]:
    db = Database(infra.api_conninfo)
    gate = SecretGate([KNOWN_SECRET])
    oidc = OIDCSettings(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=jwks_url,
        admin_subjects=frozenset({ADMIN_SUBJECT}),
    )
    ctx = ApiContext(
        db=db,
        ws=Workspace(db, BlobStore(s3_client(infra), BUCKET), gate),
        registry=registry,
        verifier=TokenVerifier(oidc),
        oidc=oidc,
        gate=gate,
    )
    app = create_app(ctx)
    await db.open()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://api.test",
        ) as client:
            yield client
    finally:
        await db.close()


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def sign_in(
    api: httpx.AsyncClient, token: TokenFactory, subject: str
) -> tuple[str, dict[str, str]]:
    headers = bearer(token(subject))
    response = await api.get("/me", headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["user_id"]), headers


async def engagement_with(
    api: httpx.AsyncClient, token: TokenFactory, members: dict[str, str]
) -> dict[str, Any]:
    """An engagement with the given subject -> role memberships, created by the admin."""
    _admin_id, admin = await sign_in(api, token, ADMIN_SUBJECT)
    engagement = (
        await api.post("/engagements", json={"name": "Test engagement"}, headers=admin)
    ).json()
    users: dict[str, tuple[str, dict[str, str]]] = {}
    for subject, role in members.items():
        users[subject] = await sign_in(api, token, subject)
        granted = await api.post(
            f"/engagements/{engagement['engagement_id']}/members",
            json={"user_id": users[subject][0], "role": role},
            headers=admin,
        )
        assert granted.status_code == 201, granted.text
    return {"engagement_id": engagement["engagement_id"], "users": users, "admin": admin}


def subject() -> str:
    return f"user-{uuid.uuid4().hex[:8]}"
