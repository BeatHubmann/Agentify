"""Slice 2b: artifact-authoring kinds, parallel sections, synthesis, the inbox and prompt versions."""

import asyncio
import json
from typing import Any

import pytest
from pydantic_ai.messages import RetryPromptPart

from agentify import inbox
from agentify.domain.spec import Specification, SpecSection
from agentify.editing import EditBatch, PatchRequest, apply_edit_batch
from agentify.harness import execute, runs
from agentify.registry import Registry
from agentify.store.workspace import Workspace
from tests.orchestration.test_run_exit_paths import commit_golden
from tests.support.agents import (
    INPUT_PATH,
    add_process_input,
    paragraph_op,
    run_kind,
    section_steps,
    synthesis_steps,
)
from tests.support.scripted import Script, Step

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
SECTIONS = ("current", "desired", "constraints")


async def body(ws: Workspace, process_id: str, path: str) -> dict[str, Any]:
    data = await ws.read(process_id, path)
    assert data is not None
    return dict(json.loads(data)["body"])


def retry_errors(script: Script) -> list[str]:
    """Error codes of every retry prompt the model received (the last request holds the full history)."""
    return [
        json.loads(str(part.content))["error"]
        for message in script.seen[-1]
        for part in getattr(message, "parts", [])
        if isinstance(part, RetryPromptPart) and str(part.content).startswith("{")
    ]


async def changed_paths(ws: Workspace, process_id: str) -> list[tuple[str, list[str]]]:
    """Per revision: (run id or author kind, paths whose blob differs from the parent revision)."""
    async with ws.db.tx() as conn:
        revisions = await (
            await conn.execute(
                "SELECT id, parent_id, run_id, author_kind FROM revision WHERE process_id = %s ORDER BY created_at",
                (process_id,),
            )
        ).fetchall()
        out = []
        for rev in revisions:
            mine = await ws.path_map(conn, str(rev["id"]))
            parent = await ws.path_map(
                conn, None if rev["parent_id"] is None else str(rev["parent_id"])
            )
            out.append(
                (
                    str(rev["run_id"] or rev["author_kind"]),
                    sorted(p for p in set(mine) | set(parent) if mine.get(p) != parent.get(p)),
                )
            )
    return out


