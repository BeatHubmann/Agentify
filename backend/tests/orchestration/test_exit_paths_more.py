"""Remaining slice-1 obligations of VERIFICATION.md for invariants 1, 3, 6, 7 and 10."""

import asyncio
import itertools
from typing import Any

import psycopg
import pytest
from botocore.exceptions import ClientError

from agentify.agents.flow_tools import build_modeler
from agentify.domain.canonical import canonical_json, sha256_hex
from agentify.domain.envelope import Corrupt, decode_flow
from agentify.harness import execute, runs
from agentify.registry import Registry
from agentify.store.blobs import BlobStore
from agentify.store.workspace import Workspace
from tests.conftest import BUCKET, Infra, s3_client
from tests.orchestration.test_admission import _commit_raw
from tests.orchestration.test_run_exit_paths import (
    FLOW_PATH,
    commit_golden,
    head_flow,
    history,
    leftovers,
    run_once,
)
from tests.support.scripted import Script, Step, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
KIND = "current-state-modeler"


def add(
    node_id: str, kind: str = "step", execution: str = "human", parent: str | None = None
) -> Step:
    fields: dict[str, Any] = {"id": node_id, "label": node_id, "kind": kind, "execution": execution}
    if parent is not None:
        fields["parent"] = parent
    return Step(calls=[("add_node", fields)])


def link(a: str, b: str) -> Step:
    return Step(calls=[("add_edge", {"id": f"e-{a}-{b}", "source": a, "target": b})])


async def test_incremental_run_that_stages_nothing_ends_unchanged(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    before = await history(workspace, process_id)
    script = Script([Step(calls=[("read_flow", {})]), Step(text="nothing to change")])
    run_id, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents[KIND],
        mode="incremental",
        initiated_by="c",
        entry_point="test",
    )
    assert (
        final,
        (await runs.run_state(workspace, run_id))["state"],
        await history(workspace, process_id),
        again.startswith("run_"),
    ) == ("unchanged", "unchanged", before, True)


async def test_invariant_violating_flow_is_rolled_back_with_a_gate_report(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    steps = [
        add("in"),
        add("decide", "actor", "ai"),
        add("check", "task", "ai", parent="decide"),
        add("out"),
        link("in", "decide"),
        link("decide", "out"),
        Step(text="done"),
    ]
    run_id, final = await run_once(workspace, registry, process_id, Script(steps))
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], await history(workspace, process_id)) == (
        "rolled_back",
        "gate_failed",
        [],
    )
    assert "container_too_few_children" in str(state["outcome_detail_scrubbed"])


