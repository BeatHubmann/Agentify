"""Integration fixtures: real Postgres and MinIO containers (A§26.1). Nothing here is stubbed."""

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import boto3
import psycopg
import pytest
from testcontainers.community.minio import MinioContainer
from testcontainers.community.postgres import PostgresContainer

from agentify.registry import Registry, load_registry
from agentify.store.blobs import BlobStore
from agentify.store.db import SAFE_LOG_SETTINGS, Database
from agentify.store.migrate import create_application_role, upgrade
from agentify.store.workspace import Workspace
from tests.support.gates import make_gate

APP_ROLE = "agentify_app"  # the worker role: bypasses row-level security by design
API_ROLE = "agentify_api"  # the API role: row-level security applies
API_PASSWORD = "api-password-for-tests"  # throwaway container credential
APP_PASSWORD = "app-password-for-tests"  # throwaway container credential
BUCKET = "artifacts"
REGISTRY_ROOT = Path(__file__).resolve().parents[2] / "registry"
KNOWN_SECRET = "planted-known-credential-value"
TEST_DEPLOYMENT_LIMIT = 1_000_000_000_000  # micro-USD: scripted models cost nothing real
# MinIO no longer publishes images to Docker Hub; Chainguard builds it from source. Pinned by digest.
MINIO_IMAGE = "cgr.dev/chainguard/minio@sha256:2136d593beacf4461187035eca949fe49abd589d5079735025c711ca58fee0c8"
POSTGRES_IMAGE = "postgres:17-alpine"


@dataclass(frozen=True)
class Infra:
    app_conninfo: str
    api_conninfo: str
    admin_conninfo: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    postgres_container_id: str


@pytest.fixture(scope="session")
def infra() -> Iterator[Infra]:
    with (
        PostgresContainer(POSTGRES_IMAGE, driver="psycopg").with_command(
            " ".join(["postgres", *(f"-c {k}={v}" for k, v in SAFE_LOG_SETTINGS.items())])
        ) as pg,
        MinioContainer(MINIO_IMAGE) as minio,
    ):
        admin_url = pg.get_connection_url()
        upgrade(admin_url)
        create_application_role(admin_url, APP_ROLE, APP_PASSWORD, bypass_rls=True)
        create_application_role(admin_url, API_ROLE, API_PASSWORD)
        with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://")) as admin:
            admin.execute(  # the default deployment's budget; without one every call is refused
                "INSERT INTO budget_limit (scope_kind, scope_id, limit_microusd) "
                "VALUES ('deployment', 'local', %s)",
                (TEST_DEPLOYMENT_LIMIT,),
            )
        host, port, db = pg.get_container_host_ip(), pg.get_exposed_port(5432), pg.dbname
        config = minio.get_config()
        endpoint = f"http://{config['endpoint']}"
        boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=config["access_key"],
            aws_secret_access_key=config["secret_key"],
            region_name="eu-central-1",
        ).create_bucket(Bucket=BUCKET)
        yield Infra(
            app_conninfo=f"host={host} port={port} dbname={db} user={APP_ROLE} password={APP_PASSWORD}",
            api_conninfo=f"host={host} port={port} dbname={db} user={API_ROLE} password={API_PASSWORD}",
            admin_conninfo=admin_url.replace("postgresql+psycopg://", "postgresql://"),
            s3_endpoint=endpoint,
            s3_access_key=config["access_key"],
            s3_secret_key=config["secret_key"],
            postgres_container_id=pg.get_wrapped_container().id,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def registry() -> Registry:
    return load_registry(REGISTRY_ROOT)


def s3_client(infra: Infra):  # type: ignore[no-untyped-def]
    return boto3.client(
        "s3",
        endpoint_url=infra.s3_endpoint,
        aws_access_key_id=infra.s3_access_key,
        aws_secret_access_key=infra.s3_secret_key,
        region_name="eu-central-1",
    )


@pytest.fixture
async def workspace(infra: Infra) -> AsyncIterator[Workspace]:
    db = Database(infra.app_conninfo)
    await db.open()
    try:
        yield Workspace(db, BlobStore(s3_client(infra), BUCKET), make_gate(KNOWN_SECRET))
    finally:
        await db.close()


@pytest.fixture
async def process_id(workspace: Workspace) -> str:
    pid = f"proc-{uuid.uuid4().hex[:12]}"
    await workspace.create_process(f"eng-{uuid.uuid4().hex[:8]}", pid)
    return pid
