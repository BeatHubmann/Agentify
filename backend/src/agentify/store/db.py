"""Postgres access. Short transactions only: no connection is held across model calls (§8.6)."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_ENGAGEMENT_SCOPE: ContextVar[tuple[str, ...] | None] = ContextVar("engagement_scope", default=None)


@contextmanager
def engagement_scope(engagement_ids: tuple[str, ...]) -> Iterator[None]:
    """Every transaction opened in this context sees only these engagements (row-level security).

    Used by the API per request. Without a scope, the API role sees nothing (fail closed)."""
    if any("," in e for e in engagement_ids):
        raise ValueError("engagement ids cannot contain commas")
    token = _ENGAGEMENT_SCOPE.set(engagement_ids)
    try:
        yield
    finally:
        _ENGAGEMENT_SCOPE.reset(token)


class Database:
    def __init__(self, conninfo: str, *, max_size: int = 10) -> None:
        self._pool = AsyncConnectionPool(
            conninfo, min_size=1, max_size=max_size, open=False, kwargs={"row_factory": dict_row}
        )

    async def open(self) -> None:
        await self._pool.open(wait=True)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncConnection[dict[str, object]]]:
        async with self._pool.connection() as connection, connection.transaction():
            scope = _ENGAGEMENT_SCOPE.get()
            if scope is not None:
                await connection.execute(
                    "SELECT set_config('agentify.engagement_ids', %s, true)", (",".join(scope),)
                )
            yield connection  # type: ignore[misc]


SAFE_LOG_SETTINGS = {
    "log_statement": "none",
    "log_parameter_max_length": "0",
    "log_parameter_max_length_on_error": "0",
    # A constraint violation's DETAIL line quotes the offending values; terse drops it.
    "log_error_verbosity": "terse",
}


class UnsafeDatabaseLogging(Exception):
    """The server would log statement parameters or quoted values (A§5.2, A§18.3)."""

    def __init__(self, settings: dict[str, str]) -> None:
        super().__init__(f"unsafe database log settings: {settings}")
        self.settings = settings


async def check_log_settings(db: Database) -> None:
    """Read the log settings back at startup; refuse to run on a server that could log secrets."""
    async with db.tx() as conn:
        rows = await (
            await conn.execute(
                "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
                (list(SAFE_LOG_SETTINGS),),
            )
        ).fetchall()
    found = {str(r["name"]): str(r["setting"]) for r in rows}
    wrong = {k: found.get(k, "missing") for k, v in SAFE_LOG_SETTINGS.items() if found.get(k) != v}
    if wrong:
        raise UnsafeDatabaseLogging(wrong)
