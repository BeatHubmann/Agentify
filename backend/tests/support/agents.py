"""Shared helpers for agent-layer tests: inputs, section scripts, and running any kind once."""

import uuid
from pathlib import Path
from typing import Any

from agentify import intake
from agentify.agents.catalog import build_agent
from agentify.harness import execute, runs
from agentify.harness.prompts import compose_prompt
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.support.scripted import Script, ScriptedSource, Step

INPUT_PATH = "inputs/process.md"
INPUT_TEXT = (
    Path(__file__).resolve().parents[3] / "evals" / "synthetic" / "minimal-linear-4" / "spec.md"
).read_text()
QUOTES = {
    "current": "An invoice arrives from the supplier portal.",
    "desired": "The processing fee is computed from the fee table.",
    "constraints": "A clerk approves the invoice and its fee.",
}
PREFIX = {"current": "cur", "desired": "des", "constraints": "con"}


async def add_process_input(ws: Workspace, process_id: str) -> None:
    await intake.add_input(
        ws, process_id=process_id, user_id="consultant-1", name="process.md", text=INPUT_TEXT
    )


def paragraph_op(section: str, number: int = 1, quote: str | None = None) -> dict[str, Any]:
    return {
        "op": "add",
        "path": "/paragraphs/-",
        "value": {
            "id": f"{PREFIX[section]}-{number:03d}",
            "text": f"{section} paragraph {number}",
            "sources": [{"input": INPUT_PATH, "quote": quote or QUOTES[section]}],
        },
    }


def section_steps(section: str) -> list[Step]:
    return [
        Step(
            calls=[
                ("patch_artifact", {"path": f"spec/{section}.json", "ops": [paragraph_op(section)]})
            ]
        ),
        Step(text=f"{section} section written"),
    ]


def synthesis_steps() -> list[Step]:
    sections = [
        {"section": s, "paragraphs": [paragraph_op(s)["value"]]}
        for s in ("current", "desired", "constraints")
    ]
    ops = [{"op": "add", "path": "/sections/-", "value": value} for value in sections]
    return [
        Step(calls=[("patch_artifact", {"path": "spec/specification.json", "ops": ops})]),
        Step(text="specification assembled"),
    ]


async def run_kind(
    ws: Workspace,
    registry: Registry,
    process_id: str,
    kind_id: str,
    script: Script,
    *,
    mode: runs.Mode = "from_scratch",
    confirmation: str | None = None,
    config: execute.HarnessConfig | None = None,
) -> tuple[str, str]:
    kind = registry.agents[kind_id]
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
        ws, queue=kind.id, owner=f"w-{uuid.uuid4().hex[:6]}", lease_seconds=30, run_id=run_id
    )
    assert claimed is not None
    prompt = await compose_prompt(
        ws,
        process_id=process_id,
        base_revision_id=claimed.base_revision_id,
        kind=kind,
        instructions=claimed.instructions,
        output_language="en",
    )
    final = await execute.execute(
        ws,
        claimed,
        kind,
        agent=build_agent(kind, registry),
        source=ScriptedSource(script),
        prompt=prompt,
        registry=registry,
        config=config,
    )
    return run_id, final
