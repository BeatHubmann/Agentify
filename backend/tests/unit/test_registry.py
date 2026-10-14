from pathlib import Path

from agentify.registry import load_registry

REGISTRY = Path(__file__).resolve().parents[3] / "registry"


def test_repository_registry_loads_and_declares_the_modeler() -> None:
    registry = load_registry(REGISTRY)
    kind = registry.agents["current-state-modeler"]
    assert (kind.output_path, kind.gate.min_nodes, bool(registry.prompt_text(kind))) == (
        "flows/current/flow.json",
        3,
        True,
    )
