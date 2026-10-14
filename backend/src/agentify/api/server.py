"""Serving the API (deployment entrypoint, A§2). uvicorn's own log configuration is disabled, so its
records (including tracebacks of unhandled errors, which Starlette re-raises after the 500 handler
answered) reach the root handler and pass the secret gate (A§18.2)."""

from typing import Any

import uvicorn
from fastapi import FastAPI


def uvicorn_config(app: FastAPI, *, host: str, port: int) -> uvicorn.Config:
    return uvicorn.Config(
        app, host=host, port=port, log_config=None, access_log=False, proxy_headers=True
    )


def serve(app: FastAPI, *, host: str = "0.0.0.0", port: int = 8000, **_: Any) -> None:  # noqa: S104 - bound inside the private network
    uvicorn.Server(uvicorn_config(app, host=host, port=port)).run()
