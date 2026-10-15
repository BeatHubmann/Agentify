"""The per-role model policy (A§17.4): which EU model each agent role uses, at which prices.

Validated at load, so a misconfiguration stops startup instead of reaching a provider: only EU
inference profiles of the allowed Claude families, region `eu-central-1` (OD-4), every role the
registry uses covered, every model priced."""

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

REGION = "eu-central-1"
SHIPPED_POLICY = Path(__file__).resolve().parents[4] / "config" / "model-policy.yaml"
# Fable-family models are excluded (A§17.2): they need a retention mode Agentify does not accept.
_EU_PROFILE = re.compile(r"^eu\.anthropic\.claude-(opus|sonnet|haiku)-[0-9a-z-]+$")


class PolicyInvalid(Exception):
    pass


class ModelPrices(BaseModel):
    """USD per million tokens. One token at P USD per million costs P micro-USD."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal

    def cost_microusd(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> int:
        exact = (
            input_tokens * self.input
            + output_tokens * self.output
            + cache_read_tokens * self.cache_read
            + cache_write_tokens * self.cache_write
        )
        return math.ceil(exact)


class _ModelDecl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prices: ModelPrices
    max_output_tokens: int


class _PolicyFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    region: str
    prices: dict[str, str]
    models: dict[str, _ModelDecl]
    roles: dict[str, str]


@dataclass(frozen=True)
class ModelSpec:
    role: str
    model_id: str
    prices: ModelPrices
    max_output_tokens: int


@dataclass(frozen=True)
class ModelPolicy:
    region: str
    specs: dict[str, ModelSpec]

    def for_role(self, role: str) -> ModelSpec:
        return self.specs[role]

    def require(self, roles: set[str]) -> None:
        """Startup check: every role the registry uses has a model."""
        missing = sorted(roles - set(self.specs))
        if missing:
            raise PolicyInvalid(f"no model for roles: {', '.join(missing)}")


def _problems(policy: _PolicyFile, roles: set[str]) -> list[str]:
    found = [] if policy.region == REGION else [f"region must be {REGION}, not {policy.region}"]
    for model_id, decl in policy.models.items():
        if not _EU_PROFILE.match(model_id):
            found.append(f"{model_id} is not an EU inference profile of an allowed Claude family")
        prices = decl.prices
        if min(prices.input, prices.output, prices.cache_read, prices.cache_write) <= 0:
            found.append(f"{model_id}: prices must be positive")
        if decl.max_output_tokens < 1:
            found.append(f"{model_id}: max_output_tokens must be positive")
    for role, model_id in sorted(policy.roles.items()):
        if not _EU_PROFILE.match(model_id):
            found.append(
                f"{role}: {model_id} is not an EU inference profile of an allowed Claude family"
            )
        elif model_id not in policy.models:
            found.append(f"{role}: {model_id} has no prices")
    missing = sorted(roles - set(policy.roles))
    if missing:
        found.append(f"no model for roles: {', '.join(missing)}")
    return found


def load_policy(path: Path, *, roles: set[str]) -> ModelPolicy:
    """The policy at `path`, checked against the roles the registry declares."""
    try:
        parsed = _PolicyFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise PolicyInvalid(f"{path}: {exc}") from exc
    problems = _problems(parsed, roles)
    if problems:
        raise PolicyInvalid("; ".join(problems))
    specs = {
        role: ModelSpec(
            role=role,
            model_id=model_id,
            prices=parsed.models[model_id].prices,
            max_output_tokens=parsed.models[model_id].max_output_tokens,
        )
        for role, model_id in parsed.roles.items()
    }
    return ModelPolicy(region=parsed.region, specs=specs)
