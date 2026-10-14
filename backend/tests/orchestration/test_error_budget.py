"""HZ-6 / A§10.3: corrective attempts are bounded cumulatively, including framework-generated retries."""

import pytest

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import history, run_once
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

BAD_EDGE = ("add_edge", {"id": "e1", "source": "ghost", "target": "phantom"})
GOOD_READ = ("read_flow", {})
MISSING_ARGS = ("add_node", {"id": "x"})  # rejected by the framework's argument validation


async def test_alternating_failures_are_bounded_by_the_cumulative_budget_not_the_request_cap(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    steps = [Step(calls=[BAD_EDGE]) if i % 2 == 0 else Step(calls=[GOOD_READ]) for i in range(30)]
    script = Script(steps)
    run_id, final = await run_once(workspace, registry, process_id, script)
    state = await runs.run_state(workspace, run_id)
    model_calls = len(script.seen)
    # Failures land on model calls 1, 3, 5, ...; the per-tool limit is 5, so the 6th failure (call 11)
    # aborts when its retry prompt is counted, before call 12 is sent: 11 calls, far below the cap of 40.
    assert (final, state["outcome"], model_calls, await history(workspace, process_id)) == (
        "rolled_back",
        "aborted",
        11,
        [],
    )
    assert "error_budget_exhausted" in str(state["outcome_detail_scrubbed"])


async def test_framework_validation_retries_are_counted_too(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    steps = [
        Step(calls=[MISSING_ARGS]) if i % 2 == 0 else Step(calls=[GOOD_READ]) for i in range(30)
    ]
    script = Script(steps)
    run_id, final = await run_once(workspace, registry, process_id, script)
    state = await runs.run_state(workspace, run_id)
    assert (final, state["outcome"], len(script.seen)) == ("rolled_back", "aborted", 11)
    assert '"framework_validation"' in str(state["outcome_detail_scrubbed"])


async def test_a_corrected_mistake_still_commits(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    from tests.support.scripted import golden_calls

    steps = [Step(calls=[BAD_EDGE]), *golden_calls()]
    _run_id, final = await run_once(workspace, registry, process_id, Script(steps))
    assert final == "committed"
