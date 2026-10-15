"""Inbox tools shared by every agent kind (A§6.6). Agents can ask and read; they cannot answer."""

from typing import Any, Protocol

from pydantic_ai import Agent, RunContext

from agentify.agents.flow_tools import FRAMEWORK_RETRIES
from agentify.harness.ports import InboxPort, correctable


class HasInbox(Protocol):
    inbox: InboxPort | None
    inbox_categories: tuple[str, ...]


def register_inbox_tools(agent: Agent[Any, str]) -> None:
    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def inbox_ask(
        ctx: RunContext[HasInbox], category: str, question: str, options: list[str] | None = None
    ) -> str:
        """Ask the user a question through the inbox. Never ask questions in chat text."""
        deps = ctx.deps
        if deps.inbox is None or category not in deps.inbox_categories:
            raise correctable(
                "inbox_category_not_allowed", f"{category!r} is not one of {deps.inbox_categories}."
            )
        return await deps.inbox.ask(category, question, options or [])

    @agent.tool(retries=FRAMEWORK_RETRIES)
    async def inbox_read(ctx: RunContext[HasInbox], status: str | None = None) -> str:
        """Read the inbox (optionally only open, answered or acknowledged items)."""
        if ctx.deps.inbox is None:
            return "[]"
        return await ctx.deps.inbox.read(status)
