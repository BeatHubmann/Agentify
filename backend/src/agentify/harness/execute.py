"""Run execution: the agent loop, the gate and a shielded finish on every exit path (A§8.4).

The harness owns the lifecycle. Whatever the model does, and however the task ends (normal end,
model error, cancel, runtime cancellation), `finish` either commits a gated result or rolls back
and verifies. A lost lease means another process owns the run: this executor stops writing.
"""

import asyncio
import contextlib
import traceback
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio
from pydantic_ai import Agent
from pydantic_ai.exceptions import RunCancelled, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from agentify import chat, llm
from agentify.agents.artifact_tools import ArtifactToolDeps
from agentify.agents.flow_tools import FlowToolDeps, lock_view
from agentify.agents.router_tools import RouterDeps
from agentify.domain.edits import EditLayer
from agentify.domain.envelope import Corrupt, decode_edit_layer
from agentify.harness import artifact_gate, events, gate, runs
from agentify.harness.budget import ErrorBudget, RunAbort
from agentify.harness.run_ports import HeadReader, RouterDispatcher, RunInbox, RunStaging
from agentify.llm.policy import SHIPPED_POLICY
from agentify.observability import logger
from agentify.registry import AgentKind, ArtifactAgentKind, FlowAgentKind, Registry, RouterKind
from agentify.secretgate import SecretFound
from agentify.store import budget
from agentify.store.workspace import (
    AgentRunWriter,
    BaseMovedConflict,
    CancelRequested,
    CommitValidationFailed,
    Fence,
    LeaseLost,
    MissingBlob,
    Validator,
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
    prompt_version: str | None = None
    """Recorded on every revision the run commits (A§9.5)."""
    attachment_grace_seconds: float = 15.0
    """A chat-attached run whose stream heartbeat is older than this is stopped like a cancel (A§8.5)."""
    policy: llm.ModelPolicy = field(
        default_factory=lambda: llm.load_policy(SHIPPED_POLICY, roles=set())
    )
    """Which model each role uses, at which prices (A§17.4)."""
    deployment: str = "local"
    """The deployment budget scope every call is also counted against (A§17.3)."""


@contextlib.asynccontextmanager
async def _lease_keeper(
    ws: Workspace, fence: Fence, config: HarnessConfig, lost: asyncio.Event
) -> AsyncIterator[None]:
    async def renew() -> None:
        """A refused renewal means the lease is lost. A failed one is retried at the next tick: the
        lease has slack, and if storage stays down the sweeper takes the run over (A§8.5)."""
        while True:
            await asyncio.sleep(config.renew_every)
            try:
                renewed = await runs.renew_lease(ws, fence, config.lease_seconds)
            except Exception as exc:  # noqa: BLE001 - transient storage error; never ends the run here
                logger().warning(
                    "lease_renewal_failed", run_id=fence.run_id, error=type(exc).__name__
                )
                continue
            if not renewed:
                lost.set()
                return

    task = asyncio.create_task(renew())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _call_context(
    ws: Workspace, claimed: runs.ClaimedRun, kind: AgentKind, config: HarnessConfig
) -> llm.CallContext:
    async with ws.db.tx() as conn:
        engagement = await ws.engagement_of(conn, claimed.process_id)
    return llm.CallContext(
        db=ws.db,
        gate=ws.gate,
        scopes=budget.Scopes(claimed.run_id, claimed.process_id, engagement),
        deployment=config.deployment,
        run_ceiling=int(kind.budgets.money_usd * 1_000_000),
    )


async def _edit_layer_at_base(
    ws: Workspace, claimed: runs.ClaimedRun, kind: FlowAgentKind
) -> EditLayer:
    data = await ws.read(claimed.process_id, kind.edit_layer_path, claimed.base_revision_id)
    if data is None:
        return EditLayer()
    layer = decode_edit_layer(data)
    if isinstance(layer, Corrupt):  # admission refuses this already; fail closed anyway
        raise RunAbort("edit_layer_unreadable", layer.reason)
    return layer


async def _deps(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    registry: Registry | None,
    output_language: str,
) -> Any:
    staging = RunStaging(ws, claimed, frozenset(kind.output_paths))
    port = RunInbox(ws, registry, claimed) if registry is not None else None
    if isinstance(kind, FlowAgentKind):
        layer = (
            await _edit_layer_at_base(ws, claimed, kind)
            if claimed.mode == "incremental"
            else EditLayer()
        )
        locked, anchors = lock_view(layer)
        return FlowToolDeps(
            staging=staging,
            output_path=kind.output_path,
            flow_mode=kind.flow_mode,
            output_language=output_language,
            locked=locked,
            lock_anchors=anchors,
            inbox=port,
            inbox_categories=kind.inbox_categories,
        )
    if registry is None:
        raise ValueError(f"{kind.id} needs the registry")
    if isinstance(kind, ArtifactAgentKind):
        outputs = {o.path: (o, registry.artifact_types[o.artifact_type]) for o in kind.outputs}
        return ArtifactToolDeps(
            staging=staging,
            outputs=outputs,
            readable_prefixes=kind.input_paths,
            output_language=output_language,
            from_scratch=claimed.mode == "from_scratch",
            inbox=port,
            inbox_categories=kind.inbox_categories,
        )
    return RouterDeps(
        workspace=HeadReader(ws, claimed.process_id),
        dispatcher=RouterDispatcher(ws, registry, claimed),
        registry=registry,
        inbox=port,
        inbox_categories=kind.inbox_categories,
    )


async def _drive_agent(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    *,
    agent: Agent[Any, str],
    model: Model,
    prompt: str,
    deps: Any,
    lost: asyncio.Event,
    grace: float,
) -> tuple[Outcome | None, str | None, str | None]:
    """Run the agent step by step; between steps, count retries and check the stop signals.
    Returns (stop outcome, finish reason, final text)."""
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
                for event_kind, payload in events.from_node(node):
                    await events.publish(ws, claimed.fence, event_kind, payload)
                reason = None if lost.is_set() else await _stop_reason(ws, claimed, grace)
                if lost.is_set():
                    stop = Outcome("lease_lost")
                elif reason is not None:
                    stop = Outcome("cancelled", reason)
                if stop is not None:
                    agent_run.cancel()
                    break
            if stop is None and agent_run.result is not None:
                return None, agent_run.result.response.finish_reason, str(agent_run.result.output)
    except RunCancelled:
        if stop is None:
            raise
    return stop, None, None


CHAT_ENTRY_POINTS = frozenset({"chat", "router_followup"})
MAX_TRACEBACK_CHARS = 4000


def _traceback_detail(exc: BaseException) -> str:
    """The exception type, then its traceback: the run record keeps it, scrubbed (A§10.2)."""
    trace = "".join(traceback.format_exception(exc))
    return f"{type(exc).__name__}\n{trace[-MAX_TRACEBACK_CHARS:]}"


async def _stop_reason(ws: Workspace, claimed: runs.ClaimedRun, grace: float) -> str | None:
    reason = await runs.stop_requested(ws, claimed.run_id, attachment_grace_seconds=grace)
    return (
        None
        if reason is None
        else ("client disconnected" if reason == "disconnected" else "cancel requested")
    )


async def _gate(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    registry: Registry | None,
    finish_reason: str | None,
) -> gate.GateResult:
    if isinstance(kind, FlowAgentKind):
        return await gate.check(ws, claimed, kind, finish_reason)
    if isinstance(kind, ArtifactAgentKind) and registry is not None:
        return await artifact_gate.check(ws, claimed, kind, registry, finish_reason)
    return gate.GateResult(True, unchanged=True)  # the router writes nothing


async def _loop(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    *,
    agent: Agent[Any, str],
    model: Model,
    prompt: str,
    output_language: str,
    lost: asyncio.Event,
    registry: Registry | None,
    grace: float,
) -> Outcome:
    deps = await _deps(ws, claimed, kind, registry, output_language)
    stop, finish_reason, text = await _drive_agent(
        ws,
        claimed,
        kind,
        agent=agent,
        model=model,
        prompt=prompt,
        deps=deps,
        lost=lost,
        grace=grace,
    )
    if stop is not None:
        return stop
    reason = await _stop_reason(ws, claimed, grace)
    if reason is not None:
        return Outcome("cancelled", reason)  # a cancelled run never reaches the gate
    if not await runs.move_to_gating(ws, claimed.fence):
        return Outcome("lease_lost")
    if isinstance(kind, RouterKind) and text is not None:
        await runs.record_result(ws, claimed.fence, text)
        if claimed.entry_point in CHAT_ENTRY_POINTS:
            await chat.append(
                ws,
                process_id=claimed.process_id,
                author_kind="agent_run",
                author_id=claimed.run_id,
                content=text,
                run_id=claimed.run_id,
            )
    result = await _gate(ws, claimed, kind, registry, finish_reason)
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
    *,
    config: HarnessConfig | None = None,
    registry: Registry | None = None,
) -> str:
    """The single place a run ends. Returns the final run state, or `left_for_sweeper` when
    storage did not answer in time (the run then ends when its lease expires)."""
    final = "left_for_sweeper"
    with anyio.move_on_after((config or HarnessConfig()).storage_timeout):
        final = await _finish(
            ws, claimed, kind, outcome, config=config or HarnessConfig(), registry=registry
        )
        await budget.release_run(ws.db, claimed.run_id)  # reservations are never left open
        if final in runs.TERMINAL_STATES:
            await events.publish(ws, claimed.fence, "finished", {"state": final})
        if final in runs.TERMINAL_STATES and claimed.parent_run_id is not None:
            await runs.record_continuation(ws, claimed.run_id)
    return final


