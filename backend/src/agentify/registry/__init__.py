"""Declarative extension registry (A§9.4). Every enumeration the application uses comes from here."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentify.domain.flow import FlowMode
from agentify.domain.validate import GateThresholds


class _Decl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GateDecl(_Decl):
    min_nodes: int = Field(ge=1)
    max_shrink: float = Field(ge=0, le=1)

    def thresholds(self) -> GateThresholds:
        return GateThresholds(min_nodes=self.min_nodes, max_shrink=self.max_shrink)


class BudgetDecl(_Decl):
    request_limit: int = Field(ge=1)
    errors_per_tool: int = Field(ge=1)
    errors_per_class: int = Field(ge=1)
    errors_per_run: int = Field(ge=1)


class AgentKind(_Decl):
    id: str
    model_role: str
    prompt: str
    flow_mode: FlowMode
    output_path: str
    edit_layer_path: str
    modes: tuple[Literal["from_scratch", "incremental"], ...]
    gate: GateDecl
    budgets: BudgetDecl


class Registry(_Decl):
    root: Path
    agents: dict[str, AgentKind]

    def prompt_text(self, kind: AgentKind) -> str:
        return (self.root / "agents" / kind.prompt).read_text(encoding="utf-8")


def load_registry(root: Path) -> Registry:
    agents: dict[str, AgentKind] = {}
    for path in sorted((root / "agents").glob("*.yaml")):
        kind = AgentKind.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if kind.id in agents:
            raise ValueError(f"duplicate agent kind {kind.id!r} in {path}")
        if not (root / "agents" / kind.prompt).is_file():
            raise ValueError(f"agent kind {kind.id!r} names a missing prompt {kind.prompt!r}")
        agents[kind.id] = kind
    return Registry(root=root, agents=agents)
