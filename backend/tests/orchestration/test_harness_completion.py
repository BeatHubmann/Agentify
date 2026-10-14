"""Slice 2a: the remaining harness obligations from VERIFICATION.md (rows named per test)."""

import asyncio
import contextlib
import itertools
from typing import Any

import psycopg
import pytest

from agentify.agents.flow_tools import build_modeler
from agentify.domain.envelope import FLOW_SCHEMA_VERSION, encode
from agentify.harness import execute, runs, sweeper
from agentify.registry import Registry
from agentify.store.blobs import BlobStore
from agentify.store.workspace import Fence, LeaseLost, Workspace
from tests.conftest import BUCKET, Infra
from tests.orchestration.test_run_exit_paths import (
    FLOW_PATH,
    commit_golden,
    head_flow,
    history,
    run_once,
)
from tests.support.flows import linear, minimal_golden
from tests.support.scripted import Script, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"


def add_many(ids: list[str]) -> list[Step]:
    """Ten graph mutations per model response (parallel tool calls) to build large flows."""
    calls: list[tuple[str, dict[str, Any]]] = [
        ("add_node", {"id": i, "label": i, "kind": "step", "execution": "human"}) for i in ids
    ]
    steps = [Step(calls=calls[k : k + 10]) for k in range(0, len(calls), 10)]
    links: list[tuple[str, dict[str, Any]]] = [
        ("add_edge", {"id": f"e-{a}-{b}", "source": a, "target": b})
        for a, b in itertools.pairwise(ids)
    ]
    steps += [Step(calls=links[k : k + 10]) for k in range(0, len(links), 10)]
    return steps


async def fence_of(ws: Workspace, run_id: str) -> Fence:
    state = await runs.run_state(ws, run_id)
    return Fence(run_id, str(state["lease_owner"]), int(str(state["lease_epoch"])))


# INV-1.VF-TRUNC (second case): tool calls cut off at the output limit never commit.
async def test_truncated_tool_calls_are_counted_and_never_commit(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    truncated_call = ("add_node", '{"id": "a", "label": "cut of')
    script = Script([Step(calls=[truncated_call], finish_reason="length") for _ in range(10)])  # type: ignore[list-item]
    run_id, final = await run_once(workspace, registry, process_id, script)
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], await history(workspace, process_id)) == (
        "rolled_back",
        "aborted",
        [],
    )
    assert "error_budget_exhausted" in str(state["outcome_detail_scrubbed"])


# INV-1.PK-STALL (commit path): a stale fence cannot commit after the sweeper took the run over.
async def test_a_stale_fence_cannot_commit(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    stalled = await runs.claim(
        workspace, queue=kind.id, owner="stalled", lease_seconds=30, run_id=run_id
    )
    assert stalled is not None
    data = encode("flow", FLOW_SCHEMA_VERSION, minimal_golden(), output_language="en")
    sha = await workspace.stage(stalled.fence, process_id, kind.output_path, data)
    assert await runs.move_to_gating(workspace, stalled.fence)
    async with workspace.db.tx() as conn:
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (run_id,)
        )
    await sweeper.sweep(
        workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=600
    )
    with pytest.raises(LeaseLost):
        await workspace.commit(
            process_id=process_id,
            writer=execute.AgentRunWriter(run_id, "c", KIND),
            base_revision_id=None,
            staged={kind.output_path: sha},
            fence=stalled.fence,
            message="stale",
        )
    assert (
        (await runs.run_state(workspace, run_id))["state"],
        await history(workspace, process_id),
    ) == ("rolled_back", [])


# INV-6.ME: every failure exit frees the slot.
@pytest.mark.parametrize("exit_path", ["validation_failure", "user_cancel", "runtime_cancel"])
async def test_each_failure_exit_frees_the_slot(
    workspace: Workspace, registry: Registry, process_id: str, exit_path: str
) -> None:
    kind = registry.agents[KIND]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="w1", lease_seconds=30, run_id=run_id
    )
    assert claimed is not None
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.sleep(3600)

    async def cancel() -> None:
        await runs.request_cancel(workspace, run_id)

    steps = {
        "validation_failure": [*golden_calls()[:1], Step(text="stop")],
        "user_cancel": [Step(calls=golden_calls()[0].calls, before=cancel)],
        "runtime_cancel": [*golden_calls()[:1], Step(before=hang)],
    }[exit_path]
    task = asyncio.create_task(
        execute.execute(
            workspace,
            claimed,
            kind,
            agent=build_modeler("m"),
            model=Script(steps).model(),
            prompt="go",
        )
    )
    if exit_path == "runtime_cancel":
        await started.wait()
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    assert ((await runs.run_state(workspace, run_id))["state"], again.startswith("run_")) == (
        "rolled_back",
        True,
    )


