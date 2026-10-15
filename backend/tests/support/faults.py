"""Fault injection for transient infrastructure errors, in the one form the suite rules allow."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from psycopg import AsyncConnection

from agentify.store.db import Database


class FlakyDatabase(Database):
    """Fault injection: after `skip` more transactions, the next `failures` raise a connection
    error, as a database failover or a pool timeout would."""

    def __init__(self, conninfo: str) -> None:
        super().__init__(conninfo)
        self.skip = 0
        self.failures = 0

    def fail(self, failures: int, *, skip: int = 0) -> None:
        self.failures, self.skip = failures, skip

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncConnection[dict[str, object]]]:
        if self.skip > 0:
            self.skip -= 1
        elif self.failures > 0:
            self.failures -= 1
            raise psycopg.OperationalError("connection lost (injected)")
        async with super().tx() as connection:
            yield connection
