"""Pure tests for the registry, prompt versions and citation grounding (no containers)."""

import shutil
from pathlib import Path

import pytest

from agentify.artifacts import CheckContext, run_checks
from agentify.domain.spec import InputCitation, SpecParagraph, SpecSection
from agentify.harness import prompt_version
from agentify.registry import load_registry

REGISTRY = Path(__file__).resolve().parents[3] / "registry"


def copy_registry(tmp_path: Path) -> Path:
    target = tmp_path / "registry"
    shutil.copytree(REGISTRY, target)
    return target


def test_prompt_version_is_stable_and_changes_with_any_prompt(tmp_path: Path) -> None:
    root = copy_registry(tmp_path)
    first, again = prompt_version.compute(root), prompt_version.compute(root)
    (root / "agents" / "router.md").write_text(
        (root / "agents" / "router.md").read_text() + "\nOne more rule."
    )
    assert (first == again, prompt_version.compute(root) != first) == (True, True)


@pytest.mark.parametrize(
    ("file", "edit", "message"),
    [
        (
            "agents/spec-section-desired.yaml",
            ("path: spec/desired.json", "path: spec/current.json"),
            "written by both",
        ),
        (
            "artifacts/spec_section.yaml",
            ("spec_section.complete", "spec_section.made_up"),
            "unknown check",
        ),
        (
            "skills/specification.md",
            ("spec-synthesis]", "spec-synthesis, no-such-kind]"),
            "unknown agent kind",
        ),
        ("agents/router.yaml", ("phase: intake", "phase: nowhere"), "unknown phase"),
    ],
    ids=["two-writers", "unknown-check", "unknown-kind-in-skill", "unknown-phase"],
)
def test_inconsistent_registries_fail_to_load(
    tmp_path: Path, file: str, edit: tuple[str, str], message: str
) -> None:
    root = copy_registry(tmp_path)
    path = root / file
    text = path.read_text()
    assert edit[0] in text
    path.write_text(text.replace(edit[0], edit[1], 1))
    with pytest.raises(ValueError, match=message):
        load_registry(root)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("An invoice arrives from the supplier portal.", []),
        ("An  invoice arrives\nfrom the supplier portal.", []),
        ("An invoice arrives by fax.", ["quote_not_in_input"]),
    ],
    ids=["verbatim", "whitespace-differs", "invented"],
)
async def test_quotes_must_occur_in_their_input(quote: str, expected: list[str]) -> None:
    document = b"# Invoice\n\n1. An invoice arrives from the supplier portal.\n"

    async def read(path: str) -> bytes | None:
        return document if path == "inputs/a.md" else None

    section = SpecSection(
        section="current",
        paragraphs=(
            SpecParagraph(
                id="cur-001", text="t", sources=(InputCitation(input="inputs/a.md", quote=quote),)
            ),
        ),
    )
    found = await run_checks(("spec.sources_grounded",), section, CheckContext(read))
    assert [v.code for v in found] == expected


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