async def _validator(
    ws: Workspace, claimed: runs.ClaimedRun, kind: AgentKind, registry: Registry | None
) -> Validator:
    if isinstance(kind, FlowAgentKind):
        base_flow = await gate.base_flow_of(ws, claimed.process_id, claimed.base_revision_id, kind)
        return gate.commit_validator(
            kind, incremental=claimed.mode == "incremental", base_flow=base_flow
        )
    if isinstance(kind, ArtifactAgentKind) and registry is not None:
        return artifact_gate.commit_validator(kind, registry)
    raise ValueError(f"{kind.id} has no outputs to commit")


async def _commit(
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    config: HarnessConfig,
    registry: Registry | None,
) -> Outcome:
    """Commit a gated run. Returns `committed`, `lease_lost`, or the failure to roll back with."""
    try:
        await ws.commit(
            process_id=claimed.process_id,
            writer=AgentRunWriter(claimed.run_id, claimed.initiated_by, kind.id),
            base_revision_id=claimed.base_revision_id,
            staged=await ws.staged(claimed.run_id),
            semantic_inputs=frozenset(kind.input_paths),
            validate=await _validator(ws, claimed, kind, registry),
            fence=claimed.fence,
            message=f"run {kind.id} {claimed.run_id}: {', '.join(kind.output_paths)}",
            prompt_version=config.prompt_version,
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
    ws: Workspace,
    claimed: runs.ClaimedRun,
    kind: AgentKind,
    outcome: Outcome,
    *,
    config: HarnessConfig,
    registry: Registry | None,
) -> str:
    if outcome.kind == "unchanged" and await runs.mark_unchanged(ws, claimed.fence):
        return "unchanged"
    if outcome.kind == "gate_passed":
        reason = await _stop_reason(ws, claimed, config.attachment_grace_seconds)
        outcome = (
            Outcome("cancelled", reason)
            if reason
            else await _commit(ws, claimed, kind, config, registry)
        )
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
    agent: Agent[Any, str],
    source: llm.ModelSource,
    prompt: str,
    output_language: str = "en",
    config: HarnessConfig | None = None,
    registry: Registry | None = None,
    credentials: Sequence[str] = (),
) -> str:
    """Run one claimed run to a final state. `credentials` are its per-request credentials (A§17.2):
    known values in every sink, other runs' included, until the run, its `finish` too, is over.
    The model is built here, from `source`, through `llm.model_for`: every call the run makes is
    gated and accounted, whatever the source (A§17.1, BC-B.M4)."""
    config = config or HarnessConfig()
    with ws.gate.registered(credentials):
        lost = asyncio.Event()
        outcome: Outcome
        try:
            model = llm.model_for(
                kind.model_role,
                await _call_context(ws, claimed, kind, config),
                policy=config.policy,
                source=source,
            )
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
                    registry=registry,
                    grace=config.attachment_grace_seconds,
                )
        except asyncio.CancelledError:
            with anyio.CancelScope(shield=True):
                await finish(
                    ws,
                    claimed,
                    kind,
                    Outcome("cancelled", "runtime cancellation"),
                    config=config,
                    registry=registry,
                )
            raise
        except RunAbort as exc:
            outcome = Outcome("aborted", f"{exc.reason}: {exc.detail}")
        except budget.BudgetExhausted as exc:
            outcome = Outcome("aborted", f"money_budget_exhausted: {exc}")
        except llm.KnownCredentialInRequest as exc:
            outcome = Outcome("aborted", f"known_value_in_model_request: {exc}")
        except (UsageLimitExceeded, UnexpectedModelBehavior) as exc:
            outcome = Outcome("aborted", type(exc).__name__)
        except LeaseLost:
            outcome = Outcome("lease_lost")
        except SecretFound as exc:
            outcome = Outcome("aborted", f"secret_gate: {exc.rule} at {exc.where}")
        except Exception as exc:  # noqa: BLE001 - any failure, including provider errors, ends in rollback
            outcome = Outcome("aborted", _traceback_detail(exc))
        with anyio.CancelScope(shield=True):
            final = await finish(ws, claimed, kind, outcome, config=config, registry=registry)
    return final
