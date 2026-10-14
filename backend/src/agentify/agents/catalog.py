"""Builds the agent for a registry agent kind from its declared toolset (no per-kind code paths)."""

from typing import Any

from pydantic_ai import Agent

from agentify.agents.artifact_tools import build_artifact_agent
from agentify.agents.flow_tools import build_modeler
from agentify.agents.inbox_tools import register_inbox_tools
from agentify.agents.router_tools import build_router
from agentify.registry import AgentKind, ArtifactAgentKind, FlowAgentKind, Registry


def build_agent(kind: AgentKind, registry: Registry) -> Agent[Any, str]:
    prompt = registry.prompt_text(kind)
    if isinstance(kind, FlowAgentKind):
        agent = build_modeler(prompt)
        register_inbox_tools(agent)
        return agent
    if isinstance(kind, ArtifactAgentKind):
        return build_artifact_agent(prompt)
    return build_router(prompt, registry)
