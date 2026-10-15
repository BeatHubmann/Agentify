"""§6.8, A§17.1: model clients are built only in `agentify.llm`. Static, over the source tree."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "agentify"
PROVIDER_MODULES = ("pydantic_ai.models.bedrock", "pydantic_ai.providers", "anthropic")


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        modules = []
        if isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        elif isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        found += [f"imports {m}" for m in modules if m.startswith(PROVIDER_MODULES)]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "client"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith("bedrock")
        ):
            found.append(f"creates a {node.args[0].value} client")
    return found


def test_no_module_outside_llm_builds_a_model_client() -> None:
    files = [p for p in SRC.rglob("*.py") if "llm" not in p.relative_to(SRC).parts]
    found = {str(p.relative_to(SRC)): violations(p.read_text()) for p in files}
    assert (len(files) > 40, {k: v for k, v in found.items() if v}) == (True, {})


@pytest.mark.parametrize(
    "source",
    [
        "from pydantic_ai.models.bedrock import BedrockConverseModel\n",
        "from pydantic_ai.providers.bedrock import BedrockProvider\n",
        "import anthropic\n",
        "s.client('bedrock-runtime', region_name='eu-central-1')\n",
    ],
    ids=["bedrock-model", "provider", "anthropic-sdk", "raw-client"],
)
def test_each_way_around_the_abstraction_is_detected(source: str) -> None:
    assert len(violations(source)) == 1
