"""Brief §12.5C: process kill mid-run, and a stalled executor resuming after the sweeper took over."""

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from agentify.agents.flow_tools import build_modeler
from agentify.domain.edits import merge
from agentify.harness import execute, runs, sweeper
from agentify.registry import Registry
from agentify.store.workspace import LeaseLost, Workspace
from tests.conftest import REGISTRY_ROOT, Infra
from tests.orchestration.test_run_exit_paths import commit_golden, head_flow, history, leftovers
from tests.orchestration.test_user_edits import edit, edit_layer, relabel
from tests.support.scripted import Script, golden_calls

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
BACKEND = Path(__file__).resolve().parents[2]


async def staged_rows(ws: Workspace, run_id: str) -> int:
    return (await leftovers(ws, run_id))[0]


async def kill_mid_run(
    infra: Infra, ws: Workspace, run_id: str, *, mode: str = "mid_run", before_kill: Any = None
) -> tuple[str, int, list[tuple[str, str]]]:
    """Run a worker subprocess to its first staged write, SIGKILL it, then let the sweeper run."""
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.worker_proc",
        infra.app_conninfo,
        infra.s3_endpoint,
        infra.s3_access_key,
        infra.s3_secret_key,
        run_id,
        str(REGISTRY_ROOT),
        mode,
        cwd=BACKEND,
        stdout=asyncio.subprocess.PIPE,
    )
    assert worker.stdout is not None
    try:
        line = (await asyncio.wait_for(worker.stdout.readline(), timeout=60)).decode()
        staged_before_kill = await staged_rows(ws, run_id)
        if before_kill is not None:
            await before_kill()
        os.kill(worker.pid, signal.SIGKILL)
        await asyncio.wait_for(worker.wait(), timeout=10)
    finally:
        if worker.returncode is None:
            worker.kill()
            await worker.wait()
    await asyncio.sleep(2.5)  # let the 2 s lease expire
    swept = await sweeper.sweep(
        ws, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=600
    )
    return line.strip(), staged_before_kill, [(s.run_id, s.final_state) for s in swept]


async def test_sigkill_mid_run_is_recovered_by_the_sweeper_to_the_base_state(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents["current-state-modeler"]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    line, staged_before_kill, swept = await kill_mid_run(infra, workspace, run_id)
    state = await runs.run_state(workspace, run_id)
    assert (
        line,
        staged_before_kill,
        [s for s in swept if s[0] == run_id],
        state["state"],
        state["outcome"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
        await workspace.verify_rollback(run_id),
    ) == ("STAGED", 1, [(run_id, "rolled_back")], "rolled_back", "executor_lost", [], (0, 0), [])


async def test_a_user_edit_made_before_a_kill_survives_the_sweepers_recovery(
    infra: Infra, workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """INV-2.PK: the sweeper rolls back only the run's staging, never the user's revision."""
    await commit_golden(workspace, registry, process_id)
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=registry.agents["current-state-modeler"],
        mode="incremental",
        initiated_by="consultant-1",
        entry_point="test",
    )

    async def user_edits() -> None:
        await edit(workspace, registry, process_id, relabel("approve", "edited before the kill"))

    line, staged, swept = await kill_mid_run(
        infra, workspace, run_id, mode="mid_run_edit", before_kill=user_edits
    )
    head = await head_flow(workspace, process_id)
    assert head is not None
    merged = merge(head, await edit_layer(workspace, process_id)).flow.node_map()
    state = await runs.run_state(workspace, run_id)
    assert (
        (line, staged),
        [s for s in swept if s[0] == run_id],
        (state["state"], state["outcome"]),
        [r["author_kind"] for r in await history(workspace, process_id)],
        (merged["approve"].label, merged["receive"].label),
        await leftovers(workspace, run_id),
    ) == (
        ("STAGED", 1),
        [(run_id, "rolled_back")],
        ("rolled_back", "executor_lost"),
        ["agent_run", "user"],
        ("edited before the kill", "receive"),
        (0, 0),
    )


async def test_stalled_executor_cannot_write_or_commit_after_the_sweeper_took_over(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents["current-state-modeler"]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    stalled = await runs.claim(
        workspace, queue=kind.id, owner="stalled", lease_seconds=30, run_id=run_id
    )
    assert stalled is not None
    async with workspace.db.tx() as conn:  # the stall: the lease runs out while the executor sleeps
        await conn.execute(
            "UPDATE run SET lease_expires_at = now() - interval '1 second' WHERE id = %s", (run_id,)
        )
    swept = await sweeper.sweep(
        workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=600
    )
    with pytest.raises(LeaseLost):
        await workspace.stage(stalled.fence, process_id, kind.output_path, b'{"late": "write"}')
    final = await execute.execute(
        workspace,
        stalled,
        kind,
        agent=build_modeler("m"),
        model=Script(golden_calls()).model(),
        prompt="go",
    )
    state = await runs.run_state(workspace, run_id)
    assert (
        [s.final_state for s in swept if s.run_id == run_id],
        final,
        state["state"],
        await history(workspace, process_id),
        await leftovers(workspace, run_id),
    ) == (["rolled_back"], "abandoned", "rolled_back", [], (0, 0))


async def test_unclaimed_admitted_run_does_not_hold_the_slot_forever(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    kind = registry.agents["current-state-modeler"]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    swept = await sweeper.sweep(
        workspace, owner="sweeper-1", lease_seconds=30, admission_timeout_seconds=0
    )
    again = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="consultant-1",
        entry_point="test",
    )
    assert ([s.final_state for s in swept if s.run_id == run_id], again.startswith("run_")) == (
        ["rolled_back"],
        True,
    )