async def test_a_section_agent_commits_a_grounded_section(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    _run_id, final = await run_kind(
        workspace, registry, process_id, "spec-section-current", Script(section_steps("current"))
    )
    section = SpecSection.model_validate(await body(workspace, process_id, "spec/current.json"))
    assert (final, section.section, [s.input for p in section.paragraphs for s in p.sources]) == (
        "committed",
        "current",
        [INPUT_PATH],
    )


async def test_an_invented_quote_is_a_correctable_error_and_the_corrected_section_commits(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    invented = paragraph_op("current", quote="Invoices are faxed to the head office.")
    script = Script(
        [
            Step(calls=[("patch_artifact", {"path": "spec/current.json", "ops": [invented]})]),
            *section_steps("current"),
        ]
    )
    _run_id, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    assert (final, retry_errors(script)) == ("committed", ["quote_not_in_input"])


@pytest.mark.parametrize(
    ("steps", "expected_violation"),
    [
        ([Step(text="nothing to write")], "nothing_staged"),
        (
            [
                Step(
                    calls=[
                        (
                            "patch_artifact",
                            {
                                "path": "spec/current.json",
                                "ops": [
                                    {
                                        "op": "add",
                                        "path": "/paragraphs/-",
                                        "value": {"id": "cur-001", "text": "unsourced"},
                                    }
                                ],
                            },
                        )
                    ]
                ),
                Step(text="done"),
            ],
            "paragraph_unsourced",
        ),
    ],
    ids=["nothing-staged", "unsourced-paragraph"],
)
async def test_the_artifact_gate_rejects_incomplete_sections(
    workspace: Workspace,
    registry: Registry,
    process_id: str,
    steps: list[Step],
    expected_violation: str,
) -> None:
    await add_process_input(workspace, process_id)
    run_id, final = await run_kind(
        workspace, registry, process_id, "spec-section-current", Script(steps)
    )
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        state["outcome"],
        expected_violation in str(state["outcome_detail_scrubbed"]),
    ) == ("rolled_back", "gate_failed", True)


async def test_a_section_agent_cannot_write_another_sections_artifact(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    script = Script(
        [
            Step(
                calls=[
                    (
                        "patch_artifact",
                        {"path": "spec/desired.json", "ops": [paragraph_op("desired")]},
                    )
                ]
            ),
            *section_steps("current"),
        ]
    )
    _run_id, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    assert (final, retry_errors(script), await workspace.read(process_id, "spec/desired.json")) == (
        "committed",
        ["undeclared_output"],
        None,
    )


async def test_parallel_sections_each_commit_only_their_own_writes(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    results = await asyncio.gather(
        *(
            run_kind(workspace, registry, process_id, f"spec-section-{s}", Script(section_steps(s)))
            for s in SECTIONS
        )
    )
    by_run = dict(await changed_paths(workspace, process_id))
    assert (
        sorted(final for _, final in results),
        sorted(tuple(by_run[run_id]) for run_id, _ in results),
    ) == (
        ["committed"] * 3,
        [("spec/constraints.json",), ("spec/current.json",), ("spec/desired.json",)],
    )


async def test_a_section_failure_leaves_its_siblings_completed_artifacts_intact(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)

    async def provider_down() -> None:
        raise ConnectionError("model endpoint unavailable")

    scripts = {
        "current": Script(section_steps("current")),
        "desired": Script([section_steps("desired")[0], Step(before=provider_down)]),
        "constraints": Script(section_steps("constraints")),
    }
    results = await asyncio.gather(
        *(
            run_kind(workspace, registry, process_id, f"spec-section-{s}", scripts[s])
            for s in SECTIONS
        )
    )
    present = [
        s for s in SECTIONS if await workspace.read(process_id, f"spec/{s}.json") is not None
    ]
    assert ([final for _, final in results], present) == (
        ["committed", "rolled_back", "committed"],
        ["current", "constraints"],
    )


async def test_synthesis_conflicts_when_a_section_changes_underneath_it(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    for s in SECTIONS:
        assert (
            await run_kind(
                workspace, registry, process_id, f"spec-section-{s}", Script(section_steps(s))
            )
        )[1] == ("committed")

    async def another_kind_commits_a_section() -> None:
        extra = Script(
            [
                Step(
                    calls=[
                        (
                            "patch_artifact",
                            {"path": "spec/current.json", "ops": [paragraph_op("current", 2)]},
                        )
                    ]
                ),
                Step(text="ok"),
            ]
        )
        _r, final = await run_kind(
            workspace, registry, process_id, "spec-section-current", extra, mode="incremental"
        )
        assert final == "committed"

    steps = synthesis_steps()
    steps[-1] = Step(text="assembled", before=another_kind_commits_a_section)
    run_id, final = await run_kind(workspace, registry, process_id, "spec-synthesis", Script(steps))
    state = await runs.run_state(workspace, run_id)
    assert (
        final,
        state["outcome"],
        await workspace.read(process_id, "spec/specification.json"),
    ) == ("rolled_back", "commit_failed", None)
    assert "BaseMovedConflict" in str(state["outcome_detail_scrubbed"])


async def test_synthesis_assembles_the_three_sections(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    for s in SECTIONS:
        await run_kind(
            workspace, registry, process_id, f"spec-section-{s}", Script(section_steps(s))
        )
    _r, final = await run_kind(
        workspace, registry, process_id, "spec-synthesis", Script(synthesis_steps())
    )
    spec = Specification.model_validate(
        await body(workspace, process_id, "spec/specification.json")
    )
    assert (final, [s.section for s in spec.sections]) == ("committed", list(SECTIONS))


async def test_two_kinds_with_disjoint_paths_and_a_user_edit_all_commit(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    await commit_golden(workspace, registry, process_id)
    modeler = registry.agents["current-state-modeler"]

    async def user_edit() -> None:
        async with workspace.db.tx() as conn:
            head = await workspace.head_revision(conn, process_id)
        await apply_edit_batch(
            workspace,
            process_id=process_id,
            kind=modeler,
            base_revision_id=head,  # type: ignore[arg-type]
            user_id="consultant-2",
            batch=EditBatch(
                patches=(PatchRequest("modify_node", "approve", {"label": "Approve"}),)
            ),
        )

    section = Script(
        [Step(calls=section_steps("desired")[0].calls, before=user_edit), Step(text="done")]
    )
    flow_edit = Script(
        [
            Step(calls=[("edit_node", {"id": "receive", "label": "Receive invoice"})]),
            Step(text="ok"),
        ]
    )
    results = await asyncio.gather(
        run_kind(workspace, registry, process_id, "spec-section-desired", section),
        run_kind(
            workspace, registry, process_id, "current-state-modeler", flow_edit, mode="incremental"
        ),
    )
    authors = [author for author, _paths in await changed_paths(workspace, process_id)]
    assert ([final for _, final in results], authors.count("user"), len(authors)) == (
        ["committed", "committed"],
        2,
        5,
    )


async def test_agents_ask_through_the_inbox_and_only_people_answer(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    script = Script(
        [
            Step(calls=[("inbox_ask", {"category": "design", "question": "not my category"})]),
            Step(
                calls=[
                    (
                        "inbox_ask",
                        {
                            "category": "spec-clarification",
                            "question": "Who approves the fee?",
                            "options": ["clerk", "manager"],
                        },
                    )
                ]
            ),
            *section_steps("current"),
        ]
    )
    run_id, final = await run_kind(workspace, registry, process_id, "spec-section-current", script)
    [question] = await inbox.items(workspace, process_id)
    with pytest.raises(inbox.InboxRejected) as by_agent:
        await inbox.answer(
            workspace,
            registry,
            process_id=process_id,
            item_id=question.item_id,
            author=inbox.AgentAuthor(run_id),
            text="clerk",
        )
    await inbox.answer(
        workspace,
        registry,
        process_id=process_id,
        item_id=question.item_id,
        author=inbox.UserAuthor("consultant-1"),
        text="clerk",
    )
    latest = {i.item_id: i for i in await inbox.items(workspace, process_id)}
    answers = [i for i in latest.values() if i.parent_item_id == question.item_id]
    assert (
        final,
        retry_errors(script),
        question.author_kind,
        by_agent.value.code,
        latest[question.item_id].status,
        [(a.author_kind, a.author_id, a.body) for a in answers],
    ) == (
        "committed",
        ["inbox_category_not_allowed"],
        "agent_run",
        "human_answer_required",
        "answered",
        [("user", "consultant-1", "clerk")],
    )


async def test_commits_record_the_prompt_version(
    workspace: Workspace, registry: Registry, process_id: str
) -> None:
    await add_process_input(workspace, process_id)
    config = execute.HarnessConfig(prompt_version="pv-test-0123")
    run_id, final = await run_kind(
        workspace,
        registry,
        process_id,
        "spec-section-current",
        Script(section_steps("current")),
        config=config,
    )
    async with workspace.db.tx() as conn:
        row = await (
            await conn.execute("SELECT prompt_version FROM revision WHERE run_id = %s", (run_id,))
        ).fetchone()
    assert (final, row and row["prompt_version"]) == ("committed", "pv-test-0123")
