"""Database logs never record statement parameters or the values a constraint error quotes
(INV-8.DBLOG, A§5.2, A§18.3)."""

import time
import uuid

import docker
import psycopg
import pytest

from agentify.store.db import Database, UnsafeDatabaseLogging, check_log_settings
from agentify.store.workspace import Workspace
from tests.conftest import Infra

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def server_log(infra: Infra) -> str:
    return docker.from_env().containers.get(infra.postgres_container_id).logs().decode()


async def insert_twice(conn: psycopg.AsyncConnection[object], engagement_id: str) -> None:
    """The second insert fails on the primary key; the error quotes the key in its DETAIL."""
    insert = "INSERT INTO engagement (id, name) VALUES (%s, 'log drill')"
    await conn.execute(insert, (engagement_id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        await conn.execute(insert, (engagement_id,))


async def test_database_logs_never_record_parameters_or_constraint_values(
    infra: Infra, workspace: Workspace
) -> None:
    canary = f"db-log-canary-{uuid.uuid4().hex}"
    control = f"db-log-control-{uuid.uuid4().hex}"
    async with workspace.db.tx() as conn:
        await conn.execute("SELECT %s::text", (canary,))
    async with await psycopg.AsyncConnection.connect(infra.app_conninfo) as app:
        await insert_twice(app, canary)
    # Evaluator validity: at a louder verbosity, the same error does quote its value in the log.
    # (A bare `= default` would reset to the server's configured value, terse.)
    async with await psycopg.AsyncConnection.connect(infra.admin_conninfo) as admin:
        await admin.execute("SET log_error_verbosity = verbose")
        await insert_twice(admin, control)
    deadline = time.monotonic() + 10
    while control not in server_log(infra) and time.monotonic() < deadline:
        time.sleep(0.1)  # noqa: ASYNC251 - waits for the container's log stream, no event exists
    logs = server_log(infra)
    await check_log_settings(workspace.db)
    assert (control in logs, canary in logs) == (True, False)


async def test_startup_refuses_a_server_that_would_log_parameters(infra: Infra) -> None:
    unsafe = Database(f"{infra.admin_conninfo}?options=-c%20log_parameter_max_length_on_error%3D-1")
    await unsafe.open()
    try:
        with pytest.raises(UnsafeDatabaseLogging) as refused:
            await check_log_settings(unsafe)
    finally:
        await unsafe.close()
    assert refused.value.settings == {"log_parameter_max_length_on_error": "-1"}
