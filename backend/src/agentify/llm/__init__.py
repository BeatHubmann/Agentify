"""Provider abstraction, per-role model policy and budget ledger (A§17). `model_for` is the only
way to get a model (§6.8): it wraps every base model in the `LedgerModel`."""

from agentify.llm.model import CallContext, KnownCredentialInRequest, LedgerModel
from agentify.llm.policy import ModelPolicy, PolicyInvalid, load_policy
from agentify.llm.sources import BedrockSource, ModelSource

__all__ = [
    "BedrockSource",
    "CallContext",
    "KnownCredentialInRequest",
    "LedgerModel",
    "ModelPolicy",
    "ModelSource",
    "PolicyInvalid",
    "load_policy",
    "model_for",
]


def model_for(
    role: str, context: CallContext, *, policy: ModelPolicy, source: ModelSource
) -> LedgerModel:
    spec = policy.for_role(role)
    return LedgerModel(source.model(spec), spec=spec, context=context)
