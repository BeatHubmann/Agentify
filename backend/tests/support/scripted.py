"""Scripted models: the real Pydantic AI framework runs; only the model's answers are scripted."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

Call = tuple[str, dict[str, Any]]
Hook = Callable[[], Awaitable[None]]


@dataclass
class Step:
    calls: Sequence[Call] = ()
    text: str | None = None
    before: Hook | None = None
    finish_reason: str | None = None


@dataclass
class Script:
    steps: list[Step]
    seen: list[list[ModelMessage]] = field(default_factory=list)

    def model(self) -> FunctionModel:
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.seen.append(list(messages))
            step = self.steps.pop(0) if self.steps else Step(text="done")
            if step.before is not None:
                await step.before()
            parts: list[Any] = [ToolCallPart(name, args) for name, args in step.calls]
            if step.text is not None or not parts:
                parts.append(TextPart(step.text or "done"))
            return ModelResponse(parts=parts, finish_reason=step.finish_reason)  # type: ignore[arg-type]

        return FunctionModel(respond)


def golden_calls() -> list[Step]:
    """The canonical four-step case, one tool call per step (evals/synthetic/minimal-linear-4)."""
    nodes = [
        ("receive", "step", "external"),
        ("classify", "task", "ai"),
        ("compute-fee", "step", "deterministic"),
        ("approve", "step", "human"),
    ]
    steps = [
        Step(calls=[("add_node", {"id": i, "label": i, "kind": k, "execution": x})])
        for i, k, x in nodes
    ]
    for a, b in zip([n[0] for n in nodes], [n[0] for n in nodes][1:], strict=False):
        steps.append(Step(calls=[("add_edge", {"id": f"e-{a}-{b}", "source": a, "target": b})]))
    steps.append(Step(text="The current-state flow is complete."))
    return steps
