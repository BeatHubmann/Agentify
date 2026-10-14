# Agentify backend

Python 3.13, managed with `uv`. Design: `../ARCHITECTURE.md`; proof ledger: `../VERIFICATION.md`.

```
uv sync --locked      # install the pinned toolchain and dependencies
make check            # everything CI runs: format, lint, types, import contracts, tests
```

`make test` needs a running Docker daemon: orchestration and failure tests start real Postgres 17
and MinIO containers (testcontainers). Without Docker those suites fail; they never skip silently.
