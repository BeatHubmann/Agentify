# Agentify: architecture

> **Status:** signed off by the operator at gate G2 on 2026-10-14 (PR #1), together with the approvals recorded in A§1.1. No implementation code exists yet. Mechanisms below are designed, not built; `VERIFICATION.md` tracks, per requirement, whether it is proposed, implemented or verified. Changes to this document now go through governed development: a PR that states the change and its reason.
> **Authority:** `genesis/PRODUCTION-BRIEF.md` and `genesis/IMPLEMENTATION-PROMPT.md` are the specification. This document is the design that answers them. Where it departs from the brief, §25 says so, with reasons. Section references like "§6.3" point into the brief; "A§n" points into this document.
> **Date:** 2026-10-14.

## Contents

1. Operator decisions
2. System shape
3. Technology choices
4. Module layout and dependency rules
5. Persistent data model
6. Artifact contracts
7. Workspace versioning and run isolation
8. Run harness
9. Agent layer and extension registry
10. Error contract
11. Artifact lifecycle and readiness
12. Governance engine
13. Node profile and provenance
14. Acceptance contracts, code generation, sandbox, conformance
15. Simulation (release 2, designed now)
16. Regulatory mapping (deferred, designed at contract level)
17. Provider abstraction and budgets
18. Secret gate
19. Identity, authorization, tenancy, separation of duties
20. Chat, inbox, languages
21. Observability
22. Invariant enforcement map (the ten required items, item 1 and 2)
23. Hazard register (required item 4)
24. Open problems: launch and deferral (required item 5)
25. Deviations from the brief (required item 6)
26. Test strategy (required item 10)
27. Build order and first slice
28. Evidence log (spikes and research)

The implementation prompt's ten required items map to: (1) and (2) A§22; (3) A§11; (4) A§23; (5) A§24; (6) A§25; (7) A§4–A§6, A§13–A§16; (8) A§10; (9) A§12.2–A§12.3, A§14.1–A§14.2, A§15; (10) A§26.

---

## 1. Operator decisions

Recorded 2026-10-14 (gate G1). They are binding inputs; changing one is an operator decision, not a design change.

| ID | Decision | Answer |
|---|---|---|
| OD-1 | Deployment | Cloud-hosted, built to a production standard; architecture functionally similar to the operator's reference project (Vercel UI, public gateway, private agent, worker) |
| OD-2 | Hosting | Vercel serves the UI; Fly.io (`fra`) runs a public **gateway**, a private **agent** (no public IP) and a **worker** from one image; managed Postgres and object storage in the same region; sandboxes are ephemeral Fly Machines on an isolated network |
| OD-3 | Residency | All stored data in the EU (Frankfurt), including backups and logs; **all model inference in the EU** |
| OD-4 | Model provider | Claude on AWS Bedrock, `eu-central-1`, EU-scoped inference profiles only |
| OD-5 | Gateway and budgets | No gateway by default; the agent calls Bedrock through the provider abstraction; a Postgres budget ledger enforces limits; AWS Budgets is a lagging backstop; short-lived credentials via workload identity. **A corporate model gateway is a supported configuration option** (endpoint, auth style, budget source) |
| OD-6 | Stack | Python backend; TypeScript frontend with generated API types |
| OD-7 | Tenancy | One firm per deployment; **client engagements are the segregation unit**, enforced in the database, storage, background work and sandboxes |
| OD-8 | Sign-in | The firm's identity provider via generic OpenID Connect; roles and engagement assignments held by Agentify |
| OD-9 | Roles | Per engagement: consultant, waiver approver, qualified reviewer. Per deployment: admin (users, assignments, rule packs, catalogs; no engagement content by default). **Four-eyes enforced:** a waiver or sign-off must come from a role holder who did not author the affected revision |
| OD-10 | Languages | UI and output: English (source), German, Dutch, Spanish, Swedish. Non-source locales machine-translated; CI enforces parity; each key records whether a human reviewed it |
| OD-11 | Frontend | Backend first. UI work begins only after the operator supplies or approves a design-system document |
| OD-12 | Release 1 | Foundation + node profile and governance + acceptance contracts and conformance (§12.4 steps 1–6). Simulation and regulatory mapping later |
| OD-13 | Code generation | Pluggable executor; first adapter is Claude Code headless in a sandbox, on Bedrock EU, with all of its model traffic through an Agentify-controlled endpoint |
| OD-14 | Launch-blocking open problems | §13.2 items 1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 15. Deferred: 4 (regulatory not in release 1), 8, 12, 14 |
| OD-15 | Optional capabilities (§5.1) | None in release 1 |
| OD-16 | Units | Currency (ISO 4217) and reporting period per engagement; every value carries its unit; staffing data (headcount, capacity, shift hours) in the profile |
| OD-17 | Golden corpus | Synthetic cases (labelled) now, for harness bring-up; ~10 anonymized real multilingual specifications from the operator gate release 1; only real cases are product evidence |
| OD-18 | Coverage reference | The merged current-state flow, including user edits |
| OD-19 | Repository | Public, with branch protection, secret scanning and push protection. **No client-derived material in the repository**, anonymized or not; the real corpus lives in a private EU store |
| OD-20 | Review | Draft PR on `design/architecture`; fresh-context invariant review before operator review |
| OD-21 | Database (2026-10-14, after G2) | **AWS RDS for PostgreSQL in `eu-central-1`** by default. Fly Managed Postgres only if Fly confirms in writing that its backups stay in the EU, decided before deployment (A§28 O-1) |

### 1.1 G2 approvals (2026-10-14)

The operator merged PR #1 on 2026-10-14 at 20:29 CEST (18:29 UTC) and confirmed that the merge approves, as proposed:

- the design in this document;
- the initial rule pack `agentify-core@1.0.0`: its four blocking rules and the autonomy ceilings (A§12.4);
- deviations D-1 to D-15 (A§25);
- the starting values: error budgets 5 per tool, 8 per error class, 20 per run (A§10.3); incremental shrink bound 30% (A§8.6); disconnect grace period 15 s (A§8.5); from-scratch confirmation window 15 minutes (A§8.2). Starting values are tunable from measured retry and eval statistics; each change is recorded with its evidence.

Branch protection on `main` is to be added at scaffolding, with the CI status checks (A§26.6).

Not yet decided, because their capability is not in release 1: jurisdictions, first obligation catalog, reviewer qualifications, liability wording (§13.2.4).

---

## 2. System shape

```
                Browser (UI later, OD-11) · API clients and tests today
                                │ HTTPS
  Vercel        ┌───────────────▼─────────────┐
                │ web (static UI, later)       │
                └───────────────┬─────────────┘
                                │ HTTPS, OIDC bearer token
  Fly.io, main  ┌───────────────▼─────────────┐
  organization  │ gateway (public)             │ auth, authz, rate limits, HTTP API,
  region fra    │                              │ chat stream relay, job endpoints
                └──────┬──────────────┬───────┘
                       │ private 6PN   │
                ┌──────▼──────┐  ┌────▼──────────────┐      ┌─────────────────────────┐
                │ agent        │  │ worker             │      │ sandbox-gateway: Bedrock │
                │ (private)    │  │ (private)          │      │ pass-through             │
                │ interactive  │  │ assessment, codegen│      │ sandbox-exchange: bundle │
                │ runs         │  │ conformance,       │      │ and result per job       │
                │              │  │ sweeper            │      │ (both public, per job)   │
                └──┬───────┬──┘  └──┬──────┬─────┬───┘      └───────▲────────┬────────┘
                   │       │        │      │     │ Machines API      │        │
                   │       │        │      │  ┌──▼────────────────┐  │        │
  Fly.io, sandbox  │       │        │      │  │ sandbox machines   │──┘ 443   │
  organization     │       │        │      │  │ (ephemeral, per    │  only    │
  (nothing else)   │       │        │      │  │  job, no creds)    │          │
                   │       │        │      │  └────────────────────┘          │
                   │       └────────┼──────┼─────────────┐                    │
                   │                │      │     SigV4, short-lived creds     │
                   │                │      │   ┌─────────▼────────────────────▼─┐
  AWS eu-central-1 │                │      │   │ Bedrock (EU inference profiles) │
                   │                │      │   └────────────────────────────────┘
                ┌──▼────────────────▼──┐ ┌─▼──────────────────────────────┐
                │ Postgres (EU)         │ │ S3 eu-central-1                 │
                │ runs, revisions,      │ │ artifacts bucket (immutable)    │
                │ inbox, chat, ledger … │ │ runtime bucket (scratch, TTL)   │
                └──────────────────────┘ └────────────────────────────────┘
```

**Process roles.** One application image, pinned by digest, with five roles selected by command: `gateway`, `agent`, `worker`, `sandbox-gateway` and `sandbox-exchange`. The `sandbox` image is separate, because it contains the code-generation toolchain, and runs only in the sandbox organization. Model credentials exist only in `agent`, `worker` and `sandbox-gateway`; storage credentials only in `agent`, `worker` and (for its prefix) `sandbox-exchange`; the gateway holds neither and has no route to Bedrock (as in the reference project, where provider keys live only in the private agent).

**Why a separate agent and worker.** Interactive runs (chat turns, flow authoring dispatched from chat) need low latency and stream to a user. Background jobs (code generation, conformance, assessment) are long, CPU-bound or wait on sandboxes. Both roles run the **same run harness** (A§8) against the same durable queue, partitioned by queue name. Splitting them keeps a long code-generation job from starving chat, and lets each scale independently.

**Why a sandbox gateway.** Sandboxed code generation must hold no AWS credentials, and its model traffic must pass the budget ledger and the secret gate (OD-13). Sandboxes live in a separate Fly organization with no private-network route to Agentify (A§14.5), so they reach Agentify only over two public, per-job-authenticated endpoints: the model gateway and a separate exchange for bundles and results, so that the service holding model credentials holds no storage credentials and vice versa. The `agent` and `worker` call Bedrock in-process through the provider abstraction (A§17); the sandbox gateway is a thin HTTP layer over that same abstraction, so there is one code path for model access.

**Chat path.** The client sends one new user message to the gateway. The gateway authenticates, then asks the harness to admit a run (A§8.2). The run executes in an `agent` process. Its events (model text, tool activity, subagent previews) are appended to a run-event log in Postgres and published on a notification channel. The gateway relays them to the client as an AI SDK UI message stream (A§3). The HTTP request never owns the run: the gateway can restart, and the client can reconnect, without touching the run's lifecycle. A client disconnect is reported to the harness, which cancels the run if the client does not reconnect within a configurable grace period (A§8.5).

---

## 3. Technology choices

Each choice names the requirement it serves. Versions are pinned in lockfiles at scaffolding; the versions below are the ones the spikes ran against.

| Concern | Choice | Why (requirement) | Evidence |
|---|---|---|---|
| Backend language | Python 3.13 | OD-6; numerics and property-testing ecosystem (§12.3) | — |
| Package and env manager | `uv` with a committed lockfile | One pinned version of everything (§8.8, §11.10) | — |
| HTTP API | FastAPI + Uvicorn | Generates OpenAPI from Pydantic models; frontend types are generated from it (§7.13) | — |
| Schemas | Pydantic v2 | Every artifact schema is a Pydantic model; JSON Schema is exported for contracts and tools (§12.1 layer 1) | — |
| Agent framework | Pydantic AI 2.56 | Scripted-model seam (`FunctionModel`) that drives the real framework (§12.5B); step-wise `agent.iter()` for harness-owned cancellation (§6.3 long-session loop); `AgentRun.cancel()`; explicit Bedrock provider construction (§6.8); built-in Vercel AI and AG-UI stream adapters (§6.1) | Spike S1 (A§28) |
| Streaming chat protocol | Vercel AI SDK UI message stream (data stream protocol v6), via Pydantic AI's `VercelAIAdapter` | Established, publicly specified, consumed by off-the-shelf chat UIs (§6.1, §7.8) | Adapter present (S1) |
| Database | PostgreSQL with pooled connections: **AWS RDS in `eu-central-1`** (OD-21); Fly Managed Postgres in `fra` only if Fly confirms EU backup storage in writing (A§28 O-1) | Concurrent writers, transactional admission and commit, row-level security (§11.3, §8.6) | Research R2 |
| Migrations | Alembic for the database; registered pure functions for artifact schemas (A§6.10) | §11.7, §13.2.10 | — |
| Durable queue | Postgres-backed queue (table + `SELECT … FOR UPDATE SKIP LOCKED`, leases) owned by the harness | Restart-safe, multi-worker, no extra infrastructure, same transaction as admission (§11.2) | — |
| Object storage | AWS S3 `eu-central-1` (A§28 O-2), two physically separate buckets: `artifacts` and `runtime` | Shared across machines; versioned artifacts and runtime state physically separate (§6.5) | Research R2 |
| Graph algorithms | NetworkX | Reachability, dominance and path queries for governance (§6.9); well-tested library over hand-written traversals | — |
| Property testing | Hypothesis | §12.3 property layer, generated graphs with planted violations | — |
| Simulation (release 2) | NumPy (`Generator` with `SeedSequence.spawn`) + SimPy for discrete-event queues | Reproducible, parallelizable Monte Carlo (§6.11) | — |
| Tests | pytest, anyio, testcontainers (Postgres 17, MinIO) | Integration tests against real storage (§8.11). MinIO no longer publishes images to Docker Hub; tests use Chainguard's source-built MinIO image, pinned by digest | Slice 1 (E-1) |
| Lint, format, types | ruff (lint and format), mypy `--strict`, import-linter for layering | §12.2 DoD-code; layering rules (A§4) are executable | — |
| Secret scanning (repository) | gitleaks in CI, plus GitHub push protection | §12.2 DoD-code | Enabled 2026-10-14 |
| Observability | OpenTelemetry (Pydantic AI and FastAPI instrumentation), exported to an EU backend chosen at deployment | §11.8, OP-15 | — |
| Frontend (later) | TypeScript; API types generated from OpenAPI | §7.13 | — |
| Code-generation engine | Executor interface; first adapter: Claude Code headless | OD-13 | Research R3 |

---

## 4. Module layout and dependency rules

```
backend/
  pyproject.toml, uv.lock
  src/agentify/
    domain/          pure: flow schema, text normalization, user-edit merge, flow invariants,
                     coverage, node profile, provenance, units, acceptance-contract schema,
                     readiness function, identifiers
    governance/      pure: rule language, evaluator, witnesses, autonomy scale
    dataflow/        pure: schema subset and structural subtype check (dataflow typing)
    registry/        loads and validates declarative extensions (A§9.4)
    store/           Postgres repositories, blob store, revisions, run staging,
                     runtime-state store (separate module, separate bucket)
    harness/         admission, run state machine, queue, cancellation, gate, commit,
                     verified rollback, sweeper, error budget, artifact locks, worker loop
    editing/         user-edit batches: server-side anchors, 409 on a stale base, commit as a user revision
    agents/          router, subagent kinds, flow-authoring tools, profiling agent,
                     contract agent; catalog building each kind's agent from its declared toolset
    llm/             provider abstraction, per-role model policy, budget ledger
    sandboxgw/       sandbox model gateway: Bedrock pass-through over llm/
    exchange/        sandbox exchange: per-job bundle download and result upload
    secretgate/      the single exit gate (A§18)
    sandbox/         executor interface; Fly Machines adapter; local container adapter (dev/CI)
    codegen/         Claude Code adapter, stream reader, layout normalization
    conformance/     contract test runner input/output, report builder
    inbox/           inbox service
    identity/        OIDC verification, users, roles, assignments, four-eyes checks
    api/             FastAPI app, routes, stream relay
    i18n/            message catalogs, parity check, output-language directive
    observability/   tracing, structured run events, cost attribution
  migrations/        Alembic
  tests/             unit/ property/ analytic/ orchestration/ failure/ api/ sandbox_local/ sandbox_drill/
                     smoke/ e2e_canary/   (orchestration, failure and api use real Postgres and MinIO containers)
registry/            declarative extensions: skills/, agents/, artifacts/, inbox/,
                     rulepacks/, defaults/, catalogs/ (data only, no code)
evals/               eval harness code, rubric, synthetic corpus (real corpus: private store)
contracts/           generated OpenAPI document and TypeScript client types
deploy/              Dockerfiles (base images by digest), fly.*.toml, verification scripts
```

**Dependency direction** (§6.1), enforced by import-linter in CI:

`api → harness → agents → {governance, dataflow, domain}`; `harness → store`; `agents → llm`. Agents need to stage writes, count errors and request child runs, but must not depend on the harness implementation, so the harness defines small protocols in `harness.ports` (`StagingPort`, `ErrorBudgetPort`, `AdmissionPort`) that it injects into each run's dependencies; `agents` may import `harness.ports` and nothing else from `harness` or `store`. `{domain, governance, dataflow}` import nothing from `store`, `harness`, `agents`, `llm`, `api`, and perform no I/O. `secretgate` is imported by every exit (A§18) and imports only `domain` types. Nothing except `llm/` constructs a model provider (A§17); a lint contract forbids importing provider modules elsewhere.

**Naming.** The brief's terms are descriptive (§4.1). Agentify's names:

| Brief term | Agentify name |
|---|---|
| process (engagement process under redesign) | `Process` |
| client engagement | `Engagement` |
| task agent / orchestrator / actor / non-agent step | `NodeKind.TASK` / `ORCHESTRATOR` / `ACTOR` / `STEP` |
| execution type | `Execution.DETERMINISTIC` / `AI` / `HUMAN` / `EXTERNAL` |
| current-state flow / redesigned flow | `Flow` with `mode = CURRENT` / `REDESIGN` |
| user-edit layer | `EditLayer` |
| run harness gate | `Gate` |
| rule pack / finding / witness / waiver | `RulePack` / `Finding` / `Witness` / `Waiver` |
| acceptance contract | `NodeContract` |
| regulatory sign-off | `SignOff` |

---

## 5. Persistent data model

### 5.1 Storage locations

| Location | Holds | Versioned | Touched by rollback |
|---|---|---|---|
| Postgres (EU) | Identity, engagements, processes, runs, run staging, revisions and their path maps, inbox, chat, waivers, sign-offs, readiness records, governance metrics, budget ledger, job queue, run events | Revisions are immutable rows; mutable operational tables are not artifacts | Only the run's own staging rows and run record |
| Bucket `artifacts` (EU) | Content-addressed artifact blobs, `<engagement_id>/sha256/<hex>`, written with a conditional `If-None-Match: *` put so an existing key is never overwritten; deduplicated within an engagement, never across engagements | Yes, through revisions | **Never** (blobs are only added; garbage collection, A§7.3, removes only unpinned, unreferenced blobs) |
| Bucket `runtime` (EU) | Sandbox exchange objects (per-job bundles and results, deleted after the job), package caches, simulation scratch | No | **Never.** No commit can reference it; the rollback code path has no client for it |

Two buckets with separate IAM roles. The `agent` role can write only `artifacts`; the `sandbox-exchange` role can read and write only the `runtime/exchange/` prefix; the `sandbox-gateway` role has no storage access; the `worker` holds both roles, in two separate clients, because it stages sandbox bundles and commits results. For `agent`, `sandbox-gateway` and `sandbox-exchange`, "runtime state is never committed and never deleted by rollback" (§6.5, HZ-17) is a property of the credentials. In the `worker` it rests on code structure (the commit and rollback modules receive only the `artifacts` client), which the HZ-17 drill tests. Sandboxes hold no storage credentials at all (A§14.5).

### 5.2 Core tables (abridged; Alembic owns the DDL)

```
engagement(id, name, currency CHAR(3), period, default_output_language, created_by, …)
membership(engagement_id, user_id, role)                  -- consultant | waiver_approver | reviewer
process(id, engagement_id, name, output_language, …)
workspace_head(process_id PK, revision_id, updated_at)    -- the single moving pointer per process
revision(id, process_id, parent_id, run_id NULL, author_kind, author_id, message,
         prompt_version, created_at)                       -- immutable
revision_path(revision_id, path, blob_sha256, schema_version)   -- full path map per revision
run(id, process_id, kind, mode, state, base_revision_id, initiated_by, entry_point,
    intent_confirmation_id NULL,                           -- human confirmation for from-scratch (A§8.2)
    lease_owner, lease_epoch, lease_expires_at,            -- fencing token (A§8.4)
    cancel_requested_at, stream_attached_until NULL,       -- durable disconnect detection (A§8.5)
    staged_paths TEXT[],                                   -- snapshot used by rollback verification
    instructions TEXT,                                     -- what the router or API asked the run to do
    outcome, outcome_detail_scrubbed, error_budget_json, created_at, finished_at)
run_staging(run_id, path, blob_sha256, schema_version, written_at)   -- the run's private writes
blob_pin(engagement_id, blob_sha256, pinned_by_run, pinned_at)       -- protects blobs from GC (A§7.3)
revision_input(revision_id, kind, ref)                 -- inbox items, answers, current-state basis a run consumed
run_event(run_id, seq, kind, payload_scrubbed, created_at)          -- append-only
job(id, queue, run_id, payload, state, attempts, lease_owner, lease_expires_at, …)
assessment_job(id, process_id, revision_key UNIQUE, commit_seq, state, lease_owner, lease_expires_at, …)  -- commit_seq orders jobs per process
chat_message(process_id, seq, author_kind, author_id, run_id, content_scrubbed, created_at)  -- append-only
inbox_item(item_id, version, process_id, parent_item_id, kind, category, status, options, body_scrubbed,
           author_kind, author_id, created_at)   -- PK (item_id, version); a status change is a new version
intent_confirmation(id, process_id, run_kind, scope, confirmed_by, created_at)   -- attributed human intent
waiver(id, process_id, finding_key, revision_key, approver_id, justification_scrubbed,
       checked_authors UUID[], created_at)
sign_off(id, process_id, artifact_path, revision_key, reviewer_id, statement_scrubbed,
         checked_authors UUID[], created_at)
rule_pack_activation(pack_id, version, approved_by, approved_at, activated_by, activated_at)
readiness(revision_key PK, process_id, state, report_blob, computed_at)
budget_reservation(id, scope_kind, scope_id, run_id, amount_microunits, state, …)
budget_limit(scope_kind, scope_id, period, limit_microunits)
rule_waiver_stats(rule_id, pack_version, findings, waivers)     -- governance metrics
```

**Constraints that carry invariants:**

- `run`: partial unique index on `(process_id, kind) WHERE state NOT IN ('committed','unchanged','rolled_back','rollback_failed')`, that is, over every non-terminal state, so a new state added to the machine is covered automatically. A test enumerates the state enum and asserts the index predicate matches exactly the non-terminal states. That index **is** invariant 6: no code path can insert a second active run of a kind for a process, whichever entry point calls admission (A§8.2).
- `workspace_head`: updated only by compare-and-swap inside the commit transaction (A§7.3).
- `revision`, `revision_path`, `revision_input`, `chat_message`, `inbox_item`, `run_event`, `waiver`, `sign_off`, `intent_confirmation`: no `UPDATE` or `DELETE` grant for the application role. Immutability is enforced by database permissions; history cannot be rewritten by application code (invariant 7).
- Database logging never records statement parameters (`log_statement = none`, `log_parameter_max_length = 0`, `log_parameter_max_length_on_error = 0`), so query values cannot leak through database logs (A§18).
- Every engagement-scoped table has `engagement_id` (directly or through `process`) and a **row-level security** policy keyed on a per-transaction setting `agentify.engagement_ids`. The API layer checks membership; RLS is the second wall (OD-7).

---

## 6. Artifact contracts

All artifacts are Pydantic models serialized as canonical JSON (sorted keys, UTF-8, no insignificant whitespace) so that content hashes are stable. Every artifact has the same **envelope**:

```python
class Envelope(BaseModel):
    schema_version: int
    artifact_type: str                  # registry artifact type id
    output_language: LanguageCode       # the process setting at write time (A§20)
    inputs: InputManifest               # what it was derived from: blob hashes, revision key,
                                        # rule-pack / catalog / engine / prompt versions, seed
    body: <type-specific model>
```

**Normalization happens in the canonical serializer**, not only in field types: before any artifact, edit-layer patch, registry-typed document or test-case payload is hashed or stored, the serializer applies `normalize_text` (A§6.1) to **every string leaf**, including strings inside free-form `dict` payloads, test-case inputs and JSON-Schema-validated documents. There is one serializer and every write path uses it, so §4.2's "every text field, every write path" holds by construction (HZ-3).

Paths inside a workspace:

```
inputs/<upload-id>.<ext>        uploaded documents; no UI action deletes them
spec/current.json  spec/desired.json  spec/constraints.json  spec/specification.json
flows/current/flow.json         flows/current/edits.json      flows/current/layout.json
flows/redesign/flow.json        flows/redesign/edits.json     flows/redesign/layout.json
profiles/current.json           profiles/redesign.json
contracts/redesign.json
governance/redesign.report.json dataflow/redesign.report.json   (derived; assessment jobs)
build/plan.json  build/handover.json   build/handover.md (rendered from handover.json for reading)
generated/MANIFEST.json         GeneratedManifest: ready revision key, contracts blob, tree hash, iteration, executor version
generated/…                     code-generation output, committed by the codegen job
conformance/report.json         (derived)
```

Each path's writer kinds are declared in the registry (A§9.4); write tools are bound to those paths. `layout.json` holds only positions and layout direction; it is part of the user-edit layer but kept in its own path so that dragging a node is neither a gate input nor part of the revision key (A§11.1).

### 6.1 Flow (authored level)

```python
class Flow(BaseModel):
    mode: FlowMode                      # CURRENT | REDESIGN
    nodes: list[Node]
    edges: list[Edge]
    coverage: list[CoverageEntry] = []  # REDESIGN only

class Node(BaseModel):
    id: AgentNodeId                     # ^[a-z][a-z0-9_-]{0,63}$, never starts with "u_"
    label: NormalizedText
    description: NormalizedText = ""
    kind: NodeKind                      # TASK | ORCHESTRATOR | ACTOR | STEP
    execution: Execution                # DETERMINISTIC | AI | HUMAN | EXTERNAL
    parent: NodeId | None = None        # container membership
    roles: set[NodeRole] = set()        # e.g. CHECKPOINT (a human step that can stop an effect)
    outputs: list[Output] = []          # Output(id, name, schema_ref | None)
    inputs: list[InputBinding] = []     # InputBinding(name, source: OutputRef): data dependencies
    preconditions: Precondition | None  # AllOf | AnyOf | NoneOf over ConditionAtom

class ConditionAtom(BaseModel):         # a test on an upstream output
    output: OutputRef
    op: Literal["eq", "ne", "in", "lt", "le", "gt", "ge", "present", "absent"]
    value: JsonScalar | list[JsonScalar] | None

class Edge(BaseModel):
    id: AgentEdgeId
    source: NodeId
    target: NodeId
    condition: NormalizedText | None = None

class CoverageEntry(BaseModel):
    redesign_node: NodeId
    covers: list[CurrentNodeRef]        # ids in the merged current-state flow (OD-18)
```

The current-state basis a redesign was built against is **not** authored: the harness records it in the redesign revision's input manifest (`inputs.current_state = (flow blob, edit-layer blob)`), from what the run actually read.

**Decisions the brief leaves to the design (§4.2):**

- **Ordering has one source of truth: edges.** `inputs` are *data* dependencies only. Invariant: the producer of every input must be an ancestor of the consumer in the execution graph (below), so the two encodings cannot disagree.
- **Self-loops are rejected.** A rework loop must pass through at least one other node, so loops are visible and boundable.
- **At most one edge per ordered pair `(source, target)`.** Alternative conditions between the same pair are one edge with one condition label. This keeps witnesses unambiguous.
- **Archetype implies AI execution:** `kind ∈ {TASK, ORCHESTRATOR, ACTOR}` requires `execution = AI`. Violations are rejected at authoring time with a correctable error (A§10). Migrations may normalize silently; nothing else does.
- **Containment:** `parent` must resolve to an `ORCHESTRATOR` or `ACTOR`; containment must be acyclic; an edge never connects a node to its own ancestor or descendant. A **child** of container *c* is a node whose `parent == c.id`; direct children only. That one function (`children(flow, c)`) is used by the gate check and in its error message (§6.3).
- **Text normalization:** `normalize_text` applies, in order: HTML-entity decoding iterated to a fixpoint, Unicode NFC, removal of control characters except `\n` and `\t`, and trimming. Applying it twice equals applying it once (property test). It runs as the `NormalizedText` field validator and in the canonical serializer (A§6).

### 6.1.1 Execution semantics

Governance path rules, dataflow ancestry and simulation all need one definition of what a flow *means*. Token semantics, defined once in `domain.semantics` and used by all three:

- **Splits.** If all outgoing edges of a node are conditional, the split is **exclusive** (XOR): exactly one is taken, and its branch probabilities sum to 1 (A§6.3). If all are unconditional, the split is **parallel** (AND): all are taken. A node with both kinds of outgoing edges is rejected at the integrity level (correctable error).
- **Joins.** A node with several incoming edges waits for **all** of them if its preconditions are `AllOf` over its predecessors' outputs (AND-join), and proceeds on the **first** arrival otherwise (XOR-join).
- **Start and end.** Start nodes are top-level nodes (no parent) without incoming edges; end nodes are top-level nodes without outgoing edges. Every flow has at least one of each.
- **Containers are subprocesses.** An edge into a container enters the start nodes among its children (children without incoming edges from siblings); the container is left through its outgoing edges once its children's end nodes complete. Edges may connect siblings only (nodes with the same parent), so containment and edges never contradict each other.
- **The execution graph** is the flow with every container expanded into its children by those rules. Path rules (A§12), dataflow ancestry and the expected-value model (A§13) operate on it. A child task with an irreversible effect inside an actor is therefore on every path through that actor, and the oversight rule sees it.

**Validation levels.**

1. *Schema*: Pydantic parse.
2. *Integrity*: unique node and edge ids; edge endpoints exist; parents resolve; containment acyclic; edges only between siblings; references in inputs, outputs and preconditions resolve; no self-loops; no duplicate pairs; archetype–execution consistency; no mixed splits; data dependencies consistent with the execution graph.
3. *Flow invariants*: integrity, plus structural soundness (at least one start and one end; every node lies on some start-to-end path in the execution graph), non-degeneracy, container arity and (for `REDESIGN`) coverage.

Level 2 runs on every write (each mutation tool, every user edit, every migration). Level 3 runs in the harness gate (A§8.6), in **every commit transaction** for each merged flow the commit affects (A§7.3), and in the readiness computation.

**Resolved level** (`ResolvedFlow`): the merge of `Flow` with the edit layer (A§6.2) and layout, plus provenance per item, lock flags, findings overlay and render flags. It is computed, cached by input hash, and **never authored or stored as an artifact** (§4.2 recommendation, §7.4).

### 6.2 Edit layer (user edits)

```python
class EditLayer(BaseModel):
    flow_path: str
    patches: list[Patch]                # applied in order
    locks: list[Lock]

class Layout(BaseModel):                # flows/*/layout.json, also user-owned
    positions: dict[NodeId, Position]
    layout_direction: LayoutDirection | None

class Patch(BaseModel):
    id: PatchId
    op: Literal["add_node","modify_node","remove_node","add_edge","modify_edge","remove_edge",
                "profile_override","contract_edit"]
    target: NodeId | EdgeId | ProfileFieldRef | ContractRef | None   # None for add_*
    anchor: ContentHash | None          # see "Anchors"
    payload: dict                       # validated per op; strings normalized by the serializer
    author_id: UserId
    created_at: datetime

class Lock(BaseModel):
    target: NodeId | EdgeId
    anchor: ContentHash
    author_id: UserId
```

- **Namespaces.** User-created node and edge ids are `u_<uuid7>`. Agent ids cannot start with `u_` (type constraint on `AgentNodeId`). A regenerated agent flow therefore cannot collide with a user item, by construction (§6.4, HZ-15).
- **Anchors.** A patch on an existing item records a content hash, and the merge applies it only if the target exists and its current hash equals the anchor. Otherwise the patch is **orphaned**: reported in the conflict report, never applied, never dropped.
  - `modify_*`, `remove_*` and locks: hash of the item's canonical authored content.
  - `add_*` of a user item that references agent items (a parent, an edge endpoint): the hashes of those referenced items; if one vanished or changed, the addition is orphaned instead of being attached to different content.
  - `profile_override` (including values set by answering an inbox question, A§13): the hash of the **node's** authored content, not of the agent's value. A re-estimate by the profiler does not orphan a human's value; a changed or removed node does, and the user is asked to confirm it again.
  - `contract_edit`: the hash of the contract clause it edits and of the node.
- **Removals cascade:** removing a node removes edges touching it, in the merged view.
- **Locks** apply to *agent-authored* nodes and edges. `lockable(item)` is one function used by the API, the merge and the gate. Locks on user-created items are accepted but have no effect on agent runs, because agent runs cannot modify user items (no write path, A§9.3). That answers §13.2.2's open question. A lock whose anchor no longer matches is a **ghost lock**: shown to the user, and incremental runs are refused at admission until the user resolves it (re-lock or release), rather than failing at the gate after the model has been paid for.
- **The merge** `merge(flow, layer, layout) -> (ResolvedFlow, ConflictReport)` is pure, deterministic and total: it raises nothing; every failure mode is a value in the report. If the edit layer cannot be parsed, callers pass `EditLayerUnreadable`: the merge returns the authored flow with a report entry saying so, rendering still works, and lock-sensitive runs and readiness are refused (A§22.3).
- **User edits are validated on write.** The edit API takes a batch and the edit-layer revision it was made against. In one commit transaction it checks that the layer has not changed since (otherwise `409 Conflict` with the newer layer, so two sessions cannot silently drop each other's patches), applies the batch, merges, and runs level-2 validation and the flow invariants on the merged result. A batch that would break an invariant is rejected with a structured error. Batches are atomic, so a multi-step edit (remove a child, add its replacement) can be submitted together (§4.2: every write path validates).

### 6.3 Node profile

```python
class NodeProfile(BaseModel):
    node: NodeId
    volume: Value[PerPeriod] | None            # cases per period
    processing_time: Value[Duration] | None
    waiting_time: Value[Duration] | None
    cost_per_execution: Value[Money] | None
    human_effort_per_case: Value[Duration] | None
    staffing_pool: PoolRef | None             # OD-16
    error_rate: Value[Probability] | None
    detection_probability: Value[Probability] | None
    rework_share: Value[Probability] | None
    autonomy: Value[AutonomyLevel] | None     # scale in A§12.3
    confidence_threshold: Value[Probability] | None
    escalation_target: NodeId | None
    decides_about_natural_persons: Value[bool] | None
    effect_reversibility: Value[Reversibility] | None   # REVERSIBLE | COSTLY | IRREVERSIBLE
    external_effect: Value[bool] | None
    data_categories: Value[set[DataCategory]] | None    # categories from the declared taxonomy
    data_leaves_organization: Value[bool] | None
    branch_probabilities: dict[EdgeId, Value[Probability]] = {}
    loop_bound: Value[int] | None             # maximum visits, for nodes that start a rework loop

class ProfileSet(BaseModel):
    flow_path: str
    flow_anchor: ContentHash                   # blob hash of the authored flow it was built for
    currency: CurrencyCode                     # from the engagement; mixing is rejected
    period: Period
    pools: list[StaffingPool]                  # StaffingPool(id, role, headcount, capacity_per_period, shift_hours)
    nodes: list[NodeProfile]
```

`Value[T]` is a discriminated union: `Point(value)`, `Range(low, high)` and `Triangular(low, mode, high)`, each with a `unit` and a `Provenance`:

```python
class Provenance(BaseModel):
    kind: Literal["stated", "estimated", "measured", "default"]
    citation: SpecCitation | None     # required for stated-from-document
    answered_by: UserId | None        # required for stated-by-user (inbox answer or override)
    reason: NormalizedText | None     # required for estimated
    defaults_set: DefaultsRef | None  # required for default: (set id, version, key)
    measurement: MeasurementRef | None  # required for measured
```

Validation (correctable errors to the profiling agent, A§10): exactly the fields required by `kind` are present; `estimated` and `default` values must be `Range` or `Triangular`, never `Point` (an estimate is uncertain by definition, §4.3); `low ≤ mode ≤ high`; probabilities in [0, 1]; currency matches the engagement; branch probabilities on each conditional node's outgoing conditional edges sum to 1 (point values; ranges must admit a sum of 1 and are resolved by normalization at sampling time, documented in A§15); a profile references only nodes of the flow it is anchored to; per-node entries carry the node's content hash, so entries for changed nodes are reported as stale instead of silently applied.

**Provenance propagation.** Every function that derives a figure from profile values returns a `Derived[T]` that carries the set of input field references and the set of provenance kinds it depends on. The type has no method that drops them, and a lint rule forbids constructing figures from raw numbers in report and rendering modules; Python cannot make this impossible, so the property tests check that every rendered figure has a non-empty trace (HZ-24).

**No figure without its range.** `Point` is allowed only with `stated` or `measured` provenance, for a value that is exact (a stated count, a probability fixed by the specification); everything uncertain is a `Range` or `Triangular` (§4.3). A `Derived` figure always carries an interval: for release-1 computations the interval is propagated from the input ranges (expected values are linear in node values for fixed branch probabilities; probability ranges are bounded by evaluating their range end points, with the method stated). The renderer refuses a `Derived` without an interval; a `Derived` whose interval has zero width is rendered with an explicit "exact" label, which is only possible when every input is a stated or measured `Point`. Nothing renders as a bare number (§5, HZ-23).

### 6.4 Governance rules, findings, waivers

See A§12 for the rule language. Data shapes:

```python
class Rule(BaseModel):
    id: RuleId                   # stable, e.g. "oversight.natural-persons-checkpoint"
    version: int
    severity: Literal["blocking", "advisory"]
    rationale: NormalizedText    # required; blocking rules must justify blocking
    predicate: RuleExpr          # A§12.2
    remediation: NormalizedText  # shown to agents and users
    inbox_on_finding: InboxCategory | None   # findings needing human judgment

class RulePack(BaseModel):
    id: str; version: SemVer; effective_from: date
    rules: list[Rule]
    autonomy_ceilings: list[AutonomyCeiling]

class Finding(BaseModel):
    rule_id: RuleId; rule_version: int; severity
    witness: Witness             # NodeWitness | EdgeWitness | PathWitness(node ids, edge ids)
    message: NormalizedText
    key: FindingKey              # hash(rule_id, rule_version, canonical witness)

class Waiver(BaseModel):          # stored in the waiver table; immutable
    finding_key: FindingKey
    revision_key: RevisionKey    # the exact assessed revision tuple (A§11)
    approver_id: UserId; justification: NormalizedText; created_at: datetime
    checked_authors: list[UserId]  # the author set the four-eyes check was evaluated against (A§11.2)
```

### 6.5 Acceptance contracts

```python
class NodeContract(BaseModel):
    node: NodeId
    node_anchor: ContentHash          # hash of the node's authored content when drafted
    input_schema: SchemaDoc           # A§14.1 schema subset
    output_schema: SchemaDoc
    preconditions: list[NormalizedText]
    success_criteria: list[Criterion]
    below_threshold: Literal["escalate", "retry", "refuse"]
    below_threshold_detail: NormalizedText
    cases: list[TestCase]             # ≥ 1 with kind = negative
    judgment: JudgmentSpec | None     # required for ORCHESTRATOR and ACTOR (A§14.2)

class TestCase(BaseModel):
    id: str; kind: Literal["typical", "edge", "negative"]
    input: dict; expect: Expectation
    trace: SpecTrace | AssumptionTrace

class Expectation(BaseModel):          # all assertions must hold
    assertions: list[Assertion]        # ≥ 1, and not only `matches_schema` (A§14.1)

Assertion = (Equals(path, value) | MatchesSchema(schema: SchemaDoc) | FieldPredicate(path, op, value)
             | DecisionIn(values) | DecisionNotIn(values) | Escalated(expected: bool) | Refused(expected: bool))
             # path: a JSON Pointer into the output; op as in ConditionAtom

class Criterion(BaseModel):            # success criterion, checked on every typical case
    id: str; description: NormalizedText; assertion: Assertion

class JudgmentSpec(BaseModel):         # A§14.2
    decision_values: list[str]
    scenarios: list[JudgmentScenario]  # ≥ 1 negative
    policy_constraints: list[PolicyConstraint]   # deterministic: when(ConditionAtom over input) -> Assertion
    rubric: list[RubricDimension] = [] # judged, reported separately

class JudgmentScenario(BaseModel):
    id: str; input: dict; acceptable: list[str]; forbidden: list[str]; must_escalate: bool
    negative: bool; trace: SpecTrace | AssumptionTrace

class SpecTrace(BaseModel):            # grounding reference, checked by code (A§14.1)
    artifact: Literal["spec/specification.json"]; artifact_blob: ContentHash
    section_id: str; paragraph_id: str; span: tuple[int, int]; quote_sha256: str

class AssumptionTrace(BaseModel):
    assumption: NormalizedText

class ContractSet(BaseModel):
    flow_anchor: ContentHash           # blob hash of the redesign flow it was drafted for
    contracts: list[NodeContract]
    orphaned: list[OrphanedContract]   # contracts whose node changed or vanished; never used for readiness
```

### 6.6 Simulation inputs and outputs (release 2)

Defined now so profiles collect the right data (OD-16). Inputs: both flows (merged), both profile sets, staffing pools, a seed, replication count, horizon, and a sensitivity plan. Outputs: per metric (throughput time, cost per case, human effort per period, escalation rate) the percentiles p5, p25, p50, p75 and p95 with their unit and a `trace` (the input fields and provenance kinds it depends on); error propagation (caught vs escaped per node, cost of escapes); stuck-case counts per node; sensitivity ranking; break-even curves; and the recorded inputs (flow and profile blob hashes, `seed`, `engine_version`). A§15 describes the method.

### 6.7 Obligation catalogs (deferred capability)

```python
class Catalog(BaseModel):
    id: str; framework: str; jurisdiction: str; version: SemVer
    effective_from: date; effective_to: date | None
    maintainers: list[str]                    # people; agents have no write path
    applicability_questions: list[ApplicabilityQuestion]
    obligations: list[Obligation]             # Obligation(id, citation, applies_when: RuleExpr over answers and profile,
                                              #            controls: list[ControlRef], mechanizable: RuleExpr | None)
```

### 6.8 Run and revision records

Shown in A§5.2. Every derived artifact (governance report, dataflow report, contract set, conformance report, later business case and register) records its `inputs`: the revision key it was computed from, rule-pack or catalog version, prompt version and, for simulation, seed (§4.4).

### 6.9 Identifier types

Distinct types for distinct identifiers (C-5): `EngagementId`, `ProcessId`, `RunId`, `RevisionId`, `JobId`, `AgentNodeId`, `UserNodeId` (union `NodeId`), `AgentEdgeId`, `UserEdgeId`, `PatchId`, `RuleId`, `FindingKey`, `RevisionKey`, `ContentHash`, `UserId`. They are `NewType`s over `str` with validated constructors; mypy `--strict` rejects mixing them.

### 6.10 Schema evolution (OP-10, launch-blocking)

- Every artifact envelope carries `schema_version`. Each artifact type registers pure migration functions `vN → vN+1`.
- **Reads** are tolerant: load, migrate in memory to the latest version, validate.
- **Writes** always produce the latest version.
- A migration returns the migrated artifact *and an anchor map* `old_hash → new_hash` for every item whose canonical content changed. Migrating an edit layer rewrites its anchors through that map, so user patches remain anchored across schema changes (§13.2.2 open question: identity survives migration).
- Migrations are tested like code: fixture artifacts of every historical version, plus a property test that migrating then re-migrating is stable and that anchored patches still apply to the same items.
- A migration is a commit by the system author (`author_kind = "migration"`), one revision per process, through the same commit path.

### 6.11 Specification, build and report artifacts

```python
class SpecSection(BaseModel):          # spec/current.json, spec/desired.json, spec/constraints.json
    section: Literal["current", "desired", "constraints"]
    paragraphs: list[SpecParagraph]
    open_questions: list[InboxRef]

class SpecParagraph(BaseModel):
    id: str                            # stable within the artifact, e.g. "cur-012"
    text: NormalizedText
    sources: list[InputCitation]       # (input upload id, page or offset span, quote_sha256)

class Specification(BaseModel):        # spec/specification.json, assembled by synthesis
    sections: list[SpecSection]        # exactly the three sections, in order

class BuildPlan(BaseModel):            # build/plan.json
    revision_key: RevisionKey          # the ready key it was planned from (A§11)
    nodes: list[BuildItem]             # BuildItem(node, contract_ref, entry_point, depends_on, notes)
    order: list[NodeId]
    out_of_scope: list[NormalizedText]

class HandoverPrompt(BaseModel):       # build/handover.md is rendered from this; the JSON is the artifact
    revision_key: RevisionKey; plan_blob: ContentHash; contracts_blob: ContentHash
    text: NormalizedText; output_language: LanguageCode

class AssessmentReport(BaseModel):     # governance/*.report.json, dataflow/*.report.json
    revision_key: RevisionKey; engine_version: str; rule_pack: PackRef
    findings: list[Finding]            # empty is a valid result; failure is never an empty report
    evaluated_values: list[EvaluatedValue]   # every profile value a rule read, with provenance (A§12.2)

class ConformanceReport(BaseModel):    # conformance/report.json
    code_blob_tree: ContentHash; contracts_blob: ContentHash; revision_key: RevisionKey
    nodes: list[NodeConformance]       # passed | failed(cases, reasons) | not_testable(reason), judged results separate
```

A citation is grounded when the cited quote's hash equals the hash of the cited span in the cited artifact blob. That check is code, so a fabricated citation fails validation (A§13, A§14.1).

### 6.12 Tool and HTTP interface contracts

The tool schema is the contract the model sees (§12.1 layer 3); each signature below is generated from these Python signatures and pinned by a snapshot test of the JSON schema the model receives.

**Flow-authoring tools** (current-state modeler, redesigner):

| Tool | Arguments | Returns / correctable errors |
|---|---|---|
| `add_node` | `id, label, kind, execution, parent?, description?, roles?, outputs?, inputs?, preconditions?` | `ok` / duplicate id, unknown parent, archetype–execution mismatch, invalid reference, locked item |
| `edit_node` | `id, fields: partial node` | `ok` / unknown id, locked item, any integrity violation |
| `delete_node` | `id` | `ok` (cascades edges) / unknown id, locked item, would orphan children |
| `add_edge` / `edit_edge` / `delete_edge` | `id, source, target, condition?` / `id, fields` / `id` | `ok` / unknown endpoint, duplicate pair, self-loop, cross-container edge, mixed split, locked item |
| `set_coverage` (redesign) | `redesign_node, covers: [current node ids]` | `ok` / unknown id, already covered elsewhere |
| `patch_text` | `path, search, replace` | escape hatch for long text fields: `ok` / no match, non-unique match, result invalid |
| `read_flow` | `merged: bool` | the staged flow (or merged view) |
| `governance_precheck` (redesign) | — | findings as correctable errors (A§12.5) |
| `finalize` | — | advisory gate pre-run (A§8.6) |

**Generic artifact tools** for registry artifact types without bespoke tools: `read_artifact(path)` and `patch_artifact(path, ops)`, where `ops` is an RFC 6902 JSON Patch applied to the staged artifact and validated against the type's schema and declared checks after every call. This keeps edits small and self-validating for new types without code (§6.3 item 1, §14).

**HTTP (generated OpenAPI is authoritative; abridged):** `POST /processes/{id}/chat` (stream), `POST /processes/{id}/runs` (admit), `POST /runs/{id}/cancel`, `GET /runs/{id}` and `/runs/{id}/events` (stream), `GET /processes/{id}/revisions`, `GET /processes/{id}/diff?from&to`, `POST /processes/{id}/restore` (`{revision, paths}`), `POST /processes/{id}/edits` (`{flow, base_layer_revision, patches[]}`, 409 on a stale base), `GET|POST /processes/{id}/inbox`, `POST /processes/{id}/waivers` (`{finding_key, revision_key, justification}`), `GET /processes/{id}/readiness`. Errors use one problem-details shape with a stable `code` and a translated `title` key; bodies are produced through the secret gate (A§18).

---

## 7. Workspace versioning and run isolation

### 7.1 Model

Each process has a **workspace**: a chain of immutable revisions. A revision is a full map `path → blob hash` (A§5.2), so any revision can be read without replaying history. History, diff and restore come from this model:

- **diff** between two revisions: the paths whose blob hashes differ, with an artifact-aware structural diff for flows, profiles and contracts (node added, field changed, provenance changed), and a text diff for everything else;
- **restore**: a new revision whose path map copies the old one for the restored paths (§6.5: restores are new revisions);
- **history**: never rewritten; database grants forbid `UPDATE`/`DELETE` on revision tables.

This is the brief's alternative to a version-control repository per workspace (§6.5). The reason for choosing it is in A§25 (D-1).

### 7.2 Run isolation

A run never writes to the shared workspace. Its write tools write to **`run_staging`** rows keyed by `run_id`: first a `blob_pin` row, then the blob (content-addressed, conditional put), then the staging row, all fenced by the run's lease epoch (A§8.4). Reads inside the run see `base revision ⊕ run's own staging`. Nothing outside the run can see the staging, and nothing the run does is visible to any other commit until its own commit transaction succeeds. A commit landing mid-run (a user edit, a sibling section run) cannot capture the run's partial writes, because those writes are not in any shared structure (HZ-10).

### 7.3 Commit

One function, `commit(author, base_revision, staged, …)`, is used for **every** writer: agent runs, human edits, restores, migrations and derived-report jobs. One Postgres transaction:

1. **Fence and lock.** For agent runs: `UPDATE run SET state = 'committing' WHERE id = $run AND state = 'gating' AND lease_owner = $me AND lease_epoch = $epoch AND cancel_requested_at IS NULL`. Zero rows updated raises and aborts the transaction (lease lost, or cancel requested after the last check). Then `SELECT … FROM workspace_head WHERE process_id = $1 FOR UPDATE`.
2. **Conflict check.** Let `head` be the current head and `base` the writer's base revision. Compute the paths changed between them. The commit fails with `base_moved_conflict` if any of them is a path the writer staged, or a path the writer declared as a **semantic input**: the current-state flow and its edit layer for a redesign run (coverage basis, OD-18), and the flow itself for a human edit batch. A change to the edit layer during an incremental run is *not* a conflict by itself: its locks are re-checked against head in step 4, and its patches are re-merged in step 4. Layout changes are never conflicts.
3. **Blob existence.** Every staged blob is checked to exist in the bucket (a `HEAD` request issued before the transaction and confirmed against the pin rows), so no revision can reference a missing blob.
4. **Validate the result.** Build the new path map: `head`'s map with **only the writer's staged paths** replaced. For every flow whose authored artifact, edit layer or current-state basis changed in this map, run the merge and level-3 validation (A§6.1) on the merged result; for agent runs, also re-check locks at head. Any failure aborts with a structured reason: an agent run rolls back; a human edit or restore is rejected with the reason (`422`). A current-state edit that leaves the committed redesign's coverage incomplete is allowed (people decide what the process is) but records the redesign as **coverage stale**: its revision key changes (A§11.1), it is no longer valid, readiness is blocked, and an inbox notice asks for an incremental redesign run.
5. **Write.** Insert the new `revision` (`parent = head`, run id, author, `prompt_version`, systematic message `run <kind> <run_id>: <paths>`), its `revision_path` rows and its `revision_input` rows (inbox items and answers consumed, current-state basis); update `workspace_head`; mark the run `committed`; delete its staging rows and pins; insert assessment jobs for affected revision keys (A§11.5).
6. Commit.

"Commits contain only the run's own paths" (§6.3 item 4) holds because step 5 builds the new map from head plus the writer's staging, nothing else. "One revision per run" (§6.5) holds because the fenced state transition in step 1 can succeed at most once per run.

**Restores** are commits whose staged content is the old revision's blobs for the chosen paths; they go through steps 1–6, including validation, like any other write.

**Garbage collection.** A periodic mark-and-sweep deletes a blob only if no revision references it, no staging row references it, no pin row from the last 24 hours covers it, and it is older than the grace period. Because the pin row is written *before* the deduplicated put is skipped or performed, a run reusing an old unreferenced blob protects it before the collector can see it as garbage.

### 7.4 Rollback and its verification

Rollback is: under the run's fence, mark the run `rolling_back`; record `staged_paths` on the run row (the snapshot verification needs); delete its staging rows and pins; mark it `rolled_back` with the reason. Because shared state was never touched, there is nothing to undo; rollback only discards. "Base state" means: **no effect of the run remains** in the workspace. Commits by other writers during the run (typically user edits) are preserved, as §12.5B requires ("a later rollback preserves it").

**Verification** (§6.3 item 4: rollback verifies itself) runs after every rollback and checks, against the database (not memory):

- no revision row has this `run_id`;
- no staging or pin rows remain for this `run_id`;
- for every path in `staged_paths`, the current head's blob hash equals the base revision's hash or was written by a revision of another writer (the run's writes are absent);
- the run's state is `rolled_back`.

If any check fails, the run is marked `rollback_failed`, an alert-level event is emitted, the process is **quarantined** (new admissions for that process are refused until an admin resolves it), and the failure is shown to the user. It is never reported as rolled back (HZ-14).

Rollback never touches the `runtime` bucket: the rollback code path has no client for it.

### 7.5 Cross-process locking

- Head updates: row lock on `workspace_head` inside the commit transaction (works across processes and machines).
- Run state: fenced conditional updates (`lease_owner`, `lease_epoch`) on the run row (A§8.4).
- Admission: the partial unique index (A§5.2).
- Human edits: the same commit function, with the edit-layer revision the client edited against as the base (409 on conflict, A§6.2).

### 7.6 Retention

History is immutable. Storage growth is bounded by content addressing (unchanged artifacts share blobs) and by retention policy, which is an operator setting; history garbage collection (§11.9) removes only blobs unreachable from any revision.

---

## 8. Run harness

### 8.1 Responsibilities

Every agent invocation, from any entry point, is a **run** owned by the harness. The harness, not the HTTP request and not the model, owns: admission, execution lease, cancellation, the gate, commit, rollback, rollback verification, run events and the run record (§6.1, §10 further requirements).

### 8.2 Admission

`harness.admit(process_id, kind, mode, initiated_by, entry_point, parent_run_id, intent_confirmation_id=None) -> RunId` is the **only** function that creates runs. It runs in one transaction:

1. Check the process is not quarantined (A§7.4).
2. **Mode and human intent.** A from-scratch run ignores locks and replaces the agent flow, so it needs **attributed human intent** whenever there is anything to lose: the existing flow is non-empty, has locks, or is corrupt, or its edit layer is unreadable. In those cases `admit` requires an `intent_confirmation` row for this process and run kind, created by a human through an explicit API action or by answering an inbox confirmation question. A confirmation is **single-use** (admission consumes it and records it on the run), valid for a configurable window (default 15 minutes), and scoped to `(process, run kind, from-scratch mode, head revision at confirmation time)`: if the workspace head moved since, it no longer applies, because the human confirmed discarding a specific state. The router's `dispatch_subagent` tool cannot create one: if it asks for from-scratch on a non-empty flow, the harness creates an inbox confirmation question and refuses with `needs_confirmation`. Agents therefore never choose to ignore locks (§6.3 item 5, invariant 3).
3. **Incremental runs** read and parse the edit layer at the base revision. If it is unreadable, refuse with `edit_layer_unreadable` (fail closed, §6.3 item 5). If it holds ghost locks, refuse with `ghost_locks` and the list, so the user resolves them before any model cost. If the authored flow is corrupt, refuse with `flow_corrupt` (rebuilding needs from-scratch with human intent, so corruption is never silently regenerated, §6.4).
4. **Redesign runs** load the merged current-state flow at the base revision and run the flow invariants on it. If missing or invalid, refuse with `current_state_invalid` (§6.3 item 3).
5. **Long jobs that require readiness** (`build-planner`, `codegen`, `conformance`) record the ready revision key they start from; their commit re-checks it (A§11.4).
6. Reserve the run's initial budget (A§17.3). If over budget, refuse.
7. Insert the run row (`state = admitted`). The partial unique index rejects a second active run of the same kind for the process: the insert fails and admission returns `already_running` with the active run's id.
8. Enqueue its job on the run kind's queue, in the same transaction.

Entry points that call `admit`: the chat endpoint (router turn), router continuation turns (A§9.1), the router's `dispatch_subagent` and `dispatch_job` tools, the job API endpoints and migrations. Assessment has its own queue (A§11.5). Because uniqueness is a database constraint, an entry point that forgot to check would still be refused (invariant 6).

### 8.3 Run state machine

```
admitted ──claim──► running ──agent loop ends──► gating ─┬─pass──► committing ──► committed
   │                  │                                  ├─pass, nothing staged──► unchanged
   │                  │                                  └─fail──► rolling_back ──► rolled_back
   │                  ├─cancel / disconnect / error / budget / lease lost ───────► rolling_back
   └─cancel before claim ─────────────────────────────────────────────────────► rolled_back
                                     rolling_back ──verification fails──► rollback_failed (quarantine)
```

Transitions are guarded `UPDATE … WHERE state = <expected>` statements, so a run cannot move twice. `committed`, `unchanged`, `rolled_back` and `rollback_failed` are terminal. `unchanged` is an incremental run whose gate passed with nothing staged; it creates no revision. A sweeper that finds a run with no revision and no staging rows records it as `rolled_back` with nothing to discard, which is the same workspace state.

### 8.4 Execution

A job and its run share one lease. A worker (in `agent` or `worker`) claims a job with `SKIP LOCKED` **only if its run is in `admitted`**, and in the same transaction moves the run to `running`, sets `lease_owner` and increments `lease_epoch` (the fencing token). The lease is renewed every few seconds by a conditional update on `(lease_owner, lease_epoch)`. **If a renewal fails, the executor stops itself**: it cancels the agent run and abandons it without writing anything, because another process now owns the run. Every staging write and the commit transaction carry the same condition, so a stalled executor that wakes up after the sweeper took over cannot write or commit: its writes match zero rows and raise (A§7.3).

```python
async def execute(run):
    try:
        async with agent_kind.iter(run) as agent_run:          # Pydantic AI step-wise loop
            async for node in agent_run:
                await events.publish_snapshot(run, node)        # live preview (scrubbed)
                budget.count_retry_prompts(run, node)           # A§10.3, sees framework retries too
                if await stop_requested(run):                   # cancel flag or stale stream attachment
                    agent_run.cancel()
            finish_reason = agent_run.result.response.finish_reason
        if await stop_requested(run):
            outcome = Outcome.cancelled()                       # a cancelled run never reaches the gate
        else:
            outcome = await gate.check(run, finish_reason)      # A§8.6, reads staging fresh
    except BaseException as exc:                                # includes CancelledError, RunCancelled, RunAbort
        outcome = Outcome.from_exception(exc)
        if isinstance(exc, asyncio.CancelledError):
            await anyio_shielded(finish(run, outcome))          # rollback must run even when cancelled
            raise
    await anyio_shielded(finish(run, outcome))                  # commit or rollback, then verify
```

- `finish` is the single place that ends a run. It commits only if the outcome is a passing gate; the commit transaction re-checks the cancel flag and the fence (A§7.3 step 1), so a cancel that arrives during the gate still ends in rollback. Otherwise it rolls back and verifies. It runs under a cancellation shield, so runtime-level cancellation (task cancel, server shutdown) cannot skip it (§6.3 item 4, HZ-12).
- **Spike S1 confirmed** that external `task.cancel()` propagates as `asyncio.CancelledError` through `agent.iter()` and is not translated by the framework. That is why `execute` catches `BaseException` and re-raises after a shielded `finish`.
- **Per-artifact locks inside a run.** Spike S1 confirmed that several tool calls in one model response execute concurrently. All mutating tools acquire an `asyncio.Lock` keyed by `(process_id, path)` for read-modify-write of the run's staged artifact, and they share one in-memory parsed-flow cache per run that is invalidated on every write (§6.3 item 2, HZ-9).
- **Long CPU work** (graph checks in a pre-check tool, validation of large flows) runs in a worker thread so the event loop keeps renewing the lease.

### 8.5 Cancellation, disconnect, kill

| Event | Mechanism | End state |
|---|---|---|
| User cancel | API sets `run.cancel_requested_at` and sends a notification; the executor sees it between steps, calls `AgentRun.cancel()`, skips the gate; the commit transaction also refuses when the flag is set | Rollback, verified; no new revision |
| Client disconnect (chat stream) | **Durable attachment heartbeat:** while a client is attached, the gateway sets `run.stream_attached_until = now + 10 s` every 5 s. The executor treats an attachment older than the grace period (configuration, default 15 s) as a stop request. A reconnect within the grace period renews the attachment and resumes the stream from the run-event log. A killed or redeployed gateway stops renewing, so its clients' runs stop too | Same as cancel |
| Runtime-level cancellation (task cancelled, worker shutting down) | `CancelledError` path in `execute`; shielded `finish` | Same as cancel |
| Process kill (SIGKILL, machine lost) | The lease expires. The **sweeper** (in every worker, every few seconds, plus at startup) takes over runs in any non-terminal state with an expired lease by incrementing the epoch, then calls `finish` with outcome `executor_lost`. A run killed inside the commit transaction is atomic: the sweeper finds either a revision with the run id (marks it `committed`) or none (rollback). Runs in `admitted` whose job is gone, exhausted its attempts or was not claimed within the admission timeout are finished the same way, so they cannot hold the slot forever. The sweeper also destroys sandbox machines labelled with the job of any run it finishes (A§14.5) | Never a partial flow; no record left non-terminal (HZ-13) |
| Storage failure during commit | The transaction aborts; `finish` records `commit_failed`, rolls back and verifies. If the database or blob store does not answer within `storage_timeout` (configuration, default 30 s), the executor stops trying (`left_for_sweeper`); the lease expires and the sweeper finishes the run once storage is back | Base state; run marked failed |

**Base-state identification after restart** (§13.2.1 open question): the run row stores `base_revision_id`, and the run's writes exist only as staging rows keyed by its id. Recovery therefore needs no snapshot: discarding the staging rows removes every effect of the run, and verification checks it against `staged_paths`.

### 8.6 The gate

The gate is called by the harness after the agent loop ends, whatever the reason the loop ended (model finished, gave up, or the model never called any finalize tool). It does not depend on any model action (HZ-8). Every agent kind has a gate: the **flow gate** below for flow-authoring kinds, and an **artifact gate** declared per artifact type in the registry for all other kinds (A§9.4).

**Flow gate:**

1. **Finish reason.** If the final model response stopped because of the output limit, or ended with a truncated or unanswered tool call, the run fails the gate (`truncated_finish`) whatever the staged content looks like. A model stopping early mid-sentence is not a finished flow (HZ-2, HZ-5).
2. **Re-read from storage, bypassing the run's cache:** load the run's staged blobs for its declared output paths from the blob store, parse them from bytes.
3. **Schema + integrity validation** (A§6.1 level 2).
4. **Flow invariants** (A§6.1 level 3) on the merged result (staged flow with the edit layer at head), using thresholds from the agent-kind declaration, set per flow mode (current state, redesign) and applying equally to from-scratch and incremental runs:
   - **Structural soundness:** at least one start and one end node; every node on some start-to-end path in the execution graph (A§6.1.1). A flow abandoned halfway typically has dangling nodes and fails here.
   - **Non-degeneracy:** at least `min_nodes` (current state default 3, redesign default 2); for redesign, complete coverage. For incremental runs, the run may not remove more than `max_shrink` of the base flow's nodes (default 30%); a larger restructuring needs a from-scratch run with human intent (A§8.2). This catches the incremental run that deletes most of a good flow and stops (HZ-5).
   - **Container arity:** each `ORCHESTRATOR` and `ACTOR` has `len(children(flow, c)) ≥ 2`.
   - **Coverage (redesign):** against the merged current-state flow the run read (recorded in the run's input manifest). Every current-state node id appears in exactly one `CoverageEntry.covers`; no unknown ids; no duplicates. If the current state changed since, the commit fails with `base_moved_conflict` (A§7.3).
5. **Locks (incremental mode):** re-read the edit layer **at the current head** (not the base), so locks added during the run are honored. For each lock, the run's staged item must exist with content hash equal to the lock anchor. Any violation fails the gate (invariant 3).
6. **Unchanged output (incremental mode):** if the run staged nothing, the gate passes with "no change", the run ends `unchanged` and no revision is created.

The commit transaction repeats the level-3 validation on the final merged state (A§7.3 step 4), so a user edit that lands between gate and commit is also covered.

**Artifact gate:** the type's schema; its declared checks (for example "every node of the anchored flow has a profile entry" for `profiles/*`, "every AI and deterministic node has a contract" and the contract validator of A§14.3 for `contracts/*`, "all three sections non-empty and every paragraph has a source" for `spec/*`); its declared non-degeneracy bound; and the finish-reason check. A contract drafter that stops after three of twenty nodes fails the gate.

A failure produces a structured gate report (scrubbed) stored on the run record and shown to the user as "the run did not produce a valid result; nothing was changed". The user is never asked to fix a file.

A model-facing `finalize` tool runs steps 3–5 on the staged artifact early and returns findings through the error channel (A§10), to save round-trips. It is advisory; the gate above is the enforcement.

### 8.7 Budgets per run

Per run kind (configuration): a request cap (`UsageLimits.request_limit`, confirmed in S1), output-token limits sized for writing agents, a cumulative error budget (A§10), and a money budget enforced by the ledger (A§17.3). Prompt caching is enabled for the system prompt and tool definitions, where the model supports it (all Claude 5.5 models on Bedrock do, per research R1).

---

## 9. Agent layer and extension registry

### 9.1 Router

The top-level agent of each chat turn is the **router**: a Pydantic AI agent whose instructions list the registered skills (name, description, phase) generated from the registry at startup. Its tools:

| Tool | Effect |
|---|---|
| `read_workspace(path)` | Read an artifact at the current head (merged view for flows) |
| `load_skill(name)` | Inject the skill's full playbook (markdown) into the context |
| `inbox_ask(category, question, options)` / `inbox_read(filter)` | The only way agents ask humans anything |
| `dispatch_subagent(kind, instructions, mode)` | Calls `harness.admit` for a child run and **returns immediately** with its id; the child's live preview streams into the chat. When the child ends, the harness admits a **router continuation turn** (a new router run) with the child's outcome, so multi-step playbooks continue without any tool waiting (§8.5). A from-scratch `mode` on a non-empty flow returns `needs_confirmation` (A§8.2) |
| `dispatch_job(kind, params)` | Calls `harness.admit` for a background job; returns immediately with the job id (no waiting, §8.5) |

The router has **no write tools**. A skill is text; it cannot grant a tool the router does not have (§6.2). Router output is chat text: it passes the secret gate and the verdict-phrase scanner (A§20) before it is stored or streamed. Off-topic requests are refused by default. A bounded general Q&A mode is a registered skill (`general-qa`) with a token cap and read-only tools (§8.9). Clarifying questions go only through `inbox_ask`. The router's instructions forbid asking in chat, but the enforcement is structural: only an inbox item can be answered, attributed and versioned, so a question asked in chat text has no effect on any artifact.

### 9.2 Subagent kinds (release 1)

| Kind | Writes (declared paths) | Notes |
|---|---|---|
| `spec-section` ×3 (`current`, `desired`, `constraints`) | `spec/<section>.json` | Run in parallel as three child runs, each its own kind, so admission allows them together and each commits only its own path (§12.5B parallel sections) |
| `spec-synthesis` | `spec/specification.json` | Reads the three sections |
| `current-state-modeler` | `flows/current/flow.json` | Modes: from-scratch, incremental |
| `redesigner` | `flows/redesign/flow.json` | Requires a valid merged current-state flow; may call the governance pre-check |
| `profiler` | `profiles/current.json`, `profiles/redesign.json` | Raises inbox questions ranked by influence |
| `contract-drafter` | `contracts/redesign.json` | Release 1 |
| `build-planner` | `build/plan.json`, `build/handover.md` | Requires readiness |

Background jobs (not agents, or agents without chat): `assess` (governance + dataflow typing + readiness, deterministic, runs after every commit), `codegen`, `conformance`.

Each subagent receives a **workspace snapshot** in its first prompt (the artifacts its declaration names as inputs, at the base revision), which saves the parallel section agents several read turns (§7.10).

### 9.3 Write scope

Write tools are constructed per run from the agent kind's declaration: each tool is bound to the declared output paths and rejects any other path with a correctable error. No agent kind has a tool that writes `flows/*/edits.json` (the edit layer), `inputs/`, or any database table other than through the inbox tool. Invariant 2's "no agent write path to the user layer" is therefore structural: the tool does not exist.

### 9.4 Extension registry

`registry/` holds declarations only. One loader validates them at startup and at CI time; the application reads every enumeration (phases, skills, agent kinds, artifact types, inbox categories, rule packs, defaults sets, catalogs) from the loaded registry, never from code.

```
registry/
  phases/<phase>.yaml            id, order, label key, panel
  skills/<skill>.md              frontmatter: name, phase, description, agent_kinds, inbox_categories
  agents/<kind>.yaml             prompt ref, model role, toolset, input paths, output paths,
                                 gate thresholds, budgets, modes
  artifacts/<type>.yaml          path pattern, schema ref (a JSON Schema in registry/schemas/ for new types),
                                 diff kind, writer kinds, gate (declared checks from the check library,
                                 non-degeneracy bound), tools (bespoke or generic JSON Patch)
  inbox/<category>.yaml          phase, label key, answer kinds
  rulepacks/<pack>/<version>.yaml
  defaults/<set>/<version>.yaml  defaults for profile fields, with citations
  catalogs/<id>/<version>.yaml   (deferred capability)
  schemas/*.json
```

**Extension contract (§14):** adding a phase, an artifact type, a rule pack, a defaults set or a catalog is adding files here plus a small test (a registry-validation fixture). A CI check rejects source code that hard-codes registry identifiers (an AST lint over `src/` for string literals matching registry ids outside tests). Built-in artifact types (flow, profile, contract) have Python schemas because their invariants are code; their registry entries point to those schemas. A *new* artifact type gets JSON Schema validation, the generic `read_artifact` / `patch_artifact` tools (A§6.12), a gate assembled from declared checks in the check library (non-empty collections, references resolve to nodes of the anchored flow, every node of a given kind covered, grounded citations), generic structural diff and versioning, all without code. A type that needs a genuinely new kind of check needs one library function and its test; that is the one place the extension contract admits code, and it is listed as such.

### 9.5 Prompt and skill versioning (OP-9, launch-blocking)

- `prompt_version` = SHA-256 over: agent and tool source files, prompt templates, skill documents, registry declarations and artifact schemas. Deliberately over-broad (§12.5D).
- Every revision and every derived artifact records it. Every eval scorecard records it.
- **Re-assessment with newer skills:** deterministic assessments (governance, typing, readiness) always use the current engine and rule pack and record both versions. Agent regeneration under newer prompts is an ordinary run: incremental runs honor locks, waivers never carry over (they are bound to a revision key, A§11), and the UI shows "built with prompt version X, current is Y".
- A prompt or skill change is a release gated by the eval harness (§12.1 layer 4).

---

## 10. Error contract (required item 8)

### 10.1 Principle

One closed corrective loop, every link in code (§6.3): **self-validating tools → structured error to the model → counted, bounded corrective attempts → harness gate → verified rollback.** Governance findings from the pre-check, contract errors and profile inconsistencies reuse the same channel.

### 10.2 Classes

| Class | Examples | Reaches the model as | Counted | Aborts the run |
|---|---|---|---|---|
| **Correctable** | schema or integrity violation from a mutation; reference to a missing node, edge, output or path; non-unique or non-matching patch search string; write to an undeclared path; attempt to change a locked item (incremental mode); archetype–execution contradiction; transient artifact read failure; tool arguments that fail validation (the usual symptom of truncation) and unknown tool names, both retried by the framework itself; profile inconsistency; contract error; blocking finding from the governance pre-check; content in a staged write that the secret gate flags (hint: remove or paraphrase the credential-like text) | A retry prompt carrying a JSON payload: `{error, message, location, hint, attempts_left}` | Yes: per tool, per error class, and per run (A§10.3) | Only when a budget is exhausted |
| **Abort** | error budget exhausted; request cap reached (`UsageLimitExceeded`); money budget exhausted; cancel / disconnect / runtime cancellation; lease lost; staging storage unavailable after bounded internal retries; provider authentication failure (401/403); provider unavailable after bounded backoff (5xx, throttling); framework `UnexpectedModelBehavior`; any unexpected exception in tool code (a bug); a second secret-gate hit on the same fingerprint in one run, outcome `secret_gate_repeated` (reported to the user with the location, not the value) | Not sent; the run ends | — | Yes: rollback, verified, reason recorded |

Unexpected exceptions are aborts, not correctable errors, on purpose: an unknown failure in tool code means the tool's own validation cannot be trusted for that call. It is recorded (scrubbed traceback in the run record) and shows up in retry statistics. Spike S1 showed that a plain exception in a Pydantic AI tool propagates out of the run, so the tool wrapper converts it explicitly instead of relying on framework behavior.

**Secret-gate false positives.** If client text legitimately resembles a credential (a sample IBAN, an example key in a specification), every run would fail on it. An admin can record an allow-list entry for that exact fingerprint (hash of the matched value plus pattern id) in that engagement, with a justification; the entry is attributed and versioned. Known credential values can never be allow-listed.

### 10.3 Counting

Spike S1 measured the framework's own retry budget: a tool's `retries` counter **resets after any successful call** of that tool, and `ToolFailed` is not counted at all. A model alternating failing and succeeding calls is bounded only by the request cap. That does not meet §6.3 item 1 ("a bounded per-tool or per-error-class budget, not only the run's overall request cap"). So Agentify counts itself, at two points:

- **Tools report, the harness counts.** Mutating tools raise `ModelRetry` with the structured payload; they do not count. The harness loop inspects every request node before it is sent and counts **every** retry prompt in it, per tool, per error class (from the payload's `error` field) and per run. That one counting point covers errors raised by tool code and the retry prompts the **framework** generates before tool code runs (argument validation failures, unknown tools), so the "truncated call, then a small successful call" pattern is bounded too. Slice 1 verified this (O-8; `tests/orchestration/test_error_budget.py`).
- When any counter exceeds its limit, the harness raises `RunAbort("error_budget_exhausted")` and the run rolls back.
- The framework's per-tool `retries` is set above the Agentify limit, so Agentify's counter always trips first. Tests pin that ordering, the alternating-failure pattern and the framework-retry pattern.
- **Truncation** is detected from the response's finish reason (output-limit stop) and from incomplete tool calls; the first occurrence is a counted correctable error with the hint "make smaller edits"; a final response that is truncated fails the gate (A§8.6).
- Limits are per agent kind in the registry. Starting values: 5 per tool, 8 per error class, 20 per run. They are tuned from retry statistics in evals, not intuition.
- Retry statistics (per tool, per class, per prompt version) are exported to the eval harness (§11.4).

### 10.4 Assessment errors

The deterministic `assess` job is not a model loop. A corrupt rule pack, an unparseable contract set or an engine failure makes the assessment **fail loudly**: no readiness record is written for that revision key, the failure is shown, and readiness is never granted on a partial evaluation (§12.5C).

---

## 11. Artifact lifecycle and readiness (required item 3)

### 11.1 Revision key

Readiness, findings, waivers and sign-offs attach to a **revision key**, not to a flow file:

```
RevisionKey = sha256(canonical(
    flow_blob,                 # flows/redesign/flow.json at head
    edit_layer_blob,           # flows/redesign/edits.json at head (layout.json is excluded)
    current_state_key,         # merged current-state flow (flow blob + edit layer blob), OD-18
    profile_blob,              # profiles/redesign.json (and profiles/current.json)
    contract_blob,             # contracts/redesign.json
    rule_pack_id@version,
    engine_version             # governance + dataflow engine version
))
```

The key is computed from the current workspace head every time it is needed. Any change to any component (flow, user edits, current state, profile, contracts, rule pack, engine) produces a different key. Node positions and layout direction live in `layout.json` and are deliberately **not** part of the key: moving a node changes nothing that governance, contracts or readiness depend on, so it must not reset readiness or void waivers (HZ-26).

### 11.2 States

| State | Holds when | Gate |
|---|---|---|
| **Valid** | The merged flow passes every flow invariant and is non-degenerate, **for this key**. For a redesign this includes coverage of the merged current-state flow in the key; a later current-state edit can make a committed redesign *coverage stale*, hence not valid | Level-3 validation in every commit transaction (A§7.3), on top of the harness gate (A§8.6) and edit-API validation (A§6.2); `valid(key)` is recomputed from the key, like readiness |
| **Assessed** | A readiness record exists **for the current revision key**: governance findings computed (missing or non-exempting profile values appear as findings, A§12.2, rather than preventing assessment); dataflow typing computed; the contract set's anchor matches the key's flow and the set passed its artifact gate | An assessment job per committed revision key, enqueued in the commit transaction (A§11.5) |
| **Ready for handover** | Assessed, **and** every blocking finding for this key has a waiver whose `revision_key` equals the current key, **and** every AI and deterministic node has a non-orphaned contract (including a judgment spec for orchestrators and actors), **and** dataflow typing passes on every edge | Computed by the pure function `readiness(record, waivers)`; required by `build-planner`, `codegen` and `conformance` at admission **and again in their commit transaction** (A§11.4) |

`readiness(head)` looks up the record by the key computed from head. If none exists, the state is *valid (assessment pending)*. **A stale ready status is impossible by construction:** a ready status is only ever read for the key it was computed for, and every edit changes the key (§4.4, HZ-31). Editing the flow, a profile value, a contract, the edit layer or the rule pack therefore returns the flow to *valid* until the new key is assessed.

Waivers are immutable rows bound to `(finding_key, revision_key)`. A waiver never applies to another key, so a waiver given on one revision does not silently carry to the next (§4.5: one finding, one revision). A later UI may offer "re-affirm the previous waiver", which creates a new waiver row with the approver's explicit action; it is never automatic.

Waiver creation checks, server-side: the approver holds `waiver_approver` on the engagement; the justification is non-empty (minimum length configurable); the finding exists and is blocking for the current key; **four-eyes** (OD-9): the approver is not in `authors(key)`.

`authors(key)` = the union of: every `Patch.author_id` and `Lock.author_id` in the edit layers in the key (redesign and current state), every human answer consumed by the revisions that produced the key's current blobs (`revision_input`), and the initiators of the runs that produced those blobs. System writers (migration, assessment) are transparent: they contribute nobody and do not hide the previous human authors, because the union is taken over content, not over the last revision. The computed set is stored on the waiver or sign-off (`checked_authors`) so the decision can be audited later.

### 11.3 Regulatory lifecycle (deferred capability)

`draft → signed_off → exportable`, bound to a revision key that additionally includes the catalog version and the applicability answers. Export requires a sign-off row for the current key by a `reviewer` who is not in `authors(key)`. Any upstream change produces a new key, which has no sign-off, so the artifact is a draft again (§4.4, invariant 14).

### 11.4 Derived artifacts record their inputs

Every derived artifact carries `inputs: {revision_key, components…, rule_pack, engine_version, prompt_version, seed?}`. The governance report, dataflow report, contract set and conformance report are committed as revisions by their jobs; their readiness records reference them.

The **build plan, the handover prompt and the generated-code manifest** record the ready revision key they were produced from. Their runs re-check readiness inside the commit transaction: the key computed from head must equal the recorded key and must have a ready record. Otherwise the run fails with `readiness_changed` and rolls back, so code generated against superseded contracts is never committed as if it were current (§4.4, HZ-31). The UI shows each of these artifacts as *current* only while its recorded key equals the head key.

### 11.5 Assessment scheduling

Assessment is deterministic work, not an agent run, so it has its own queue table `assessment_job(process_id, revision_key UNIQUE, state, …)`. The commit transaction of every revision that changes a key component inserts a job for the new key (`ON CONFLICT DO NOTHING`, so the same key is never assessed twice). Jobs for one process run in `commit_seq` order (a per-process sequence assigned in the commit transaction), one at a time, so **every committed revision is evaluated** and its report is versioned with it (§6.9); none is skipped by coalescing. A rule-pack activation (DoD-rules) inserts a job for the current key of every process assessed under the previous version, and creates an inbox notice in the design category ("re-assessed under rule pack X"), so affected flows receive re-assessment notices (§12.2). Catalog version changes will do the same (A§16).

---

## 12. Governance engine (required item 9, rule format and autonomy scale)

### 12.1 Evaluation

`evaluate(flow: ResolvedFlow, profile: ProfileSet, pack: RulePack, typing: DataflowReport) -> list[Finding]` is pure and deterministic: rules are evaluated in pack order; witnesses are canonicalized (sorted ids, shortest path with lexicographic tie-break); findings are sorted by key. Same flow, profile and pack give byte-identical reports (§6.9, §12.5E). It is code, never a model call (§8.15).

The engine works on a NetworkX `DiGraph` of the merged flow's execution graph (A§6.1.1), so container children are on the paths through their container. Path rules are evaluated with reachability on derived graphs, not by enumerating paths, so cost is polynomial: a rule of the form "every path from A to B passes a node matching C" is checked by removing the C-nodes and testing whether B is still reachable from A. If it is, the shortest remaining path is the witness. Expected flow sizes are tens to a few hundred nodes. Evaluation time is not claimed here: a benchmark over generated graphs up to a few hundred nodes records it, and the operational latency budget for assessment (A§21.1) is set from that measurement.

### 12.2 Rule expression format (OP-6)

Rules are YAML in the registry, validated against a schema. The format is a small, closed vocabulary that an operator can read: **selectors** over node attributes, combined with `all` / `any` / `not`, inside a fixed set of **quantified checks**. There is no general-purpose language, no user-defined functions and no loops.

```yaml
id: oversight.natural-persons-checkpoint
version: 1
severity: blocking
rationale: >
  Decisions about natural persons with irreversible or external effects are where liability
  concentrates (actor archetype). A human checkpoint must be able to stop the effect.
remediation: Insert a HUMAN approval step between the actor and the effect, or make the effect reversible.
check:
  every_path:
    from: {all: [{kind: ACTOR}, {profile: decides_about_natural_persons, is: true}]}
    to:   {any: [{profile: effect_reversibility, is: IRREVERSIBLE}, {profile: external_effect, is: true}]}
    passes: {all: [{execution: HUMAN}, {role: CHECKPOINT}]}
on_missing: finding
exempting_provenance: [stated, measured]
```

Selector grammar (the whole language for node tests):

```
selector   := { all: [selector, …] } | { any: [selector, …] } | { not: selector } | test
test       := { kind: NodeKind } | { execution: Execution } | { role: NodeRole }
            | { profile: field, is: value } | { profile: field, in: [value, …] }
            | { profile: field, cmp: lt|le|gt|ge, value: number | AutonomyLevel }
            | { profile: field, present: true|false }
field      := a NodeProfile field name (A§6.3)
```

Quantified checks (release 1): `every_node` (each selected node satisfies a selector); `every_path` (as above); `reachable_from_start`; `reaches_end`; `cycles_bounded` (each cycle contains a node with a stated `loop_bound`); `and_join_satisfiable` (AND-joins whose inputs come from mutually exclusive conditional branches); `field_present`; `at_most_ceiling` (autonomy against the pack's ceiling table); `data_categories_allowed`; `dataflow_typed` (consumes the dataflow report, witness is the edge).

**Missing data fails closed.** `on_missing: finding` (the default for blocking rules) makes a missing profile value a finding, with the node as witness and "required value missing" as message. Governance cannot pass because data is absent.

**Estimates cannot exempt a node.** A selector that would *exclude* a node from a blocking check (for example `decides_about_natural_persons is false`, `effect_reversibility is REVERSIBLE`, `external_effect is false`, `data_leaves_organization is false`, an autonomy level under the ceiling) counts only if the value's provenance is in the rule's `exempting_provenance`, which defaults to `[stated, measured]`. An `estimated` or `default` value that would exempt the node is treated as missing: it produces a finding ("exemption rests on an estimate") and an inbox question in the design category, so a human states the value. The model can propose; only a person can take the most liability-relevant checks off a node (§1). Every assessment report lists the profile values each rule read, with their provenance (`evaluated_values`, A§6.11).

**Ranges are evaluated conservatively.** A comparison against a range or triangular value uses its worst case for the rule (for example the upper bound of autonomy against a ceiling). The finding message states that the range, not the mode, triggered it.

Mechanizable catalog obligations (deferred capability) compile into rules of this same format, tagged with their obligation id (§4.7).

### 12.3 Autonomy scale (OP-6)

| Level | Name | Meaning |
|---|---|---|
| A0 | Manual | A human performs the step; AI is not involved |
| A1 | Assist | AI drafts or recommends; a human decides and acts on every case |
| A2 | Approve | AI decides and prepares the action; a human approves every case before any effect |
| A3 | Supervise | AI decides and acts; cases below the confidence threshold escalate; humans monitor and sample |
| A4 | Autonomous | AI decides and acts within declared bounds without routine human involvement; audited after the fact |

Five levels: coarse enough for consistent estimates (each level differs in *who acts* and *when a human sees the case*), fine enough for meaningful ceilings. **Decision class** is derived from the profile: `(decides_about_natural_persons, effect_reversibility, external_effect)`. The rule pack carries a ceiling table per decision class.

### 12.4 Initial rule pack `agentify-core@1.0.0` (approved by the operator 2026-10-14, A§1.1)

**Blocking (four, each justified).** Reachability and termination are no longer a governance rule: they are part of validity (A§6.1 level 3), so a flow with unreachable or dead-end steps cannot commit at all.

| Rule | Check | Rationale |
|---|---|---|
| `oversight.natural-persons-checkpoint` | As in A§12.2 | Liability concentrates in actors that decide about people with irreversible or external effects |
| `escalation.ai-threshold-and-target` | Every AI `TASK` and `ACTOR` node has a confidence threshold and an escalation target that resolves to a node in the flow. Orchestrators are exempt: they distribute and do not decide (§4.1) | An AI step that decides without a defined way to hand off uncertain cases has no failure mode a human can see |
| `autonomy.within-ceiling` | Autonomy ≤ ceiling for the node's decision class | Ceilings are the operator's explicit risk appetite; exceeding them is a policy breach, not a design choice |
| `dataflow.typed-edges` | Every input binding's source output schema satisfies the input schema | A type mismatch is a defect in the handover; the generated code cannot satisfy both contracts |

Proposed ceilings: natural persons + irreversible → A2; natural persons + reversible or costly → A3; no natural persons + irreversible or external → A3; otherwise A4.

**Advisory:** `structure.cycles-bounded` (rework loops without `loop_bound`; simulation reports such cases as stuck, A§15), `structure.and-join-satisfiable`, `data.categories-allowed` (needs an agreed data-category taxonomy before it can block), `data.external-flagged` (external nodes receiving data must state `data_leaves_organization`).

**Waiver-rate review:** `rule_waiver_stats` counts findings and waivers per rule and pack version. A rule waived on more than half of the flows it fires on, over a trailing window of at least 10 flows, is flagged in the operator view for review (§6.9, HZ-26). The threshold is configuration.

### 12.5 Governance in redesign runs

The redesigner has a `governance_precheck` tool that runs `evaluate` on its staged flow merged with the edit layer. Blocking findings come back as correctable errors (A§10), counted. They never prevent a valid flow from committing: the gate checks validity; readiness is separate (§6.9). Findings that need human judgment (rule `inbox_on_finding`) create inbox items in the design category.

---

## 13. Node profile and provenance

- The **profiler** agent proposes values for both flows. A value found verbatim in the inputs is `stated` with a `SpecCitation`; the validator checks that the cited text actually occurs in the cited input at the cited revision (deterministic grounding, so a citation cannot be invented). Everything else the agent proposes is `estimated`, with a recorded reason.
- **Defaults** come only from declared defaults sets in the registry, labelled `default` with set id, version and key. There is no code path from model memory to a `default` value (§6.10).
- **Users override** through the edit layer: `profile_override` patches, anchored to the node (A§6.2). **Answering an inbox question about a profile value creates exactly such a patch** (author = the answering human, provenance `stated`, `answered_by`), written through the edit API, never by an agent and never into `profiles/*.json`. The profiler receives existing overrides as fixed inputs in its snapshot; a re-estimate does not touch them, and the merged profile shows the human's value. If the node changes, the override is orphaned and the question is re-asked.
- **Elicitation ranking (release 1).** Simulation ships in release 2, but §6.10 wants missing or uncertain high-impact values ranked by influence now. Release 1 uses a deterministic expected-value model over the execution graph (A§6.1.1): exclusive splits form an absorbing Markov chain whose expected visits per node are the fundamental matrix `N = (I − Q)⁻¹` (NumPy). For **cost and human effort**, which add up across parallel branches, the expected value per case is linear in the node values, so each estimated value's influence is its partial derivative times its range width; this is exact for expected values. For **throughput time** the model is exact only for flows without parallel splits, because a parallel join takes the maximum of its branches; on flows with parallel splits, time sensitivity is not computed in release 1 and the ranking says so. The top-ranked *estimated* values become inbox questions. The ranking is labelled "expected-value sensitivity; distributional sensitivity follows with simulation". In release 1 it is used to rank questions; where it is shown, it is shown as an interval with its method and limits, never as a business-case figure.
- **Profile consistency errors** (probabilities not summing to 1, inverted ranges, required fields missing, currency mismatch) reach the profiler through the error channel.
- **Staffing pools** (OD-16) are recorded per profile set and referenced by `HUMAN` nodes; release 1 validates and stores them, release 2 simulates them.

---

## 14. Acceptance contracts, code generation, sandbox, conformance

### 14.1 Contract schema formalism and dataflow typing (OP-7)

Contract input and output schemas are **JSON Schema 2020-12, restricted to a declared subset**: `type` (object, array, string, number, integer, boolean, null), `properties`, `required`, `additionalProperties`, `items`, `enum`, `const`, numeric and length bounds, a fixed list of `format`s (date, date-time, email, uri), local `$defs` without recursion, and `oneOf` only as a discriminated union on a `const` property. JSON Schema is established, readable by people and code generators, and Pydantic emits it. The restriction exists so that **dataflow typing is decidable and deterministic**: `dataflow.is_subtype(output, input)` is a structural check (required properties of the input are required in the output with compatible subschemas; enums are subsets; bounds are contained; arrays compare item schemas; closed objects reject extra properties). It is sound and conservative: it may reject a pair that a more powerful checker would accept, never the reverse. A schema outside the subset is a correctable contract error.

Typing runs on every **input binding**: each `InputBinding.source` names an output of an upstream node; that output's schema must satisfy the consuming node's input schema at that property. A mismatch becomes a `dataflow.typed-edges` finding whose witness is the edge (or the edge path) between producer and consumer (§6.12).

**Test-case expectations** are structured assertions evaluated deterministically: `equals`, `matches_schema`, `field` predicates (`eq`, `in`, `lt`, `gt`, `contains`, `absent`), `decision_in`, `escalated`, `refused`. Free-text expectations are not allowed, so a case cannot merely restate the specification (§8.17).

**Grounding.** A `SpecTrace` names the specification artifact, the section and the quoted span; the contract validator checks that the span occurs in that artifact at the referenced revision. An `AssumptionTrace` states the assumption explicitly and is shown as such everywhere (§4.6, invariant 13).

### 14.2 Contracts for judgment nodes (OP-7)

Task agents produce checkable outputs. Orchestrators and actors exercise judgment, so their contracts test **constraints on judgment**, not a single correct answer:

- **Decision schema:** the node's output must contain `decision` (from a declared enumeration), `rationale`, `escalate` (bool) and `evidence` (references to inputs). This makes judgment observable.
- **Behavioral scenarios:** given an input, the set of *acceptable* decisions, the set of *forbidden* decisions, and whether escalation is *required*. At least one scenario is negative (a forbidden decision or a required escalation).
- **Policy constraints:** deterministic predicates over input and output (for example "if the claim exceeds the threshold, `escalate` is true"; "never `decision = reject` without `evidence`").
- **Orchestrators:** scenarios assert dispatch constraints (which children are invoked, ordering constraints, no decision made by the orchestrator itself).
- **Judged rubric (optional):** dimensions scored by a calibrated model judge (A§26.5), reported in the conformance report as **judged**, separately from deterministic passes, never counted as a pass of the contract.

Readiness requires a judgment spec with at least one negative scenario for every orchestrator and actor.

### 14.3 Contract drafting

The `contract-drafter` agent writes `contracts/redesign.json` for every AI and deterministic node. Contract errors (unparseable schema, schema outside the subset, untraced case, missing negative case, missing below-threshold behavior, missing judgment spec) are correctable errors. Contracts are anchored to `(node id, node content hash)`: when a regeneration changes a node, its contract moves to `orphaned` and is reported, never kept silently, and never used for readiness (§4.6). Contract edits by users are edit-layer patches anchored to the clause they change (A§6.2).

### 14.4 Code generation (OD-13)

```python
class Executor(Protocol):
    async def run(self, job: CodegenJob, sandbox: SandboxHandle) -> ExecutionResult: ...
```

The first adapter runs **Claude Code** (CLI, pinned version) headless inside a sandbox (A§14.5). Its configuration, from research R3:

- **Model access only through Agentify:** `CLAUDE_CODE_USE_BEDROCK=1`, `ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8443/bedrock`, `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1`. The local endpoint is a **root-owned forwarder** inside the VM (A§14.5) that adds the job token and the VM's Fly OIDC token and relays to the model gateway; the workload process holds neither. The sandbox holds **no AWS credentials**; the model gateway signs requests to Bedrock.
- **Managed settings baked into the sandbox image** (`/etc/claude-code/managed-settings.json`, highest precedence): `allowedProviders: ["customEndpoint"]` with the base URL pinned (requires Claude Code ≥ 2.1.285; local version 2.1.299), `availableModels` restricted to EU inference-profile ids and enforced, WebFetch and WebSearch denied, `skipWebFetchPreflight: true`.
- **Explicit models:** `ANTHROPIC_MODEL` and `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` set to `eu.anthropic.…` ids, and `CLAUDE_CODE_DISABLE_MODEL_ACCESS_FALLBACK=1`, because unpinned Claude Code silently falls back between models.
- **No non-essential traffic:** `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `DISABLE_AUTOUPDATER=1`, `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1`. `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST` is never set (it re-enables metrics on Bedrock). The sandbox's egress firewall blocks everything except the sandbox gateway anyway (A§14.5); these settings avoid failing requests and noise.
- **Headless invocation:** `claude --bare -p --output-format stream-json --verbose --permission-mode dontAsk --allowedTools <explicit list> --max-turns <n> --max-budget-usd <secondary cap>`, working directory `/work/generated`. `--bare` skips hooks, plugins, MCP and memory. `dontAsk` with an explicit allow-list is the documented mode for unattended runs; `bypassPermissions` is not used.
- **Per-run tool state** (§6.7): `CLAUDE_CONFIG_DIR=/run/agentify/claude` and `CLAUDE_CODE_TMPDIR=/run/agentify/tmp`, inside the job's own VM, which is destroyed after the job. `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
- **Environment is built from an allow-list** (§6.7, §8.3): the executor passes exactly the variables above plus `PATH`, `HOME`, `LANG` and the output-language directive. Nothing is inherited.
- **Stream reader:** reads stdout line by line with a 64 MiB line limit (no documented maximum line size exists). A longer line fails the job loudly with `stream_line_too_long`; nothing is truncated (HZ-1). The final `result` event carries the session id and cost; exit status is recorded.
- **Session resume:** the code-generation VM stays up for the whole bounded iteration loop (generate → conformance feedback → fix, up to the iteration budget), so the session and its config directory survive between iterations. A root-owned supervisor in the VM runs one Claude Code invocation per iteration, resuming the session with `--resume <id>`; conformance feedback for the next iteration arrives through the exchange (A§14.5). If a resume fails without any assistant output, it is retried once with a fresh session and the feedback as the prompt (HZ-19).
- **Transcripts** (plaintext, in the config dir) never leave the sandbox as files. The tool-call log shown to users is extracted from the stream and passed through the secret gate.
- **Write scope:** only `/work/generated` is returned. After each iteration the root supervisor packs that directory and uploads it through the exchange; the worker rejects symlinks, absolute paths, `..` components, special files and archives over the size and entry limits, then **normalizes layout** (a pure function that, for example, hoists a single nested project directory) before anything reads it (HZ-20). Everything else in the VM is discarded with it.
- **Secret gate:** the normalized tree passes the secret gate (A§18) before it is committed under `generated/` as a revision of the codegen run. A hit fails the job; the code is not committed.

### 14.5 Sandbox (OP-3, launch-blocking)

From research R2, Fly's network policies match ports only, not destinations, and do not cover traffic through the Fly proxy, which is how managed Postgres is reached. Port policies alone can therefore not stop a sandbox reaching the database. The design uses **organization-level separation plus in-VM controls**, decomposed into separately tested parts (§6.7, §8.2).

**Two services face the sandbox, both in the main organization, both public HTTPS, and they are deliberately separate:**

- **Model gateway** (`sandbox-gateway`): Bedrock pass-through only. Its IAM role can invoke EU models and nothing else; it has no storage credentials. Surface: `POST /bedrock/model/{id}/invoke[-with-response-stream]` (bodies inspected, not rewritten; event streams relayed unbuffered; `retry-after` and error bodies preserved; model ids outside the EU allow-list refused), plus the best-effort inference-profile listing Claude Code asks for.
- **Exchange** (`sandbox-exchange`): `GET /bundle` and `PUT /result` for the calling job only. Its storage role is restricted to the `runtime/exchange/` prefix. The worker pre-stages each job's bundle under `runtime/exchange/<job-id>-<random 128-bit>/` and deletes it when the job ends, so even a defect in the exchange's job lookup can expose only in-flight exchange objects, never `artifacts` and never another engagement's history.

| Part | Control | Test |
|---|---|---|
| Machine | Fly Machine (Firecracker microVM) per job, created through the Machines API with `auto_destroy`, `restart: no`, guest size from configuration, image pinned by digest, labelled with the job id | Image digest checked through the OIDC claim (below) |
| Network reachability | Sandboxes live in a **separate Fly organization** that contains nothing else, so there is no private-network route to the database, the agent, the worker or other workspaces | Escape drill: connect to the main organization's private and Flycast addresses, and to the database |
| Name resolution | No DNS for the workload. Root init resolves the two service hostnames once, writes them to `/etc/hosts`, and the firewall allows no port 53 at all. DNS cannot be used as an exfiltration channel | Escape drill: resolve or query any name over UDP/TCP 53 |
| Egress | Root init installs nftables rules allowing TCP 443 only to the two resolved service addresses (code-generation jobs), or only to the exchange during input fetch and result upload (conformance jobs, below). The workload runs as an unprivileged uid without `CAP_NET_ADMIN`. A Fly egress network policy allowing only 443 is a second layer | Escape drill: reach an arbitrary internet host by IP; change the firewall as the workload user |
| OS identity | Root runs init, the forwarder and the supervisor; the workload (Claude Code, generated code, tests) runs as a dedicated non-root uid; the bundle is read-only to it; `/work/generated` is its only writable project directory. Root init restricts the Fly machine API socket (`/.fly/api`) to root, so the workload cannot mint OIDC tokens | Escape drill: write outside the directory; open the API socket as the workload user (A§28 O-7) |
| Environment | Allow-listed environment (A§14.4); no Fly secrets on the sandbox app | Environment-construction unit test; escape drill: dump the environment and scan it |
| Credentials | No AWS or storage credentials in the VM. The IAM trust policies trust only the **main** organization's OIDC issuer and named apps, so a token minted in the sandbox organization cannot assume any role | Escape drill: mint a token in the sandbox and call `AssumeRoleWithWebIdentity` |
| Service authentication | Each request needs (a) the per-job token, added by the root-owned forwarder, (b) a Fly OIDC token from the sandbox organization whose `machine_id` and `image_digest` match the job record, also added by the forwarder, (c) a source address among the sandbox organization's static egress IPs, and (d) a job whose run is **non-terminal and whose lease is live**. The job token expires at the job's deadline | Contract tests per factor; escape drill: reuse one job's token from another job; use a token after the run ended |
| Lifetime | Root init enforces the job deadline itself and powers the VM off when it passes; the worker also stops and destroys it; the sweeper destroys machines labelled with a finished or lost run (A§8.5). A killed worker therefore cannot leave a sandbox calling the model or uploading results | Fault test: SIGKILL the worker mid-job; the VM is gone and its token refused |
| Resources | CPU and memory per job size; upload size and entry limits | Fault test: oversized upload refused |
| Dependencies | The sandbox image pins every runtime and package (by digest and lockfile). Generated projects may use only dependencies present in the image or vendored into the bundle; no package registry is reachable | Smoke test against the built image |

**Local and CI adapter.** For development and CI, a local container adapter runs the same image with an internal-only network and the same uid and environment rules. It exercises the executor and conformance logic. It is **not** evidence for invariant 9 in production: the escape drills run against the real sandbox organization in a staging environment on a schedule and before every release (§12.2 DoD-release).

Open items, to verify during implementation (A§28): whether Machines API create accepts `image@sha256:…` directly (if not, the image is referenced by an immutable tag and the digest is enforced through the OIDC `image_digest` claim, which the services check anyway); whether Fly egress policies apply to all egress (the in-VM firewall does not depend on it); whether the machine API socket can be restricted to root (if not, the OIDC factor is dropped for model calls and the remaining factors stand, which is stated as such).

### 14.6 Conformance

After each code-generation iteration, a `conformance` job runs in a **fresh** sandbox, so generated code cannot tamper with the runner or with earlier results. Root init fetches the contracts bundle and the generated tree from the exchange, then switches the firewall to **deny all**; the workload (the contract test runner, which is Agentify code pinned in the image, executing the generated node implementations through the entry-point convention defined in the build plan) runs with no network at all. When the workload exits or the deadline passes, root re-opens egress to the exchange only and uploads the report.

The **conformance report** gives, per node: `passed`, `failed` (with the failing cases and reasons) or `not_testable` (with the reason, for example an external dependency), plus judged rubric results reported separately. It records the code tree hash, the contracts blob and the ready revision key it tested, and is committed as a revision of the conformance job, which re-checks readiness at commit (A§11.4). Failures become corrective feedback to the code-generation step, bounded by a per-job iteration budget (registry, starting at 3), or inbox items in the verification category when a human decision is needed (§6.12).

**Discrimination (mutation check, §12.5F):** for golden nodes, the eval corpus holds deliberately broken reference implementations; their contracts must fail them.

---

## 15. Simulation (release 2, designed now; required item 9)

- **Method:** Monte Carlo over a discrete-event model of each flow (§6.11 recommendation, §7.20). Each replication draws profile values from their distributions, routes a case through the merged flow by branch probabilities, and accumulates time, cost and human effort. With staffing pools (OD-16), `HUMAN` steps queue for pool capacity (SimPy `Resource`), so waiting time emerges instead of being assumed. Without pools, capacity is unbounded and the business case says so in its stated limitations.
- **Error propagation:** each AI node draws an error with its `error_rate`; downstream detection draws with each node's `detection_probability`; an error not caught before an end node *escapes*, with its cost recorded per node.
- **Reproducibility:** NumPy `Generator(PCG64)` seeded from `SeedSequence(seed).spawn(replications)`; one independent stream per replication, so results are identical whether replications run serially or in parallel on several workers (§11.9). Same flows, profiles and seed → identical results (§6.11).
- **Conservation:** every case ends at an end node, or is reported **stuck** when it exceeds `loop_bound` visits or a global step limit. Stuck counts per node are part of the result, never dropped (HZ-25).
- **Range normalization for branch probabilities:** when branch probabilities are ranges, each replication samples them and renormalizes to sum 1; this is documented in the business case's method section.
- **Outputs** as in A§6.6. Sensitivity: rank correlation of each sampled input with each output, plus one-at-a-time sweeps for the top inputs; break-even curves (for example the AI error rate at which the redesign's expected cost equals the current state's) by root-finding over the parameter.
- **Narrative:** model-written, constrained to a structured list of computed claims it may reference; a faithfulness eval checks every sentence against those claims (§12.5G).
- **Analytic tests:** linear, branching and rework-loop flows whose expectations are computable by hand (and by the release-1 Markov model, A§13) must match within tolerances derived from the replication count.

## 16. Regulatory mapping (deferred; designed at contract level)

Not in release 1 (OD-12, OD-14). The data model (A§6.7), lifecycle (A§11.3), human-only applicability (inbox category `applicability`; answers accepted only from humans, rejected if `author_kind ≠ user`), compilation of mechanizable obligations into rules (A§12.2) and re-assessment notices on catalog version change are designed so the capability can be added without schema migration of existing artifacts. Launch-blocking open problem 4 (jurisdictions, catalogs, reviewer qualifications, liability wording) must be answered by the operator before it ships.

---

## 17. Provider abstraction and budgets

### 17.1 One abstraction (§6.8)

`llm.model_for(role: ModelRole, context: CallContext) -> Model` is the only constructor of model clients. It:

- builds a `boto3` Bedrock Runtime client **explicitly** from credentials obtained for this call (A§17.2) and region `eu-central-1`, and passes it to Pydantic AI's Bedrock model as `bedrock_client`. Credentials are never left for the SDK to discover from the environment: research showed the provider otherwise falls back to environment variables, which is the "provider inferred" hazard in another form (HZ-4);
- resolves the role's model id from the per-role model policy (configuration, A§17.4); only `eu.` inference-profile ids are accepted by configuration validation;
- computes model settings **per model** (max output tokens sized for writing agents, caching points, effort where supported);
- wraps the model in a `LedgerModel` (A§17.3) and an instrumentation wrapper.

Agents, the eval judge, the sandbox gateway and every later capability get models only from here; import-linter forbids importing provider modules elsewhere (§6.8: no feature bypasses the abstraction).

### 17.2 Credentials

- **Default:** Fly OIDC workload identity. The `agent`, `worker`, `sandbox-gateway` and `sandbox-exchange` apps request an OIDC token (audience `sts.amazonaws.com`) from the machine API socket and assume their own IAM role with `AssumeRoleWithWebIdentity` (model invocation for `agent`, `worker` and `sandbox-gateway`; storage roles as in A§5.1); each role's trust policy matches the main organization's issuer and that app's name only. Credentials are short-lived and refreshed automatically.
- **IAM policy:** allow `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on the EU inference-profile ARNs in `eu-central-1` and on the underlying foundation-model ARNs in the EU destination regions, conditioned on `bedrock:InferenceProfileArn`; **explicitly deny** invocation where `aws:RequestedRegion` is `unspecified`, which blocks `global.*` profiles. Bedrock long-term API keys are denied (`bedrock:CallWithBearerToken`).
- **Account settings:** model invocation logging off (it is off by default; if enabled for audit it may only write to EU destinations); data retention mode set to `none` where the models allow it. Fable-family models require a retention mode with up to 30 days of AWS review and have no EU profile anyway; they are excluded by configuration validation.
- **Per-request credential override** (§6.8): a `CallContext` can carry an override (for example a client-funded credential); it is passed into each provider construction and never cached across contexts. Known credential values are registered with the secret gate for the lifetime of the context.

### 17.3 Budget ledger (enforced, OP-13)

AWS Budgets refreshes a few times a day and alerts may arrive after spending passes a threshold (research R1), so enforcement is in Agentify:

- `LedgerModel.request()` **reserves** the worst-case cost of the call (input tokens estimated from the request, plus the max output tokens, at the model's price table) against every applicable scope (run, process, engagement, deployment) in one transaction, using `budget_limit` per scope and period. If any scope would exceed its limit, the call is refused and the run aborts with `money_budget_exhausted`.
- After the call it **settles** with actual usage from the response (Bedrock returns token counts), releasing the difference.
- Reservations are never left open: a run's open reservations are released in `finish`, and the sweeper releases reservations of finished or lost runs.
- The **remaining budget** shown in the UI is computed from the ledger (§5 budget indicator); per-run cost is shown alongside it (§11.6).
- AWS Budgets alarms on the account act as a backstop, not the control.

### 17.4 Model policy (per role, configuration)

`config/model-policy.yaml` maps roles (router, spec-section, synthesis, current-state-modeler, redesigner, profiler, contract-drafter, build-planner, judge, codegen) to an EU model id, settings and an optional fallback chain. Release 1 starts **every role on the same capable model** (Opus 5.5 or Sonnet 5.5, chosen by the first eval run) and changes a role's model only when the eval harness shows no regression (§8.1). Fallback chains stay within EU profiles.

### 17.5 Corporate gateway option (OD-5)

`llm.provider` is configuration: `bedrock` (default) or `gateway`. A `gateway` entry declares the endpoint, the API flavor (Bedrock-compatible or Anthropic-Messages-compatible), the authentication style (bearer token from a secret, mutual TLS, or SigV4 with a named role) and the budget source (`ledger-only`, or `gateway-reported` with the response header or endpoint that reports remaining budget, cached briefly). Exactly one authentication style is active at a time. The ledger keeps enforcing either way. Switching providers is a configuration change validated at startup.

---

## 18. Secret gate

### 18.1 One gate, every exit (§11.11, invariant 8)

`secretgate.scrub(content, context) -> Scrubbed[T]` and `secretgate.check_tree(path) -> TreeVerdict`. Matchers:

- **Known values:** configured credentials, per-request credentials registered for the context, the OIDC and AWS session tokens currently in use, database URLs. Matched literally, plus their base64 and URL-encoded forms.
- **Patterns:** credential formats (AWS access keys and session tokens, private-key blocks, JWTs, bearer tokens, common vendor key formats, connection strings with passwords), from a maintained rule set (the gitleaks rule set, vendored and pinned), plus a high-entropy check for values assigned to credential-like names.
- **File-name classes:** `.env*`, `*.pem`, `*.key`, `id_*`, `credentials*`, `.npmrc`, `.pypirc`, `.netrc`, `*.p12`, and similar. Any such file in generated code or an export fails the check regardless of its content.

### 18.2 Making bypass structurally hard

Every exit API accepts only `Scrubbed[...]` values, which only the gate constructs (a private constructor, checked by mypy and a lint rule):

| Exit | Where the gate sits |
|---|---|
| Logs | A structlog processor that scrubs every event; standard-library handlers route through it |
| Process stdout/stderr | The container entrypoint is a small supervisor that starts the Python process and passes its stdout and stderr through the gate before the platform log collector sees them, so a crash before logging is configured (an import-time traceback) is still scrubbed |
| Run events and live previews | `events.publish` takes `Scrubbed[RunEvent]` |
| Run records | `outcome_detail`, gate reports and error-budget details are `Scrubbed` fields |
| Chat history | `chat.append` takes `Scrubbed[ChatContent]` |
| Inbox, waivers, sign-offs | `inbox.create`, waiver justification and sign-off statement take `Scrubbed[...]` |
| HTTP responses | The exception handler and the problem-details serializer pass every error body through the gate, so an echoed input cannot carry a credential out |
| Version history | `store.blobs.put` takes `Scrubbed[bytes]`; every revision blob passes the gate |
| Generated code | `check_tree` on the normalized tree before commit (A§14.4) |
| Reports and derived artifacts | They are blobs, so they pass the blob gate |
| Traces | An OpenTelemetry span processor scrubs attributes before export |
| Database logs | Statement parameters are never logged (A§5.2) |
| Exports (later) | Every entry passes the blob gate, plus a manifest-level check |
| Model requests | Bedrock is inside the trust boundary (Agentify's own AWS account, EU, no training, no logging, A§17.2), so it is not an exit in the brief's list. One rule still applies: **known credential values** (configured and per-request credentials, tokens in use) are never sent to a model. `LedgerModel` and the sandbox model gateway check outgoing request bodies for known values and refuse the call (abort) on a hit; pattern-only matches in client content are allowed, because client content is the input |

A hit in content destined for storage is **redacted with a marker** for logs, process output, events, traces, chat and HTTP responses, and **fails the write** for artifacts, generated code, inbox items, waivers and sign-offs (an artifact or a human decision must not be silently altered). An agent writer gets a correctable error (A§10.2); a human writer gets an API rejection (`422`) naming the location, never the value. Every hit is recorded as a security event without the secret.

**Fix at the cause** (§8.12): a hit is treated as a defect to trace (why did the value reach that exit?), not just redacted. The leak drill tests (A§26) plant credentials at every exit in this table and assert both the block and the security event.

---

## 19. Identity, authorization, tenancy, separation of duties

- **Authentication:** the gateway verifies OIDC ID or access tokens from the configured issuer (signature against cached JWKS, issuer, audience, expiry). Users are created on first sign-in. The API never trusts a client-supplied user id.
- **Authorization:** every engagement-scoped request checks membership and role in the service layer. Postgres row-level security keyed on the transaction's engagement set is the second wall. Object-storage keys are prefixed by engagement (`<engagement_id>/sha256/…`), and the blob store API requires the engagement context and refuses keys outside it, so content is never deduplicated or addressable across engagements, and an engagement's data can be exported or purged as a unit. Storage credentials are held only by `agent`, `worker` and, for its prefix, `sandbox-exchange`; never by the client, the gateway or a sandbox.
- **Background work and sandboxes** carry the engagement id in the job record; the worker sets the RLS context from it; a sandbox receives only its own job's bundle, through a token bound to that job.
- **Roles** (OD-9): `consultant`, `waiver_approver`, `reviewer` per engagement; `admin` per deployment. Admin manages users and assignments and **activates** approved rule-pack and catalog versions; admin has no engagement content access unless also a member.
- **Rule-pack and catalog management** (§11.13): packs and catalogs are registry files, so adding, versioning, reviewing and retiring them is a pull request with required review; a pack version records `approved_by` and `approved_at` (the operator approval of its blocking rules, §6.9) and an `effective_to` date when retired. At runtime an admin activates an approved version for the deployment (`rule_pack_activation`), which triggers re-assessment notices (A§11.5). Catalog entries are edited only by their named human maintainers through the same review; agents have no write path to the registry.
- **Four-eyes:** waivers and sign-offs check `approver ∉ authors(key)` (A§11.2). The check is server-side; the stored decision records the author set it was checked against (`checked_authors`).
- **Attribution:** every inbox answer, edit, waiver, sign-off and override stores the user id and time, and is immutable (§1, §11.12).
- **Rate limits:** per user and per engagement, stored in Postgres (not in process memory), so they hold across gateway machines.

---

## 20. Chat, inbox, languages

- **Chat history** is server-authoritative and append-only (`chat_message`, no update or delete grants). The client sends only the new message; the server builds the model history from the table. Concurrent sessions on one process append with a per-process sequence under a row lock, so neither overwrites the other, and the table is usable as an audit trail (§5).
- **Inbox** (§6.6): items have kind (`agent_question`, `user_answer`, `user_steering`, `confirmation`), category (from the registry, per phase), options, status (`open → answered → acknowledged`) and threading by parent. An item is a sequence of immutable versions, keyed `(item_id, version)`; a status change is a new version. Agents see the full item including its category. Answers record the answering human; answers in the `applicability` category (later) and `confirmation` items (A§8.2) are accepted only from humans. Each revision records, in `revision_input`, the inbox items and answers its run consumed, so inbox content is versioned with the artifacts it shaped. There is no delete endpoint for inputs or inbox items.
- **Verdict vocabulary** (§1 force 2, HZ-30): a phrase scanner (per output language, maintained in the registry: "compliant", "safe", "guaranteed", "will save", and their equivalents) runs on every model-written text: chat and router output, narratives, handover text, governance-report summaries. In artifacts a hit is a correctable error to the writing agent. In chat the message is stored and shown with a visible note that the system does not issue verdicts, and the hit is counted per prompt version.
- **Languages** (OD-10): UI and message catalogs in `i18n/` with English as the source and German, Dutch, Spanish and Swedish machine-translated; each key records `reviewed_by` or `machine`. CI fails if any key is missing in any locale (translation parity, §14). **Output language** is a process setting (defaulting to the engagement's default), recorded in every artifact envelope (A§6) and passed to every agent as a runtime instruction (prompts are not translated), including Claude Code (A§14.4).

## 21. Observability

- OpenTelemetry traces across gateway → harness → router → subagent → tools → model calls → assessment, with run id, process, engagement, agent kind, prompt version and rule-pack version as attributes; the span processor scrubs attributes (A§18).
- **Cost attribution** (OP-15): every model call's settled cost is attached to its span and to the ledger with `(run, agent kind, capability)`, so cost rolls up per run, per capability and per engagement.
- Structured run events (scrubbed before storage) back the live previews and the run history.
- Governance metrics: findings and waivers per rule and pack version (A§12.4).
- Retry statistics per tool and error class feed the eval harness (A§10.3).
- The exporter target is an EU-hosted backend chosen at deployment; nothing is exported outside the EU (OD-3).
- Health: nested `/health` (gateway includes agent, worker and database status), plus post-deploy verification scripts as in the reference project.

### 21.1 Operational contracts (§12.1 layer 5)

Measured from run records and the ledger, alerted through the observability backend, and reviewed per release. Targets marked *to be set* are engineering targets fixed after the first measurements; they are not claims.

| Contract | Measure | Target | Enforcement |
|---|---|---|---|
| Reliability contract (§15.1) | Share of flow-authoring runs ending `committed` with a gate pass, or `rolled_back` with verification passed, over all finished runs, trailing 7 days | ≥ 99% | Alert below target; release blocked if the eval corpus run falls below |
| Rollback correctness | Count of `rollback_failed` and quarantined processes | 0 | Page on any occurrence |
| Stuck runs | Runs non-terminal longer than lease timeout + sweeper interval | 0 | Alert; sweeper lag metric |
| Cost per run | Settled cost per run kind | Per-kind ceiling in configuration | Enforced by the ledger (A§17.3) |
| Chat responsiveness | Time to first streamed event, p95 | To be set | Alert on regression |
| Assessment duration | Commit → readiness record, p95 | To be set | Alert on regression |
| Code generation and conformance | Job wall-clock, p95 | Bounded by the job deadline | Deadline enforced in the VM and the worker (A§14.5) |

---

## 22. Invariant enforcement map (required items 1 and 2)

**Rule for this table:** a mechanism counts as enforcement only if it is code that runs whatever the model does, and whatever the UI does. Prompts, model-called tools and UI behavior appear only as optimizations.

### 22.1 Invariant → enforcing mechanism

| Inv | Invariant (§10) | Enforcing mechanism (code) | Where |
|---|---|---|---|
| 1 | A flow-authoring run ends in exactly one of: valid flow committed, or workspace verifiably at base | Writes only to fenced run staging; harness gate after every loop exit (finish reason, structure, non-degeneracy, shrink bound); cancelled runs skip the gate; one fenced, atomic commit transaction that re-validates the merged result; shielded `finish` on every exit including `CancelledError`; lease epoch + sweeper for kills and stalls; rollback verification against the database; quarantine on verification failure | A§7.2–7.4, A§8.3–8.6 |
| 2 | User edits live in a separate layer, merged deterministically; no agent run can lose, misapply or overwrite them | No agent tool can write the edit layer or layout (tools are built from declared paths); inbox answers become edit-layer patches written by the edit API; commits replace only the writer's staged paths on top of the current head; user-namespace ids; anchored patches (including additions referencing agent items) with orphan reporting; pure total merge; human edits carry their base and get 409 on conflict | A§6.2, A§7.3, A§9.3, A§13 |
| 3 | Locked items survive incremental runs, or the run fails and rolls back; from-scratch may ignore locks by explicit intent | Gate lock check against the edit layer at head; ghost locks and unreadable edit layers refuse admission; from-scratch on a flow with anything to lose requires an attributed human `intent_confirmation`, which no agent tool can create; `lockable` defined once | A§6.2, A§8.2, A§8.6 |
| 4 | Redesigned flows cover every current-state node exactly once | Gate coverage check; admission refuses on a missing or invalid current state; current-state changes during the run are a `base_moved_conflict`; current-state changes after commit make the redesign *coverage stale* (not valid, not ready) through `valid(key)` | A§7.3, A§8.2, A§8.6, A§11.2 |
| 5 | Containers have ≥ 2 children | Gate and every commit transaction with the single `children()` definition; edit API validates merged flows on write | A§6.1, A§6.2, A§7.3 |
| 6 | One active run per process per kind, across every entry point | Partial unique index over all non-terminal states, tested against the state enum; `admit` is the only creator of runs; jobs start only `admitted` runs; sweeper ends lost and unclaimed runs | A§5.2, A§8.2, A§8.4, A§8.5 |
| 7 | Every write lands in history; one revision per run with only its writes; history immutable | One commit function for every writer; new path map = head + the writer's staging only; fenced state transition allows one commit per run; blob existence checked and GC protected by pins; `run_id` and author on the revision; no `UPDATE`/`DELETE` grants on history tables; restores are new, validated revisions | A§5.2, A§7.1–7.3 |
| 8 | Secrets never leave the backend | Every exit API accepts only `Scrubbed[T]`, including process output, run records, HTTP errors and human decisions; tree check on generated code; known values never sent to a model; no parameter logging; leak drills per exit | A§18 |
| 9 | Subprocesses executing generated or imported code cannot touch anything outside their scope | Separate Fly organization; per-job microVM with self-enforced deadline; no DNS; root-installed egress firewall; unprivileged workload uid; allow-listed environment; no credentials in the VM; read-only bundle; model gateway and exchange split by credentials; per-job, lease-bound tokens; sweeper destroys orphaned machines | A§14.5 |
| 10 | Corrupt inputs degrade gracefully on read without dropping constraints; the gate is absolute on write | Per-artifact tolerance table (A§22.3); constraints (locks, edit layer, rule pack) fail closed; corrupt flows are never silently regenerated (rebuild needs human intent); gates and commit-time validation on every write | A§8.2, A§22.3 |
| 11 | Ready only for the current revision, with no unwaived blocking finding, contracts for every AI and deterministic node, typed edges; witnesses on every finding; waivers named, justified and versioned | Readiness keyed by revision key computed from head; every committed key assessed (no coalescing); deterministic rule engine; estimates cannot exempt nodes; waivers bound to `(finding_key, revision_key)` with role, content-based four-eyes and stored author set; readiness re-checked inside commits of long jobs; assessment fails loudly on corrupt inputs | A§11, A§12 |
| 12 | Every figure traceable and honest | `Value` with mandatory provenance; estimates are always ranges; `Derived` carries inputs and provenance kinds; rendering requires an interval and a trace; (release 2) seeded simulation with conservation | A§6.3, A§13, A§15 |
| 13 | Every acceptance contract grounded, with a negative case, tied to node identity | Contract artifact gate: span-grounding check by hash, negative case required, `matches_schema` never the only assertion, schema subset, judgment spec for containers; node anchor; orphaning on node change; readiness ignores orphaned contracts | A§14.1–14.3 |
| 14 | No regulatory output claims more than it covers | (Deferred capability) draft lifecycle bound to a key including the catalog version and answers; reviewer role with four-eyes; human-only applicability; export gate; verdict-phrase scanner | A§11.3, A§16, A§20 |

### 22.2 Exit-path matrix for the foundation invariants

Exit paths: **S** success · **ME** model error · **VF** validation failure · **UC** user cancel · **CD** client disconnect · **RC** runtime-level cancellation (§6.3 item 4, HZ-12) · **PK** process kill or stall · **SF** storage failure · **CR** concurrent runs on one process · **UE** user edits during a run.

| Exit | Inv 1 (commit or base) | Inv 2 (user edits) | Inv 3 (locks) | Inv 4/5 (coverage, arity) | Inv 6 (admission) | Inv 7 (history) | Inv 10 (corruption) |
|---|---|---|---|---|---|---|---|
| **S** | Gate passes → one fenced, atomic commit that re-validates the merged result | New map = head + run paths; head's edit layer and layout preserved | Gate and commit check locks at head | Gate and commit check both | Run leaves the active states on commit | One revision, only run paths, run id | Gate re-reads staged bytes from storage |
| **ME** | `RunAbort` / provider error → shielded `finish` → rollback → verify | Staging discarded only; no shared write | Nothing committed | Nothing committed | Run ends `rolled_back`; slot freed | No revision | — |
| **VF** | Gate fails (including truncated finish, structure, shrink bound) → rollback → verify; the user sees "nothing was changed" | As ME | Lock violation is a gate failure | Violation is a gate failure | As ME | No revision | Corrupt staged artifact fails the gate |
| **UC** | Cancel flag → `AgentRun.cancel()` → gate skipped → shielded `finish` → rollback → verify; a cancel during the gate is caught by the commit's fence | As ME | As ME | As ME | As ME | No revision | — |
| **CD** | Attachment heartbeat older than the grace period → stop request → as UC; a reconnect within grace renews it; a killed gateway stops renewing, so the run stops | As ME | As ME | As ME | As ME | No revision | — |
| **RC** | `CancelledError` caught as `BaseException` → shielded `finish` → rollback → verify → re-raise | As ME | As ME | As ME | As ME | No revision | — |
| **PK** | Lease expires → sweeper bumps the epoch and finishes the run (`executor_lost`); a stalled executor that wakes up cannot write or commit (fence); a kill inside the commit transaction is atomic | No shared write before commit | As ME | As ME | Sweeper ends the run; unclaimed `admitted` runs too; slot freed | At most one revision (fenced transaction) | — |
| **SF** | Commit transaction fails → `commit_failed` → rollback → verify; database unreachable → lease expiry → sweeper when back; blob write failure during the run → abort; a missing blob is caught before the revision is written | As ME | As ME | As ME | Run marked failed, never left non-terminal | No revision; never a revision pointing at a missing blob | Unreadable artifact on read → tolerance table |
| **CR** | Same kind: second admission refused by the unique index. Different kinds touching overlapping paths: commit detects `base_moved_conflict` → rollback | Each commit replaces only its own paths on the current head | Each run checks locks at head | Gate inputs moved → conflict → rollback | Database constraint | Each run its own revision | — |
| **UE** | A user edit commits as its own revision (with its own base, 409 on conflict); the run's commit rebases onto it and re-validates the merged result; a current-state edit during a redesign run is a conflict → rollback | The user's revision is never part of the run's revision and is untouched by the run's rollback | Locks added during the run are honored (checked at head) | Merged result re-validated in the commit; current-state edits conflict | Edits are not runs; no admission | The user's edit is one human-authored revision | Edit API and commit validate the merged result |

### 22.3 Tolerance on read (invariant 10)

| Artifact | If unreadable or corrupt on read | Constraint impact |
|---|---|---|
| Authored flow | Reads return a `Corrupt` marker with the parse error; the canvas shows the corruption (never an empty flow); incremental runs are refused; rebuilding needs a from-scratch run with attributed human intent (A§8.2), so it is never silently regenerated; assessment fails loudly; nothing derived from it is served | Corruption is never served downstream (§6.4) |
| Edit layer | Merge returns the authored flow plus "edit layer unreadable"; rendering works; incremental (lock-sensitive) runs and readiness are refused until resolved | Locks fail closed (HZ-16) |
| Layout | Ignored with a report entry; the flow renders with automatic layout | None: layout carries no constraint |
| Profile set | Assessment fails loudly; no readiness record | No readiness on partial data |
| Contract set | Assessment fails loudly | As above |
| Rule pack | Registry validation fails at load; assessment refuses to run; readiness never granted | §12.5C |
| Specification artifacts | Agents receive the corruption as a correctable read error and may regenerate their section; downstream runs refuse | — |
| Run staging | Gate failure | Absolute gate |

All corrupt reads are surfaced as events; agent artifacts are never silently regenerated (§6.4).

### 22.4 Exit paths for the remaining invariants

| Exit | Inv 8 (secrets) | Inv 9 (sandbox scope) | Inv 11 (readiness) | Inv 12 (figures) | Inv 13 (contracts) |
|---|---|---|---|---|---|
| **S** | Every stored or emitted value passed the gate on its exit | Results enter only through the exchange and the worker's checks | Readiness computed for the new key by its own assessment job | Every figure carries interval and trace | Contract gate passed; anchors recorded |
| **ME / VF** | Error texts and gate reports are scrubbed run-record fields | Job fails; VM destroyed; token refused afterwards | No new key, readiness unchanged | Nothing new rendered | Partial contract set fails the artifact gate |
| **UC / CD / RC** | As ME | Worker destroys the VM; the token stops working once the run is terminal | No new key | — | Rolled back, no partial set |
| **PK** | The process supervisor scrubs stdout/stderr, including a crash traceback | VM self-terminates at its deadline; sweeper destroys labelled machines; token bound to a live lease | A killed assessment job leaves no record for its key, so the key stays *assessment pending*, never ready; the job is retried from its queue | — | As UC |
| **SF** | A write that fails is never retried without the gate | Upload refused; job fails | Assessment fails loudly; no readiness record | — | — |
| **CR** | Gate is per call, stateless | One VM per job; tokens bound to one job | Assessment jobs per key run in commit order; waiver creation checks the finding against the current key inside its transaction, so a waiver for a key that moved is rejected | — | One contract-drafter per process (admission) |
| **UE** | User text passes the gate on the edit API | — | Any key-component edit changes the key; long jobs re-check readiness at commit | Overrides carry `stated` provenance | A node edit orphans its contract; the contract set's anchor no longer matches, so readiness waits for a new set |

Invariant 14 belongs to the deferred regulatory capability; its admission-level guard in release 1 is that no export or sign-off endpoint exists.

---

## 23. Hazard register (required item 4)

All 32 hazards of §9 have a design answer now. Their pinning tests land with the capability they belong to: HZ-22, HZ-25, HZ-28 and HZ-29 belong to deferred capabilities and are tested when those ship. "Test" names the pinning test (one per hazard minimum, §12.3); `VERIFICATION.md` tracks each as `HZ-n`.

| # | Hazard | Design | Pinning test |
|---|---|---|---|
| 1 | Stream line-limit exhaustion | 64 MiB line limit in the code-generation stream reader; fail loudly, never truncate (A§14.4) | Oversized fake line → job fails with `stream_line_too_long` |
| 2 | Truncated output misread as malformed arguments | Output limits per model and role (A§17.1); small mutation tools; framework retries counted by the harness loop (A§10.3); a truncated final response fails the gate (A§8.6) | Scripted truncated tool call, then a small successful call, repeated |
| 3 | Encoded entities in model text | `normalize_text`, idempotent, applied by the canonical serializer to every string leaf on every write path, and by the `NormalizedText` field type (A§6, A§6.1) | Property: idempotent; applied through every write path |
| 4 | Provider inferred from a bare model name | Explicit client construction with explicit credentials; env discovery disabled (A§17.1) | Construction test with an empty and a hostile environment |
| 5 | Degenerate output accepted | Finish-reason check, structural soundness, minimum size and incremental shrink bound in the gate (A§8.6) | Scripted early stop in from-scratch mode, and an incremental run deleting most nodes → previous flow survives |
| 6 | Unbounded corrective loops | Cumulative Agentify error budget counted in tools and in the harness loop; framework counter set above it (A§10.3) | Scripted repeated, alternating and framework-retried failures |
| 7 | Common mistakes treated as fatal | Correctable class with an explicit abort list (A§10.2) | Scripted tool exception per class |
| 8 | Optional gate | Harness-owned gate after every loop exit (A§8.6) | Scripted run that never calls `finalize` |
| 9 | Concurrent tool calls race | Per-(process, path) locks and a shared invalidated cache per run (A§8.4) | Four concurrent mutations in one model response |
| 10 | Shared working tree | Run staging; commits replace only run paths (A§7.2) | User edit and sibling commit landing mid-run |
| 11 | Request-scoped lifecycle | Runs owned by the harness; gateway only relays; disconnect detected by a durable attachment heartbeat (A§2, A§8.5) | Disconnect mid-run; kill the gateway mid-run |
| 12 | Runtime cancellation bypasses error handling | `BaseException` path with shielded `finish` (A§8.4) | Cancel the asyncio task directly |
| 13 | Stranded running records | Fenced leases, sweeper at startup and periodically over all non-terminal states including unclaimed `admitted` runs; every failure path updates the record (A§8.4–8.5) | Commit failure; SIGKILL and restart; executor stalled past its lease, then resumed |
| 14 | Rollback fails silently | Rollback verification against the database; quarantine; loud event (A§7.4) | Injected verification failure |
| 15 | Identity collision after regeneration | `u_` namespace, anchors, orphans, total merge (A§6.2) | Property suite with colliding ids, vanished parents, edge overlays |
| 16 | Constraints fail open on corrupt input | Fail-closed admission and readiness; from-scratch needs human intent (A§8.2, A§22.3) | Corrupt the edit layer, then dispatch a from-scratch run from the router |
| 17 | Runtime state in the versioned tree | Separate bucket and credentials; commit path cannot write it, rollback cannot delete it (A§5.1) | Plant runtime files; commit and roll back |
| 18 | Shared agent-tool state directories | Per-job VM and per-run `CLAUDE_CONFIG_DIR` (A§14.4) | Environment-construction test |
| 19 | Stale session resume loops | One fresh retry on a zero-output resume failure (A§14.4) | Scripted failing resume |
| 20 | Generated layout drift | Pure normalization before any read (A§14.4) | Pure-function test over nested layouts |
| 21 | Secrets leave through an overlooked exit | `Scrubbed[T]` at every exit (A§18) | Leak drill per exit |
| 22 | Untrusted import treated as trusted | Import not in release 1 (OD-15). When added: same validation and gate as agent output; imported waivers and sign-offs dropped; executable content quarantined; size, count and path bounds | Hostile-archive suite, written when import is scheduled |
| 23 | Fabricated precision | `Value`/`Derived` only; no point figure renders without range and trace (A§6.3) | Rendering refuses a bare number |
| 24 | Estimate laundering | Provenance mandatory and propagated by `Derived` (A§6.3) | Property: every output knows its input provenance |
| 25 | Simulation non-conservation | Stuck cases reported; loop bounds (A§15) | Flows with loops and dead ends (release 2) |
| 26 | Waiver fatigue | Four blocking rules, each justified; orchestrators exempt from escalation; layout outside the revision key; waiver-rate review (A§11.1, A§12.4) | Eval dimension: waiver-free readiness rate; dragging a node keeps readiness |
| 27 | Tautological contracts | Structured expectations only; `matches_schema` never the only assertion; negative cases required; mutation check (A§14) | Deliberately broken component fails its contract |
| 28 | Silent applicability inference | Human-only answers in the applicability category (A§16) | Agent-authored answer rejected (with the deferred capability) |
| 29 | Stale catalogs | Catalog version in the revision key; re-assessment notices (A§11.3, A§16) | Catalog version change flags flows (deferred) |
| 30 | False assurance | Draft labels, coverage statements, sign-off gate, verdict-phrase scanner (A§16); for release 1, the verdict scanner also runs on the governance report and the handover text | Export blocked without sign-off; scanner fixture set |
| 31 | Stale readiness | Revision-key readiness (A§11) | Edit after readiness resets it, for each component |
| 32 | Leaked event loops or resources in tests | pytest with warnings as errors; anyio resource checks; dated suppressions with removal dates | CI configuration |

---

## 24. Open problems (required item 5)

| # | Open problem (§13.2) | Release 1 | Answer in this design |
|---|---|---|---|
| 1 | Verified rollback on every exit path | **Solve** | Fenced run staging + one validating commit transaction + shielded finish + leases with epochs + verification (A§7, A§8, A§22.2). Base state after restart is the run row's `base_revision_id`; the run's writes exist only as its staging rows |
| 2 | Stable identity between agent and user layers | **Solve** | `u_` namespace; anchored patches; orphans shown in the edit-layer conflict report and on the canvas (later); locks on user items are inert for agent runs; anchor maps carry identity across migrations (A§6.2, A§6.10) |
| 3 | Sandbox isolation | **Solve** | A§14.5 |
| 4 | Regulatory scope and liability | Deferred (operator, OD-12) | Capability not shipped until answered |
| 5 | Evals for subjective artifacts | **Solve for flow authoring and contract meaningfulness** | A§26.5 |
| 6 | Autonomy scale and rule format | **Solve** | A§12.2, A§12.3 |
| 7 | Judgment nodes and contract formalism | **Solve** | A§14.1, A§14.2 |
| 8 | Assumption quality and calibration | Deferred (OD-14) | Provenance `measured` exists from day one, so later calibration can replace estimates without schema changes; replacing a value changes the revision key and resets readiness and sign-off, as it must |
| 9 | Prompt and skill versioning | **Solve** | A§9.5 |
| 10 | Schema evolution | **Solve** | A§6.10 |
| 11 | Durable orchestration semantics | **Solve** | Postgres queue with leases; cancellation is a durable flag; half-completed runs are not resumed but rolled back (resumability is not needed for correctness and would complicate isolation); commits happen at most once per run, by the fenced state transition inside the transaction; admission covers every entry point (A§8) |
| 12 | Multi-tenant security | Deferred (OD-14) | One firm per deployment; engagement segregation (A§19). Per-tenant key management is needed only for multi-firm deployments |
| 13 | Cost governance | **Solve** | Ledger with reservations (A§17.3) |
| 14 | Conversational UX contract | Deferred (OD-14) | Release-1 elicitation is sensitivity-ranked (A§13), which makes the later measurement possible |
| 15 | Observability across the agent tree | **Solve** | A§21 |

---

## 25. Deviations from the brief (required item 6)

| ID | Brief says | Design does | Reason |
|---|---|---|---|
| D-1 | Recommended: a version-control repository per workspace (§6.5); object store plus revisions table allowed where scale demands it | Object store plus revisions in Postgres | The operator chose multi-machine cloud hosting (OD-2). Fly volumes attach to one machine (research R2), so per-workspace repositories would need a shared filesystem that does not exist there. Revisions in Postgres make the commit one transaction with the fence, head update, validation and staging cleanup, so rollback never has anything to undo, only staging to discard; tests verify it on every exit path. Diff and restore are implemented per artifact type, which the UI needs anyway (structural diffs of flows) |
| D-2 | Corrective attempts bounded per tool or error class (§6.3) | Requirement kept; **the framework's own retry counter is not used as the bound** | Measured (S1): Pydantic AI's per-tool counter resets after a success and does not count `ToolFailed`. Agentify counts cumulatively itself (A§10.3) |
| D-3 | Disconnect handling owned by the harness; disconnect ends like cancel (§12.5B) | Kept, with a configurable reconnect grace period (default 15 s) before the cancel | Network blips on long chat turns would otherwise roll back work a user is still watching. A test pins that a disconnect without reconnect ends exactly like cancel |
| D-4 | Decide whether self-loops and parallel edges are allowed (§4.2) | Self-loops rejected; at most one edge per ordered node pair | Loops stay visible and boundable; witnesses stay unambiguous |
| D-5 | Locks reference items the agents can honor, "unless the design defines how incremental runs treat them" (§12.5A) | Locks on user-created items are accepted and inert for agent runs; ghost locks on agent items count as violated | Agent runs have no write path to user items, so such locks are always honored; ghost locks fail closed |
| D-6 | No feature bypasses the provider abstraction (§6.8) | Claude Code makes its own model calls, but only to the sandbox gateway, which is a thin HTTP layer over the same abstraction, ledger and secret gate | OD-13. The abstraction is preserved at the network boundary rather than the import boundary; managed settings pin Claude Code to that endpoint |
| D-7 | Half-completed runs: "resumability" is an open question (§13.2.11) | Not resumed; always rolled back | Rollback only discards staging, which is cheap and tested on every exit path; resuming would require trusting partial model state |
| D-8 | §6.10 ranks elicitation by business-case sensitivity (§6.11) | Release 1 ranks by expected-value sensitivity from an absorbing Markov model; Monte Carlo sensitivity replaces it in release 2 | Simulation is not in release 1 (OD-12); the Markov model is exact for expected values and labelled as such |
| D-9 | §5 phases include the five-phase UI | Backend and API only in release 1 | OD-11 |
| D-10 | Remaining budget is read from provider or gateway responses and cached briefly (§6.8) | Remaining budget comes from Agentify's ledger; with a corporate gateway that reports budget, the gateway figure is shown as well and the lower of the two governs | Bedrock reports no remaining budget, and AWS Budgets lag by hours (R1.4); enforcement needs a live figure (§13.2.13) |
| D-11 | The user-edit layer holds positions and layout direction (§6.4) | Kept in the user layer, but in its own path `layout.json`, outside the revision key and outside gate inputs | Moving a node must not reset readiness, void waivers or roll back a running incremental run (HZ-26) |
| D-12 | Invariant 1: the workspace "verifiably restored to the run's base state" | Interpreted as: no effect of the run remains; other writers' commits during the run are preserved | §12.5B requires that a user edit made during a run survives that run's rollback; a literal reset to the base revision would destroy it |
| D-13 | Waivers, sign-offs and inbox Q&A are versioned with the artifacts they apply to (§6.5) | Stored as immutable, attributed database rows bound to revision keys, linked from revisions through `revision_input`, not as files inside revisions | They are decisions about a revision, not content of it; binding them to the key makes "one waiver, one revision" structural. History is fully reconstructable, and an export (later) bundles them with the artifacts |
| D-14 | §6.9 recommended rule family "structural" (reachability, termination) is a governance rule | Reachability and termination are part of **validity** (A§6.1 level 3): a flow with unreachable or dead-end steps cannot commit | An abandoned, half-connected flow is exactly the degenerate output the reliability contract must reject (HZ-5); keeping it only as a readiness rule would let it replace a good flow |
| D-15 | Extension contract: a new artifact type is a declarative change (§6.2, §14) | Declarative for types whose checks come from the check library; a genuinely new kind of check needs one library function and its test | A check that does not exist cannot be declared; the library keeps such additions small, reviewed and reusable |

There is no deviation from any §10 invariant; D-12 states how invariant 1's wording is read, because the brief's own behavior contract requires that reading.

---

## 26. Test strategy (required item 10)

### 26.1 Principles

- **Contract and orchestration tests drive the real framework.** Pydantic AI runs for real; only the model is replaced, by a `FunctionModel` script (spike S1 showed the script sees the real tool schemas). The real Postgres (testcontainers), real S3-compatible store (MinIO container), real commit, rollback, secret gate and rule engine run. No test replaces the agent or stubs what it claims to cover (§8.11, §12.5B).
- **Fault injection uses real components with injected faults**, not replacements: a storage wrapper that raises at a named point, a database connection killed mid-transaction, a worker process sent SIGKILL.
- **Every test asserts the artifact state and the version history** (§12.5B), not only the return value.
- **One test per hazard at minimum** (A§23), and one per exit path per foundation invariant (A§22.2).
- **Pure unit tests** (`domain`, `governance`, `dataflow`) touch no filesystem, network or database. Integration suites are separate and marked.
- **Tested documentation** (§8.7, §11.10): a doc test checks that every module path, registry id and `A§` reference in this document exists, that every invariant and hazard row maps to a `VERIFICATION.md` row, and that schema excerpts here match the schemas generated from the Pydantic models. A mismatch fails CI, so the architecture document cannot drift silently from the code.

### 26.2 Layers and suites

| Suite | Contents | Runs |
|---|---|---|
| `unit` | Tools, normalization, environment construction, layout normalization, stream reader, secret-gate matchers, unit conversions | Every push |
| `property` (Hypothesis) | Flow schema and every invariant on generated graphs; merge properties with colliding ids, vanished parents, edge overlays (§12.5A); every governance rule with planted violations (each found, nothing else, correct witness); determinism; normalization idempotence; provenance propagation; migration anchor stability; dataflow subtype soundness against generated instances | Every push |
| `analytic` | Markov expected values on linear, branching and loop flows (release 1); simulation analytics (release 2) | Every push |
| `orchestration` | §12.5B cases, each a separate test (error channel, bounded attempts incl. alternating, tool exception, degenerate stop, gate not called, governance feedback, cancel between steps, disconnect and runtime cancellation, parallel sections, section failure, user edit during run, concurrent admission from every entry point, request-limit exhaustion) | Every push (Postgres and MinIO containers) |
| `failure` | §12.5C case table: one row per corruption or attack; a new invariant or attack is a new row (A§26.3) | Every push |
| `api` | HTTP surface: auth, RLS segregation, redesign flow, version restore, waiver and four-eyes, budget refusal | Every push |
| `sandbox-local` | Executor and conformance logic on the local container adapter | Every push (Linux CI) |
| `smoke` | Against the built images: every role starts, all runtime dependencies are present, health endpoints report every dependency, the sandbox image contains the pinned toolchain | Every release build |
| `sandbox-drill` | Escape drills against the real sandbox organization in staging | Nightly and before release |
| `e2e-canary` | One golden spec through intake → specification → flows → assessment → contracts on real Bedrock EU; asserts artifact contracts only | Nightly |
| `evals` | Golden corpus with rubric and judges (A§26.5) | On prompt, skill, schema, rule-pack changes and before release |
| `ops` | Operational contracts measured in a deployed environment (A§21.1); each alert is shown once to fire on a planted condition | Continuously in staging and production; alert drills before release |

### 26.3 Failure-injection table

Shape: `Case(name, corrupt: Callable[[World], None], leg: Leg, expect: EndState)`. Release-1 rows: executor stalled past its lease, then resumed (fencing); gateway killed mid-stream (attachment heartbeat); router dispatching from-scratch on a corrupt or locked flow (refused without human intent); ghost lock (admission refused); estimated value that would exempt a node from a blocking rule (finding, not exemption); blob reused while the collector runs (pin protects it); node dragged after readiness (readiness kept); crash traceback containing a credential on stderr (scrubbed); sandbox VM outliving a killed worker (destroyed, token refused); corrupt flow artifact; corrupt edit layer; locked item changed in an incremental run; rollback verification failure; commit failure; process kill mid-run and mid-commit; planted credential at each exit of A§18.2 (generated code, files outside `generated/`, logs, process stdout/stderr, run events, run records, chat, inbox, waivers and sign-offs, HTTP error bodies, version history, reports, traces, database logs, outgoing model requests; exports once they exist), each with a known value, a pattern-only credential and a credential-named file; runtime state planted then commit and rollback; edit after readiness for each key component; corrupt rule pack; Bedrock 401, 403, 429 and 5xx; budget exhaustion mid-run; normalization on every write path.

### 26.4 Definitions of done (§12.2), as gates

- **DoD-code (every PR):** ruff, ruff format check, mypy `--strict`, import-linter, registry validation, translation parity, spell checking (`typos`) on code, docs and message catalogs, the architecture doc test (A§26.1), gitleaks, unit + property + analytic + orchestration + failure + api + sandbox-local suites with warnings as errors, coverage ratchet (never decreases), zero-tests-discovered fails the job.
- **DoD-agent:** eval suite shows no regression on affected capabilities; retry-statistics diff attached; `prompt_version` changed.
- **DoD-rules:** rule property tests and the planted-violation corpus pass.
- **DoD-release:** all of the above, plus the smoke test against the built images (all runtime dependencies present, every role starts), schema-migration tests, sandbox drills, the e2e canary and the golden-corpus eval.
- **DoD-product:** §12.2 over the real corpus (OD-17). Synthetic cases never count toward it.

### 26.5 Eval harness (§12.5D–F)

- **Corpus:** `evals/synthetic/` in the repository (labelled synthetic; the canonical minimal case: a four-step linear process with one step per execution type, expecting exactly four nodes). Each case's expectations record holds, besides the flow expectations of §12.5D, the planted governance violations with their expected witnesses, human-provided profile values for judging estimates, and expected contract properties. The **real corpus lives in a private S3 bucket in `eu-central-1`** and never enters the public repository (OD-19). Expectations are data, versioned with the corpus.
- **Programmatic dimensions:** validity, coverage, arity, node-count bounds per spec; for governance (release 1): planted violations found with correct witnesses, and the share of golden redesigns reaching readiness without waivers, plus waiver rate per rule; for contracts (release 1): completeness and mutation discrimination.
- **Judged dimensions:** current-state faithfulness, archetype correctness, profile-estimate quality against human-provided values (§12.5E), contract meaningfulness (§12.5F). One judge call per dimension; 0.0–1.0 in 0.1 steps with a one-line reason; harsh scale. The judge uses the same provider abstraction (Bedrock EU).
- **Gate semantics, harness self-tests and the human-anchoring loop** exactly as §12.5D: about 20 human anchors per judged dimension; a judge must place at least 90% of scores within 0.2 of the human's before it gates; about 10% of judged runs re-reviewed; baselines updated only by humans, never in CI.
- **Where it runs:** on a protected-branch CI environment that obtains AWS credentials through GitHub OIDC (no stored keys), never on fork pull requests. Scorecards with per-case reasons are stored in the private bucket; only aggregate scores and the prompt version appear in the public repository.
- **Repeated trials:** each spec runs several times per evaluation (the count is set once the per-run variance is measured on the first corpus); results report the sample size.

### 26.6 CI and repository protection

GitHub Actions as thin orchestration over the same commands developers run (`uv run …`), with actions pinned to full commit SHAs, minimum token permissions, no secrets available to fork pull requests and no privileged triggers running PR code. Branch protection on `main` (now possible, the repository is public) requires the DoD-code checks and one review. It is configured at scaffolding, when the checks exist (G2 scope).

---

## 27. Build order and first slice

Follows §12.4 with the release-1 scope of OD-12:

1. **Slice 1, walking skeleton of the reliability contract (built 2026-10-14; evidence VERIFICATION E-1):** flow schema, normalization, integrity validation and invariants (with property tests); edit layer and merge (§12.5A property suite); Postgres + MinIO store with run staging, commit, rollback and verification; admission and run state machine; harness gate; one `current-state-modeler` run driven by a scripted model through the real framework; the synthetic minimal golden case. **Done when**, in `VERIFICATION.md`, the `INV-1` and `INV-7` rows for the exit paths S, ME, VF, UC, RC, PK and SF, the `INV-6` concurrent-admission row and the `INV-10` tolerance rows for the authored flow and the edit layer are verified, and every other row is visibly pending.
2. Remaining §12.5B orchestration cases and §12.5C failure rows for invariants 1–10; router, skills, parallel spec sections; chat stream path.
3. Eval harness MVP for flow authoring (§12.5D) with the synthetic corpus; real corpus once supplied.
4. Node profile, profiler, Markov sensitivity and governance engine with readiness (§12.5E).
5. Acceptance contracts, dataflow typing, sandbox gateway and sandbox organization, Claude Code executor, conformance (§12.5F).
6. Deployment hardening: Fly apps, OIDC federation, private networking, drills, canary.

Each slice is its own branch and PR under governed development, with its task contract, proof plan and evidence.

---

## 28. Evidence log (spikes and research)

Claims in this document that depend on external behavior, and how they were established. "Run" = observed by executing code; "Docs" = read in official documentation; "Open" = not yet verified.

| ID | Date | Claim | Basis |
|---|---|---|---|
| S1.1 | 2026-10-14 | Pydantic AI 2.56: exceeding a tool's `retries` raises `UnexpectedModelBehavior` | Run |
| S1.2 | 2026-10-14 | The per-tool retry counter resets after a successful call of that tool | Run (alternating fail/ok × 3 with `retries=1` completed) |
| S1.3 | 2026-10-14 | `ToolFailed` does not consume the retry budget | Run, and docstring |
| S1.4 | 2026-10-14 | A plain exception in a tool propagates out of the run | Run |
| S1.5 | 2026-10-14 | `UsageLimits(request_limit=n)` stops the run with `UsageLimitExceeded` | Run |
| S1.6 | 2026-10-14 | External `task.cancel()` propagates `CancelledError` through `agent.iter()`; `AgentRun.cancel()` exists for application cancel | Run, and source |
| S1.7 | 2026-10-14 | Several tool calls in one response execute concurrently | Run (max concurrency 4 of 4) |
| S1.8 | 2026-10-14 | `FunctionModel` sees the real tool schemas | Run |
| S1.9 | 2026-10-14 | Vercel AI (protocol v5–v7) and AG-UI adapters ship with Pydantic AI; Bedrock provider accepts an explicit `bedrock_client` and otherwise falls back to environment variables | Source |

The spike script and its output are kept under `spikes/2026-10-14-pydantic-ai/` (exploratory code, kept apart from product code, never imported by it). S1 was re-run at the versions pinned at scaffolding (pydantic-ai-slim 2.56.0 in `backend/uv.lock`) with identical results (VERIFICATION E-1).
| R1.1 | 2026-10-14 | Opus 5.5, Sonnet 5.5, Haiku 5.5 (and several 4.x models) are reachable from `eu-central-1` via `eu.anthropic.*` inference profiles routing only to EU regions; Fable 5.1 has no EU profile | Docs (AWS model cards) |
| R1.2 | 2026-10-14 | Global profiles can be blocked by denying invocation when `aws:RequestedRegion` is `unspecified` | Docs |
| R1.3 | 2026-10-14 | Bedrock does not use content for training or share it with model providers; invocation logging is off by default | Docs |
| R1.4 | 2026-10-14 | AWS Budgets refresh a few times a day, 8–12 hours apart; alerts can lag | Docs |
| R1.5 | 2026-10-14 | Prompt caching is supported for the 5.5 models on Bedrock; structured outputs and count-tokens are not available on bedrock-runtime for them | Docs |
| R2.1 | 2026-10-14 | Fly Machines can obtain OIDC tokens for `AssumeRoleWithWebIdentity`; claims include app, machine and image digest | Docs |
| R2.2 | 2026-10-14 | Fly network policies match ports and protocols only, and do not cover Fly-proxy traffic; managed Postgres is reached through Flycast (proxy) | Docs |
| R2.3 | 2026-10-14 | Organizations do not share private networks | Docs |
| R2.4 | 2026-10-14 | Fly Managed Postgres is available in `fra` with HA, PITR and PgBouncer | Docs, CLI |
| R2.5 | 2026-10-14 | Volumes attach to one machine only | Docs |
| R2.6 | 2026-10-14 | Fly secrets can be scoped per process group | Docs |
| R2.7 | 2026-10-14 | The Fly proxy closes connections idle for about 60 s; streams send heartbeats every 15 s | Community posts (not docs) |
| R3.1 | 2026-10-14 | Claude Code (local 2.1.299) supports a Bedrock-format gateway with `ANTHROPIC_BEDROCK_BASE_URL` and `CLAUDE_CODE_SKIP_BEDROCK_AUTH`, holding no AWS credentials; managed `allowedProviders: ["customEndpoint"]` pins the endpoint (≥ 2.1.285) | Docs |
| R3.2 | 2026-10-14 | Headless flags (`--bare`, `stream-json`, `dontAsk`, `--max-turns`, `--max-budget-usd`, `--resume`), `CLAUDE_CONFIG_DIR`, non-essential-traffic switches | Docs, local `--help` |
| O-1 | — | **Where Fly Managed Postgres stores backups** is not documented; OD-3 requires EU backups. **Decided (OD-21): AWS RDS for PostgreSQL in `eu-central-1` is the default**, reached over TLS from Fly's static egress addresses; Fly Managed Postgres replaces it only if Fly confirms EU backup storage in writing before deployment | Decided default; Fly confirmation optional |
| O-2 | — | Tigris single-region buckets claim data stays in the region, but metadata location and data-processing terms are undocumented. **Object storage is S3 in `eu-central-1`** (contractual EU region, same account) | Decision |
| O-3 | — | Whether the Machines API accepts `image@sha256:` on create | Open, implementation spike |
| O-4 | — | Whether the Bedrock data-retention setting applies in every EU destination region of the inference profile | Open, AWS support question |
| O-5 | — | Exact IAM chain for application inference profiles wrapping EU profiles | Open, implementation spike |
| O-6 | — | Whether Claude Code's best-effort `/inference-profiles` calls can be refused by the gateway without side effects | Open, implementation spike |
| O-7 | — | Whether root init can restrict the Fly machine API socket (`/.fly/api`) to root inside a sandbox VM | Open, implementation spike; fallback in A§14.5 |
| O-8 | 2026-10-14 | The harness loop observes framework-generated retry prompts (argument validation failures) in each request node, as A§10.3 relies on | Run (slice 1, `test_framework_validation_retries_are_counted_too`); resolved |
