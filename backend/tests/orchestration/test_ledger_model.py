"""`LedgerModel` in a real run (A§17.3, OP-13, OPS-4): every call is reserved before it is sent and
settled to the reported usage; a call over any limit is refused before it reaches the model; a
failed call counts at its worst case; finished and lost runs leave no reservation open."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.usage import RequestUsage

from agentify.agents.flow_tools import build_modeler
from agentify.harness import execute, runs, sweeper
from agentify.registry import AgentKind, Registry
from agentify.store import budget
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import history, modeler
from tests.support.scripted import Script, ScriptedSource, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
OPUS_INPUT, OPUS_OUTPUT = Decimal("4.40"), Decimal("22.00")  # USD per million tokens (policy)


async def reservations(ws: Workspace, run_id: str) -> list[dict[str, Any]]:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT state, reserved_microusd, settled_microusd FROM budget_reservation
                   WHERE run_id = %s ORDER BY created_at""",
                (run_id,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def run(
    ws: Workspace,
    kind: AgentKind,
    process_id: str,
    script: Script,
    config: execute.HarnessConfig | None = None,
) -> tuple[str, str]:
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    claimed = await runs.claim(ws, queue=kind.id, owner="w-ledger", lease_seconds=30, run_id=run_id)
    assert claimed is not None
    final = await execute.execute(
        ws,
        claimed,
        kind,
        agent=build_modeler("Model the process."),
        source=ScriptedSource(script),
        prompt="Model the process.",
        config=config,
    )
    return run_id, final


def with_ceiling(kind: AgentKind, usd: str) -> AgentKind:
    return kind.model_copy(
        update={"budgets": kind.budgets.model_copy(update={"money_usd": Decimal(usd)})}
    )


async def test_every_call_is_reserved_first_and_settled_to_the_usage_it_reports(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    usages = [(1_000 * (i + 1), 100 * (i + 1)) for i in range(len(golden_calls()))]
    steps = [
        Step(calls=s.calls, text=s.text, usage=RequestUsage(input_tokens=i, output_tokens=o))
        for s, (i, o) in zip(golden_calls(), usages, strict=True)
    ]
    run_id, final = await run(workspace, modeler(registry), process_id, Script(steps))
    rows = await reservations(workspace, run_id)
    expected = [int(i * OPUS_INPUT + o * OPUS_OUTPUT) for i, o in usages]  # exact: whole micro-USD
    assert (
        final,
        [r["state"] for r in rows],
        [r["settled_microusd"] for r in rows],
        all(r["reserved_microusd"] >= r["settled_microusd"] for r in rows),
    ) == ("committed", ["settled"] * len(usages), expected, True)


async def test_every_request_is_capped_at_the_models_max_output_tokens(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """The worst-case reservation assumes this cap (policy: 32000 for every model)."""
    script = Script(golden_calls())
    await run(workspace, modeler(registry), process_id, script)
    assert {s.get("max_tokens") for s in script.settings} == {32_000}


async def test_finish_releases_a_reservation_left_open(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = modeler(registry)
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    claimed = await runs.claim(workspace, queue=kind.id, owner="w", lease_seconds=30, run_id=run_id)
    assert claimed is not None
    async with workspace.db.tx() as conn:
        engagement = await workspace.engagement_of(conn, process_id)
    await budget.reserve(
        workspace.db,
        budget.Scopes(run_id, process_id, engagement),
        deployment="local",
        model_id="m",
        amount=500_000,
        run_ceiling=2_000_000,
    )
    await execute.finish(workspace, claimed, kind, execute.Outcome("aborted", "test"))
    assert [r["state"] for r in await reservations(workspace, run_id)] == ["released"]


async def test_a_call_over_the_run_ceiling_is_refused_before_the_model_sees_it(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """First call: 50k in, 5k out = 0.33 USD. The second call's worst case (about 0.73 USD) would
    pass a ceiling of 1.00 USD, so it is refused and the run rolls back."""
    heavy = RequestUsage(input_tokens=50_000, output_tokens=5_000)
    first, *rest = golden_calls()
    script = Script([Step(calls=first.calls, usage=heavy), *rest])
    kind = with_ceiling(modeler(registry), "1.00")
    run_id, final = await run(workspace, kind, process_id, script)
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        str(state["outcome_detail_scrubbed"]).split(":")[0],
        len(script.seen),
        [(r["state"], r["settled_microusd"]) for r in await reservations(workspace, run_id)],
        await history(workspace, process_id),
    ) == ("rolled_back", "money_budget_exhausted", 1, [("settled", 330_000)], [])


async def test_without_a_deployment_budget_no_call_is_sent(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    script = Script(golden_calls())
    config = execute.HarnessConfig(deployment=f"unbudgeted-{uuid.uuid4().hex[:8]}")
    run_id, final = await run(workspace, modeler(registry), process_id, script, config)
    state = await runs.run_state(workspace, run_id)
    assert (final, str(state["outcome_detail_scrubbed"]), len(script.seen)) == (
        "rolled_back",
        "money_budget_exhausted: no deployment budget configured",
        0,
    )


async def test_a_call_that_fails_counts_at_its_worst_case(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    async def provider_down() -> None:
        raise ConnectionError("model endpoint unavailable")

    script = Script([Step(before=provider_down)])
    run_id, final = await run(workspace, modeler(registry), process_id, script)
    rows = await reservations(workspace, run_id)
    assert (
        final,
        [(r["state"], r["settled_microusd"] == r["reserved_microusd"]) for r in rows],
    ) == (
        "rolled_back",
        [("settled", True)],
    )


async def test_a_request_the_provider_rejects_is_released_not_charged(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """A 4xx answer means the request was refused before inference, which is not billed."""

    async def refused() -> None:
        raise ModelHTTPError(status_code=403, model_name="eu.anthropic.claude-opus-5-5", body="no")

    run_id, final = await run(
        workspace, modeler(registry), process_id, Script([Step(before=refused)])
    )
    rows = await reservations(workspace, run_id)
    assert (final, [(r["state"], r["settled_microusd"]) for r in rows]) == (
        "rolled_back",
        [("released", None)],
    )


async def test_finished_and_lost_runs_leave_no_reservation_open(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = modeler(registry)
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="dies", lease_seconds=30, run_id=run_id
    )
    assert claimed is not None
    async with workspace.db.tx() as conn:
        engagement = await workspace.engagement_of(conn, process_id)
    scopes = budget.Scopes(run_id, process_id, engagement)
    await budget.reserve(
        workspace.db,
        scopes,
        deployment="local",
        model_id="m",
        amount=500_000,
        run_ceiling=2_000_000,
    )
    async with workspace.db.tx() as conn:  # the executor died mid-call; its lease runs out
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (run_id,)
        )
    await sweeper.sweep(workspace, owner="sweeper", lease_seconds=30, admission_timeout_seconds=600)
    assert [r["state"] for r in await reservations(workspace, run_id)] == ["released"]
