"""What agents may use from the harness (A§4): protocols injected into each run's dependencies."""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol


class StagingPort(Protocol):
    async def read(self, path: str) -> bytes | None:
        """The run's own staged content for a path, or the base revision's."""

    async def write(self, path: str, data: bytes) -> None:
        """Stage new content for a declared output path (fenced by the run's lease)."""

    async def list_paths(self, prefix: str) -> list[str]:
        """Paths under a prefix at the run's base revision (inputs, artifacts)."""


@dataclass(frozen=True)
class DispatchResult:
    status: str  # "admitted" | "needs_confirmation" | "already_running" | "refused"
    run_id: str | None = None
    detail: str = ""


class DispatchPort(Protocol):
    async def dispatch(self, kind: str, mode: str, instructions: str) -> DispatchResult:
        """Admit a child run of another kind; returns at once (A§9.1)."""


class InboxPort(Protocol):
    async def ask(self, category: str, question: str, options: list[str]) -> str:
        """Create an inbox question in a category this agent kind may use; returns its id."""

    async def read(self, status: str | None) -> str:
        """The process's inbox items, as JSON."""


@dataclass
class ArtifactLocks:
    """Per-(process, path) locks for read-modify-write within one run (A§8.4, HZ-9)."""

    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def for_path(self, path: str) -> asyncio.Lock:
        return self._locks.setdefault(path, asyncio.Lock())
