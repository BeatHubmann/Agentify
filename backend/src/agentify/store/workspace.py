"""Workspace versioning and run isolation (A§7).

Runs write only to fenced staging rows; the shared workspace changes only inside `commit`, one
Postgres transaction for every kind of writer. Rollback discards staging and verifies itself
against the database.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from psycopg import AsyncConnection

from agentify.domain.canonical import sha256_hex
from agentify.domain.validate import Violation
from agentify.secretgate import SecretGate
from agentify.store.blobs import BlobStore
from agentify.store.db import Database

Conn = AsyncConnection[dict[str, object]]
PathMap = dict[str, str]
_HEAD = "SELECT revision_id FROM workspace_head WHERE process_id = %s"
_HEAD_FOR_UPDATE = _HEAD + " FOR UPDATE"


@dataclass(frozen=True)
class Fence:
    """The run's lease as a fencing token (A§8.4)."""

    run_id: str
    owner: str
    epoch: int


@dataclass(frozen=True)
class AgentRunWriter:
    run_id: str
    initiated_by: str
    kind: str


@dataclass(frozen=True)
class UserWriter:
    user_id: str


@dataclass(frozen=True)
class SystemWriter:
    name: str


Writer = AgentRunWriter | UserWriter | SystemWriter


class LeaseLost(Exception):
    """The fence no longer matches: another process owns the run, or it ended."""


class CancelRequested(Exception):
    """A cancel arrived before the commit could take effect."""


class BaseMovedConflict(Exception):
    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"paths changed since the base revision: {paths}")
        self.paths = paths


class CommitValidationFailed(Exception):
    def __init__(self, violations: list[Violation]) -> None:
        super().__init__(f"{len(violations)} invariant violations in the merged result")
        self.violations = violations


class MissingBlob(Exception):
    pass


class PathReader:
    """Reads artifacts of a path map; used by commit-time validation."""

    def __init__(self, blobs: BlobStore, engagement_id: str, paths: Mapping[str, str]) -> None:
        self._blobs = blobs
        self._engagement_id = engagement_id
        self.paths = dict(paths)

    async def read(self, path: str) -> bytes | None:
        sha = self.paths.get(path)
        return None if sha is None else await self._blobs.get(self._engagement_id, sha)


Validator = Callable[[PathReader, frozenset[str]], Awaitable[list[Violation]]]


async def _no_validation(_reader: PathReader, _changed: frozenset[str]) -> list[Violation]:
    return []


