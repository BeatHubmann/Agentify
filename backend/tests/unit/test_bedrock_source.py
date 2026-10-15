"""HZ-4 (§6.8, A§17.1): the Bedrock client is built only from what the caller supplies. Hostile
environment variables can change neither the credentials, the region nor the endpoint."""

import json
from typing import Any

import boto3
import pytest
from botocore.awsrequest import AWSResponse

from agentify.llm import BedrockSource
from agentify.llm.sources import profile_session

# Built at runtime so the repository holds no credential-shaped literal.
EXPLICIT_KEY = "AKIA" + "EXPLICITSUPPLIED"
HOSTILE_KEY = "AKIA" + "HOSTILEENVIRONMT"
HOSTILE_ENV = {
    "AWS_ACCESS_KEY_ID": HOSTILE_KEY,
    "AWS_SECRET_ACCESS_KEY": "hostile-secret-" + "x" * 25,
    "AWS_SESSION_TOKEN": "hostile-session-token",
    "AWS_REGION": "us-east-1",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_ENDPOINT_URL": "https://attacker.invalid",
    "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "https://attacker.invalid",
}


class _Raw:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, **_: object) -> Any:
        yield self._body


def recorded_request(session: boto3.Session) -> tuple[str, str]:
    client: Any = BedrockSource(session).client
    seen: dict[str, str] = {}

    def record(request: Any, **_: object) -> AWSResponse:
        seen["url"] = request.url
        seen["authorization"] = request.headers["Authorization"].decode()
        body = json.dumps(
            {
                "output": {"message": {"role": "assistant", "content": []}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        )
        return AWSResponse(request.url, 200, {}, _Raw(body.encode()))  # type: ignore[arg-type]

    client.meta.events.register("before-send.bedrock-runtime.Converse", record)
    client.converse(modelId="eu.anthropic.claude-opus-5-5", messages=[])
    return seen["url"], seen["authorization"]


def hostile(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in HOSTILE_ENV.items():
        monkeypatch.setenv(name, value)


def test_the_client_uses_only_the_supplied_identity_region_and_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile(monkeypatch)
    session = boto3.Session(
        aws_access_key_id=EXPLICIT_KEY,
        aws_secret_access_key="explicit-secret-" + "y" * 24,
        region_name="eu-west-1",  # the session's own region is not used either
    )
    url, authorization = recorded_request(session)
    assert (
        url.startswith("https://bedrock-runtime.eu-central-1.amazonaws.com/"),
        f"Credential={EXPLICIT_KEY}/" in authorization,
        "/eu-central-1/bedrock/" in authorization,
        HOSTILE_KEY in authorization,
    ) == (True, True, True, False)


def test_a_named_profile_wins_over_aws_profile_and_environment_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text(
        f"[agentify-bedrock]\naws_access_key_id = {EXPLICIT_KEY}\n"
        f"aws_secret_access_key = explicit-secret-{'y' * 24}\n"
        f"[attacker]\naws_access_key_id = {HOSTILE_KEY}\n"
        f"aws_secret_access_key = hostile-secret-{'x' * 25}\n"
    )
    hostile(monkeypatch)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "missing-config"))
    monkeypatch.setenv("AWS_PROFILE", "attacker")
    url, authorization = recorded_request(profile_session("agentify-bedrock"))
    assert (url.split("/")[2], f"Credential={EXPLICIT_KEY}/" in authorization) == (
        "bedrock-runtime.eu-central-1.amazonaws.com",
        True,
    )
