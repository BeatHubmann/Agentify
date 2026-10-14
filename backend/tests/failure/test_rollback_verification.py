"""HZ-14: a rollback that fails its verification is surfaced loudly and quarantines the process."""

import pytest

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import run_once
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_failed_rollback_verification_quarantines_the_process(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    class BrokenVerification(Workspace):
        async def verify_rollback(self, run_id: str) -> list[str]:
            return [*await super().verify_rollback(run_id), "injected: base state mismatch"]

    ws = BrokenVerification(workspace.db, workspace.blobs, workspace.gate)
    run_id, final = await run_once(ws, registry, process_id, Script([Step(text="stop at once")]))
    state = await runs.run_state(ws, run_id)
    with pytest.raises(runs.AdmissionRefused) as refused:
        await runs.admit(
            ws,
            process_id=process_id,
            kind=registry.agents["current-state-modeler"],
            mode="from_scratch",
            initiated_by="consultant-1",
            entry_point="test",
        )
    assert (final, state["state"], refused.value.code) == (
        "rollback_failed",
        "rollback_failed",
        "process_quarantined",
    )
    assert "injected" in str(state["outcome_detail_scrubbed"])
