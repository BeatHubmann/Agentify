"""Content-addressed, engagement-prefixed blob store on S3 (A§5.1). Blobs are never overwritten."""

from collections.abc import Callable
from typing import TYPE_CHECKING

import anyio
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

from agentify.domain.canonical import sha256_hex
from agentify.secretgate import Scrubbed

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_ALREADY_EXISTS = {"PreconditionFailed", "412"}
_MISSING = {"404", "NoSuchKey", "NotFound"}
_TRANSIENT = {"InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout", "500", "503"}
ATTEMPTS = 3
_BACKOFF_SECONDS = 0.1


class BlobUnavailable(Exception):
    """A read that still failed transiently after the bounded retries: unavailable, never absent."""


def _transient(exc: Exception) -> bool:
    if isinstance(exc, ClientError):
        return _error_code(exc) in _TRANSIENT
    return isinstance(exc, BotoConnectionError | HTTPClientError)


async def _with_retries[T](call: Callable[[], T]) -> T:
    """Run a blocking storage call in a thread; retry transient errors with linear backoff."""
    attempt = 1
    while True:
        try:
            return await anyio.to_thread.run_sync(call)
        except (ClientError, BotoConnectionError, HTTPClientError) as exc:
            if attempt == ATTEMPTS or not _transient(exc):
                raise
        await anyio.sleep(_BACKOFF_SECONDS * attempt)
        attempt += 1


async def _read[T](call: Callable[[], T], sha: str) -> T:
    """A retried read; transient errors that outlast the retries become `BlobUnavailable`."""
    try:
        return await _with_retries(call)
    except (ClientError, BotoConnectionError, HTTPClientError) as exc:
        if _transient(exc):
            raise BlobUnavailable(f"blob {sha} unreadable after {ATTEMPTS} attempts") from exc
        raise


def _error_code(exc: ClientError) -> str | None:
    """The S3 error code of a client error, or None when the response carries none."""
    error = exc.response.get("Error")
    if error is None:
        return None
    return error.get("Code")


class BlobStore:
    def __init__(self, client: "S3Client", bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @staticmethod
    def key(engagement_id: str, sha: str) -> str:
        return f"{engagement_id}/sha256/{sha}"

    async def put(self, engagement_id: str, data: Scrubbed[bytes]) -> str:
        """Store the gated bytes; an existing key is left untouched (conditional put). Transient
        storage errors are retried, bounded, with the same gated bytes (A§18.3); a retry after a
        lost response finds the key present, which counts as stored."""
        sha = sha256_hex(data.value)

        def _put() -> None:
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=self.key(engagement_id, sha),
                    Body=data.value,
                    IfNoneMatch="*",
                )
            except ClientError as exc:
                if _error_code(exc) not in _ALREADY_EXISTS:
                    raise

        await _with_retries(_put)
        return sha

    async def get(self, engagement_id: str, sha: str) -> bytes:
        """The blob's bytes, checked against its hash. Transient errors are retried, bounded; if
        they persist the read raises `BlobUnavailable` (A§10.2)."""

        def _get() -> bytes:
            body = self._client.get_object(Bucket=self._bucket, Key=self.key(engagement_id, sha))[
                "Body"
            ]
            return body.read()

        data = await _read(_get, sha)
        if sha256_hex(data) != sha:
            raise ValueError(f"blob {sha} failed its content check")
        return data

    async def exists(self, engagement_id: str, sha: str) -> bool:
        """Whether the blob is stored; transient errors are retried like a read (A§10.2)."""

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=self.key(engagement_id, sha))
            except ClientError as exc:
                if _error_code(exc) in _MISSING:
                    return False
                raise
            return True

        return await _read(_head, sha)
