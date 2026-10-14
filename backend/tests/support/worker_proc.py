"""A real worker process for the kill test: claims one run and hangs after staging (A§8.5)."""

import asyncio
import sys
from pathlib import Path

import boto3

from agentify.agents.flow_tools import build_modeler
from agentify.harness import execute, runs
from agentify.registry import load_registry
from agentify.secretgate import SecretGate
from agentify.store.blobs import BlobStore
from agentify.store.db import Database
from agentify.store.workspace import Workspace
from tests.support.scripted import Script, Step, golden_calls


async def main(argv: list[str]) -> None:
    conninfo, endpoint, key, secret, run_id, registry_root = argv
    db = Database(conninfo)
    await db.open()
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="eu-central-1",
    )
    ws = Workspace(db, BlobStore(s3, "artifacts"), SecretGate())
    kind = load_registry(Path(registry_root)).agents["current-state-modeler"]
    claimed = await runs.claim(
        ws, queue=kind.id, owner="doomed-worker", lease_seconds=2, run_id=run_id
    )
    assert claimed is not None

    async def hang() -> None:
        print("STAGED", flush=True)
        await asyncio.sleep(3600)

    script = Script([*golden_calls()[:3], Step(before=hang)])
    await execute.execute(
        ws,
        claimed,
        kind,
        agent=build_modeler("m"),
        model=script.model(),
        prompt="go",
        config=execute.HarnessConfig(lease_seconds=2, renew_every=0.5),
    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:7]))
