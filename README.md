# Agentify

Agentify helps consultants capture how a client process works today, redesign it with AI agents, and hand over a design that a human has checked. Agents propose flows and specifications; deterministic checks and attributed human decisions decide what is kept. The specification is `genesis/PRODUCTION-BRIEF.md`.

## Status (checkpoint after slices 1 to 2e, `main` at `1c4ffa4`, 2026-10-15)

What exists is the backend foundation: the run harness, storage, the API and the first agents. It runs only under test, with scripted models.

Built and verified (see `VERIFICATION.md` evidence E-1 to E-14):

- Versioned workspace on Postgres and S3-compatible storage. A run either commits one revision or rolls back, and the rollback is verified. Leases, fencing and a sweeper recover from killed and stalled workers.
- The run harness on Pydantic AI. Flow and artifact gates re-read stored bytes, every correctable error is counted, and runs are cancelled on request or on client disconnect.
- The current-state modeler, the three specification-section agents, specification synthesis and a router that dispatches them.
- A user-edit layer that agent runs never overwrite, with locks.
- The HTTP API with OpenID Connect sign-in, engagement roles, row-level security and a chat stream (Vercel AI SDK protocol).
- A secret gate on logs, run records, run events, chat, the inbox, HTTP bodies, version history and database logs, with keyed fingerprints, security events and an admin allow-list. Process stdout and traces are not drilled yet (BC-C.7).

Not built yet: a model provider (Claude on Bedrock EU), so no real model has run; the evaluation harness; the redesigner, profiler and governance engine; acceptance contracts and the sandboxed code generator; the frontend (waits for an approved design system, OD-11); deployment. Of the 285 requirement rows in the ledger, 106 are verified, 14 implemented, 142 proposed and 23 deferred.

## Documents

| Document | What it is |
|---|---|
| `genesis/` | The specification (highest authority) |
| `ARCHITECTURE.md` | The signed-off design, operator decisions (A§1) and deviations (A§25). It describes the whole system, built or not |
| `VERIFICATION.md` | Per requirement: proposed, implemented or verified, with the evidence records |
| `AGENTS.md` | How work is done here: process, skills, checks, rules |

## Setup and checks

Needs Python 3.13 with `uv`, and a running Docker daemon (the integration tests start Postgres and MinIO containers; without Docker they fail, they never skip).

```sh
cd backend && uv sync --locked && make check   # format, lint, mypy --strict, import contracts, all tests
cd .. && typos && gitleaks git . --redact      # from the repository root, as in CI
```

## Trying what exists

There is no command-line entrypoint yet. The orchestration tests are the way to watch a run end to end: a scripted model drives the real framework against real storage.

```sh
cd backend
uv run pytest tests/orchestration/test_run_exit_paths.py -v   # one run per exit path
uv run pytest tests/api/test_chat.py -v                       # the API and the chat stream
```

The synthetic eval case in `evals/synthetic/` is harness scaffolding, not product evidence. Real client material never enters this public repository.
