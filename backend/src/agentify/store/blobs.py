"""Content-addressed, engagement-prefixed blob store on S3 (A§5.1). Blobs are never overwritten."""

from typing import TYPE_CHECKING

import anyio
from botocore.exceptions import ClientError

from agentify.domain.canonical import sha256_hex
from agentify.secretgate import Scrubbed

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_ALREADY_EXISTS = {"PreconditionFailed", "412"}
_MISSING = {"404", "NoSuchKey", "NotFound"}


class BlobStore:
    def __init__(self, client: "S3Client", bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @staticmethod
    def key(engagement_id: str, sha: str) -> str:
        return f"{engagement_id}/sha256/{sha}"

    async def put(self, engagement_id: str, data: Scrubbed[bytes]) -> str:
        """Store the gated bytes; an existing key is left untouched (conditional put)."""
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
                if exc.response.get("Error", {}).get("Code") not in _ALREADY_EXISTS:
                    raise

        await anyio.to_thread.run_sync(_put)
        return sha

    async def get(self, engagement_id: str, sha: str) -> bytes:
        def _get() -> bytes:
            body = self._client.get_object(Bucket=self._bucket, Key=self.key(engagement_id, sha))[
                "Body"
            ]
            return body.read()

        data = await anyio.to_thread.run_sync(_get)
        if sha256_hex(data) != sha:
            raise ValueError(f"blob {sha} failed its content check")
        return data

    async def exists(self, engagement_id: str, sha: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=self.key(engagement_id, sha))
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    return False
                raise
            return True

        return await anyio.to_thread.run_sync(_head)
