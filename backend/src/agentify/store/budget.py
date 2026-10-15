"""The budget ledger (A§17.3): reserve a call's worst case before it is sent, settle to the actual
cost after it, release what never completed. Amounts are integer micro-USD.

Every reservation takes one transaction-scoped advisory lock, so concurrent calls cannot each pass
a limit that they exceed together. That serializes reservations across the deployment, which is
cheap at Agentify's scale: one short transaction per model call."""

import uuid
from dataclasses import dataclass
from typing import Literal

from agentify.store.db import Database

ScopeKind = Literal["run", "process", "engagement", "deployment"]
LimitKind = Literal["process", "engagement", "deployment"]
_COLUMN: dict[ScopeKind, str] = {
    "run": "run_id",
    "process": "process_id",
    "engagement": "engagement_id",
    "deployment": "deployment",
}
_USED = """COALESCE(SUM(CASE state WHEN 'open' THEN reserved_microusd
                                 WHEN 'settled' THEN settled_microusd ELSE 0 END), 0)"""


@dataclass(frozen=True)
class Scopes:
    run_id: str
    process_id: str
    engagement_id: str


class BudgetExhausted(Exception):
    def __init__(self, scope: ScopeKind, *, used: int, amount: int, limit: int) -> None:
        super().__init__(
            f"{scope} budget: {used} + {amount} would exceed {limit} micro-USD"
            if limit
            else f"no {scope} budget configured"
        )
        self.scope, self.used, self.amount, self.limit = scope, used, amount, limit


async def set_limit(db: Database, kind: LimitKind, scope_id: str, limit: int) -> None:
    """An operator action: the application roles may read limits, never change them."""
    async with db.tx() as conn:
        await conn.execute(
            """INSERT INTO budget_limit (scope_kind, scope_id, limit_microusd) VALUES (%s, %s, %s)
               ON CONFLICT (scope_kind, scope_id, period)
               DO UPDATE SET limit_microusd = EXCLUDED.limit_microusd, set_at = now()""",
            (kind, scope_id, limit),
        )


async def spent(db: Database, kind: ScopeKind, scope_id: str) -> int:
    """Settled cost plus open reservations in a scope."""
    async with db.tx() as conn:
        row = await (
            await conn.execute(
                f"SELECT {_USED} AS used FROM budget_reservation WHERE {_COLUMN[kind]} = %s",  # noqa: S608 - column from a fixed map
                (scope_id,),
            )
        ).fetchone()
    return int(str(row["used"])) if row else 0


async def reserve(
    db: Database, scopes: Scopes, *, deployment: str, model_id: str, amount: int, run_ceiling: int
) -> str:
    """Hold `amount` against every scope, or raise `BudgetExhausted` naming the first scope it
    would exceed. A deployment without a limit has a limit of zero (fail closed)."""
    keys: dict[ScopeKind, str] = {
        "run": scopes.run_id,
        "process": scopes.process_id,
        "engagement": scopes.engagement_id,
        "deployment": deployment,
    }
    async with db.tx() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('budget-ledger'))")
        limits = {
            str(r["scope_kind"]): int(str(r["limit_microusd"]))
            for r in await (
                await conn.execute(
                    """SELECT scope_kind, limit_microusd FROM budget_limit
                       WHERE (scope_kind, scope_id) IN (('process', %s), ('engagement', %s),
                                                        ('deployment', %s))""",
                    (scopes.process_id, scopes.engagement_id, deployment),
                )
            ).fetchall()
        }
        limits["run"] = run_ceiling
        limits.setdefault("deployment", 0)
        for kind in ("run", "process", "engagement", "deployment"):
            if kind not in limits:
                continue
            row = await (
                await conn.execute(
                    f"SELECT {_USED} AS used FROM budget_reservation WHERE {_COLUMN[kind]} = %s",  # noqa: S608 - column from a fixed map
                    (keys[kind],),
                )
            ).fetchone()
            used = int(str(row["used"])) if row else 0
            if used + amount > limits[kind]:
                raise BudgetExhausted(kind, used=used, amount=amount, limit=limits[kind])
        reservation = f"bud_{uuid.uuid4().hex}"
        await conn.execute(
            """INSERT INTO budget_reservation (id, run_id, process_id, engagement_id, deployment,
                                               model_id, reserved_microusd)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                reservation,
                scopes.run_id,
                scopes.process_id,
                scopes.engagement_id,
                deployment,
                model_id,
                amount,
            ),
        )
    return reservation


async def settle(db: Database, reservation_id: str, *, actual: int) -> None:
    async with db.tx() as conn:
        await conn.execute(
            """UPDATE budget_reservation SET state = 'settled', settled_microusd = %s,
                 closed_at = now() WHERE id = %s AND state = 'open'""",
            (actual, reservation_id),
        )


async def release(db: Database, reservation_id: str) -> None:
    """A call the provider refused before running it: nothing is held or spent."""
    async with db.tx() as conn:
        await conn.execute(
            """UPDATE budget_reservation SET state = 'released', closed_at = now()
               WHERE id = %s AND state = 'open'""",
            (reservation_id,),
        )


async def release_run(db: Database, run_id: str) -> int:
    """Release a finished or lost run's open reservations (A§17.3). Returns how many."""
    async with db.tx() as conn:
        released = await conn.execute(
            """UPDATE budget_reservation SET state = 'released', closed_at = now()
               WHERE run_id = %s AND state = 'open'""",
            (run_id,),
        )
    return released.rowcount
