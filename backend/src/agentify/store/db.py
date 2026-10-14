"""Postgres access. Short transactions only: no connection is held across model calls (§8.6)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


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
            yield connection  # type: ignore[misc]
