"""Spike: verify Pydantic AI semantics the Agentify error contract and harness depend on.

Each check prints OBSERVED behaviour; nothing here is product code.
"""

import asyncio
import json

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import ToolFailed, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, RetryPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits


def tool_results(messages: list[ModelMessage]) -> list[str]:
    out = []
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, (ToolReturnPart, RetryPromptPart)):
                out.append(type(p).__name__)
    return out


def always_call(tool_name: str, n_calls_then_text: int | None = None):
    count = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        count["n"] += 1
        if n_calls_then_text is not None and count["n"] > n_calls_then_text:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(tool_name, {"x": count["n"]})])

    return fn, count


async def check_model_retry_budget() -> None:
    fn, count = always_call("mutate")
    agent = Agent(FunctionModel(fn))

    @agent.tool_plain(retries=2)
    def mutate(x: int) -> str:
        raise ModelRetry(f"correctable error #{x}")

    try:
        await agent.run("go")
        print("1 ModelRetry budget: run SUCCEEDED (unexpected)")
    except UnexpectedModelBehavior as e:
        print(f"1 ModelRetry budget (retries=2): {type(e).__name__}: {e} | model calls={count['n']}")


async def check_model_retry_resets_on_success() -> None:
    seq = iter(["fail", "ok", "fail", "ok", "fail", "ok", "text"])
    calls = {"n": 0}

    def fn(messages, info):
        calls["n"] += 1
        step = next(seq)
        if step == "text":
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("mutate", {"mode": step})])

    agent = Agent(FunctionModel(fn))

    @agent.tool_plain(retries=1)
    def mutate(mode: str) -> str:
        if mode == "fail":
            raise ModelRetry("bad")
        return "ok"

    try:
        r = await agent.run("go")
        print(f"2 retry counter resets after success? run ok, output={r.output!r}, model calls={calls['n']}")
    except UnexpectedModelBehavior as e:
        print(f"2 retry counter is cumulative per run: {e} | model calls={calls['n']}")


async def check_tool_failed_not_counted() -> None:
    fn, count = always_call("mutate", n_calls_then_text=6)
    agent = Agent(FunctionModel(fn))

    @agent.tool_plain(retries=1)
    def mutate(x: int) -> str:
        raise ToolFailed(f"terminal #{x}")

    r = await agent.run("go")
    print(f"3 ToolFailed x6 with retries=1: run ok={r.output!r}; parts={tool_results(r.all_messages())[:3]}...")


async def check_plain_exception() -> None:
    fn, _ = always_call("mutate")
    agent = Agent(FunctionModel(fn))

    @agent.tool_plain
    def mutate(x: int) -> str:
        raise KeyError("boom")

    try:
        await agent.run("go")
    except Exception as e:  # noqa: BLE001 - spike observes the propagated type
        print(f"4 plain exception in tool propagates as: {type(e).__name__}: {e}")


async def check_request_limit() -> None:
    fn, count = always_call("ok_tool")
    agent = Agent(FunctionModel(fn))

    @agent.tool_plain
    def ok_tool(x: int) -> str:
        return "fine"

    try:
        await agent.run("go", usage_limits=UsageLimits(request_limit=3))
    except UsageLimitExceeded as e:
        print(f"5 request_limit=3: {type(e).__name__}: {e} | model calls={count['n']}")


async def check_iter_and_external_cancel() -> None:
    fn, _ = always_call("slow")
    agent = Agent(FunctionModel(fn))

    @agent.tool_plain
    async def slow(x: int) -> str:
        await asyncio.sleep(0.05)
        return "ok"

    steps: list[str] = []

    async def run():
        async with agent.iter("go") as agent_run:
            async for node in agent_run:
                steps.append(type(node).__name__)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print(f"6 external task.cancel(): CancelledError propagated after {len(steps)} nodes; kinds={sorted(set(steps))}")


async def check_parallel_tool_calls() -> None:
    active = {"now": 0, "max": 0}

    def fn(messages, info):
        if len(messages) > 1:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart("edit", {"i": i}) for i in range(4)])

    agent = Agent(FunctionModel(fn))

    @agent.tool_plain
    async def edit(i: int) -> str:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.05)
        active["now"] -= 1
        return "ok"

    await agent.run("go")
    print(f"7 four tool calls in one response: max concurrent executions={active['max']}")


async def check_tool_schema_visible() -> None:
    seen = {}

    def fn(messages, info: AgentInfo):
        seen["tools"] = [(t.name, json.dumps(t.parameters_json_schema)[:120]) for t in info.function_tools]
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(fn))

    @agent.tool_plain
    def add_node(node_id: str, label: str) -> str:
        """Add a node to the flow."""
        return "ok"

    await agent.run("go")
    print(f"8 scripted model sees tool schema: {seen['tools']}")


async def main() -> None:
    for check in (
        check_model_retry_budget,
        check_model_retry_resets_on_success,
        check_tool_failed_not_counted,
        check_plain_exception,
        check_request_limit,
        check_iter_and_external_cancel,
        check_parallel_tool_calls,
        check_tool_schema_visible,
    ):
        try:
            await check()
        except Exception as e:  # noqa: BLE001
            print(f"!! {check.__name__} crashed: {type(e).__name__}: {e}")


asyncio.run(main())
