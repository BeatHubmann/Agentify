"""INV-10.T-SPEC (A§22.3): a corrupt specification artifact reaches agents as a correctable read error,
and runs that consume it refuse admission; nothing reads it as empty or as text. Runs read their base
revision, so only the router, which reads the live head, can meet a corruption that admission did not
refuse."""

import pytest

from agentify.harness import runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_admission import _commit_raw
from tests.orchestration.test_agent_layer import retry_errors
from tests.support.agents import add_process_input, run_kind, section_steps, synthesis_steps
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
TRUNCATED = b'{"schema_version": 1, "artifact_type": "spec_section", "body": {"para'


async def specified(ws: Workspace, registry: Registry, process_id: str) -> None:
    await add_process_input(ws, process_id)
    for section in ("current", "desired", "constraints"):
        kind = f"spec-section-{section}"
        assert (await run_kind(ws, registry, process_id, kind, Script(section_steps(section))))[
            1
        ] == "committed"
    synthesis = Script(synthesis_steps())
    assert (await run_kind(ws, registry, process_id, "spec-synthesis", synthesis))[1] == "committed"


async def refusal(ws: Workspace, registry: Registry, process_id: str, kind: str, mode: str) -> str:
    try:
        await runs.admit(
            ws,
            process_id=process_id,
            kind=registry.agents[kind],
            mode=mode,  # type: ignore[arg-type]
            initiated_by="consultant-1",
            entry_point="test",
        )
    except runs.AdmissionRefused as refused:
        return str(refused)
    return "admitted"


async def test_a_corrupt_section_is_a_read_error_to_agents_and_its_consumers_refuse(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await specified(workspace, registry, process_id)
    await _commit_raw(workspace, process_id, "spec/current.json", TRUNCATED)
    read = Script(
        [Step(calls=[("read_workspace", {"path": "spec/current.json"})]), Step(text="ok")]
    )
    _run, router_final = await run_kind(
        workspace, registry, process_id, "router", read, mode="incremental"
    )
    assert (
        retry_errors(read),
        router_final,
        await refusal(workspace, registry, process_id, "spec-synthesis", "incremental"),
        await refusal(workspace, registry, process_id, "spec-synthesis", "from_scratch"),
    ) == (
        ["read_failure"],
        "unchanged",
        "input_corrupt: spec/current.json",
        "input_corrupt: spec/current.json",
    )


async def test_a_corrupt_specification_refuses_the_modeler(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await specified(workspace, registry, process_id)
    await _commit_raw(workspace, process_id, "spec/specification.json", TRUNCATED)
    assert await refusal(
        workspace, registry, process_id, "current-state-modeler", "from_scratch"
    ) == ("input_corrupt: spec/specification.json")
