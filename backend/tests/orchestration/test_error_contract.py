"""BC-B.3 (A§10.2): every correctable class reaches the model as a retry prompt with the contract's
payload, is counted, and the corrected run still commits. Classes that need kinds not built yet
(profile, contract, governance pre-check) join when those kinds exist; the secret-gate class is in
tests/failure/test_leak_drill.py and the transient read class in test_read_failures.py."""

import json
from typing import Any

import pytest
from pydantic_ai.messages import RetryPromptPart

from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import commit_golden, run_once
from tests.support.agents import add_process_input, paragraph_op, run_kind, section_steps
from tests.support.scripted import Call, Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
PAYLOAD_KEYS = ["attempts_left", "error", "hint", "location", "message"]
PER_TOOL = 5  # registry budgets of both kinds: errors_per_tool 5, per class 8, per run 20


def retry_prompts(script: Script) -> list[RetryPromptPart]:
    return [
        part
        for message in script.seen[-1]
        for part in getattr(message, "parts", [])
        if isinstance(part, RetryPromptPart)
    ]


def contract_view(part: RetryPromptPart) -> tuple[str, list[str], int] | str:
    """(error class, payload keys, attempts left) for Agentify payloads; framework prompts carry the
    framework's own text and are counted as `framework_validation`."""
    if not isinstance(part.content, str) or not part.content.startswith("{"):
        return "framework"
    payload: dict[str, Any] = json.loads(part.content)
    return payload["error"], sorted(payload), payload["attempts_left"]


FLOW_CASES: list[tuple[str, Call, tuple[str, list[str], int] | str]] = [
    (
        "integrity violation",
        ("add_edge", {"id": "e-x", "source": "receive", "target": "ghost"}),
        ("unknown_edge_endpoint", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "missing node",
        ("edit_node", {"id": "ghost", "label": "x"}),
        ("unknown_node", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "missing edge",
        ("delete_edge", {"id": "ghost"}),
        ("unknown_edge", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "archetype contradiction",
        ("add_node", {"id": "t", "label": "t", "kind": "task", "execution": "human"}),
        ("archetype_requires_ai", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    ("argument validation", ("add_node", {"id": "x"}), "framework"),
    ("unknown tool", ("no_such_tool", {}), "framework"),
]


@pytest.mark.parametrize(
    ("bad_call", "expected"), [c[1:] for c in FLOW_CASES], ids=[c[0] for c in FLOW_CASES]
)
async def test_a_correctable_flow_tool_error_follows_the_contract_and_the_run_commits(
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    bad_call: Call,
    expected: tuple[str, list[str], int] | str,
) -> None:
    await commit_golden(workspace, registry, process_id)
    fix = Step(calls=[("edit_node", {"id": "receive", "label": "Receive invoice"})])
    script = Script([Step(calls=[bad_call]), fix, Step(text="fixed")])
    _run, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    assert (final, [contract_view(p) for p in retry_prompts(script)]) == ("committed", [expected])


def patch(path: str, *ops: dict[str, Any]) -> Call:
    return ("patch_artifact", {"path": path, "ops": list(ops)})


NO_ID = {"op": "add", "path": "/paragraphs/-", "value": {"text": "a paragraph without an id"}}
ARTIFACT_CASES: list[tuple[str, Call, tuple[str, list[str], int]]] = [
    (
        "undeclared path",
        patch("spec/desired.json", paragraph_op("desired")),
        ("undeclared_output", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "unreadable path",
        ("read_artifact", {"path": "spec/specification.json"}),
        ("path_not_readable", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "missing input",
        ("read_artifact", {"path": "inputs/missing.md"}),
        ("not_found", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "non-matching patch",
        patch("spec/current.json", {"op": "test", "path": "/section", "value": "desired"}),
        ("invalid_patch", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "schema violation",
        patch("spec/current.json", NO_ID),
        ("schema_violation", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
    (
        "invented quote",
        patch("spec/current.json", paragraph_op("current", quote="Nobody wrote this sentence.")),
        ("quote_not_in_input", PAYLOAD_KEYS, PER_TOOL - 1),
    ),
]


@pytest.mark.parametrize(
    ("bad_call", "expected"), [c[1:] for c in ARTIFACT_CASES], ids=[c[0] for c in ARTIFACT_CASES]
)
async def test_a_correctable_artifact_tool_error_follows_the_contract_and_the_run_commits(
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    bad_call: Call,
    expected: tuple[str, list[str], int],
) -> None:
    await add_process_input(workspace, process_id)
    script = Script([Step(calls=[bad_call]), *section_steps("current")])
    _run, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    assert (final, [contract_view(p) for p in retry_prompts(script)]) == ("committed", [expected])


async def test_attempts_left_reports_the_smallest_remaining_limit(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """Two tools fail with one class, alternately: the per-tool limit (5) binds first, then the
    per-class limit (8) does, so the eighth failure reports 0 left and a ninth would abort."""
    await commit_golden(workspace, registry, process_id)
    ghosts = [("edit_node", {"id": "ghost", "label": "x"}), ("delete_node", {"id": "ghost"})]
    script = Script([*(Step(calls=[ghosts[i % 2]]) for i in range(8)), Step(text="gave up")])
    _run, final = await run_once(workspace, registry, process_id, script, mode="incremental")
    assert (final, [contract_view(p) for p in retry_prompts(script)]) == (
        "unchanged",
        [("unknown_node", PAYLOAD_KEYS, left) for left in (4, 4, 3, 3, 2, 2, 1, 0)],
    )
