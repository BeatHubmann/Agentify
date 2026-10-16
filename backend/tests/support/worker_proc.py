"""Real worker processes for the kill, stall and shutdown tests (A§8.5).

Modes:
- `mid_run`: claim one run, stage some writes, then hang (the parent SIGKILLs it).
- `mid_run_edit`: as `mid_run`, for an incremental run over the golden flow (one node edited).
- `in_commit`: run to the commit and block inside the commit transaction (SIGKILL).
- `after_commit`: commit, then block before the run's completion is delivered (SIGKILL).
- `stall`: stage, then pause in a model step (the parent SIGSTOPs and SIGCONTs), then try to continue.
- `worker`: run the real worker loop; SIGTERM requests a graceful stop.
"""

import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

import boto3

from agentify.agents.flow_tools import build_modeler
from agentify.harness import execute, runs
from agentify.harness.worker import WorkerConfig, run_worker
from agentify.registry import load_registry
from agentify.store.blobs import BlobStore
from agentify.store.db import Database
from agentify.store.workspace import Workspace
from tests.support.gates import make_gate
from tests.support.scripted import Script, ScriptedSource, Step, golden_calls


class HangInsideCommit(Workspace):
    """Blocks inside the commit transaction, after the head row is locked, until killed."""

    async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
        inner = kwargs["validate"]

        async def hang_in_transaction(reader: Any, changed: Any) -> Any:
            result = await inner(reader, changed)
            print("IN_COMMIT", flush=True)
            await asyncio.sleep(3600)
            return result

        return await super().commit(**{**kwargs, "validate": hang_in_transaction})


class HangAfterCommit(Workspace):
    """Fault injection: the commit completes, then the process blocks until killed, as an executor
    that dies right after its run became terminal."""

    async def commit(self, **kwargs: Any) -> str:  # type: ignore[override]
        await super().commit(**kwargs)
        print("COMMITTED", flush=True)
        await asyncio.sleep(3600)
        raise AssertionError("killed before this point")


async def _hang() -> None:
    print("STAGED", flush=True)
    await asyncio.sleep(3600)


async def _stall() -> None:
    print("STAGED", flush=True)
    await asyncio.sleep(6)  # the parent SIGSTOPs the process inside this window, then SIGCONTs it


def _script(where: str) -> Script:
    if where in {"in_commit", "after_commit"}:
        return Script(golden_calls())
    if where == "mid_run_edit":
        return Script(
            [Step(calls=[("edit_node", {"id": "receive", "label": "R"})]), Step(before=_hang)]
        )
    if where == "stall":
        return Script([*golden_calls()[:3], Step(before=_stall), *golden_calls()[3:]])
    return Script([*golden_calls()[:3], Step(before=_hang)])


async def _as_worker(ws: Workspace, registry_root: str) -> None:
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    config = WorkerConfig(
        owner="worker-proc",
        queues=("current-state-modeler",),
        poll_interval=0.1,
        harness=execute.HarnessConfig(lease_seconds=5, renew_every=1),
    )
    await run_worker(
        ws,
        load_registry(Path(registry_root)),
        lambda _kind: ScriptedSource(_script("mid_run")),
        config,
        stop,
    )
    print("STOPPED", flush=True)


async def main(argv: list[str]) -> None:
    conninfo, endpoint, key, secret, run_id, registry_root = argv[:6]
    where = argv[6] if len(argv) > 6 else "mid_run"
    db = Database(conninfo)
    await db.open()
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="eu-central-1",
    )
    blobs = BlobStore(s3, "artifacts")
    workspace_for = {"in_commit": HangInsideCommit, "after_commit": HangAfterCommit}
    ws = workspace_for.get(where, Workspace)(db, blobs, make_gate())
    if where == "worker":
        await _as_worker(ws, registry_root)
        return
    kind = load_registry(Path(registry_root)).agents["current-state-modeler"]
    claimed = await runs.claim(
        ws, queue=kind.id, owner="doomed-worker", lease_seconds=2, run_id=run_id
    )
    assert claimed is not None
    final = await execute.execute(
        ws,
        claimed,
        kind,
        agent=build_modeler("m"),
        source=ScriptedSource(_script(where)),
        prompt="go",
        config=execute.HarnessConfig(lease_seconds=2, renew_every=0.5),
    )
    print(f"FINAL {final}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
