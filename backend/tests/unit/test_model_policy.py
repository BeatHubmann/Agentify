"""The per-role model policy (A§17.4): only EU inference profiles, every role covered, exact prices."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from agentify.llm.policy import PolicyInvalid, load_policy
from agentify.registry import load_registry

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "config" / "model-policy.yaml"


def roles() -> set[str]:
    return {kind.model_role for kind in load_registry(ROOT / "registry").agents.values()}


def test_the_shipped_policy_covers_every_registry_role_with_an_eu_profile() -> None:
    policy = load_policy(POLICY, roles=roles())
    specs = {role: policy.for_role(role) for role in roles()}
    assert (
        policy.region,
        sorted(roles()),
        {spec.model_id for spec in specs.values()},
        specs["current-state-modeler"].prices.cost_microusd(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
    ) == (
        "eu-central-1",
        ["current-state-modeler", "router", "spec-section", "synthesis"],
        {"eu.anthropic.claude-opus-5-5"},
        26_400_000,  # 4.40 + 22.00 USD in micro-USD
    )


def write(tmp_path: Path, **changes: object) -> Path:
    data = yaml.safe_load(POLICY.read_text())
    for dotted, value in changes.items():
        node = data
        *path, last = dotted.split("__")
        for key in path:
            node = node[key]
        if value is None:
            del node[last]
        else:
            node[last] = value
    target = tmp_path / "policy.yaml"
    target.write_text(yaml.safe_dump(data))
    return target


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"roles__router": "global.anthropic.claude-opus-5-5"}, "not an EU inference profile"),
        ({"roles__router": "anthropic.claude-opus-5-5"}, "not an EU inference profile"),
        ({"roles__router": "eu.anthropic.claude-fable-5-1"}, "not an EU inference profile"),
        ({"roles__router": "eu.anthropic.claude-sonnet-4-6"}, "has no prices"),
        ({"region": "us-east-1"}, "region must be eu-central-1"),
        ({"roles__synthesis": None}, "no model for roles: synthesis"),
        ({"models__eu.anthropic.claude-opus-5-5__prices__output": "-1"}, "prices must be positive"),
    ],
    ids=["global", "bare-name", "fable", "unpriced", "region", "uncovered-role", "negative-price"],
)
def test_an_invalid_policy_is_refused_at_load(
    tmp_path: Path, changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(PolicyInvalid) as refused:
        load_policy(write(tmp_path, **changes), roles=roles())
    assert reason in str(refused.value)


def test_cost_rounds_up_to_the_next_micro_dollar() -> None:
    prices = load_policy(POLICY, roles=roles()).for_role("router").prices
    assert (
        prices.cost_microusd(
            input_tokens=1, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0
        ),
        prices.cost_microusd(
            input_tokens=0, output_tokens=0, cache_read_tokens=10, cache_write_tokens=0
        ),
        prices.input,
    ) == (5, 3, Decimal("4.40"))  # 4.4 and 2.2 micro-USD, rounded up
