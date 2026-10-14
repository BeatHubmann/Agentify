"""Builds the agent for a registry agent kind from its declared toolset (no per-kind code paths)."""

from collections.abc import Callable

from pydantic_ai import Agent

from agentify.agents.flow_tools import FlowToolDeps, build_modeler
from agentify.registry import AgentKind, Registry

_TOOLSETS: dict[str, Callable[[str], Agent[FlowToolDeps, str]]] = {"flow_authoring": build_modeler}


def build_agent(kind: AgentKind, registry: Registry) -> Agent[FlowToolDeps, str]:
    return _TOOLSETS[kind.toolset](registry.prompt_text(kind))
