"""The router's tools (A§9.1): read, load skills, ask, dispatch. No write tools exist here.

A skill is text; it cannot grant a power the router does not have. The router may only dispatch
the agent kinds named by a skill it loaded in this turn."""

import json
from dataclasses import dataclass, field

from pydantic_ai import Agent, RunContext

from agentify.agents.flow_tools import FRAMEWORK_RETRIES
from agentify.agents.inbox_tools import register_inbox_tools
from agentify.domain.envelope import INPUTS_PREFIX, Corrupt, decode_artifact
from agentify.harness.ports import DispatchPort, InboxPort, StagingPort, correctable
from agentify.registry import Registry


@dataclass
class RouterDeps:
    workspace: StagingPort
    dispatcher: DispatchPort
    registry: Registry
    inbox: InboxPort | None = None
    inbox_categories: tuple[str, ...] = ()
    loaded_skills: set[str] = field(default_factory=set)


def skill_listing(registry: Registry) -> str:
    return "\n".join(
        f"- {s.name} ({s.phase}): {s.description}"
        for s in sorted(registry.skills.values(), key=lambda s: s.name)
    )


def register_router_tools(agent: Agent[RouterDeps, str]) -> None:
    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def list_workspace(ctx: RunContext[RouterDeps], prefix: str = "") -> list[str]:
        """List artifact paths in the process workspace."""
        return await ctx.deps.workspace.list_paths(prefix)

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def read_workspace(ctx: RunContext[RouterDeps], path: str) -> str:
        """Read one artifact or input."""
        data = await ctx.deps.workspace.read(path)
        if data is None:
            raise correctable("not_found", f"{path} does not exist.", (path,))
        if not path.startswith(INPUTS_PREFIX):
            envelope = decode_artifact(data)
            if isinstance(envelope, Corrupt):
                raise correctable(
                    "read_failure", f"{path} is unreadable: {envelope.reason}", (path,)
                )
        return data.decode("utf-8", errors="replace")

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def load_skill(ctx: RunContext[RouterDeps], name: str) -> str:
        """Load a skill's full playbook."""
        skill = ctx.deps.registry.skills.get(name)
        if skill is None:
            raise correctable(
                "unknown_skill", f"No skill {name!r}.", hint=skill_listing(ctx.deps.registry)
            )
        ctx.deps.loaded_skills.add(name)
        return skill.body

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def dispatch_subagent(
        ctx: RunContext[RouterDeps], kind: str, instructions: str, mode: str = "incremental"
    ) -> str:
        """Start a subagent run of a kind named by a loaded skill. Returns at once."""
        deps = ctx.deps
        allowed = {k for name in deps.loaded_skills for k in deps.registry.skills[name].agent_kinds}
        if kind not in allowed:
            raise correctable(
                "kind_not_in_loaded_skills",
                f"{kind!r} is not named by a loaded skill.",
                hint=f"Loaded: {sorted(deps.loaded_skills)}; dispatchable: {sorted(allowed)}",
            )
        result = await deps.dispatcher.dispatch(kind, mode, instructions)
        return json.dumps(
            {"status": result.status, "run_id": result.run_id, "detail": result.detail}
        )


def build_router(instructions: str, registry: Registry) -> Agent[RouterDeps, str]:
    agent: Agent[RouterDeps, str] = Agent(
        deps_type=RouterDeps,
        output_type=str,
        instructions=f"{instructions}\n\nRegistered skills:\n{skill_listing(registry)}",
    )
    register_router_tools(agent)
    register_inbox_tools(agent)
    return agent
