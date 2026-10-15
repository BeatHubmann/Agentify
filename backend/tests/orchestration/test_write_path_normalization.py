"""HZ-3 / BC-C.13 on the user-edit and `patch_artifact` write paths (A§6, A§6.1): every string leaf
stored is normalized, typed fields and free-form payloads alike."""

import html
import json
import unicodedata
from typing import Any

import pytest

from agentify.editing import EditBatch, PatchRequest
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import commit_golden
from tests.orchestration.test_user_edits import EDITS, USER_NODE, edit
from tests.support.agents import add_process_input, paragraph_op, run_kind
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
USER_EDGE = "u_6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
RAW = "Approve &amp;amp; sign\x07 the café invoice "


def reference(text: str) -> str:
    """An independent decoder: unescape to a fixpoint, compose, drop control characters, trim."""
    while (decoded := html.unescape(text)) != text:
        text = decoded
    composed = unicodedata.normalize("NFC", text)
    return "".join(c for c in composed if c in "\n\t" or unicodedata.category(c) != "Cc").strip()


def leaves(value: Any) -> list[str]:
    """String leaves; keys are not leaves and are kept as-is (`normalize_leaves`), so that two keys
    never collapse into one."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in leaves(v)]
    if isinstance(value, list):
        return [s for v in value for s in leaves(v)]
    return []


async def test_every_string_the_edit_api_stores_is_normalized(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await commit_golden(workspace, registry, process_id)
    added = {"id": USER_NODE, "label": RAW, "kind": "step", "execution": "human"}
    batch = EditBatch(
        patches=(
            PatchRequest("modify_node", "approve", {"label": RAW, "description": RAW}),
            PatchRequest("add_node", None, {**added, "description": RAW}),
            PatchRequest(
                "add_edge", None, {"id": USER_EDGE, "source": "approve", "target": USER_NODE}
            ),
            PatchRequest("profile_override", "approve", {"note": RAW, "nested": {RAW: [RAW]}}),
        )
    )
    await edit(workspace, registry, process_id, batch)
    stored = await workspace.read(process_id, EDITS)
    assert stored is not None
    strings = leaves(json.loads(stored))
    expected = reference(RAW)
    assert (
        expected,
        [s for s in strings if s != reference(s)],
        strings.count(expected) >= 6,
    ) == ("Approve & sign the café invoice", [], True)


async def test_every_string_patch_artifact_stores_is_normalized(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    """The agent's free-form JSON Patch path: the paragraph text arrives entity-encoded."""
    await add_process_input(workspace, process_id)
    op = paragraph_op("current")
    op["value"] = {**op["value"], "text": RAW}
    script = Script([Step(calls=[("patch_artifact", {"path": "spec/current.json", "ops": [op]})])])
    _run, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    stored = await workspace.read(process_id, "spec/current.json")
    assert stored is not None
    strings = leaves(json.loads(stored))
    assert (final, [s for s in strings if s != reference(s)], reference(RAW) in strings) == (
        "committed",
        [],
        True,
    )
