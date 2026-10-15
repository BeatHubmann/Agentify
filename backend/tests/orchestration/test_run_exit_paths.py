"""Brief §12.5B / invariants 1 and 7: every exit path ends in a valid commit or a verified base state.

The real framework, storage, commit, rollback and gate run; only the model is scripted.
"""

import asyncio
import uuid
from typing import Any

import pytest

from agentify.agents.flow_tools import build_modeler
from agentify.domain.envelope import decode_flow
from agentify.domain.flow import Flow
from agentify.harness import execute, runs
from agentify.registry import AgentKind, Registry
from agentify.store.workspace import Workspace
from tests.support.flows import minimal_golden
from tests.support.scripted import Script, ScriptedSource, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
FLOW_PATH = "flows/current/flow.json"


def modeler(registry: Registry) -> AgentKind:
    return registry.agents["current-state-modeler"]


async def run_once(
    ws: Workspace,
    registry: Registry,
    process_id: str,
    script: Script,
    *,
    mode: runs.Mode = "from_scratch",
    confirmation: str | None = None,
    credentials: tuple[str, ...] = (),
) -> tuple[str, str]:
    kind = modeler(registry)
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=kind,
        mode=mode,
        initiated_by="consultant-1",
        entry_point="test",
        intent_confirmation_id=confirmation,
    )
    claimed = await runs.claim(
        ws, queue=kind.id, owner=f"worker-{uuid.uuid4().hex[:6]}", lease_seconds=30, run_id=run_id
    )
    assert claimed is not None
    assert claimed.run_id == run_id
    agent = build_modeler(registry.prompt_text(kind))
    final = await execute.execute(
        ws,
        claimed,
        kind,
        agent=agent,
        source=ScriptedSource(script),
        prompt="Model the process.",
        credentials=credentials,
    )
    return run_id, final


