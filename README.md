# Agentify

Agentify helps consultants capture how a client process works today, redesign it with AI agents, and hand over a design that a human has checked. Agents propose flows and specifications; deterministic checks and attributed human decisions decide what is kept. The specification is `genesis/PRODUCTION-BRIEF.md`.

## Status (after the step-2 close-out and slice 3.1, 2026-10-15)

What exists is the backend foundation: the run harness, storage, the API and the first agents. It runs only under test, with scripted models.

Built and verified (see `VERIFICATION.md` evidence E-1 to E-17):

- Versioned workspace on Postgres and S3-compatible storage. A run either commits one revision or rolls back, and the rollback is verified. Leases, fencing and a sweeper recover from killed and stalled workers.
- The run harness on Pydantic AI. Flow and artifact gates re-read stored bytes, every correctable error is counted, and runs are cancelled on request or on client disconnect.
- The current-state modeler, the three specification-section agents, specification synthesis and a router that dispatches them.
- A user-edit layer that agent runs never overwrite, with locks.
- The HTTP API with OpenID Connect sign-in, engagement roles, row-level security and a chat stream (Vercel AI SDK protocol).
- A secret gate on logs, run records, run events, chat, the inbox, HTTP bodies, version history and database logs, with keyed fingerprints, security events and an admin allow-list. Process stdout and traces are not drilled yet (BC-C.7).

The model layer (`agentify.llm`, slice 3.1) is built and tested, but no real model has run through it yet. Each agent role gets its model from a validated policy of EU-only Claude profiles; every call is checked for registered credentials and reserved against run, process, engagement and deployment budgets before it is sent. The live check is blocked on account access: the development AWS project cannot enable Claude (see `VERIFICATION.md`, E-17).

Not built yet: the evaluation harness; the redesigner, profiler and governance engine; acceptance contracts and the sandboxed code generator; the frontend (waits for an approved design system, OD-11); deployment. Of the 286 requirement rows in the ledger, 109 are verified, 16 implemented, 137 proposed, 23 deferred and 1 not applicable.

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

A live run on Claude in Bedrock EU is a separate, explicit command, never part of `make check` or CI. It needs a signed-in AWS profile with model access and spends real money under a local cap (see `tests/live/test_bedrock_live.py`):

```sh
cd backend
AGENTIFY_LIVE_PROFILE=agentify-bedrock uv run pytest -m live tests/live -p no:cacheprovider
```

The synthetic eval case in `evals/synthetic/` is harness scaffolding, not product evidence. Real client material never enters this public repository.
