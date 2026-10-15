"""`LedgerModel` (A§17.3, A§18.2): every model call is checked for known credential values,
reserved at its worst-case cost against every budget scope before it is sent, and settled to the
cost of the usage the provider reports."""

import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
from pydantic import TypeAdapter
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from agentify.llm.policy import ModelSpec
from agentify.secretgate import SecretGate
from agentify.store import budget, security_events
from agentify.store.db import Database

_JSON: TypeAdapter[Any] = TypeAdapter(Any)
# Tokens a request may need beyond its serialized size (message framing, tool-schema rendering).
_FRAMING_TOKENS = 1024


class KnownCredentialInRequest(Exception):
    """A registered credential value appeared in an outgoing model request (A§18.2)."""


@dataclass(frozen=True)
class CallContext:
    """Everything a run's model calls are accounted against."""

    db: Database
    gate: SecretGate
    scopes: budget.Scopes
    deployment: str
    run_ceiling: int  # micro-USD, from the agent kind's money budget (OPS-4)


def request_bytes(messages: list[ModelMessage], params: ModelRequestParameters) -> bytes:
    """What the request carries: the conversation and the tool definitions."""
    return _JSON.dump_json([messages, params], fallback=str)


def worst_case_microusd(spec: ModelSpec, request_size: int) -> int:
    """No token is shorter than a byte, so the request's size bounds its input tokens; each is
    priced at the dearer of the input and cache-write rates. Output is bounded by max tokens.

    shortcut: bytes overstate prose tokens about fourfold, so on Opus a request over about 235 KB
    exceeds a 2 USD run ceiling although it costs far less; switch to Bedrock's CountTokens before
    slice 3.3 runs real specifications."""
    prices = spec.prices
    per_input_token = max(prices.input, prices.cache_write)
    exact = (
        request_size + _FRAMING_TOKENS
    ) * per_input_token + spec.max_output_tokens * prices.output
    return math.ceil(exact)


def actual_microusd(spec: ModelSpec, usage: RequestUsage) -> int:
    """`input_tokens` includes cache reads and writes; each is priced at its own rate."""
    uncached = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    return spec.prices.cost_microusd(
        input_tokens=uncached,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )


def _refused_before_inference(exc: BaseException) -> bool:
    """A 4xx answer: the provider rejected the request (access, validation, throttling) without
    running it, which is not billed."""
    return isinstance(exc, ModelHTTPError) and 400 <= exc.status_code < 500


class LedgerModel(WrapperModel):
    def __init__(self, wrapped: Model, *, spec: ModelSpec, context: CallContext) -> None:
        super().__init__(wrapped)
        self.spec = spec
        self.context = context

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        ctx = self.context
        body = request_bytes(messages, model_request_parameters)
        hits = ctx.gate.known_hits(body)
        if hits:
            await security_events.record(
                ctx.db,
                ctx.gate,
                exit="model_request",
                hits=hits,
                location="model.request",
                run_id=ctx.scopes.run_id,
            )
            raise KnownCredentialInRequest(f"{len(hits)} known value(s) in the model request")
        amount = worst_case_microusd(self.spec, len(body))
        reservation = await budget.reserve(
            ctx.db,
            ctx.scopes,
            deployment=ctx.deployment,
            model_id=self.spec.model_id,
            amount=amount,
            run_ceiling=ctx.run_ceiling,
        )
        capped = ModelSettings(**(model_settings or {}))
        capped["max_tokens"] = min(
            capped.get("max_tokens", self.spec.max_output_tokens), self.spec.max_output_tokens
        )
        try:
            response = await self.wrapped.request(messages, capped, model_request_parameters)
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                if _refused_before_inference(exc):
                    await budget.release(ctx.db, reservation)
                else:  # failed or cancelled after acceptance: it may have been billed
                    await budget.settle(ctx.db, reservation, actual=amount)
            raise
        await budget.settle(ctx.db, reservation, actual=actual_microusd(self.spec, response.usage))
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """Not accounted yet: refused, so no call can bypass the ledger by streaming."""
        raise NotImplementedError("streamed model requests are not supported through the ledger")
        yield  # pragma: no cover - makes this an async generator