async def history(ws: Workspace, process_id: str) -> list[dict[str, Any]]:
    async with ws.db.tx() as conn:
        rows = await (
            await conn.execute(
                """SELECT r.id, r.run_id, r.author_kind, array_agg(p.path ORDER BY p.path) AS paths
               FROM revision r JOIN revision_path p ON p.revision_id = r.id
               WHERE r.process_id = %s GROUP BY r.id, r.run_id, r.author_kind, r.created_at
               ORDER BY r.created_at""",
                (process_id,),
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def leftovers(ws: Workspace, run_id: str) -> tuple[int, int]:
    async with ws.db.tx() as conn:
        staging = await (
            await conn.execute("SELECT count(*) AS n FROM run_staging WHERE run_id = %s", (run_id,))
        ).fetchone()
        pins = await (
            await conn.execute(
                "SELECT count(*) AS n FROM blob_pin WHERE pinned_by_run = %s", (run_id,)
            )
        ).fetchone()
    assert staging is not None
    assert pins is not None
    return int(str(staging["n"])), int(str(pins["n"]))


async def head_flow(ws: Workspace, process_id: str) -> Flow | None:
    data = await ws.read(process_id, FLOW_PATH)
    if data is None:
        return None
    decoded = decode_flow(data)
    assert isinstance(decoded, Flow)
    return decoded


async def commit_golden(ws: Workspace, registry: Registry, process_id: str) -> str:
    run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    assert final == "committed"
    return run_id


async def test_success_commits_exactly_one_revision_with_only_the_runs_paths(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    run_id = await commit_golden(workspace, registry, process_id)
    state = await runs.run_state(workspace, run_id)
    assert (
        await head_flow(workspace, process_id),
        await history(workspace, process_id),
        state["state"],
        await leftovers(workspace, run_id),
    ) == (
        minimal_golden(),
        [
            {
                "id": (await history(workspace, process_id))[0]["id"],
                "run_id": run_id,
                "author_kind": "agent_run",
                "paths": [FLOW_PATH],
            }
        ],
        "committed",
        (0, 0),
    )


async def test_degenerate_stop_fails_the_gate_and_the_previous_flow_survives(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    before = await history(workspace, process_id)
    confirmation = await runs.confirm_intent(
        workspace,
        process_id=process_id,
        run_kind="current-state-modeler",
        confirmed_by="consultant-1",
    )
    script = Script(
        [
            Step(
                calls=[
                    (
                        "add_node",
                        {"id": "only", "label": "only", "kind": "step", "execution": "human"},
                    )
                ]
            ),
            Step(text="done early"),
        ]
    )
    run_id, final = await run_once(
        workspace, registry, process_id, script, confirmation=confirmation
    )
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        state["outcome"],
        await head_flow(workspace, process_id),
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
    ) == ("rolled_back", "gate_failed", minimal_golden(), before, (0, 0))


async def test_model_error_mid_run_rolls_back_and_verifies(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    async def provider_down() -> None:
        raise ConnectionError("model endpoint unavailable")

    steps = [*golden_calls()[:3], Step(before=provider_down)]
    run_id, final = await run_once(workspace, registry, process_id, Script(steps))
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        state["outcome"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
        await workspace.verify_rollback(run_id),
    ) == ("rolled_back", "aborted", [], (0, 0), [])


async def test_user_cancel_between_steps_rolls_back_without_a_revision(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    holder: dict[str, str] = {}

    async def cancel() -> None:
        await runs.request_cancel(workspace, holder["run_id"])

    steps = golden_calls()
    steps[2] = Step(calls=steps[2].calls, before=cancel)
    script = Script(steps)
    kind = modeler(registry)
    holder["run_id"] = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="w1", lease_seconds=30, run_id=holder["run_id"]
    )
    assert claimed is not None
    final = await execute.execute(
        workspace,
        claimed,
        kind,
        agent=build_modeler("m"),
        source=ScriptedSource(script),
        prompt="go",
    )
    state = await runs.run_state(workspace, holder["run_id"])
    assert (
        final,
        state["outcome"],
        await history(workspace, process_id),
        await leftovers(workspace, holder["run_id"]),
    ) == ("rolled_back", "cancelled", [], (0, 0))


async def test_cancel_arriving_during_the_gate_is_refused_by_the_commit(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class CancelAtCommit(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            await runs.request_cancel(self, kwargs["fence"].run_id)
            return await super().commit(**kwargs)

    ws = CancelAtCommit(workspace.db, workspace.blobs, workspace.gate)
    run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    state = await runs.run_state(ws, run_id)
    assert (final, state["outcome"], await history(ws, process_id)) == (
        "rolled_back",
        "cancelled",
        [],
    )


async def test_runtime_cancellation_rolls_back_and_reraises(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    staged = asyncio.Event()

    async def hang() -> None:
        staged.set()
        await asyncio.sleep(3600)

    steps = [*golden_calls()[:2], Step(before=hang)]
    kind = modeler(registry)
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="w1", lease_seconds=30, run_id=run_id
    )
    assert claimed is not None
    task = asyncio.create_task(
        execute.execute(
            workspace,
            claimed,
            kind,
            agent=build_modeler("m"),
            source=ScriptedSource(Script(steps)),
            prompt="go",
        )
    )
    await staged.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await runs.run_state(workspace, run_id)
    assert (
        state["state"],
        state["outcome"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
    ) == ("rolled_back", "cancelled", [], (0, 0))


async def test_storage_failure_during_commit_ends_rolled_back_not_running(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class FailingCommit(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            inner = kwargs["validate"]

            async def failing(reader: Any, changed: Any) -> Any:
                await inner(reader, changed)
                raise OSError("storage unavailable mid-transaction")

            return await super().commit(**{**kwargs, "validate": failing})

    ws = FailingCommit(workspace.db, workspace.blobs, workspace.gate)
    run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    state = await runs.run_state(ws, run_id)
    assert (
        final,
        state["state"],
        state["outcome"],
        await history(ws, process_id),
        await leftovers(ws, run_id),
    ) == ("rolled_back", "rolled_back", "commit_failed", [], (0, 0))


async def test_truncated_final_answer_fails_the_gate(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    steps = golden_calls()
    steps[-1] = Step(text="The flow is comp", finish_reason="length")
    run_id, final = await run_once(workspace, registry, process_id, Script(steps))
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], await history(workspace, process_id)) == (
        "rolled_back",
        "gate_failed",
        [],
    )
    assert "truncated_finish" in str(state["outcome_detail_scrubbed"])
