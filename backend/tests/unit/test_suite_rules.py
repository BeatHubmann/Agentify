"""BC-B.M1 and BC-B.M2 (brief §12.5B, A§26.1), checked statically over the integration suites: the
real agent framework runs with only the model scripted, and the harness, store, secret gate and gate
are never stubbed. Fault injection is allowed only as a subclass of an agentify class whose overrides
raise or delegate to the real method, or whose docstring declares it: "Fault injection: <what>"."""

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]
SUITES = ("orchestration", "failure", "api", "support")
BUILDERS = {"build_modeler", "build_agent", "build_artifact_agent", "build_router"}
# The base module carries only types (`Model`); scripted models come from the function module only.
ALLOWED_MODEL_MODULES = {"pydantic_ai.models", "pydantic_ai.models.function"}


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return ""


def _agentify_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("agentify"):
            names |= {alias.asname or alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            names |= {
                (alias.asname or alias.name).split(".")[0]
                for alias in node.names
                if alias.name.startswith("agentify")
            }
    return names


def _agent_arguments(tree: ast.Module) -> list[str]:
    """`agent=` arguments that are neither a real builder call nor a name bound to one."""
    found: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        built = {
            target.id
            for node in ast.walk(function)
            if isinstance(node, ast.Assign) and _call_name(node.value) in BUILDERS
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if keyword.arg != "agent":
                    continue
                if _call_name(value) in BUILDERS or (
                    isinstance(value, ast.Name) and value.id in built
                ):
                    continue
                found.append(f"{function.name}: agent={ast.unparse(value)}")
    return found


def _overrides(tree: ast.Module, agentify: set[str]) -> list[str]:
    """Methods of subclasses of agentify classes that neither raise nor call the real method."""
    found: list[str] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        bases = {b.id for b in cls.bases if isinstance(b, ast.Name)}
        if not bases & agentify or (ast.get_docstring(cls) or "").startswith("Fault injection: "):
            continue
        for method in cls.body:
            if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            raises = any(isinstance(n, ast.Raise) for n in ast.walk(method))
            delegates = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == method.name
                and _call_name(n.func.value) == "super"
                for n in ast.walk(method)
            )
            if not (raises or delegates):
                found.append(f"{cls.name}.{method.name}")
    return found


def violations(source: str) -> list[str]:
    tree = ast.parse(source)
    agentify = _agentify_names(tree)
    found = [f"stub of the real framework: {v}" for v in _agent_arguments(tree)]
    found += [f"override without raise or delegation: {v}" for v in _overrides(tree, agentify)]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in {"unittest.mock", "mock"} or (
                module.startswith("pydantic_ai.models") and module not in ALLOWED_MODEL_MODULES
            ):
                found.append(f"import from {module}")
        elif isinstance(node, ast.Import) and any(
            a.name in {"unittest.mock", "mock"} for a in node.names
        ):
            found.append("import mock")
        elif isinstance(node, ast.arg) and node.arg == "monkeypatch":
            found.append("monkeypatch fixture")
        elif isinstance(node, ast.ClassDef) and "Agent" in {
            b.id for b in node.bases if isinstance(b, ast.Name)
        }:
            found.append(f"agent subclass {node.name}")
        elif isinstance(node, ast.Assign | ast.AugAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [
                f"assignment to {ast.unparse(t)}"
                for t in targets
                if isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id in agentify
            ]
    return found


def test_the_integration_suites_run_the_real_framework_and_never_stub_the_harness() -> None:
    files = sorted(p for suite in SUITES for p in (TESTS / suite).glob("*.py"))
    found = {str(p.relative_to(TESTS)): violations(p.read_text()) for p in files}
    assert (len(files) > 20, {k: v for k, v in found.items() if v}) == (True, {})


BROKEN = {
    "a stand-in agent": "async def t(ws):\n    await execute(ws, agent=object())\n",
    "a monkeypatched commit": "def test_x(monkeypatch):\n    monkeypatch.setattr('x', 1)\n",
    "a mock": "from unittest.mock import AsyncMock\n",
    "another scripted model": "from pydantic_ai.models.test import TestModel\n",
    "a module attribute replaced": "from agentify.harness import execute\nexecute.finish = None\n",
    "a commit that skips the real one": (
        "from agentify.store.workspace import Workspace\n"
        "class Fake(Workspace):\n    async def commit(self, **kw):\n        return 'rev'\n"
    ),
}


@pytest.mark.parametrize("source", BROKEN.values(), ids=BROKEN.keys())
def test_each_kind_of_stub_is_detected(source: str) -> None:
    assert len(violations(source)) == 1
