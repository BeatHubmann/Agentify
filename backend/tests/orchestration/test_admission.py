"""Invariant 6 and A§8.2: one active run per process and kind; fail-closed admission."""

import asyncio

import pytest

from agentify.domain.canonical import canonical_json
from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.workspace import SystemWriter, Workspace
from tests.conftest import Infra
from tests.orchestration.test_run_exit_paths import commit_golden

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"


async def test_concurrent_admissions_admit_exactly_one(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]

    async def attempt(entry: str) -> str:
        try:
            return await runs.admit(
                workspace,
                process_id=process_id,
                kind=kind,
                mode="from_scratch",
                initiated_by="consultant-1",
                entry_point=entry,
            )
        except runs.AdmissionRefused as refused:
            return refused.code

    outcomes = await asyncio.gather(*(attempt(f"entry-{i}") for i in range(8)))
    admitted = [o for o in outcomes if o.startswith("run_")]
    assert (len(admitted), sorted(set(outcomes) - set(admitted))) == (1, ["already_running"])


async def test_index_covers_exactly_the_non_terminal_states(workspace: Workspace) -> None:
    async with workspace.db.tx() as conn:
        row = await (
            await conn.execute(
                "SELECT pg_get_expr(indpred, indrelid) AS pred FROM pg_index "
                "WHERE indexrelid = 'run_one_active_per_kind'::regclass"
            )
        ).fetchone()
    assert row is not None
    for terminal in runs.TERMINAL_STATES:
        assert terminal in str(row["pred"])
    for active in ("admitted", "running", "gating", "committing", "rolling_back"):
        assert active not in str(row["pred"])


async def _commit_raw(ws: Workspace, process_id: str, path: str, data: bytes) -> None:
    async with ws.db.tx() as conn:
        engagement = await ws.engagement_of(conn, process_id)
        head = await ws.head_revision(conn, process_id)
    sha = await ws.blobs.put(engagement, ws.gate.check_bytes(data, "test"))
    await ws.commit(
        process_id=process_id,
        writer=SystemWriter("test-corruption"),
        base_revision_id=head,
        staged={path: sha},
        message="plant corruption",
    )


async def test_corrupt_flow_refuses_incremental_and_needs_human_intent_to_rebuild(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    await commit_golden(workspace, registry, process_id)
    await _commit_raw(workspace, process_id, kind.output_path, b"{not json")
    refusals = []
    for mode in ("incremental", "from_scratch"):
        with pytest.raises(runs.AdmissionRefused) as refused:
            await runs.admit(
                workspace,
                process_id=process_id,
                kind=kind,
                mode=mode,  # type: ignore[arg-type]
                initiated_by="consultant-1",
                entry_point="router",
            )
        refusals.append(refused.value.code)
    confirmation = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind=KIND, confirmed_by="consultant-1"
    )
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="api",
        intent_confirmation_id=confirmation,
    )
    assert (refusals, run_id.startswith("run_")) == (["flow_corrupt", "needs_confirmation"], True)


async def test_confirmation_is_single_use(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    await commit_golden(workspace, registry, process_id)
    confirmation = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind=KIND, confirmed_by="consultant-1"
    )
    first = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="api",
        intent_confirmation_id=confirmation,
    )
    await runs.request_cancel(workspace, first)
    async with workspace.db.tx() as conn:
        await conn.execute("UPDATE run SET state = 'rolled_back' WHERE id = %s", (first,))
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            workspace,
            process_id=process_id,
            kind=kind,
            mode="from_scratch",
            initiated_by="c",
            entry_point="api",
            intent_confirmation_id=confirmation,
        )
    offered_again = await runs.latest_confirmation(workspace, process_id=process_id, run_kind=KIND)
    assert (refused.value.code, offered_again) == ("confirmation_invalid", None)


async def test_unreadable_edit_layer_refuses_incremental_runs(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    await commit_golden(workspace, registry, process_id)
    await _commit_raw(
        workspace, process_id, kind.edit_layer_path, canonical_json({"truncated": True})
    )
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            workspace,
            process_id=process_id,
            kind=kind,
            mode="incremental",
            initiated_by="consultant-1",
            entry_point="router",
        )
    assert refused.value.code == "edit_layer_unreadable"


# A§5.2: history tables have no UPDATE or DELETE grant (invariant 7). `waiver` and `sign_off` join
# this list when they exist (step 4); `revision_input` when inbox consumption is recorded.
HISTORY_TABLES = (
    "revision",
    "revision_path",
    "chat_message",
    "inbox_item",
    "run_event",
    "intent_confirmation",
    "intent_use",
    "security_event",
    "secret_allowlist_entry",
)


async def test_history_tables_are_append_only_for_both_application_roles(infra: Infra) -> None:
    """Every column of every history table: a column-level UPDATE grant is a rewrite path too."""
    import psycopg
    from psycopg import sql

    async with await psycopg.AsyncConnection.connect(infra.admin_conninfo) as admin:
        rows = await (
            await admin.execute(
                """SELECT table_name::text, column_name::text FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = ANY(%s)""",
                (list(HISTORY_TABLES),),
            )
        ).fetchall()
    delete, update = (
        sql.SQL("DELETE FROM {} WHERE false"),
        sql.SQL("UPDATE {} SET {} = {} WHERE false"),
    )
    statements = [delete.format(sql.Identifier(table)) for table in HISTORY_TABLES]
    statements += [
        update.format(*map(sql.Identifier, (table, column, column))) for table, column in rows
    ]
    allowed: list[tuple[str, str]] = []
    for role, conninfo in (("worker", infra.app_conninfo), ("api", infra.api_conninfo)):
        async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
            for statement in statements:
                try:
                    await conn.execute(statement)
                except (psycopg.errors.InsufficientPrivilege, psycopg.errors.GeneratedAlways):
                    continue  # identity columns are refused before privileges are checked
                allowed.append((role, statement.as_string(conn)))
    assert (sorted({table for table, _column in rows}), allowed) == (sorted(HISTORY_TABLES), [])
