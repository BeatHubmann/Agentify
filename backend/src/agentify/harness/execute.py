"""Run execution: the agent loop, the gate and a shielded finish on every exit path (A§8.4).

The harness owns the lifecycle. Whatever the model does, and however the task ends (normal end,
model error, cancel, runtime cancellation), `finish` either commits a gated result or rolls back
and verifies. A lost lease means another process owns the run: this executor stops writing.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anyio
from pydantic_ai import Agent
from pydantic_ai.exceptions import RunCancelled, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from agentify.agents.flow_tools import FlowToolDeps, lock_view
from agentify.domain.edits import EditLayer
from agentify.domain.envelope import Corrupt, decode_edit_layer
from agentify.harness import gate, runs
from agentify.harness.budget import ErrorBudget, RunAbort
from agentify.registry import AgentKind
from agentify.secretgate import SecretFound
from agentify.store.workspace import (
    AgentRunWriter,
    BaseMovedConflict,
    CancelRequested,
    CommitValidationFailed,
    Fence,
    LeaseLost,
    MissingBlob,
    Workspace,
)


@dataclass(frozen=True)
class Outcome:
    kind: (
        str  # "gate_passed" | "unchanged" | "gate_failed" | "cancelled" | "aborted" | "lease_lost"
    )
    detail: str = ""


@dataclass(frozen=True)
class HarnessConfig:
    lease_seconds: float = 15.0
    renew_every: float = 5.0
    storage_timeout: float = 30.0
    """If the database or blob store does not answer within this time while a run is finishing,
    the executor stops trying; the lease runs out and the sweeper finishes the run (A§8.5)."""


class _Staging:
    """The `StagingPort` for one run: reads its staging over its base, writes fenced."""

    def __init__(self, ws: Workspace, claimed: runs.ClaimedRun, allowed: frozenset[str]) -> None:
        self._ws = ws
        self._claimed = claimed
        self._allowed = allowed

    async def read(self, path: str) -> bytes | None:
        # A from-scratch run starts from an empty output, never from the previous flow (§6.3 item 5).
        base = (
            None
            if self._claimed.mode == "from_scratch" and path in self._allowed
            else self._claimed.base_revision_id
        )
        return await self._ws.read_staged_or_base(
            self._claimed.run_id, self._claimed.process_id, base, path
        )

    async def write(self, path: str, data: bytes) -> None:
        if path not in self._allowed:
            raise PermissionError(f"{path} is not a declared output of this run")
        await self._ws.stage(self._claimed.fence, self._claimed.process_id, path, data)


@contextlib.asynccontextmanager
async def _lease_keeper(
    ws: Workspace, fence: Fence, config: HarnessConfig, lost: asyncio.Event
) -> AsyncIterator[None]:
    async def renew() -> None:
        while True:
            await asyncio.sleep(config.renew_every)
            if not await runs.renew_lease(ws, fence, config.lease_seconds):
                lost.set()
                return

    task = asyncio.create_task(renew())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _edit_layer_at_base(
    ws: Workspace, claimed: runs.ClaimedRun, kind: AgentKind
) -> EditLayer:
    data = await ws.read(claimed.process_id, kind.edit_layer_path, claimed.base_revision_id)
    if data is None:
        return EditLayer()
    layer = decode_edit_layer(data)
    if isinstance(layer, Corrupt):  # admission refuses this already; fail closed anyway
        raise RunAbort("edit_layer_unreadable", layer.reason)
    return layer


async def _drive_agent(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    *,
    agent: Agent[FlowToolDeps, str],
    model: Model,
    prompt: str,
    deps: FlowToolDeps,
    lost: asyncio.Event,
) -> tuple[Outcome | None, str | None]:
    """Run the agent step by step; between steps, count retries and check the stop signals."""
    budget = ErrorBudget(
        kind.budgets.errors_per_tool, kind.budgets.errors_per_class, kind.budgets.errors_per_run
    )
    limits = UsageLimits(request_limit=kind.budgets.request_limit)
    stop: Outcome | None = None
    try:
        async with agent.iter(prompt, model=model, deps=deps, usage_limits=limits) as agent_run:
            async for node in agent_run:
                request = getattr(node, "request", None)
                if isinstance(request, ModelRequest):
                    budget.count(request)
                if lost.is_set():
                    stop = Outcome("lease_lost")
                elif await runs.cancel_requested(ws, claimed.run_id):
                    stop = Outcome("cancelled")
                if stop is not None:
                    agent_run.cancel()
                    break
            if stop is None and agent_run.result is not None:
                return None, agent_run.result.response.finish_reason
    except RunCancelled:
        if stop is None:
            raise
    return stop, None


async def _loop(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    *,
    agent: Agent[FlowToolDeps, str],
    model: Model,
    prompt: str,
    output_language: str,
    lost: asyncio.Event,
) -> Outcome:
    incremental = claimed.mode == "incremental"
    layer = await _edit_layer_at_base(ws, claimed, kind) if incremental else EditLayer()
    locked, anchors = lock_view(layer)
    deps = FlowToolDeps(
        staging=_Staging(ws, claimed, frozenset({kind.output_path})),
        output_path=kind.output_path,
        flow_mode=kind.flow_mode,
        output_language=output_language,
        locked=locked,
        lock_anchors=anchors,
    )
    stop, finish_reason = await _drive_agent(
        ws, claimed, kind, agent=agent, model=model, prompt=prompt, deps=deps, lost=lost
    )
    if stop is not None:
        return stop
    if await runs.cancel_requested(ws, claimed.run_id):
        return Outcome("cancelled")  # a cancelled run never reaches the gate
    if not await runs.move_to_gating(ws, claimed.fence):
        return Outcome("lease_lost")
    result = await gate.check(
        ws,
        run_id=claimed.run_id,
        process_id=claimed.process_id,
        kind=kind,
        incremental=incremental,
        base_revision_id=claimed.base_revision_id,
        finish_reason=finish_reason,
    )
    if result.unchanged:
        return Outcome("unchanged")
    if not result.passed:
        return Outcome(
            "gate_failed", "; ".join(f"{v.code}: {v.message}" for v in result.violations)
        )
    return Outcome("gate_passed")


async def finish(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    outcome: Outcome,
    config: HarnessConfig | None = None,
) -> str:
    """The single place a run ends. Returns the final run state, or `left_for_sweeper` when
    storage did not answer in time (the run then ends when its lease expires)."""
    final = "left_for_sweeper"
    with anyio.move_on_after((config or HarnessConfig()).storage_timeout):
        final = await _finish(ws, claimed, kind, outcome)
    return final


async def _commit(ws: Workspace, claimed: runs.ClaimedRun, kind: AgentKind) -> Outcome:
    """Commit a gated run. Returns `committed`, `lease_lost`, or the failure to roll back with."""
    base_flow = await gate.base_flow_of(ws, claimed.process_id, claimed.base_revision_id, kind)
    try:
        await ws.commit(
            process_id=claimed.process_id,
            writer=AgentRunWriter(claimed.run_id, claimed.initiated_by, kind.id),
            base_revision_id=claimed.base_revision_id,
            staged=await ws.staged(claimed.run_id),
            semantic_inputs=frozenset(),
            validate=gate.commit_validator(
                kind, incremental=claimed.mode == "incremental", base_flow=base_flow
            ),
            fence=claimed.fence,
            message=f"run {kind.id} {claimed.run_id}: {kind.output_path}",
        )
    except LeaseLost:
        return Outcome("lease_lost")
    except CancelRequested:
        return Outcome("cancelled", "cancel arrived before commit")
    except (BaseMovedConflict, CommitValidationFailed, MissingBlob) as exc:
        return Outcome("commit_failed", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - storage failure during commit (A§8.5)
        return Outcome("commit_failed", type(exc).__name__)
    return Outcome("committed")


async def _finish(
    ws: Workspace, claimed: runs.ClaimedRun, kind: AgentKind, outcome: Outcome
) -> str:
    if outcome.kind == "unchanged" and await runs.mark_unchanged(ws, claimed.fence):
        return "unchanged"
    if outcome.kind == "gate_passed":
        outcome = await _commit(ws, claimed, kind)
    if outcome.kind in {"committed", "lease_lost"}:
        return "committed" if outcome.kind == "committed" else "abandoned"
    try:
        verified = await ws.rollback(
            claimed.fence, outcome=outcome.kind, detail=outcome.detail or None
        )
    except LeaseLost:
        return "abandoned"  # another process owns the run now; it will finish it
    except Exception:  # noqa: BLE001 - storage unreachable: the lease expires and the sweeper finishes
        return "left_for_sweeper"
    return "rolled_back" if verified else "rollback_failed"


async def execute(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    *,
    agent: Agent[FlowToolDeps, str],
    model: Model,
    prompt: str,
    output_language: str = "en",
    config: HarnessConfig | None = None,
) -> str:
    config = config or HarnessConfig()
    lost = asyncio.Event()
    outcome: Outcome
    try:
        async with _lease_keeper(ws, claimed.fence, config, lost):
            outcome = await _loop(
                ws,
                claimed,
                kind,
                agent=agent,
                model=model,
                prompt=prompt,
                output_language=output_language,
                lost=lost,
            )
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            await finish(ws, claimed, kind, Outcome("cancelled", "runtime cancellation"), config)
        raise
    except RunAbort as exc:
        outcome = Outcome("aborted", f"{exc.reason}: {exc.detail}")
    except (UsageLimitExceeded, UnexpectedModelBehavior) as exc:
        outcome = Outcome("aborted", type(exc).__name__)
    except LeaseLost:
        outcome = Outcome("lease_lost")
    except SecretFound as exc:
        outcome = Outcome("aborted", f"secret_gate: {exc.rule} at {exc.where}")
    except Exception as exc:  # noqa: BLE001 - any failure, including provider errors, ends in rollback
        outcome = Outcome("aborted", type(exc).__name__)
    with anyio.CancelScope(shield=True):
        final = await finish(ws, claimed, kind, outcome, config)
    return final
