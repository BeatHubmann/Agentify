"""The harness side of the agent ports (A§4): how one run reads, writes, asks and dispatches.

Agents see only the protocols in `harness.ports`; these classes implement them for a claimed run."""

import json
from dataclasses import asdict

from agentify import inbox
from agentify.harness import runs
from agentify.harness.budget import RunAbort
from agentify.harness.ports import DispatchResult, correctable
from agentify.registry import Registry
from agentify.secretgate import SecretFound
from agentify.store import security_events
from agentify.store.workspace import Workspace


class RunStaging:
    """The `StagingPort` for one run: reads its staging over its base, writes fenced."""

    def __init__(self, ws: Workspace, claimed: runs.ClaimedRun, allowed: frozenset[str]) -> None:
        self._ws = ws
        self._claimed = claimed
        self._allowed = allowed

    async def read(self, path: str) -> bytes | None:
        # A from-scratch run starts from an empty output, never from the previous one (§6.3 item 5).
        from_scratch_output = self._claimed.mode == "from_scratch" and path in self._allowed
        base = None if from_scratch_output else self._claimed.base_revision_id
        return await self._ws.read_staged_or_base(
            self._claimed.run_id, self._claimed.process_id, base, path
        )

    async def write(self, path: str, data: bytes) -> None:
        """A gate hit is correctable once; the same fingerprint again aborts the run (A§10.2)."""
        if path not in self._allowed:
            raise PermissionError(f"{path} is not a declared output of this run")
        try:
            await self._ws.stage(self._claimed.fence, self._claimed.process_id, path, data)
        except SecretFound as exc:
            ws, run_id = self._ws, self._claimed.run_id
            await security_events.record(
                ws.db,
                ws.gate,
                exit="staged_write",
                hits=[exc.hit],
                location=path,
                process_id=self._claimed.process_id,
                run_id=run_id,
            )
            if await security_events.hits_in_run(ws.db, run_id, exc.hit) > 1:
                raise RunAbort("secret_gate_repeated", f"{exc.rule} at {path}") from None
            raise correctable(
                "secret_in_content",
                f"Nothing was written: the content contains text the secret gate treats as a "
                f"credential ({exc.rule}).",
                (path,),
                hint="Remove or paraphrase the credential-like text, then write again.",
            ) from None

    async def list_paths(self, prefix: str) -> list[str]:
        return await self._ws.list_paths(
            self._claimed.process_id, prefix, self._claimed.base_revision_id
        )


class HeadReader:
    """The router's view: the live workspace head (it writes nothing)."""

    def __init__(self, ws: Workspace, process_id: str) -> None:
        self._ws = ws
        self._process_id = process_id

    async def read(self, path: str) -> bytes | None:
        return await self._ws.read(self._process_id, path)

    async def write(self, path: str, data: bytes) -> None:
        raise PermissionError("the router has no write path")

    async def list_paths(self, prefix: str) -> list[str]:
        return await self._ws.list_paths(self._process_id, prefix)


class RunInbox:
    def __init__(self, ws: Workspace, registry: Registry, claimed: runs.ClaimedRun) -> None:
        self._ws, self._registry, self._claimed = ws, registry, claimed

    async def ask(self, category: str, question: str, options: list[str]) -> str:
        return await inbox.ask(
            self._ws,
            self._registry,
            process_id=self._claimed.process_id,
            category=category,
            question=question,
            options=options,
            run_id=self._claimed.run_id,
        )

    async def read(self, status: str | None) -> str:
        found = await inbox.items(self._ws, self._claimed.process_id, status=status)
        return json.dumps([asdict(item) for item in found], sort_keys=True, ensure_ascii=False)


class RouterDispatcher:
    """Child runs go through admission like every other entry point (A§8.2, A§9.1)."""

    def __init__(self, ws: Workspace, registry: Registry, claimed: runs.ClaimedRun) -> None:
        self._ws, self._registry, self._claimed = ws, registry, claimed

    async def dispatch(self, kind: str, mode: str, instructions: str) -> DispatchResult:
        child = self._registry.agents.get(kind)
        if child is None or mode not in ("from_scratch", "incremental"):
            return DispatchResult("refused", detail=f"unknown kind or mode: {kind} {mode}")
        process_id = self._claimed.process_id
        confirmation = (
            await runs.latest_confirmation(self._ws, process_id=process_id, run_kind=kind)
            if mode == "from_scratch"
            else None
        )
        try:
            run_id = await runs.admit(
                self._ws,
                process_id=process_id,
                kind=child,
                mode=mode,  # type: ignore[arg-type]
                initiated_by=self._claimed.initiated_by,
                entry_point="router",
                instructions=instructions,
                parent_run_id=self._claimed.run_id,
                intent_confirmation_id=confirmation,
            )
        except runs.AdmissionRefused as refused:
            if refused.code in {"needs_confirmation", "confirmation_invalid"}:
                item = await inbox.ask(
                    self._ws,
                    self._registry,
                    process_id=process_id,
                    category="confirmation",
                    question=f"Regenerating {kind} from scratch would discard the current result. Confirm?",
                    options=["confirm", "cancel"],
                    run_id=self._claimed.run_id,
                    kind="confirmation",
                    metadata={"run_kind": kind},
                )
                return DispatchResult(
                    "needs_confirmation", detail=f"asked the user (inbox item {item})"
                )
            return DispatchResult(
                "already_running" if refused.code == "already_running" else "refused",
                detail=str(refused),
            )
        return DispatchResult("admitted", run_id=run_id)