async def test_dangling_island_fails_structural_soundness_at_the_gate(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    steps = [
        *golden_calls()[:-1],
        add("x"),
        add("y"),
        link("x", "y"),
        Step(calls=[("add_edge", {"id": "e-y-x", "source": "y", "target": "x"})]),
        Step(text="done"),
    ]
    run_id, final = await run_once(workspace, registry, process_id, Script(steps))
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"]) == ("rolled_back", "gate_failed")
    assert "node_off_start_end_path" in str(state["outcome_detail_scrubbed"])


async def test_cancel_before_claim_ends_rolled_back_without_any_model_call(
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
    await runs.request_cancel(workspace, run_id)
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="w1", lease_seconds=30, run_id=run_id
    )
    assert claimed is not None
    script = Script(golden_calls())
    final = await execute.execute(
        workspace, claimed, kind, agent=build_modeler("m"), model=script.model(), prompt="go"
    )
    assert (
        final,
        len(script.seen),
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
    ) == ("rolled_back", 0, [], (0, 0))


async def test_runtime_cancellation_during_a_tool_call_rolls_back(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    in_tool = asyncio.Event()

    class SlowStaging(Workspace):
        writes = 0

        async def stage(self, *args: Any, **kwargs: Any) -> str:
            self.writes += 1
            if self.writes == 3:
                in_tool.set()
                await asyncio.sleep(3600)
            return await super().stage(*args, **kwargs)

    ws = SlowStaging(workspace.db, workspace.blobs, workspace.gate)
    kind = registry.agents[KIND]
    run_id = await runs.admit(
        ws,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    claimed = await runs.claim(ws, queue=kind.id, owner="w1", lease_seconds=30, run_id=run_id)
    assert claimed is not None
    task = asyncio.create_task(
        execute.execute(
            ws,
            claimed,
            kind,
            agent=build_modeler("m"),
            model=Script(golden_calls()).model(),
            prompt="go",
        )
    )
    await in_tool.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = await runs.run_state(ws, run_id)
    assert (
        state["state"],
        state["outcome"],
        await history(ws, process_id),
        await leftovers(ws, run_id),
    ) == ("rolled_back", "cancelled", [], (0, 0))


async def test_blob_write_failure_during_the_run_aborts_and_frees_the_slot(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class FailingBlobs(BlobStore):
        puts = 0

        async def put(self, *args: Any, **kwargs: Any) -> str:
            self.puts += 1
            if self.puts == 3:
                raise OSError("object store unavailable")
            return await super().put(*args, **kwargs)

    ws = Workspace(workspace.db, FailingBlobs(workspace.blobs._client, BUCKET), workspace.gate)
    run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    state = await runs.run_state(ws, run_id)
    again = await runs.admit(
        ws,
        process_id=process_id,
        kind=registry.agents[KIND],
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    assert (
        final,
        state["outcome"],
        await history(ws, process_id),
        await leftovers(ws, run_id),
        again.startswith("run_"),
    ) == ("rolled_back", "aborted", [], (0, 0), True)


async def test_failure_exits_free_the_slot_for_the_next_admission(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    async def provider_down() -> None:
        raise ConnectionError("model endpoint unavailable")

    _run_id, final = await run_once(
        workspace, registry, process_id, Script([*golden_calls()[:2], Step(before=provider_down)])
    )
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents[KIND],
        mode="from_scratch",
        initiated_by="c",
        entry_point="test",
    )
    assert (final, again.startswith("run_")) == ("rolled_back", True)


async def test_a_staged_blob_missing_at_commit_time_is_refused(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class BlobVanishes(Workspace):
        async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
            async with self.db.tx() as conn:
                engagement = await self.engagement_of(conn, process_id)
            for sha in kwargs["staged"].values():
                s3_client(infra).delete_object(Bucket=BUCKET, Key=BlobStore.key(engagement, sha))
            return await super().commit(**kwargs)

    ws = BlobVanishes(workspace.db, workspace.blobs, workspace.gate)
    run_id, final = await run_once(ws, registry, process_id, Script(golden_calls()))
    state = await runs.run_state(ws, run_id)
    assert (final, state["outcome"], await history(ws, process_id)) == (
        "rolled_back",
        "commit_failed",
        [],
    )
    assert "MissingBlob" in str(state["outcome_detail_scrubbed"])


async def test_conditional_put_never_overwrites_an_existing_blob(
    infra: Infra, workspace: Workspace
) -> None:
    original = b"original artifact bytes"
    key = BlobStore.key("eng-put-test", sha256_hex(original))
    await workspace.blobs.put("eng-put-test", workspace.gate.check_bytes(original, "test"))
    with pytest.raises(ClientError) as refused:
        s3_client(infra).put_object(
            Bucket=BUCKET, Key=key, Body=b"different bytes", IfNoneMatch="*"
        )
    stored = s3_client(infra).get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert (refused.value.response["Error"]["Code"], stored) == ("PreconditionFailed", original)


async def test_incremental_shrink_bound_is_enforced_at_the_gate(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    ids = [f"s{i}" for i in range(10)]
    build = (
        [add(i) for i in ids] + [link(a, b) for a, b in itertools.pairwise(ids)] + [Step(text="ok")]
    )
    assert (await run_once(workspace, registry, process_id, Script(build)))[1] == "committed"

    def delete_tail(count: int) -> Script:
        return Script(
            [
                *(Step(calls=[("delete_node", {"id": i})]) for i in reversed(ids[-count:])),
                Step(text="trimmed"),
            ]
        )

    _r1, too_much = await run_once(
        workspace, registry, process_id, delete_tail(4), mode="incremental"
    )
    _r2, within = await run_once(
        workspace, registry, process_id, delete_tail(3), mode="incremental"
    )
    flow = await head_flow(workspace, process_id)
    assert flow is not None
    assert (too_much, within, len(flow.nodes)) == ("rolled_back", "committed", 7)


async def test_a_job_delivered_twice_starts_its_run_once(
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
    claims = await asyncio.gather(
        *(
            runs.claim(workspace, queue=kind.id, owner=f"w{i}", lease_seconds=30, run_id=run_id)
            for i in range(4)
        )
    )
    assert sum(claim is not None for claim in claims) == 1


async def test_expired_or_foreign_confirmations_are_refused(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    await commit_golden(workspace, registry, process_id)
    foreign = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind="other-kind", confirmed_by="c"
    )
    expired = await runs.confirm_intent(
        workspace, process_id=process_id, run_kind=KIND, confirmed_by="c"
    )
    async with await psycopg.AsyncConnection.connect(infra.admin_conninfo) as admin:
        await admin.execute(
            "UPDATE intent_confirmation SET created_at = now() - interval '16 minutes' "
            "WHERE id = %s",
            (expired,),
        )
        await admin.commit()
    codes = []
    for confirmation in (foreign, expired):
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
        codes.append(refused.value.code)
    assert codes == ["confirmation_invalid", "confirmation_invalid"]


async def test_corrupt_flow_reads_as_corrupt_never_as_empty(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    await _commit_raw(workspace, process_id, FLOW_PATH, b"\x00garbage")
    data = await workspace.read(process_id, FLOW_PATH)
    assert data is not None
    assert isinstance(decode_flow(data), Corrupt)


async def test_corrupt_edit_layer_refuses_from_scratch_without_confirmation(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents[KIND]
    await _commit_raw(workspace, process_id, kind.edit_layer_path, canonical_json({"broken": True}))
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            workspace,
            process_id=process_id,
            kind=kind,
            mode="from_scratch",
            initiated_by="c",
            entry_point="router",
        )
    assert refused.value.code == "needs_confirmation"
    assert await head_flow(workspace, process_id) is None