class Workspace:
    def __init__(self, db: Database, blobs: BlobStore, gate: SecretGate) -> None:
        self.db = db
        self.blobs = blobs
        self.gate = gate

    # -- setup -------------------------------------------------------------------------------

    async def create_process(self, engagement_id: str, process_id: str) -> None:
        async with self.db.tx() as conn:
            await conn.execute(
                "INSERT INTO engagement (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (engagement_id, engagement_id),
            )
            await conn.execute(
                "INSERT INTO process (id, engagement_id, name) VALUES (%s, %s, %s)",
                (process_id, engagement_id, process_id),
            )
            await conn.execute("INSERT INTO workspace_head (process_id) VALUES (%s)", (process_id,))

    # -- reads -------------------------------------------------------------------------------

    @staticmethod
    async def engagement_of(conn: Conn, process_id: str) -> str:
        row = await (
            await conn.execute("SELECT engagement_id FROM process WHERE id = %s", (process_id,))
        ).fetchone()
        if row is None:
            raise KeyError(process_id)
        return str(row["engagement_id"])

    @staticmethod
    async def path_map(conn: Conn, revision_id: str | None) -> PathMap:
        if revision_id is None:
            return {}
        rows = await (
            await conn.execute(
                "SELECT path, blob_sha256 FROM revision_path WHERE revision_id = %s", (revision_id,)
            )
        ).fetchall()
        return {str(row["path"]): str(row["blob_sha256"]) for row in rows}

    @staticmethod
    async def head_revision(conn: Conn, process_id: str, *, lock: bool = False) -> str | None:
        query = _HEAD_FOR_UPDATE if lock else _HEAD
        row = await (await conn.execute(query, (process_id,))).fetchone()
        if row is None:
            raise KeyError(process_id)
        return None if row["revision_id"] is None else str(row["revision_id"])

    async def list_paths(
        self, process_id: str, prefix: str, revision_id: str | None = None
    ) -> list[str]:
        async with self.db.tx() as conn:
            revision = (
                revision_id
                if revision_id is not None
                else await self.head_revision(conn, process_id)
            )
            paths = await self.path_map(conn, revision)
        return sorted(path for path in paths if path.startswith(prefix))

    async def read(
        self, process_id: str, path: str, revision_id: str | None = None
    ) -> bytes | None:
        async with self.db.tx() as conn:
            engagement_id = await self.engagement_of(conn, process_id)
            revision = (
                revision_id
                if revision_id is not None
                else await self.head_revision(conn, process_id)
            )
            paths = await self.path_map(conn, revision)
        sha = paths.get(path)
        return None if sha is None else await self.blobs.get(engagement_id, sha)

    # -- run staging -------------------------------------------------------------------------

    async def stage(self, fence: Fence, process_id: str, path: str, data: bytes) -> str:
        """Write into the run's private staging, fenced by its lease. Returns the blob hash."""
        scrubbed = self.gate.check_bytes(data, where=f"staged {path}")
        sha = sha256_hex(scrubbed.value)
        async with self.db.tx() as conn:
            engagement_id = await self.engagement_of(conn, process_id)
            pinned = await conn.execute(
                """INSERT INTO blob_pin (engagement_id, blob_sha256, pinned_by_run)
                   SELECT %s, %s, %s WHERE EXISTS (
                     SELECT 1 FROM run WHERE id = %s AND state = 'running'
                       AND lease_owner = %s AND lease_epoch = %s)
                   ON CONFLICT DO NOTHING""",
                (engagement_id, sha, fence.run_id, fence.run_id, fence.owner, fence.epoch),
            )
            if pinned.rowcount == 0 and not await self._fence_holds(conn, fence, "running"):
                raise LeaseLost(fence.run_id)
        await self.blobs.put(engagement_id, scrubbed)
        async with self.db.tx() as conn:
            staged = await conn.execute(
                """INSERT INTO run_staging (run_id, path, blob_sha256)
                   SELECT %s, %s, %s WHERE EXISTS (
                     SELECT 1 FROM run WHERE id = %s AND state = 'running'
                       AND lease_owner = %s AND lease_epoch = %s)
                   ON CONFLICT (run_id, path) DO UPDATE SET blob_sha256 = EXCLUDED.blob_sha256,
                     written_at = now()""",
                (fence.run_id, path, sha, fence.run_id, fence.owner, fence.epoch),
            )
            if staged.rowcount == 0:
                raise LeaseLost(fence.run_id)
        return sha

    @staticmethod
    async def _fence_holds(conn: Conn, fence: Fence, state: str) -> bool:
        row = await (
            await conn.execute(
                "SELECT 1 FROM run WHERE id = %s AND state = %s AND lease_owner = %s AND lease_epoch = %s",
                (fence.run_id, state, fence.owner, fence.epoch),
            )
        ).fetchone()
        return row is not None

    async def staged(self, run_id: str) -> PathMap:
        async with self.db.tx() as conn:
            rows = await (
                await conn.execute(
                    "SELECT path, blob_sha256 FROM run_staging WHERE run_id = %s", (run_id,)
                )
            ).fetchall()
        return {str(row["path"]): str(row["blob_sha256"]) for row in rows}

    async def read_staged_or_base(
        self, run_id: str, process_id: str, base_revision_id: str | None, path: str
    ) -> bytes | None:
        """What the run sees: its own staging over its base revision (A§7.2). Always from storage."""
        async with self.db.tx() as conn:
            engagement_id = await self.engagement_of(conn, process_id)
            row = await (
                await conn.execute(
                    "SELECT blob_sha256 FROM run_staging WHERE run_id = %s AND path = %s",
                    (run_id, path),
                )
            ).fetchone()
            sha = None if row is None else str(row["blob_sha256"])
            if sha is None:
                sha = (await self.path_map(conn, base_revision_id)).get(path)
        return None if sha is None else await self.blobs.get(engagement_id, sha)

    # -- commit ------------------------------------------------------------------------------

    async def commit(
        self,
        *,
        process_id: str,
        writer: Writer,
        base_revision_id: str | None,
        staged: PathMap,
        semantic_inputs: frozenset[str] = frozenset(),
        validate: Validator = _no_validation,
        fence: Fence | None = None,
        message: str,
        prompt_version: str | None = None,
    ) -> str:
        """The one commit path for every writer (A§7.3). Returns the new revision id."""
        async with self.db.tx() as conn:
            engagement_id = await self.engagement_of(conn, process_id)
        for path, sha in staged.items():
            if not await self.blobs.exists(engagement_id, sha):
                raise MissingBlob(f"{path} -> {sha}")
        async with self.db.tx() as conn:
            if fence is not None:
                moved = await conn.execute(
                    """UPDATE run SET state = 'committing' WHERE id = %s AND state = 'gating'
                         AND lease_owner = %s AND lease_epoch = %s AND cancel_requested_at IS NULL""",
                    (fence.run_id, fence.owner, fence.epoch),
                )
                if moved.rowcount == 0:
                    cancelled = await (
                        await conn.execute(
                            "SELECT cancel_requested_at IS NOT NULL AS c FROM run WHERE id = %s",
                            (fence.run_id,),
                        )
                    ).fetchone()
                    if cancelled is not None and cancelled["c"]:
                        raise CancelRequested(fence.run_id)
                    raise LeaseLost(fence.run_id)
            head = await self.head_revision(conn, process_id, lock=True)
            head_map = await self.path_map(conn, head)
            base_map = await self.path_map(conn, base_revision_id)
            changed = {
                p for p in set(head_map) | set(base_map) if head_map.get(p) != base_map.get(p)
            }
            prefixes = tuple(i for i in semantic_inputs if i.endswith("/"))
            conflicts = sorted(
                p for p in changed if p in staged or p in semantic_inputs or p.startswith(prefixes)
            )
            if conflicts:
                raise BaseMovedConflict(conflicts)
            new_map = {**head_map, **staged}
            violations = await validate(
                PathReader(self.blobs, engagement_id, new_map), frozenset(staged)
            )
            if violations:
                raise CommitValidationFailed(violations)
            revision_id = f"rev_{uuid.uuid4().hex}"
            author_kind, author_id, run_id = _author(writer)
            await conn.execute(
                """INSERT INTO revision (id, process_id, parent_id, run_id, author_kind, author_id, message,
                                         prompt_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    revision_id,
                    process_id,
                    head,
                    run_id,
                    author_kind,
                    author_id,
                    message,
                    prompt_version,
                ),
            )
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO revision_path (revision_id, path, blob_sha256) VALUES (%s, %s, %s)",
                    [(revision_id, path, sha) for path, sha in sorted(new_map.items())],
                )
            await conn.execute(
                "UPDATE workspace_head SET revision_id = %s, updated_at = now() WHERE process_id = %s",
                (revision_id, process_id),
            )
            if fence is not None:
                await conn.execute(
                    """UPDATE run SET state = 'committed', outcome = 'committed', finished_at = now(),
                         staged_paths = %s WHERE id = %s""",
                    (sorted(staged), fence.run_id),
                )
                await conn.execute("DELETE FROM run_staging WHERE run_id = %s", (fence.run_id,))
                await conn.execute("DELETE FROM blob_pin WHERE pinned_by_run = %s", (fence.run_id,))
        return revision_id

    # -- rollback ----------------------------------------------------------------------------

    async def rollback(self, fence: Fence, *, outcome: str, detail: str | None = None) -> bool:
        """Discard the run's staging and verify the result. Returns True if verification passed."""
        async with self.db.tx() as conn:
            moved = await conn.execute(
                """UPDATE run SET state = 'rolling_back',
                     staged_paths = COALESCE((SELECT array_agg(path ORDER BY path) FROM run_staging
                                              WHERE run_id = %s), '{}')
                   WHERE id = %s AND lease_owner = %s AND lease_epoch = %s
                     AND state IN ('admitted', 'running', 'gating', 'committing', 'rolling_back')""",
                (fence.run_id, fence.run_id, fence.owner, fence.epoch),
            )
            if moved.rowcount == 0:
                raise LeaseLost(fence.run_id)
            await conn.execute("DELETE FROM run_staging WHERE run_id = %s", (fence.run_id,))
            await conn.execute("DELETE FROM blob_pin WHERE pinned_by_run = %s", (fence.run_id,))
            await conn.execute(
                """UPDATE run SET state = 'rolled_back', outcome = %s, outcome_detail_scrubbed = %s,
                     finished_at = now() WHERE id = %s""",
                (outcome, detail, fence.run_id),
            )
        problems = await self.verify_rollback(fence.run_id)
        if problems:
            async with self.db.tx() as conn:
                await conn.execute(
                    "UPDATE run SET state = 'rollback_failed', outcome_detail_scrubbed = %s WHERE id = %s",
                    ("; ".join(problems), fence.run_id),
                )
                await conn.execute(
                    "UPDATE process SET quarantined_at = now() WHERE id = (SELECT process_id FROM run WHERE id = %s)",
                    (fence.run_id,),
                )
            return False
        return True

    async def verify_rollback(self, run_id: str) -> list[str]:
        """Checks against the database, not memory (A§7.4). Empty list means verified."""
        problems: list[str] = []
        async with self.db.tx() as conn:
            run = await (
                await conn.execute(
                    "SELECT process_id, base_revision_id, state, staged_paths FROM run WHERE id = %s",
                    (run_id,),
                )
            ).fetchone()
            if run is None:
                return ["run record missing"]
            if await (
                await conn.execute("SELECT 1 FROM revision WHERE run_id = %s", (run_id,))
            ).fetchone():
                problems.append("a revision carries this run id")
            if await (
                await conn.execute("SELECT 1 FROM run_staging WHERE run_id = %s", (run_id,))
            ).fetchone():
                problems.append("staging rows remain")
            if await (
                await conn.execute("SELECT 1 FROM blob_pin WHERE pinned_by_run = %s", (run_id,))
            ).fetchone():
                problems.append("pins remain")
            head = await self.head_revision(conn, str(run["process_id"]))
            head_map = await self.path_map(conn, head)
            base_map = await self.path_map(
                conn, None if run["base_revision_id"] is None else str(run["base_revision_id"])
            )
            writers = await (
                await conn.execute(
                    """WITH RECURSIVE chain AS (
                     SELECT id, parent_id, run_id FROM revision WHERE id = %s
                     UNION ALL
                     SELECT r.id, r.parent_id, r.run_id FROM revision r JOIN chain c ON r.id = c.parent_id
                       WHERE c.id IS DISTINCT FROM %s)
                   SELECT run_id FROM chain WHERE id IS DISTINCT FROM %s""",
                    (head, run["base_revision_id"], run["base_revision_id"]),
                )
            ).fetchall()
            if any(row["run_id"] == run_id for row in writers):
                problems.append("a revision after the base was written by this run")
            staged_paths = run["staged_paths"]
            for path in staged_paths if isinstance(staged_paths, list) else []:
                if head_map.get(path) != base_map.get(path) and not writers:
                    problems.append(f"{path} differs from base with no other writer")
            if run["state"] != "rolled_back":
                problems.append(f"run state is {run['state']}")
        return problems


def _author(writer: Writer) -> tuple[str, str, str | None]:
    match writer:
        case AgentRunWriter(run_id=run_id, initiated_by=initiator):
            return "agent_run", initiator, run_id
        case UserWriter(user_id=user_id):
            return "user", user_id, None
        case SystemWriter(name=name):
            return "system", name, None
