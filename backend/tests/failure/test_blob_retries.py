"""INV-8.SF: a blob put retries transient storage errors, bounded, and every attempt sends exactly
the gated bytes; nothing is diverted elsewhere (A§10.2, A§18.3)."""

import uuid
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from agentify.store.blobs import PUT_ATTEMPTS, BlobStore
from tests.conftest import BUCKET, Infra, s3_client
from tests.support.gates import make_gate

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
CONTENT = b"Receive the invoice, classify it, compute the fee and approve it."


def unavailable() -> Exception:
    return ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "busy"}}, "PutObject")


def denied() -> Exception:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject")


class FlakyClient:
    """The real S3 client, except that the first puts raise the given errors."""

    def __init__(self, real: Any, failures: list[Exception]) -> None:
        self._real, self._failures = real, failures
        self.sent: list[bytes] = []

    def put_object(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs["Body"])
        if self._failures:
            raise self._failures.pop(0)
        return self._real.put_object(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


async def put_through(infra: Infra, failures: list[Exception]) -> tuple[str, FlakyClient, str]:
    client = FlakyClient(s3_client(infra), failures)
    engagement = f"eng-{uuid.uuid4().hex[:8]}"
    outcome = "stored"
    try:
        await BlobStore(client, BUCKET).put(engagement, make_gate().check_bytes(CONTENT, "test"))
    except (ClientError, EndpointConnectionError) as exc:
        outcome = type(exc).__name__
    return outcome, client, engagement


def stored_keys(infra: Infra, engagement: str) -> int:
    listed = s3_client(infra).list_objects_v2(Bucket=BUCKET, Prefix=f"{engagement}/")
    return len(listed.get("Contents", []))


@pytest.mark.parametrize(
    "transient",
    [unavailable, lambda: EndpointConnectionError(endpoint_url="http://storage.invalid")],
    ids=["service-unavailable", "connection-error"],
)
async def test_a_transient_failure_is_retried_with_the_same_gated_bytes(
    infra: Infra, transient: Any
) -> None:
    outcome, client, engagement = await put_through(infra, [transient()])
    assert (outcome, client.sent, stored_keys(infra, engagement)) == ("stored", [CONTENT] * 2, 1)


async def test_storage_that_stays_unavailable_fails_after_bounded_attempts(infra: Infra) -> None:
    outcome, client, engagement = await put_through(infra, [unavailable() for _ in range(5)])
    assert (outcome, client.sent, stored_keys(infra, engagement)) == (
        "ClientError",
        [CONTENT] * PUT_ATTEMPTS,
        0,
    )


async def test_a_permanent_error_is_not_retried(infra: Infra) -> None:
    outcome, client, engagement = await put_through(infra, [denied()])
    assert (outcome, client.sent, stored_keys(infra, engagement)) == ("ClientError", [CONTENT], 0)
