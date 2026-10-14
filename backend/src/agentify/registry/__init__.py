"""Declarative extension registry (A§9.4). Every enumeration the application uses comes from here.

Agent kinds are a discriminated union on their toolset: flow-authoring kinds write a flow through
graph tools; artifact-authoring kinds write registry artifact types through JSON Patch; the router
writes nothing and dispatches.
"""

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agentify.artifacts import BUILTIN_SCHEMAS, check_names
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


class _KindBase(_Decl):
    id: str
    model_role: str
    prompt: str
    phase: str
    modes: tuple[Literal["from_scratch", "incremental"], ...]
    budgets: BudgetDecl
    input_paths: tuple[str, ...] = ()
    """Artifacts given to the agent as a workspace snapshot, and treated as semantic inputs at commit."""
    inbox_categories: tuple[str, ...] = ()


class FlowAgentKind(_KindBase):
    toolset: Literal["flow_authoring"]
    flow_mode: FlowMode
    output_path: str
    edit_layer_path: str
    gate: GateDecl

    @property
    def output_paths(self) -> tuple[str, ...]:
        return (self.output_path,)


class OutputDecl(_Decl):
    path: str
    artifact_type: str
    initial: dict[str, Any]
    """The body a from-scratch run starts from; JSON Patch operations apply to it."""


class ArtifactAgentKind(_KindBase):
    toolset: Literal["artifact_authoring"]
    outputs: tuple[OutputDecl, ...]

    @property
    def output_paths(self) -> tuple[str, ...]:
        return tuple(output.path for output in self.outputs)


class RouterKind(_KindBase):
    toolset: Literal["router"]

    @property
    def output_paths(self) -> tuple[str, ...]:
        return ()


AgentKind = Annotated[
    FlowAgentKind | ArtifactAgentKind | RouterKind, Field(discriminator="toolset")
]
_AGENT_KIND = TypeAdapter(AgentKind)


class ArtifactType(_Decl):
    id: str
    path_pattern: str
    schema_ref: str
    """`builtin:<name>` for schemas whose invariants are code; JSON Schema files for new types."""
    write_checks: tuple[str, ...] = ()
    """Checks run on every write (correctable errors to the agent)."""
    gate_checks: tuple[str, ...] = ()
    """Checks run by the harness gate and at commit (completeness, non-degeneracy)."""


class Phase(_Decl):
    id: str
    order: int
    label_key: str


class InboxCategory(_Decl):
    id: str
    phase: str
    label_key: str
    human_only_answers: bool = True


class Skill(_Decl):
    name: str
    phase: str
    description: str
    agent_kinds: tuple[str, ...] = ()
    body: str


class Registry(_Decl):
    root: Path
    agents: dict[str, AgentKind]
    artifact_types: dict[str, ArtifactType] = {}
    phases: dict[str, Phase] = {}
    inbox_categories: dict[str, InboxCategory] = {}
    skills: dict[str, Skill] = {}

    def prompt_text(self, kind: FlowAgentKind | ArtifactAgentKind | RouterKind) -> str:
        return (self.root / "agents" / kind.prompt).read_text(encoding="utf-8")


def _load_yaml_dir(root: Path, folder: str) -> list[tuple[Path, Any]]:
    return [
        (path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted((root / folder).glob("*.yaml"))
    ]


def _parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"skill {path.name} has no frontmatter")
    front, body = text[4:].split("\n---\n", 1)
    return Skill.model_validate({**yaml.safe_load(front), "body": body.strip()})


def _unique[T](items: list[T], key: str, what: str) -> dict[str, T]:
    out: dict[str, T] = {}
    for item in items:
        name = getattr(item, key)
        if name in out:
            raise ValueError(f"duplicate {what} {name!r}")
        out[name] = item
    return out


def load_registry(root: Path) -> Registry:
    """Load and cross-validate every declaration. Any inconsistency fails loudly at startup and in CI."""
    agents = _unique(
        [_AGENT_KIND.validate_python(data) for _, data in _load_yaml_dir(root, "agents")],
        "id",
        "agent kind",
    )
    artifact_types = _unique(
        [ArtifactType.model_validate(d) for _, d in _load_yaml_dir(root, "artifacts")],
        "id",
        "artifact type",
    )
    phases = _unique(
        [Phase.model_validate(d) for _, d in _load_yaml_dir(root, "phases")], "id", "phase"
    )
    categories = _unique(
        [InboxCategory.model_validate(d) for _, d in _load_yaml_dir(root, "inbox")],
        "id",
        "inbox category",
    )
    skills = _unique(
        [_parse_skill(p) for p in sorted((root / "skills").glob("*.md"))], "name", "skill"
    )
    registry = Registry(
        root=root,
        agents=agents,
        artifact_types=artifact_types,
        phases=phases,
        inbox_categories=categories,
        skills=skills,
    )
    _cross_validate(registry)
    return registry


def _cross_validate(registry: Registry) -> None:
    problems: list[str] = []
    writers: dict[str, str] = {}
    for kind in registry.agents.values():
        if not (registry.root / "agents" / kind.prompt).is_file():
            problems.append(f"{kind.id}: missing prompt {kind.prompt}")
        if registry.phases and kind.phase not in registry.phases:
            problems.append(f"{kind.id}: unknown phase {kind.phase}")
        problems.extend(
            f"{kind.id}: unknown inbox category {c}"
            for c in kind.inbox_categories
            if c not in registry.inbox_categories
        )
        if isinstance(kind, ArtifactAgentKind):
            problems.extend(
                f"{kind.id}: unknown artifact type {o.artifact_type}"
                for o in kind.outputs
                if o.artifact_type not in registry.artifact_types
            )
        for path in kind.output_paths:
            if path in writers:
                problems.append(f"{path} is written by both {writers[path]} and {kind.id}")
            writers[path] = kind.id
    for skill in registry.skills.values():
        problems.extend(
            f"skill {skill.name}: unknown agent kind {k}"
            for k in skill.agent_kinds
            if k not in registry.agents
        )
        if registry.phases and skill.phase not in registry.phases:
            problems.append(f"skill {skill.name}: unknown phase {skill.phase}")
    problems.extend(_artifact_type_problems(registry))
    if problems:
        raise ValueError("registry is inconsistent: " + "; ".join(problems))


def _artifact_type_problems(registry: Registry) -> list[str]:
    problems: list[str] = []
    known_checks = check_names()
    for artifact_type in registry.artifact_types.values():
        if artifact_type.schema_ref not in BUILTIN_SCHEMAS:
            problems.append(
                f"artifact type {artifact_type.id}: unknown schema {artifact_type.schema_ref}"
            )
        problems.extend(
            f"artifact type {artifact_type.id}: unknown check {c}"
            for c in (*artifact_type.write_checks, *artifact_type.gate_checks)
            if c not in known_checks
        )
    for category in registry.inbox_categories.values():
        if registry.phases and category.phase not in registry.phases:
            problems.append(f"inbox category {category.id}: unknown phase {category.phase}")
    return problems
