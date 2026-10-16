"""The budget ledger (A§17.3, OP-13, OPS-4): a call reserves its worst case against every scope
before it is sent, settles to the actual cost, and is released if it never completes. Without a
deployment limit nothing may be spent."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store import budget
from agentify.store.db import Database
from agentify.store.workspace import Workspace
from tests.conftest import Infra

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
MODEL = "eu.anthropic.claude-opus-5-5"


async def a_run(ws: Workspace, registry: Registry, process_id: str) -> budget.Scopes:
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=registry.agents["current-state-modeler"],
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    async with ws.db.tx() as conn:
        engagement = await ws.engagement_of(conn, process_id)
    return budget.Scopes(run_id=run_id, process_id=process_id, engagement_id=engagement)


@pytest.fixture
async def admin(infra: Infra) -> AsyncIterator[Database]:
    """Limits are an operator action: the application roles can read them, not set them."""
    db = Database(infra.admin_conninfo)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def deployment(admin: Database) -> str:
    """A deployment of its own per test, so tests never share a limit."""
    name = f"dep-{uuid.uuid4().hex[:8]}"
    await budget.set_limit(admin, "deployment", name, 10_000_000)
    return name


async def test_a_reservation_counts_until_it_settles_to_the_actual_cost(
    workspace: Workspace, registry: Registry, process_id: str, deployment: str
) -> None:
    scopes = await a_run(workspace, registry, process_id)
    held = await budget.reserve(
        workspace.db,
        scopes,
        deployment=deployment,
        model_id=MODEL,
        amount=700_000,
        run_ceiling=2_000_000,
    )
    reserved = await budget.spent(workspace.db, "run", scopes.run_id)
    await budget.settle(workspace.db, held, actual=123_456)
    assert (reserved, await budget.spent(workspace.db, "run", scopes.run_id)) == (700_000, 123_456)


@pytest.mark.parametrize(
    ("limits", "amount", "refused_scope"),
    [
        ({}, 2_000_001, "run"),
        ({"process": 500_000}, 600_000, "process"),
        ({"engagement": 500_000}, 600_000, "engagement"),
        ({"deployment": 500_000}, 600_000, "deployment"),
    ],
    ids=["run-ceiling", "process", "engagement", "deployment"],
)
async def test_a_call_over_any_scope_limit_is_refused_before_anything_is_held(
    *,
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    deployment: str,
    admin: Database,
    limits: dict[str, int],
    amount: int,
    refused_scope: str,
) -> None:
    scopes = await a_run(workspace, registry, process_id)
    ids = {"process": process_id, "engagement": scopes.engagement_id, "deployment": deployment}
    for kind, limit in limits.items():
        await budget.set_limit(admin, kind, ids[kind], limit)  # type: ignore[arg-type]
    with pytest.raises(budget.BudgetExhausted) as refused:
        await budget.reserve(
            workspace.db,
            scopes,
            deployment=deployment,
            model_id=MODEL,
            amount=amount,
            run_ceiling=2_000_000,
        )
    assert (refused.value.scope, await budget.spent(workspace.db, "run", scopes.run_id)) == (
        refused_scope,
        0,
    )


async def test_without_a_deployment_limit_nothing_may_be_spent(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    scopes = await a_run(workspace, registry, process_id)
    with pytest.raises(budget.BudgetExhausted) as refused:
        await budget.reserve(
            workspace.db,
            scopes,
            deployment=f"unconfigured-{uuid.uuid4().hex[:8]}",
            model_id=MODEL,
            amount=1,
            run_ceiling=2_000_000,
        )
    assert (refused.value.scope, refused.value.limit) == ("deployment", 0)


async def test_releasing_a_run_frees_its_open_reservations_and_keeps_settled_ones(
    workspace: Workspace, registry: Registry, process_id: str, deployment: str
) -> None:
    scopes = await a_run(workspace, registry, process_id)
    settled = await budget.reserve(
        workspace.db,
        scopes,
        deployment=deployment,
        model_id=MODEL,
        amount=400_000,
        run_ceiling=2_000_000,
    )
    await budget.settle(workspace.db, settled, actual=100_000)
    await budget.reserve(
        workspace.db,
        scopes,
        deployment=deployment,
        model_id=MODEL,
        amount=900_000,
        run_ceiling=2_000_000,
    )
    async with workspace.db.tx() as conn:
        released = await budget.release_run(conn, scopes.run_id)
    assert (released, await budget.spent(workspace.db, "run", scopes.run_id)) == (1, 100_000)


async def test_concurrent_reservations_never_exceed_a_limit_together(
    infra: Infra, workspace: Workspace, registry: Registry, admin: Database
) -> None:
    """Ten runs in ten processes each try to hold 0.6 USD against a 1.0 USD deployment limit at
    the same moment, each on a connection of its own (a shared pool would queue them): exactly one
    may succeed."""
    deployment = f"dep-{uuid.uuid4().hex[:8]}"
    await budget.set_limit(admin, "deployment", deployment, 1_000_000)
    scopes = []
    for _ in range(10):
        process = f"proc-{uuid.uuid4().hex[:12]}"
        await workspace.create_process(f"eng-{uuid.uuid4().hex[:8]}", process)
        scopes.append(await a_run(workspace, registry, process))

    async def attempt(s: budget.Scopes) -> str:
        own = Database(infra.app_conninfo, max_size=1)
        await own.open()
        try:
            await budget.reserve(
                own,
                s,
                deployment=deployment,
                model_id=MODEL,
                amount=600_000,
                run_ceiling=2_000_000,
            )
        except budget.BudgetExhausted as exc:
            return exc.scope
        finally:
            await own.close()
        return "held"

    outcomes = await asyncio.gather(*(attempt(s) for s in scopes))
    assert (sorted(outcomes), await budget.spent(workspace.db, "deployment", deployment)) == (
        ["deployment"] * 9 + ["held"],
        600_000,
    )
