"""Blob storage retries transient errors, bounded (A§10.2). INV-8.SF: every put attempt sends exactly
the gated bytes; nothing is diverted elsewhere (A§18.3). INV-10.SF: a read that outlasts the retries
raises `BlobUnavailable`, never "absent"."""

import uuid
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from agentify.store.blobs import ATTEMPTS, BlobStore, BlobUnavailable
from tests.conftest import BUCKET, Infra, s3_client
from tests.support.gates import make_gate

pytestmark = [pytest.mark.anyio, pytest.mark.integration]
CONTENT = b"Receive the invoice, classify it, compute the fee and approve it."


def unavailable(operation: str = "PutObject") -> Exception:
    return ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "busy"}}, operation)


def denied(operation: str = "PutObject") -> Exception:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, operation)


class FlakyClient:
    """The real S3 client, except that the next puts or gets raise the queued errors."""

    def __init__(
        self, real: Any, failures: list[Exception], get_failures: list[Exception] | None = None
    ) -> None:
        self._real, self._failures = real, failures
        self.get_failures = get_failures if get_failures is not None else []
        self.sent: list[bytes] = []
        self.gets = 0

    def put_object(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs["Body"])
        if self._failures:
            raise self._failures.pop(0)
        return self._real.put_object(**kwargs)

    def get_object(self, **kwargs: Any) -> Any:
        self.gets += 1
        if self.get_failures:
            raise self.get_failures.pop(0)
        return self._real.get_object(**kwargs)

    def head_object(self, **kwargs: Any) -> Any:
        self.gets += 1
        if self.get_failures:
            raise self.get_failures.pop(0)
        return self._real.head_object(**kwargs)

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
        [CONTENT] * ATTEMPTS,
        0,
    )


async def test_a_permanent_error_is_not_retried(infra: Infra) -> None:
    outcome, client, engagement = await put_through(infra, [denied()])
    assert (outcome, client.sent, stored_keys(infra, engagement)) == ("ClientError", [CONTENT], 0)


async def get_through(infra: Infra, failures: list[Exception]) -> tuple[str, int]:
    engagement = f"eng-{uuid.uuid4().hex[:8]}"
    sha = await BlobStore(s3_client(infra), BUCKET).put(
        engagement, make_gate().check_bytes(CONTENT, "test")
    )
    client = FlakyClient(s3_client(infra), [], failures)
    try:
        data = await BlobStore(client, BUCKET).get(engagement, sha)
    except (BlobUnavailable, ClientError) as exc:
        return type(exc).__name__, client.gets
    return ("read" if data == CONTENT else "wrong bytes"), client.gets


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        ([unavailable("GetObject")], ("read", 2)),
        ([EndpointConnectionError(endpoint_url="http://storage.invalid")], ("read", 2)),
        ([unavailable("GetObject") for _ in range(5)], ("BlobUnavailable", ATTEMPTS)),
        ([denied("GetObject")], ("ClientError", 1)),
    ],
    ids=["one-off", "connection-error", "persistent", "permanent"],
)
async def test_a_read_retries_transient_errors_and_reports_persistent_ones_as_unavailable(
    infra: Infra, failures: list[Exception], expected: tuple[str, int]
) -> None:
    assert await get_through(infra, failures) == expected


@pytest.mark.parametrize(
    ("stored", "failures", "expected"),
    [
        (True, [unavailable("HeadObject")], ("True", 2)),
        (False, [], ("False", 1)),
        (True, [unavailable("HeadObject") for _ in range(5)], ("BlobUnavailable", ATTEMPTS)),
        (True, [denied("HeadObject")], ("ClientError", 1)),
    ],
    ids=["one-off", "missing", "persistent", "permanent"],
)
async def test_an_existence_check_retries_transient_errors_like_a_read(
    infra: Infra, stored: bool, failures: list[Exception], expected: tuple[str, int]
) -> None:
    """The commit checks every staged blob exists; one busy answer must not fail the commit."""
    engagement = f"eng-{uuid.uuid4().hex[:8]}"
    gated = make_gate().check_bytes(CONTENT, "test")
    sha = await BlobStore(s3_client(infra), BUCKET).put(engagement, gated) if stored else "0" * 64
    client = FlakyClient(s3_client(infra), [], failures)
    try:
        outcome = str(await BlobStore(client, BUCKET).exists(engagement, sha))
    except (BlobUnavailable, ClientError) as exc:
        outcome = type(exc).__name__
    assert (outcome, client.gets) == expected
