"""LedgerModel without I/O: worst-case and actual cost arithmetic, and no unaccounted path."""

from typing import Any

import pytest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from agentify.llm.model import LedgerModel, actual_microusd, worst_case_microusd
from agentify.llm.policy import SHIPPED_POLICY, load_policy

SPEC = load_policy(SHIPPED_POLICY, roles=set()).for_role("current-state-modeler")


def test_the_worst_case_bounds_input_by_size_at_the_dearer_input_rate_and_output_by_max_tokens() -> (
    None
):
    # (3000 bytes + 1024 framing) x 5.50 (cache write > input 4.40) + 32000 x 22.00, in micro-USD
    assert worst_case_microusd(SPEC, 3000) == 4024 * 5.5 + 32000 * 22


def test_cached_tokens_are_priced_at_their_own_rates_and_not_twice() -> None:
    usage = RequestUsage(
        input_tokens=10_000, cache_read_tokens=6_000, cache_write_tokens=1_000, output_tokens=500
    )
    # uncached 3000 x 4.40 + read 6000 x 0.22 + write 1000 x 5.50 + out 500 x 22.00
    assert actual_microusd(SPEC, usage) == 13_200 + 1_320 + 5_500 + 11_000


@pytest.mark.anyio
async def test_a_streamed_request_is_refused_so_nothing_bypasses_the_ledger() -> None:
    def never(_messages: list[Any], _info: AgentInfo) -> Any:
        raise AssertionError("the wrapped model must not be reached")

    model = LedgerModel(FunctionModel(never), spec=SPEC, context=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        async with model.request_stream([], None, ModelRequestParameters()):
            pass


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
