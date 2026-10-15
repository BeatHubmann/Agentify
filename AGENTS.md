# AGENTS.md

## Process precedence

1. `genesis/` and the `agentic-project-harness` skill govern the project process: proposal → operator sign-off → governed development. No project files are written before the relevant proposal is approved.
2. Superpowers skills are techniques used *within* that process — test-driven development, systematic debugging, verification before completion, code review. Where a Superpowers skill prescribes its own process (brainstorming, writing or executing plans, worktrees, subagent-driven development), this file and the harness take precedence.
3. The vendored skills in `.claude/skills/` (provenance, pinned commits and scope in `.claude/skills/SOURCES.md`) are techniques too, under the same precedence: `humanizer` for prose, `ponytail-review` and `ponytail-audit` for report-only review, `ui-ux-pro-max` for UI/UX guidance once the frontend is unblocked.
4. Design output goes to `ARCHITECTURE.md`, never to `docs/superpowers/`.
5. Subagents are used only when they demonstrably help, never by default.

## Index

| Document | Role |
|---|---|
| `genesis/PRODUCTION-BRIEF.md`, `genesis/IMPLEMENTATION-PROMPT.md` | The specification. Highest authority for intended behavior |
| `ARCHITECTURE.md` | The design. Signed off at gate G2 on 2026-10-14 and authoritative. Records operator decisions and approvals (A§1) and every deviation from the brief (A§25) |
| `VERIFICATION.md` | Requirement → proof ledger. Tracks each requirement as proposed, implemented or verified, with evidence |
| Task plans and PR descriptions | Scope of one change, below the documents above |

Order of authority: specification → signed-off architecture → task plan. Tests, code, logs and eval results are evidence, not authority. Where evidence conflicts with an approved document, surface the conflict; do not silently pick one.

## Setup and checks

| Command | What it does |
|---|---|
| `cd backend && uv sync --locked` | Install the pinned toolchain and dependencies |
| `cd backend && make check` | Everything CI runs for the backend: `ruff format --check`, `ruff check`, `mypy --strict`, `lint-imports`, `pytest` |
| `typos` and `gitleaks git . --redact` (repository root) | Spell check and secret scan, as in CI |
| `cd tools/aislop && npm ci --ignore-scripts && cd ../.. && AISLOP_NO_TELEMETRY=1 tools/aislop/node_modules/.bin/aislop ci --human` | aislop quality gate, as in CI: fails on any error-severity finding or a score below `failBelow` in `.aislop/config.yml` |

`pytest` needs a running Docker daemon: orchestration and failure tests start real Postgres and MinIO containers. Without Docker they fail; they never skip silently. CI (`.github/workflows/ci.yml`) runs the same commands with actions pinned to commit SHAs.

## Working rules

- **No client-derived material in this repository**, anonymized or not. The repository is public. The real eval corpus and per-case eval results live in the private EU store described in `ARCHITECTURE.md` A§26.5; only synthetic cases (labelled as such) are committed.
- **Acceptance criteria belong to the operator.** An implementer may add tests, but changing an approved acceptance criterion or expected result needs an explicit operator decision, recorded separately from the code change.
- **Completion needs evidence.** A ledger row moves to *verified* only with the command run, the revision, the observed counts and result, and the date. A required check that cannot run is reported as *not run* or *blocked*, never as passed.
- **Commits** use Conventional Commits and the repository's configured identity, with no AI attribution or co-author trailers.
