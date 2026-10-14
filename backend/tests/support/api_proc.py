"""A real gateway process for the kill test (A§8.5): serves the API with uvicorn until SIGKILLed."""

import asyncio
import json
import sys
from pathlib import Path

import boto3
import uvicorn

from agentify.api.app import ApiContext, create_app
from agentify.api.server import uvicorn_config
from agentify.api.stream import StreamSettings
from agentify.identity import OIDCSettings, TokenVerifier
from agentify.registry import load_registry
from agentify.secretgate import SecretGate
from agentify.store.blobs import BlobStore
from agentify.store.db import Database
from agentify.store.workspace import Workspace


async def main(config: dict[str, str]) -> None:
    db = Database(config["conninfo"])
    s3 = boto3.client(
        "s3",
        endpoint_url=config["s3_endpoint"],
        aws_access_key_id=config["s3_key"],
        aws_secret_access_key=config["s3_secret"],
        region_name="eu-central-1",
    )
    gate = SecretGate()
    oidc = OIDCSettings(
        issuer=config["issuer"], audience=config["audience"], jwks_url=config["jwks_url"]
    )
    ctx = ApiContext(
        db=db,
        ws=Workspace(db, BlobStore(s3, "artifacts"), gate),
        registry=load_registry(Path(config["registry"])),
        verifier=TokenVerifier(oidc),
        oidc=oidc,
        gate=gate,
        stream=StreamSettings(heartbeat_interval=0.2, attach_seconds=0.5, poll_interval=0.05),
    )
    server = uvicorn.Server(
        uvicorn_config(create_app(ctx), host="127.0.0.1", port=int(config["port"]))
    )
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.argv[1])))
