"""Where a role's base model comes from (A§17.1). Production uses Bedrock; tests use scripted
models through the same `model_for`, so the ledger and the gate always run (BC-B.M4)."""

from typing import TYPE_CHECKING, Any, Protocol

from botocore.config import Config
from pydantic_ai.models import Model
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider

from agentify.llm.policy import REGION, ModelSpec

if TYPE_CHECKING:
    import boto3


# The stubs predate `ignore_configured_endpoint_urls`; botocore supports it, so it is passed untyped.
_CLIENT_OPTIONS: dict[str, Any] = {
    "ignore_configured_endpoint_urls": True,
    "retries": {"max_attempts": 3},
}


class ModelSource(Protocol):
    def model(self, spec: ModelSpec) -> Model: ...


def profile_session(profile: str) -> "boto3.Session":
    """Development credentials (OD-26): the named profile only; `AWS_PROFILE` and environment
    credentials do not take part."""
    import boto3  # noqa: PLC0415 - only development entrypoints build sessions this way

    return boto3.Session(profile_name=profile)


class BedrockSource:
    """A Bedrock Runtime client built explicitly from `session` for region `eu-central-1`.

    Credentials come only from the session the caller built (a named profile in development, the
    workload identity in production, A§17.2); endpoints configured through the environment or the
    shared config files are ignored, so neither can redirect model traffic (HZ-4)."""

    def __init__(self, session: "boto3.Session") -> None:
        self._client = session.client(
            "bedrock-runtime",
            region_name=REGION,
            endpoint_url=f"https://bedrock-runtime.{REGION}.amazonaws.com",
            config=Config(**_CLIENT_OPTIONS),
        )

    @property
    def client(self) -> object:
        return self._client

    def model(self, spec: ModelSpec) -> Model:
        return BedrockConverseModel(
            spec.model_id, provider=BedrockProvider(bedrock_client=self._client)
        )
