"""What agents may use from the harness (A§4): protocols injected into each run's dependencies."""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol


class StagingPort(Protocol):
    async def read(self, path: str) -> bytes | None:
        """The run's own staged content for a path, or the base revision's."""

    async def write(self, path: str, data: bytes) -> None:
        """Stage new content for a declared output path (fenced by the run's lease)."""


@dataclass
class ArtifactLocks:
    """Per-(process, path) locks for read-modify-write within one run (A§8.4, HZ-9)."""

    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def for_path(self, path: str) -> asyncio.Lock:
        return self._locks.setdefault(path, asyncio.Lock())
