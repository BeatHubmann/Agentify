"""Slice 3.1 live check (dev only, never in CI): one real current-state-modeler run on Claude in
Bedrock EU, through the unchanged harness and the ledger, under the operator's development cap
(OD-26: USD 25 in total, USD 2 per run). Run explicitly:

    AGENTIFY_LIVE_PROFILE=agentify-bedrock uv run pytest -m live tests/live -p no:cacheprovider

The cap holds across runs through a spend tally outside the repository.

Plumbing-only override (operator decision 2026-10-15, while Claude access is pending): set
AGENTIFY_LIVE_MODEL to another EU in-region Bedrock model id and AGENTIFY_LIVE_PRICES to its
"input,output" USD per million tokens. The test then builds its own one-model policy; the shipped
policy and its Claude-only validation are untouched, and the result is plumbing evidence only."""

import json
import os
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from agentify.agents.catalog import build_agent
from agentify.harness import execute, runs
from agentify.harness.prompts import compose_prompt
from agentify.llm import BedrockSource
from agentify.llm.model import actual_microusd
from agentify.llm.policy import SHIPPED_POLICY, ModelPolicy, ModelPrices, ModelSpec, load_policy
from agentify.llm.sources import profile_session
from agentify.registry import Registry
from agentify.store import budget
from agentify.store.db import Database
from agentify.store.workspace import Workspace
from tests.conftest import Infra
from tests.orchestration.test_run_exit_paths import head_flow

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.live]
DEV_CAP_MICROUSD = 25_000_000
TALLY = Path.home() / ".config" / "agentify" / "dev-spend.json"
SPEC = (
    Path(__file__).resolve().parents[3] / "evals" / "synthetic" / "minimal-linear-4" / "spec.md"
).read_text()
DEPLOYMENT = "dev-live"


def spent_so_far() -> int:
    return int(json.loads(TALLY.read_text())["spent_microusd"]) if TALLY.exists() else 0


def record_spend(run_id: str, amount: int) -> None:
    TALLY.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(TALLY.read_text()) if TALLY.exists() else {"spent_microusd": 0, "runs": []}
    data["spent_microusd"] += amount
    data["runs"].append(
        {
            "run_id": run_id,
            "microusd": amount,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    TALLY.write_text(json.dumps(data, indent=2))


def live_policy(role: str) -> ModelPolicy:
    model = os.environ.get("AGENTIFY_LIVE_MODEL")
    if not model:
        return load_policy(SHIPPED_POLICY, roles={role})
    raw_in, raw_out = os.environ["AGENTIFY_LIVE_PRICES"].split(",")
    prices = ModelPrices(
        input=Decimal(raw_in),
        output=Decimal(raw_out),
        cache_read=Decimal(raw_in),
        cache_write=Decimal(raw_in),
    )
    spec = ModelSpec(role=role, model_id=model, prices=prices, max_output_tokens=16_000)
    return ModelPolicy(region="eu-central-1", specs={role: spec})


class Recording(WrapperModel):
    """Keeps Bedrock's reported usage per response: the oracle the ledger is checked against."""

    def __init__(self, wrapped: Model, responses: list[ModelResponse]) -> None:
        super().__init__(wrapped)
        self.responses = responses

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        self.responses.append(response)
        return response


class RecordingBedrock:
    def __init__(self, bedrock: BedrockSource) -> None:
        self.bedrock, self.responses = bedrock, list[ModelResponse]()
        self.spec: ModelSpec | None = None

    def model(self, spec: ModelSpec) -> Model:
        self.spec = spec
        return Recording(self.bedrock.model(spec), self.responses)


@pytest.fixture
async def admin(infra: Infra) -> AsyncIterator[Database]:
    db = Database(infra.admin_conninfo)
    await db.open()
    try:
        yield db
    finally:
        await db.close()


async def test_one_real_current_state_run_commits_under_the_ledger(
    workspace: Workspace, registry: Registry, process_id: str, admin: Database
) -> None:
    profile = os.environ.get("AGENTIFY_LIVE_PROFILE")
    assert profile, "set AGENTIFY_LIVE_PROFILE to the development AWS profile (OD-26)"
    remaining = DEV_CAP_MICROUSD - spent_so_far()
    assert remaining > 0, f"the development cap is spent ({TALLY})"
    await budget.set_limit(admin, "deployment", DEPLOYMENT, remaining)
    kind = registry.agents["current-state-modeler"]
    run_id = await runs.admit(
        workspace,
        process_id=process_id,
        kind=kind,
        mode="from_scratch",
        initiated_by="operator",
        entry_point="test",
        instructions=f"Model the current-state process described here:\n\n{SPEC}",
    )
    claimed = await runs.claim(
        workspace, queue=kind.id, owner="live", lease_seconds=60, run_id=run_id
    )
    assert claimed is not None
    prompt = await compose_prompt(
        workspace,
        process_id=process_id,
        base_revision_id=claimed.base_revision_id,
        kind=kind,
        instructions=claimed.instructions,
        output_language="en",
    )
    source = RecordingBedrock(BedrockSource(profile_session(profile)))
    started = time.monotonic()
    try:
        final = await execute.execute(
            workspace,
            claimed,
            kind,
            agent=build_agent(kind, registry),
            source=source,
            prompt=prompt,
            config=execute.HarnessConfig(
                deployment=DEPLOYMENT,
                lease_seconds=60,
                renew_every=10,
                policy=live_policy(kind.model_role),
            ),
            registry=registry,
        )
    finally:
        settled = await budget.spent(workspace.db, "run", run_id)
        record_spend(run_id, settled)
    state = await runs.run_state(workspace, run_id)
    flow = await head_flow(workspace, process_id)
    assert source.spec is not None
    expected = sum(actual_microusd(source.spec, r.usage) for r in source.responses)
    report: dict[str, Any] = {
        "final": final,
        "outcome": state["outcome"],
        "detail": str(state["outcome_detail_scrubbed"])[:300],
        "model": source.spec.model_id,
        "calls": len(source.responses),
        "settled_microusd": settled,
        "usage_priced_microusd": expected,
        "seconds": round(time.monotonic() - started, 1),
        "nodes": sorted(n.id for n in flow.nodes) if flow else [],
        "total_dev_spend_microusd": spent_so_far(),
    }
    print("LIVE_REPORT", json.dumps(report))
    assert (final, settled) == ("committed", expected), report