# INV-6.ADM: a job delivered again after its run committed does not start it.
async def test_redelivery_after_commit_does_not_restart_the_run(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    run_id = await commit_golden(workspace, registry, process_id)
    assert (
        await runs.claim(workspace, queue=KIND, owner="late", lease_seconds=30, run_id=run_id)
        is None
    )


# INV-6.SF: a commit-transaction failure frees the slot.
async def test_commit_failure_frees_the_slot(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class FailingCommit(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            raise OSError("storage unavailable")

    ws = FailingCommit(workspace.db, workspace.blobs, workspace.gate)
    _run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents[KIND],
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    assert (final, again.startswith("run_")) == ("rolled_back", True)


# INV-6.PK-UNCLAIMED: an admitted run whose job row vanished is finished by the sweeper.
async def test_admitted_run_without_a_job_is_swept(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    async with await psycopg.AsyncConnection.connect(infra.admin_conninfo) as admin:
        await admin.execute("DELETE FROM job WHERE run_id = %s", (run_id,))
        await admin.commit()
    swept = await sweeper.sweep(
        workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=0
    )
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    assert ([s.final_state for s in swept if s.run_id == run_id], again.startswith("run_")) == (
        ["rolled_back"],
        True,
    )


# BC-B.3: an unexpected exception inside tool code aborts the run; it is never an unhandled crash.
async def test_an_exception_inside_a_tool_aborts_cleanly(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class BrokenRead(Workspace):
        reads = 0

        async def read_staged_or_base(self, *args: Any, **kwargs: Any) -> bytes | None:
            self.reads += 1
            if self.reads == 1:
                raise KeyError("bug in a read path")
            return await super().read_staged_or_base(*args, **kwargs)

    ws = BrokenRead(workspace.db, workspace.blobs, workspace.gate)
    run_id, final = await run_once(
        ws, registry, process_id, Script([Step(calls=[("read_flow", {})])] * 3)
    )
    state = await runs.run_state(ws, run_id)
    assert (
        final,
        state["outcome"],
        state["outcome_detail_scrubbed"],
        await history(ws, process_id),
    ) == ("rolled_back", "aborted", "KeyError", [])


# BC-B.13: request-limit exhaustion ends in a clean rollback, not a hang.
async def test_request_limit_exhaustion_rolls_back(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    script = Script([*golden_calls()[:-1], *(Step(calls=[("read_flow", {})]) for _ in range(60))])
    run_id, final = await run_once(workspace, registry, process_id, script)
    state = await runs.run_state(workspace, run_id)
    limit = registry.agents[KIND].budgets.request_limit
    assert (final, state["outcome"], state["outcome_detail_scrubbed"], len(script.seen)) == (
        "rolled_back",
        "aborted",
        "UsageLimitExceeded",
        limit,
    )


# INV-10.S: the gate judges the stored bytes, not the run's cache, in both directions.
@pytest.mark.parametrize(
    ("stored", "expected"), [("degenerate", "rolled_back"), ("valid", "committed")]
)
async def test_the_gate_judges_stored_bytes_not_the_tool_cache(
    workspace: Workspace, registry: Registry, process_id: str, stored: str, expected: str
) -> None:
    holder: dict[str, str] = {}
    flow = linear("only-a", "only-b") if stored == "degenerate" else minimal_golden()

    async def overwrite_staging_behind_the_cache() -> None:
        data = encode("flow", FLOW_SCHEMA_VERSION, flow, output_language="en")
        await workspace.stage(
            await fence_of(workspace, holder["run_id"]), process_id, FLOW_PATH, data
        )

    cache_content = golden_calls()[:-1] if stored == "degenerate" else golden_calls()[:1]
    steps = [*cache_content, Step(text="done", before=overwrite_staging_behind_the_cache)]
    kind = registry.agents[KIND]
    holder["run_id"] = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="w1", lease_seconds=30, run_id=holder["run_id"]
    )
    assert claimed is not None
    final = await execute.execute(
        workspace, claimed, kind, agent=build_modeler("m"), model=Script(steps).model(), prompt="go"
    )
    committed = await head_flow(workspace, process_id)
    assert (final, committed) == (expected, minimal_golden() if stored == "valid" else None)


# INV-3.INTENT: a confirmation for another process is refused.
async def test_a_confirmation_for_another_process_is_refused(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    other = f"{process_id}-other"
    async with workspace.db.tx() as conn:
        engagement = await workspace.engagement_of(conn, process_id)
    await workspace.create_process(engagement, other)
    confirmation = await runs.confirm_intent(
        workspace, process_id=other, run_kind=KIND, confirmed_by="c"
    )
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            workspace,
            process_id=process_id,
            kind=registry.agents[KIND],
            mode="from_scratch",
            initiated_by="c",
            entry_point="api",
            intent_confirmation_id=confirmation,
        )
    assert refused.value.code == "confirmation_invalid"


# INV-7.BLOB (second case): a put that reports success without storing never yields a revision.
async def test_a_put_that_silently_did_not_store_never_reaches_history(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class LyingBlobs(BlobStore):
        async def put(self, engagement_id: str, data: Any) -> str:
            from agentify.domain.canonical import sha256_hex

            return sha256_hex(data.value)

    ws = Workspace(workspace.db, LyingBlobs(workspace.blobs._client, BUCKET), workspace.gate)
    _run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    assert (final, await history(ws, process_id)) == ("rolled_back", [])


# INV-1.VF-SOUND: the remaining shapes, at the gate.
@pytest.mark.parametrize("shape", ["no_end_node", "children_never_reach_the_container_exit"])
async def test_structurally_unsound_flows_fail_the_gate(
    workspace: Workspace, registry: Registry, process_id: str, shape: str
) -> None:
    def node(
        i: str, kind: str = "step", execution: str = "human", parent: str | None = None
    ) -> Step:
        fields = {"id": i, "label": i, "kind": kind, "execution": execution}
        return Step(
            calls=[("add_node", fields if parent is None else {**fields, "parent": parent})]
        )

    def edge(a: str, b: str) -> Step:
        return Step(calls=[("add_edge", {"id": f"e-{a}-{b}", "source": a, "target": b})])

    shapes = {
        "no_end_node": [
            node("s"),
            node("a"),
            node("b"),
            node("c"),
            edge("s", "a"),
            edge("a", "b"),
            edge("b", "c"),
            edge("c", "a"),
        ],
        "children_never_reach_the_container_exit": [
            node("in"),
            node("o", "orchestrator", "ai"),
            node("t1", "task", "ai", "o"),
            node("t2", "task", "ai", "o"),
            node("out"),
            edge("in", "o"),
            edge("o", "out"),
            edge("t1", "t2"),
            edge("t2", "t1"),
        ],
    }
    run_id, final = await run_once(
        workspace, registry, process_id, Script([*shapes[shape], Step(text="done")])
    )
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"]) == ("rolled_back", "gate_failed")


# INV-1.VF-SHRINK: 29%, 30% and 31% of a 100-node base.
@pytest.mark.parametrize(
    ("removed", "expected"), [(29, "committed"), (30, "committed"), (31, "rolled_back")]
)
async def test_shrink_bound_on_a_hundred_node_base(
    workspace: Workspace, registry: Registry, process_id: str, removed: int, expected: str
) -> None:
    ids = [f"n{i:03d}" for i in range(100)]
    _r, built = await run_once(
        workspace, registry, process_id, Script([*add_many(ids), Step(text="ok")])
    )
    tail = list(reversed(ids[-removed:]))
    deletes = [
        Step(calls=[("delete_node", {"id": i}) for i in tail[k : k + 10]])
        for k in range(0, len(tail), 10)
    ]
    _r2, final = await run_once(
        workspace,
        registry,
        process_id,
        Script([*deletes, Step(text="trimmed")]),
        mode="incremental",
    )
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    assert (built, final, len(flow.nodes)) == (
        "committed",
        expected,
        100 - removed if expected == "committed" else 100,
    )
