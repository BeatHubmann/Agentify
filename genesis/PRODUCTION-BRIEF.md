# Agentify — Production Genesis Brief

> **Audience:** a frontier LLM designing and building Agentify.
> **What this is:** the canonical specification context — intent, reframe, domain model, product surface, reference architecture, recommended defaults and anti-patterns, hazards, invariants, required capabilities, contracts and tests, open problems, constraints, and success criteria.
> **Conventions:** vendor-, stack-, jurisdiction- and environment-neutral. **Must** marks a requirement. **Recommended** or **default** marks a starting point you may improve on, with stated reasons (`IMPLEMENTATION-PROMPT.md`, *Deviations*).

---

## 1. Mission, design forces and positioning

**Agentify** is a web tool consultants use on client engagements to redesign business processes so they can be **executed by AI**. A user chats with an agent system that captures how a process works today, rearchitects it around AI agents, and makes the redesign defensible. It produces validated, renderable artifacts:

- a **specification**, assembled from three sections: current state, desired behavior, constraints
- a **current-state process flow** — the process graph as it runs today
- a **redesigned process flow** — the agentic redesign, with explicit traceability to the current-state steps it replaces
- a **node profile** for both flows — volumes, timings, costs, error rates, autonomy and decision character, each value with provenance
- a **governance report** — deterministic policy findings with witnesses, and any waivers
- a **business case** — a simulated comparison of both flows, with ranges, sensitivity and a full assumptions table
- **acceptance contracts** — per-node schemas, success criteria and spec-traced test cases
- a **regulatory register** and **control matrix** — a draft for qualified human review
- a **build plan** and a **handover prompt** — inputs for a coding agent
- an optional **generated prototype** of the redesigned process, a **conformance report** against its contracts, and a live preview

The deliverable is not a picture of a better process. It is a **governed, quantified, testable redesign**.

### Design force 1 — the reliability contract (north star)

The flow-authoring agents reliably transform a written process specification into a validated, renderable process flow — **every time, with no manual correction of tool failures**. Failure is handled by rollback, not by asking the user to fix files. Every other capability reads from these flows, so this is the foundation.

**What "validated" must mean.** Schema validity is not enough: an empty flow, or one abandoned partway through, is schema-valid. *Validated* means every flow invariant holds and the flow is non-degenerate, checked by code the model cannot skip.

### Design force 2 — no false assurance

Agentify supports decisions; it does not make them.

- **Every figure carries its range and the assumptions that produced it.**
- **Every finding carries a witness:** the concrete path or node that triggers it.
- **Every regulatory output states its coverage** (which catalog, which version, which obligations were evaluated) and remains a draft until a named, qualified human signs it off.
- **Stated, estimated and measured values are never mixed silently.**
- **The system never issues verdicts** such as "compliant", "safe" or "will save X".

### Positioning

Agentify makes no claim about what share of an engagement it performs or how much time it saves. Such claims are unmeasured figures, which design force 2 rules out. Its position is defined instead by a division of labor:

- **Models propose:** specifications, flows, profile estimates, contracts, narratives, and fixes to findings.
- **Code checks:** flow invariants, governance rules, dataflow typing, simulation and obligation mapping, all deterministic and reproducible.
- **People decide, and are accountable:** what the process actually is, which assumptions hold, whether a finding may be waived, whether an obligation applies, and whether an output may leave the system. Every such decision is attributed to a named person and versioned with the artifacts it affects.

The value is that every redesign arrives with its evidence: what was checked and against which rules, what was assumed and by whom, and what remains open. Review UIs, the inbox, version history, locks, waivers and sign-offs are where these decision rights are exercised.

Claims about speed, effort or quality are made only from measured data (§12.5, §15).

---

## 2. The reframe

The product exists to embody one strategic reframe:

- **Old question:** *"Where can we add AI?"* — leads to AI bolted onto classic human-centric processes. Limited leverage, pilots that don't scale, efficiency promises that don't materialize.
- **New question:** *"How do we redesign our processes so they can be executed by AI?"* — process design starts from AI capabilities. Structural leverage instead of efficiency patches.

An AI-native process is **adaptive, consistent, end-to-end, and composable** — instead of broken transitions, parallel stacks, local optima, and patchwork complexity.

Three implications the design must honor:

1. **The tool is itself agentic-native** — agents redesigning processes to be agentic. It dogfoods the pattern it sells. The agent topology (router, subagents, flow-authoring agents) is part of the product's proof, not incidental plumbing.
2. **Extensibility is the business model.** New phases, artifact types, rule packs and obligation catalogs are declarations and documents, not code releases (§6.2, §14). Time-to-market is the stated edge.
3. **A redesign must be defensible before it is built.** "Efficiency promises that don't materialize" are the old question's failure. Agentify answers it by making every redesign governed (where may AI act, with which oversight), quantified (what changes, with what uncertainty), testable (how to tell each component works) and obligation-aware (what it triggers) — before any code exists.

---

## 3. Business drivers and their structural consequences

| Driver | Structural consequence |
|---|---|
| Extensibility: one phase should be one skill document | Router skills are text playbooks with structured metadata, auto-discovered and listed in the router's instructions. Subagents, artifacts, output locations, inbox categories, rule packs, defaults sets and catalogs are declared as data, so no extension requires code (§6.2). |
| Input quality decides output quality: text works, large graphical process diagrams don't | Multi-format document ingestion (documents and images passed to the model natively); product guidance tells users to convert diagrams to text first. |
| Human-in-the-loop governance is *the* question (where may AI act autonomously — liability concentrates in "actor" agents) | An **inbox** is a first-class primitive. A deterministic **governance engine** makes oversight requirements checkable, with witnesses on the canvas and named waivers (§6.9). |
| Clients sign off on outcomes, not node counts | A **simulation** compares current-state and redesigned flows on throughput time, cost, human effort and error escape, as distributions with sensitivity analysis (§6.11). |
| A handover must be verifiable, not just readable | **Acceptance contracts** per node, test cases traced to the specification, and a **conformance report** against the generated prototype (§6.12). |
| Obligations discovered after building are expensive | **Regulatory mapping** against operator-supplied catalogs, during design, as a draft for qualified review (§6.13). |
| Users are the accountable decision-makers in an engagement | Every human decision — an answer, an override, a waiver, a sign-off — is attributed to a named person and versioned. Version inspection, diff and restore, review history and the assumptions table make the evidence for each decision inspectable. |
| Governance orientation: human-in-the-loop placement, audit trail, end-to-end rather than island solutions | Every artifact write is versioned; flows carry provenance (agent-authored, agent-authored-and-user-edited, user-created); a redesign cannot be finalized without a valid current-state flow and complete coverage of it, so island solutions are inexpressible. |

Deployment environment, hosting model, connectivity and jurisdictions are operator inputs (`IMPLEMENTATION-PROMPT.md`); the brief assumes none.

---

## 4. Domain model

### 4.1 The process grammar

Every agent node in a flow is one of three **archetypes** (plus non-agent steps):

- **Task agent** — does exactly one thing (classify a document, extract fields, calculate a value). Discrete and verifiable.
- **Orchestrator** — distributes: who does what, in which order. Coordinates, does not decide.
- **Actor** — decides, gathers context, exercises judgment, has access to other agents as tools. **The only archetype where liability-relevant thinking is required** — governance review focuses here.

Two **flow-authoring roles** exist in the system (roles, not archetypes):

- **Current-state modeler** — captures the existing process faithfully.
- **Redesigner** — redesigns it with agentic capabilities, aiming for *fewer nodes*, questioning every step, and recording which current-state steps each new step replaces (the **coverage mapping**).

Edges carry optional condition labels. Nodes carry an execution type (deterministic, AI, human, or external), dependencies, outputs, and structured preconditions (any-of, all-of, none-of). Container archetypes (orchestrator, actor) hold child nodes.

These terms are descriptive; the design document chooses the system's own names.

### 4.2 Flow schema requirements

- **Graph integrity, at minimum:** unique node ids; unique edge ids; edges reference existing nodes; parent references resolve; containment is acyclic. The design must decide explicitly whether self-loops and parallel edges between the same pair of nodes are allowed.
- **Reference integrity:** dependencies, outputs and preconditions reference items that exist.
- **One source of truth for ordering.** Edges and dependencies must not be two independent encodings of sequence; derive one from the other, or check their consistency as an invariant.
- **Archetype implies AI execution.** Reject contradictions at authoring time with a correctable error. Silent normalization is acceptable only inside schema migrations.
- **Text normalization** (for example decoding HTML entities in model-authored text) applies to every text field, is idempotent, and runs on every write path.
- **Every write path validates.** No mutation persists a flow without full validation.
- **Recommended:** a two-level schema — an *authored* level holding only what agents and users decide, and a *resolved* level holding derived data (coordinates, render flags, computed findings). Derived data is never authored.

### 4.3 The node profile

The facts every assessment capability needs about each node, in both the current-state and the redesigned flow. The profile is an optional, versioned schema extension.

| Field group | Examples | Read by |
|---|---|---|
| Volume and timing | cases per period, processing-time distribution, waiting time | Simulation |
| Cost | cost per execution, human effort per case | Simulation |
| Quality | error rate (as a range), detection probability, rework share | Simulation, governance |
| Autonomy | autonomy level, confidence threshold, escalation target | Governance, contracts |
| Decision character | decides about natural persons, reversibility of its effect, external effect | Governance, regulatory |
| Data | data categories processed, data leaving the organization | Governance, regulatory |
| Branching | probability per outgoing conditional edge | Simulation |

Requirements:

- **Every value carries provenance:** *stated* (from the specification or a user), *estimated* (by an agent, with a recorded reason), *measured* (from logs or tests), or *default* (from a declared defaults set). The UI shows provenance as visibly as authorship.
- **Users override values through the user-edit layer**, with the same identity, anchoring and orphan guarantees as any other edit (§6.4).
- **Branch probabilities on each conditional node sum to 1.**
- **Uncertain quantities are ranges or distributions**, never forced into point values.
- **Autonomy is measured on one declared scale**, defined in the design document (§13.2.6).

### 4.4 Artifact lifecycle and readiness

Validity and readiness are separate, so that a flow with open governance issues can still be saved, shown, discussed and waived.

| State | Meaning | Gate |
|---|---|---|
| **Valid** | Every flow invariant holds; non-degenerate | Harness gate on commit (§6.3) |
| **Assessed** | Governance findings computed; contracts generated and type-checked; profile complete enough for simulation | Automatic re-evaluation on every committed revision |
| **Ready for handover** | No unwaived blocking finding; every AI and deterministic node has a contract; dataflow schemas type-check across every edge | Readiness check; required before build planning and code generation |

Requirements:

- **Readiness is bound to a revision.** Any change to the flow, profile, contracts, rule pack or user-edit layer returns the flow to *valid* until re-assessed. A stale "ready" status is impossible by construction.
- **Derived artifacts record their inputs:** each governance report, business case, contract set and regulatory register records the flow revision, profile revision, rule-pack or catalog version, and (for simulation) the seed it was computed from.
- **Regulatory artifacts have their own lifecycle:** *draft* → *signed off* by a named, qualified reviewer → *exportable*. Any upstream change returns them to draft.

### 4.5 Governance rules, findings and waivers

- A **rule** is declarative data: an identifier, a rationale, a severity (*blocking* or *advisory*), the predicate over the graph and profile, and the remediation hint shown to agents and users.
- A **rule pack** is a versioned set of rules. Packs are data, honoring the extension contract.
- A **finding** names the rule, its severity, and a **witness**: the concrete path, node or edge that violates it.
- A **waiver** clears one blocking finding for one revision. It records the named human, a written justification and the time, is versioned with the flow, and is listed in every export.

### 4.6 Acceptance contracts

For each AI and deterministic node in the redesign:

- input and output schemas
- preconditions and success criteria
- behavior below the confidence threshold: escalate, retry, or refuse
- **test cases** — typical, edge and negative — each traced to the specification passage it comes from, or explicitly marked as an assumption

Contracts are tied to node identity. When a node changes in a regeneration, its contract is reported as orphaned, never silently kept. Contracts for orchestrators and actors, whose correctness is judgment rather than output, are an open problem (§13.2.7).

### 4.7 Obligation catalogs

- A **catalog** is versioned data for one framework in one jurisdiction. It records its effective dates and, per obligation, the source citation, the applicability conditions, and the controls that can satisfy it.
- **Catalogs are maintained by people.** Agents never author or edit catalog entries.
- **Applicability questions are answered by people.** Whether an obligation applies (use-case category, whether natural persons are affected) is decided by a human through the inbox, never inferred silently by a model.
- **Mechanizable obligations compile into governance rules** (§4.5); the rest stay checklist items for the reviewer.

---

## 5. Product surface

Five phases, each with a panel in a navigation rail; a persistent chat panel; the active canvas in the main area:

1. **Intake** — upload documents; free chat with the router.
2. **Specification** — three parallel section agents extract from inputs; a synthesis agent assembles the specification. Inbox questions surface per phase.
3. **Process design** — current-state flow, then agentic redesign, on a flow canvas (auto-layout, zoom, node inspector). Users edit nodes and edges, lock items, pin positions, and edit node profiles; provenance and locks are visible. **Governance findings appear on the canvas with their witnesses highlighted**; blocking findings are fixed or waived here.
4. **Evaluation** — the business case (comparison, ranges, sensitivity ranking, break-even views, adjustable assumptions) and the regulatory register and control matrix, with the open questions for the qualified reviewer and the sign-off workflow.
5. **Build planning and verification** — acceptance contracts per node, build plan and handover prompt, then optional in-app code generation producing a runnable prototype, a **conformance report** against the contracts, an embedded live preview and a tool-call log.

Cross-cutting: a language switch, a budget indicator (remaining model spend), version history with diff and restore per artifact, subagent live-activity previews inline in chat, a **readiness indicator** per flow, and an **assumptions table** showing every profile value with its provenance.

Cross-cutting requirements:

- **Output language is a process setting.** It is recorded with each artifact and passed to every agent, including code generation. Prompts are not translated; output language is steered by a runtime directive.
- **Chat history is server-authoritative and append-only.** The client never replaces stored history. Concurrent sessions on one process must not overwrite each other, and the history must be usable as an audit trail.
- **Uncertainty is visible.** No figure is rendered without its range; estimated inputs are visibly labelled wherever they influence a result.
- **Decisions are attributed.** Every answer, override, waiver and sign-off shows who made it and when.

### 5.1 Optional capabilities (scope open)

Whether these belong in the first release is an operator decision. If included, each must meet its requirements:

- **AI-maturity assessment.** A per-client structured assessment of organizational AI maturity across several dimensions, with a facilitator chat. Its model calls go through the same provider abstraction and agent layer as everything else.
- **Demo sessions.** If anonymous sessions are offered, session isolation is enforced consistently in every layer — database, workspaces, background work — not only at the HTTP edge.
- **Process archive import/export.** Export bundles versioned artifacts, chat, inbox, waivers and sign-offs. **Import is an untrusted entry point:**
  - imported artifacts pass the same schema validation and secret scanning as agent output
  - imported waivers and sign-offs are not trusted: imported flows return to *valid* and regulatory artifacts to *draft*
  - imported executable content is quarantined, never run without review and sandboxing
  - decompressed size, entry count and paths are bounded
  - malformed archives produce structured errors, never server errors
- **Live preview.** Runs generated code in the same sandbox as code generation (§6.7), with an allow-listed environment and no access outside its workspace. A smoke test against the built image confirms preview's runtime dependencies are present.

The frontend design system is an operator input (`IMPLEMENTATION-PROMPT.md`, *Inputs not in this package*).

---

## 6. Reference architecture

This section specifies mechanisms and the requirements on each. §6.1–§6.8 are the foundation; §6.9–§6.13 are the assessment capabilities that read from it. The stack is chosen in the design document.

### 6.1 Shape

Layers with one direction of dependency:

```
HTTP API (streaming chat protocol, job endpoints)
   → run harness (admission, cancellation, rollback, job tracking)
      → agent layer (router → subagents → flow-authoring, profiling and contract agents)
      → assessment services (deterministic: governance, typing, simulation, obligation mapping)
         → per-process workspace (versioned artifacts) + runtime state (unversioned, separate)
```

- **Chat path (recommended):** adopt an established, publicly specified streaming chat protocol, translated from agent-framework events by an adapter, consumed by an off-the-shelf chat UI. Owning no custom event taxonomy removes a whole class of protocol-drift bugs.
- **Lifecycle ownership:** with streaming responses, the agent typically runs *after* the endpoint function returns. Request-scoped setup and teardown therefore cannot bracket a run. **Admission, cancellation, disconnect handling and rollback must be owned by the run harness**, never by the HTTP request.
- **Admission on every entry point.** Chat-initiated work goes through the same run harness as background work.
- **Background path:** long, non-interactive work (code generation, conformance testing, simulation) runs out of band as tracked jobs; the UI follows progress. Chat tools that start such work return immediately.
- **Models propose, code checks.** Governance evaluation, dataflow typing, simulation and obligation mapping are deterministic code, not model calls. Models produce profile estimates, contracts, fixes and narratives.

### 6.2 The skill router pattern

The top-level agent is a **skill router, not a workflow engine**:

- Its instructions list registered skills (auto-discovered documents).
- A skill-loading tool injects the full playbook on demand; the router then executes it.
- The router has **no file-writing tools** — only workspace reads, inbox access, skill loading, subagent dispatch, and job dispatch (code generation, simulation, conformance). A skill cannot grant powers the router doesn't have.
- Off-topic requests are refused by default; clarifying questions go through the inbox tool, never chat text. A bounded general-Q&A mode should be designed in from the start.

Why it works: refusals are bounded, routing is data, and dangerous capabilities (file writes, graph mutation) live in narrowly scoped subagents.

**Requirements for the extension contract:**

- **Everything an extension needs is declared as data:** subagent kinds, prompts, output artifacts and their locations, inbox categories, writable-file permissions, rule packs, profile defaults sets, and obligation catalogs. One registry; no enumeration duplicated in code.
- **Write scope is enforced in code:** each agent's write tools are bound to the artifacts its declaration allows.
- **Every dispatch goes through the run harness**, including subagents dispatched from chat.

### 6.3 The flow-authoring reliability contract (core value)

The flow-authoring agents are where "every time, without manual correction" is won or lost.

1. **Graph mutation tools, not whole-file writes.** Add, edit and delete tools for nodes and edges, an exact-string patch tool as an escape hatch, and an optional model-facing pre-check (which may include a governance pre-check, §6.9). Whole-file generation risks silent truncation and gives no incremental feedback.
   - Each mutating tool validates its own mutation and, on failure, returns a **structured, correctable error**.
   - **The error contract must count corrective attempts.** Whether failures are returned as results or raised as framework retry signals, they consume a bounded per-tool (or per-error-class) budget — not only the run's overall request cap. Design and test this against the actual semantics of the chosen agent framework.
   - **Common model mistakes are correctable, not fatal.** A non-unique search string, a reference to a missing node, or a read failure is returned as a correctable error. Which failures abort a run is stated explicitly in the error contract (§12.1 layer 3).
2. **In-memory flow cache + locks.** Cache the parsed flow per run; serialize read-modify-write per (process, artifact), because models may dispatch several tool calls concurrently. Pin with a concurrency test.
3. **The gate belongs to the harness.** After every run, the harness — not the model — re-reads the stored artifact (bypassing the cache), runs full validation, and checks every flow invariant:
   - **Coverage:** every current-state node is covered by exactly one redesigned node; no unknown references; no duplicates. A missing or invalid current-state flow **blocks** the redesign run.
   - **Container arity:** orchestrator and actor nodes have ≥ 2 children, using one precise definition of "child" shared by the check and its error message.
   - **Non-degeneracy:** an empty or implausibly small flow fails. Define the threshold per mode.

   A model-facing finalize tool may run the same checks early to save round-trips, but it is never the gate. The gate decides **validity** (§4.4); governance decides **readiness**.
4. **Run isolation and verified rollback.** A run either commits a valid result or leaves the workspace exactly at its base state.
   - **Runs write in isolation** — a per-run working copy, transactional staging, or equivalent — so no other commit can capture partial writes.
   - **Commits contain only the run's own paths.**
   - **Every exit path ends in commit or rollback**, including runtime-level task cancellation, which in many runtimes bypasses generic error handling and must be handled explicitly.
   - **Rollback verifies itself** by comparing the result with the base state, and reports failure loudly.
   - **Rollback never touches unversioned runtime state** (§6.5).
5. **Two modes.** A *from-scratch* mode starts with an empty flow and ignores locks by explicit intent; an *incremental* mode starts from the current flow and the user's locks and fails with rollback if any locked item changes.
   - **Locks fail closed.** If the user-edit layer cannot be read, incremental runs are blocked and the problem is surfaced.
   - **Lockability is defined once** and shared by the UI, the merge and the post-run check.
6. **Budgets.** A per-run request cap, output limits sized for writing agents, prompt caching where the provider supports it, and model settings computed **per model**.

**The error-channel topology — the most transferable principle.** One closed corrective loop, with every link enforced by code:

**self-validating tools → structured error to the model → bounded corrective retries → a gate the model cannot skip → verified rollback.**

The first two links make a wrong tool call cost one round-trip instead of the run. The last three guarantee that whatever the model does, the outcome is a valid commit or an untouched workspace. Governance findings, contract errors and profile inconsistencies reuse the same channel (§6.9–§6.12).

**The long-session loop (recommended shape).** Run the agent step by step: after each completed step, publish a live-message snapshot and check the harness-owned cancel signal.

### 6.4 The user-edit layer (regeneration safety)

User edits never touch agent-authored artifacts. They live in a separate layer holding positions, added, modified and removed nodes and edges, profile overrides, contract edits, locked ids, and layout direction. The rendered flow is a merge of the two layers.

Requirements:

- **The merge is pure and deterministic**, and provenance is computed by it.
- **User-created items live in their own id namespace**, so regeneration cannot collide with them.
- **Patches are anchored** to the content they were made against (for example a content hash or base revision). If the target changed or vanished in a regeneration, the patch is reported as **orphaned** — never applied to different content, never silently dropped. This applies equally to profile overrides and contract edits.
- **The merge never raises.** It returns a valid flow plus a structured conflict report. Rendering always succeeds.
- **Corruption fails closed for constraints:** an unreadable user-edit layer is surfaced to the user and blocks lock-sensitive runs.
- **Corrupt agent artifacts are surfaced**, never silently regenerated.

### 6.5 Workspace versioning

- **History is immutable**; restore writes old content back as a new revision.
- **One revision per run**, containing only that run's writes, with the run id, the authoring agent or human, and a systematic message.
- **Waivers, sign-offs and inbox Q&A are versioned** with the artifacts they apply to.
- **Versioned artifacts and unversioned runtime state live in physically separate locations.** Runtime state (preview credentials, package caches, virtual environments, tool state, simulation scratch data) is never captured by a commit and never deleted by a rollback.
- **Locking works across processes and workers.**
- **The data root is durable storage.**

**Recommended:** a version-control repository per workspace — history, diff and restore come free. An object store plus revisions table is a valid alternative where scale demands it (§11.9). Whichever is chosen, staging is by explicit path, never "everything".

### 6.6 The inbox (human-in-the-loop as a primitive)

Inbox messages have a kind (agent question, user answer, user steering), selectable answer options, a category per phase, a status lifecycle (open → answered → acknowledged), and threading by parent message. All agents share one inbox tool; agents never ask questions in chat text.

The inbox carries four kinds of human input the assessment capabilities depend on:

- clarifying questions about the process
- **high-impact assumptions**, ranked by sensitivity (§6.11)
- **applicability questions** for obligation catalogs (§6.13)
- **conformance failures** that need a human decision (§6.12)

Requirements:

- **Agents see the full message, including its category.**
- **Inbox content is versioned with the artifacts it shaped.**
- **No UI action deletes design inputs as a side effect.**
- **Every answer records the answering human.** Answers to applicability questions are never generated by an agent.

### 6.7 Code generation and execution

Code generation may use an external coding-agent tool run as a subprocess, or a pluggable executor (operator decision). Conformance testing executes contract tests against the generated code. Requirements:

- **Sandbox (established practice):** every process executing generated or imported code — code generation, conformance testing, preview — runs in container-grade isolation under its own OS identity, with no access to other workspaces, the database, or the server's environment, and with resource limits and a network policy (§13.2.3).
- **Allow-listed environment:** built from an explicit allow-list, never inherited and filtered by a deny-list. Credentials are passed in exactly one authentication style.
- **Isolation is decomposed** into separately controlled and separately tested parts — state directory, tool integrations, filesystem scope, environment, OS identity — and verified against the real model endpoint.
- **Per-run tool state:** the tool's home and configuration directories are pinned per workspace.
- **Write scope is enforced:** writes outside the generated-code location are rejected or discarded, never committed.
- **Large stream lines:** size the line limit generously and fail loudly beyond it; never truncate.
- **Session resume with fallback:** on a resume that fails without any assistant output, retry once with a fresh session.
- **Layout normalization** before any consumer reads generated code.
- **Every failure path updates the run record**, including storage errors during commit.
- **Cancellation targets the exact run.**
- **Secret gate:** see §11.11.

### 6.8 Provider abstraction

- **Construct providers explicitly with credentials.** Never let a framework infer a provider from a bare model name.
- **Per-request credential override**, passed fresh into each provider construction; remaining budget read from provider or gateway responses and cached briefly.
- **Per-role model settings as configuration**, computed per model.
- **No feature bypasses the abstraction.**

### 6.9 Governance engine (policy as code)

Governance review of a redesign must be repeatable, not done by eye. A deterministic engine evaluates the active rule pack (§4.5) against the validated flow and its profile.

**Rule families (recommended initial pack, proposed in the design document and approved by the operator):**

- **Structural:** every node reachable from a start; every path terminates; no deadlocks between parallel branches; rework loops bounded.
- **Oversight:** an actor that decides about natural persons has a human checkpoint before any irreversible or external effect.
- **Escalation:** every AI node has a confidence threshold and an escalation target.
- **Autonomy ceilings:** autonomy level is at or below the ceiling defined per decision class.
- **Data flow:** sensitive data categories reach only nodes allowed to process them; nothing leaves the organization unflagged.
- **Dataflow typing:** a node's output schema satisfies its successors' input schemas (fed by §6.12).
- **Compiled obligations:** rules generated from mechanizable catalog obligations (§6.13).

Requirements:

- **Evaluation is deterministic code**, never a model judgment. Same flow, profile and rule pack → same findings.
- **Every finding has a witness** highlighted on the canvas.
- **Evaluation runs automatically on every committed revision**, and the report is versioned with the revision it describes.
- **During redesign runs**, the redesigner may call a governance pre-check; findings reach it through the structured error channel, counted against the corrective-attempt budget. Blocking findings never prevent a valid flow from committing; they prevent readiness.
- **Findings that need human judgment go to the inbox** in the design category.
- **Few blocking rules, each justified.** Every blocking rule carries a rationale in the pack. The waiver rate per rule is tracked; a rule waived on most flows is reviewed, not tolerated.
- **Waivers require the waiver role** (§11.1), a named human and a written justification.

### 6.10 Node profiling and assumptions

A profiling agent proposes profile values (§4.3) for both flows from the specification.

- **Proposed values are *estimated*,** each with a recorded reason; values found verbatim in the inputs are *stated*, with a citation.
- **Missing or uncertain high-impact values become inbox questions**, ranked by their influence on the business case (§6.11 sensitivity). The user's answer upgrades the value to *stated*.
- **Defaults come from declared defaults sets** (data), never from model memory, and are labelled *default*.
- **Provenance propagates.** Every derived figure knows which inputs, and which provenance classes, it depends on.
- **Profile consistency errors** (probabilities not summing to 1, ranges inverted, required fields missing for simulation) reach the profiling agent through the structured error channel.

### 6.11 Simulation and business case

A stochastic simulation runs the current-state and redesigned flows using their profiles and reports the comparison as distributions.

**Outputs:**

- throughput time, cost per case, human effort per period, and escalation rate — each as a range
- **error propagation:** where AI errors are caught, where they escape, and what escapes cost
- **sensitivity analysis:** which assumptions drive the result, ranked; the top-ranked *estimated* assumptions become inbox questions
- **break-even views:** for example, the AI error rate at which the redesign stops outperforming the current state

**The business-case artifact** contains the comparison, the full assumptions table with provenance, the sensitivity ranking, explicit limitations, and a narrative. Every figure traces to the assumptions that produced it.

Requirements:

- **Deterministic code, reproducible:** the same flows, profiles and seed produce the same result.
- **Conservation:** every simulated case ends in a terminal state or is reported as stuck; unbounded rework loops are reported, never silently truncated.
- **No figure without its range and assumption trace.** No single headline number.
- **The narrative is model-written but bounded:** it may only state what the computed results support, and is evaluated for faithfulness (§12.5G).
- **Runs as a tracked background job** under the same admission, cancellation and rollback rules as every other run.
- **Recommended method:** Monte Carlo over a discrete-event model of each flow. Resource contention and queueing are an operator decision, because they need staffing data.

### 6.12 Acceptance contracts and conformance

A contract agent drafts an acceptance contract (§4.6) for each AI and deterministic node of the redesign.

**Requirements on contracts:**

- **Every test case traces** to a specification passage or is marked as an assumption.
- **Every contract has at least one negative case** and states below-threshold behavior.
- **Dataflow typing:** schemas are checked across every edge by deterministic code; mismatches become governance findings with the edge as witness.
- **Contract errors** (unparseable schemas, untraced cases, missing negatives) reach the contract agent through the structured error channel.
- **Contracts ship in the handover package**, so the build plan and handover prompt refer to them.

**Conformance:**

- After code generation, the contract tests run against the generated prototype **in the sandbox** (§6.7).
- The **conformance report** gives, per node, passed, failed, or not testable, with reasons.
- Failures become corrective feedback to the code-generation step (bounded like every corrective loop) or inbox items when a human decision is needed.
- The report is versioned with the code revision and contract revision it tested.

### 6.13 Regulatory mapping (draft for qualified review)

Regulatory mapping maps a redesign to the obligations it triggers, using operator-supplied catalogs (§4.7).

**Flow:**

1. **Classification through the inbox:** the system asks the applicability questions each catalog defines; humans answer them.
2. **Mapping:** node profiles and classification answers map to obligations; obligations map to controls in the design (checkpoints, logging, escalation, documentation).
3. **Compilation:** mechanizable obligations compile into governance rules (§6.9), so the redesigner can fix gaps directly.
4. **Artifacts:** a **risk register** (triggered obligations, triggering nodes, existing controls, gaps), a **control matrix** (obligation → control → node → evidence), and an **open-questions list** for the reviewer.

Requirements:

- **Every artifact is labelled a draft for qualified review** and states its coverage: catalog, version, effective date, obligations evaluated.
- **Export requires sign-off** by a named human holding the reviewer role (§11.1); the sign-off is versioned and reset by any upstream change.
- **No verdict vocabulary.** Outputs report triggered obligations, controls and gaps; they never state compliance.
- **Catalog changes trigger re-assessment notices** for every flow assessed against the previous version.
- **The mapping itself is deterministic** given catalog, profile and answers; models may draft explanatory text, which is bounded like the business-case narrative.
## 7. Recommended design defaults (with rationale)

Strong defaults. Each may be replaced by a better approach that meets the same requirement, with stated reasons.

**Foundation**

1. **Skill router over workflow engine** — extensibility is the business model; routing is data; capabilities are bounded per agent (§6.2).
2. **Self-validating graph mutation tools over whole-file writes** — errors become corrective feedback, not dead ends (§6.3 item 1).
3. **A harness-owned gate that re-reads storage** — the correctness check must not share the cache's tolerance, and must not depend on the model calling it (§6.3 item 3).
4. **A two-level flow schema** — authored versus resolved; derived data is never authored (§4.2).
5. **A separate user-edit layer with anchored patches** — regeneration never overwrites or misapplies human edits, including profile overrides and contract edits (§6.4).
6. **Versioned workspaces with one revision per run** (§6.5).
7. **Run isolation with verified rollback** — the enforcement mechanism of the north star (§6.3 item 4).
8. **An established streaming chat protocol**, with lifecycle owned by the run harness (§6.1).
9. **Harness-owned cancellation checked between agent steps.**
10. **Workspace snapshot pre-fetched into subagent prompts** — saves model turns for parallel section agents.
11. **Inbox as a first-class, versioned primitive** (§6.6).
12. **One secret gate on every exit** (§11.11).
13. **Frontend API types generated from the backend's API schema** — never hand-written; no untyped escape hatches.
14. **Property-based tests pinning every validator, rule and invariant.**
15. **Tolerant reads, fail-closed constraints, absolute gate.**

**Assessment**

16. **Models propose, code checks** (§6.1).
17. **Policy as code with witnesses** — findings are reproducible and point at the exact path that violates them (§6.9).
18. **Rules, catalogs and defaults as versioned data** — extension without code, and every assessment records the versions it used.
19. **Readiness separate from validity** — save and discuss imperfect flows; hand over only defensible ones (§4.4).
20. **Distributions, not point estimates** — Monte Carlo with sensitivity analysis is established practice for decisions under uncertainty (§6.11).
21. **Sensitivity-ranked elicitation** — ask humans about the assumptions that matter most, not about everything (§6.10).
22. **Contracts traced to the specification, verified in a sandbox** — the handover becomes testable and the generated code is checked against it (§6.12).
23. **Drafts with named sign-off for anything resembling professional advice** (§6.13).

---

## 8. Anti-patterns to avoid

**Foundation**

1. **Model policy by cost intuition.** Assigning cheaper models to "simple" subagents without measurement routinely degrades extraction and synthesis quality. Per-role model policy is an eval-driven decision (§12.5D).
2. **One big isolation switch.** A single all-in-one flag tends to break authentication paths that private gateways rely on, or to isolate less than it claims. Decompose isolation (§6.7).
3. **Inherited subprocess environments.** Deny-lists fix the incident that prompted them and nothing else. Use allow-lists.
4. **Process-local run state.** Keep run state behind small modules backed by a durable store, and route *every* entry point through them.
5. **Busy-waiting inside a chat tool.** Chat tools start work and return; the UI owns progress.
6. **Long-held database connections during agent runs.** Use a database that supports concurrent writers with pooling.
7. **Hand-maintained architecture docs.** Generate architecture docs or test them; keep security-relevant documentation next to, and reviewed with, the code it describes.
8. **Toolchain version drift.** Pin one version of everything; date-stamp every warning suppression with a removal date.
9. **Hard refusal with no escape valve.** Design a bounded general-Q&A mode from the start.
10. **Duplicated helpers after refactors.** Lint for duplication.
11. **Tests that replace what they claim to test.** Contract tests drive the real framework with a scripted model and the real storage layer and rule engine. A test that pins a defect as intended must be reversed explicitly.
12. **Fixing at the exit instead of the cause.** Ask what put sensitive data somewhere and fix that.

**Assessment**

13. **A single headline number.** Point estimates presented as forecasts invite false confidence and collapse at the first challenged assumption. Show ranges, sensitivity and break-even.
14. **Estimate laundering.** An agent's estimate that silently becomes an input "fact" — through a default, a copy, or a lost label — corrupts every downstream figure. Provenance must propagate through every computation.
15. **Model-judged governance.** Asking a model whether a flow "has adequate oversight" is unrepeatable and unauditable. Encode the rule; let the model fix what the rule finds.
16. **Blocking-rule inflation.** Many blocking rules train users to waive everything. Keep few, justify each, track waiver rates.
17. **Tests that restate the specification.** Contract cases that only paraphrase the spec verify nothing. Require negative and edge cases, and evaluate meaningfulness.
18. **Agent-authored regulation.** A model that writes or edits obligation catalogs, or decides applicability, produces confident fiction. Catalogs and applicability answers come from people.
19. **Verdict vocabulary.** "Compliant", "safe", "will save" — any phrasing that converts findings into assurance. State coverage and gaps instead.
20. **Stale readiness.** A "ready" status that survives edits to the flow, profile, contracts or rule pack is worse than none. Bind readiness to revisions.
21. **Unmeasured product claims.** Marketing-style statements about time saved or share of work automated, made without measured data, are false assurance about the product itself (§1, Positioning).

---

## 9. Hazards of the pattern

Properties of LLM agents that write artifacts, and of systems that quantify and assess them. Design for each from day one, and **pin each with a test**.

| # | Hazard | Consequence | Mitigation | Test |
|---|---|---|---|---|
| | **Model interaction** | | | |
| 1 | Stream line-limit exhaustion | Agent tools emit single event lines far above common reader defaults → buffer overflow mid-run | Generous line limit; fail loudly past it, never truncate | Oversized fake line |
| 2 | Output truncation misdiagnosed as malformed arguments | Large tool-call arguments cut off; the framework retries pointlessly | Output limits sized for writing agents; tools that take small edits | Scripted truncated output |
| 3 | Encoded entities in model text | Entities rendered verbatim | Idempotent decoding on every text field, every write path (§4.2) | Property: idempotent, applied everywhere |
| 4 | Provider inferred from a bare model name | Credential-less provider → misleading authentication failure | Explicit provider construction (§6.8) | Construction test per provider |
| 5 | Degenerate output accepted | A model stopping early leaves an empty or partial, schema-valid flow that replaces a good one | Non-degeneracy in the harness gate (§6.3 item 3) | Scripted early stop |
| | **Tools and error channel** | | | |
| 6 | Unbounded corrective loops | Error results not counted; a model repeats a failing call until the run cap | Counted, bounded corrective attempts (§6.3 item 1) | Scripted repeated failure |
| 7 | Common mistakes treated as fatal | A tool raises on a predictable model error; the run aborts and rolls back unrelated work | Correctable errors; explicit abort list | Scripted tool exception |
| 8 | Optional gate | The model never calls the final check; invariants go unenforced | Harness-owned gate | Scripted run that skips finalize |
| | **Concurrency and lifecycle** | | | |
| 9 | Concurrent tool calls race on read-modify-write | Corrupted artifact | Per-(process, artifact) locks | Concurrent dispatch test |
| 10 | Shared working tree | A commit mid-run captures another run's partial writes | Run isolation; explicit-path commits (§6.3 item 4) | Commit landing mid-run |
| 11 | Request-scoped lifecycle code | With streaming, teardown runs before the agent starts; cancel and disconnect are lost | Harness-owned lifecycle (§6.1) | Disconnect mid-run |
| 12 | Runtime-level cancellation bypasses error handling | Rollback skipped; activity stays "running" | Explicit handling of cancellation on every path | Cancel the task directly |
| 13 | Stranded "running" records | Zombie runs block new ones | Every failure path updates status; startup recovery sweep | Commit failure; kill and restart |
| 14 | Rollback fails silently | Workspace in an unknown state, reported as rolled back | Verified rollback | Inject reset failure |
| | **User layer** | | | |
| 15 | Identity collision after regeneration | User items dropped; patches, profile overrides or contract edits grafted onto unrelated content | Id namespaces, anchored patches, non-raising merge (§6.4) | Property suite with colliding ids |
| 16 | Constraints fail open on corrupt input | Corrupt user layer → zero locks enforced | Fail closed (§6.3 item 5) | Corrupt the user layer |
| | **Storage, subprocesses, secrets** | | | |
| 17 | Runtime state in the versioned tree | Credentials and caches committed, or deleted by rollback | Physical separation (§6.5) | Plant runtime files; commit and roll back |
| 18 | Shared agent-tool state directories | Interactive sessions on the host bleed into server-spawned runs | Per-workspace tool state | Environment-construction test |
| 19 | Stale session resume loops | Resuming a broken session fails forever | Fresh retry on zero-output resume failure | Scripted failing resume |
| 20 | Generated layout drift | Nested scaffolding breaks every downstream consumer | Normalize before any read | Pure-function test |
| 21 | Secrets leave through an overlooked exit | Credentials in run events, chat, history, reports, or files outside the scanned location | One gate on every exit (§11.11) | Leak drill per exit |
| 22 | Untrusted import treated as trusted | Unvalidated artifacts, forged waivers or sign-offs, secrets and executable code enter through an archive | Import gating (§5.1) | Hostile archive suite |
| | **Assessment** | | | |
| 23 | Fabricated precision | Point figures read as forecasts; decisions made on false certainty | Ranges everywhere; no headline number; break-even views (§6.11) | No figure renders without range and trace |
| 24 | Estimate laundering | Estimated values used or shown as stated facts | Provenance on every value, propagated through computation (§4.3, §6.10) | Property: every output knows its input provenance |
| 25 | Simulation non-conservation | Cases vanish in unbounded loops; results silently biased | Conservation invariant; stuck cases reported (§6.11) | Flows with loops and dead ends |
| 26 | Waiver fatigue | Noisy blocking rules get waived by habit; governance becomes theater | Few justified blocking rules; waiver-rate tracking (§6.9) | Eval dimension: waiver-free readiness rate |
| 27 | Tautological contracts | Test cases restate the spec; broken components pass | Negative cases required; meaningfulness judged; mutation check (§6.12) | Deliberately broken component fails its contract |
| 28 | Silent applicability inference | A model decides a regulation does or doesn't apply | Applicability answered only by humans through the inbox (§4.7) | No mapping proceeds on unanswered applicability |
| 29 | Stale catalogs | Assessments silently based on superseded obligations | Effective dates; catalog version per assessment; re-assessment notices (§6.13) | Catalog version change flags affected flows |
| 30 | False assurance | Draft output read as a verdict or legal advice | Draft labelling, coverage statements, named sign-off, no verdict vocabulary (§6.13) | Export blocked without sign-off; outputs carry coverage |
| 31 | Stale readiness | A flow edited after assessment keeps its "ready" status | Readiness bound to revision (§4.4) | Edit after readiness resets it |
| | **Test hygiene** | | | |
| 32 | Leaked event loops or resources in tests | Warnings attributed to unrelated tests; real failures masked | Explicit resource hygiene; dated, targeted suppressions only | CI with warnings as errors |

---

## 10. The invariants — the reliability and assurance contract

Each row is a **requirement**. The enforcement column states what the mechanism must achieve; how is a design decision.

| # | Invariant | Enforcement requirements | Verify with |
|---|---|---|---|
| | **Foundation** | | |
| 1 | A flow-authoring run ends in **exactly one** of two states: a **valid** flow committed (every flow invariant holds, non-degenerate, checked by code), or the workspace verifiably restored to the run's base state. Never a half-written flow — on any exit path. | Harness-owned gate; run isolation; explicit handling of cancellation; verified rollback (§6.3) | Scripted-model test per exit path; base-state comparison; soak test killing the server mid-run |
| 2 | User edits — including profile overrides and contract edits — live in a **separate layer**, merged deterministically, and **no agent run can lose, misapply, or overwrite a user edit**, including across regeneration | Id namespaces; anchored patches; orphan reporting; non-raising merge; no agent or subprocess write path to the user layer (§6.4, §6.7) | Property tests with colliding ids; orphaned edits surfaced to the user |
| 3 | Locked items survive incremental runs, or the run fails and rolls back; from-scratch runs may ignore locks by explicit intent | Post-run lock check; locks fail closed; lockability defined once (§6.3 item 5) | Scripted locked-item change; corrupt user layer blocks the run |
| 4 | Redesigned flows **cover every current-state node exactly once** — no unknown references, no duplicates | Harness gate; a missing or invalid current-state flow blocks the run (§6.3 item 3) | Unit tests; property test with generated current-state flows |
| 5 | Container archetypes (orchestrator, actor) have **≥ 2 children** | Harness gate with one shared definition of "child" | Unit and property tests |
| 6 | **One active run per process per kind**, across every entry point | Durable admission covering chat and chat-dispatched work (§6.1, §6.2, §11.2) | Concurrency test from every entry point |
| 7 | Every artifact write lands in version history; one revision per run containing **only that run's writes**; history immutable (restores are new revisions) | Explicit-path commits; run id and author recorded; cross-process locking (§6.5) | One run → exactly one revision with only its paths, message and run id |
| 8 | Secrets never leave the backend — not through logs, generated code, run events, chat history, version history, reports, or exports | One gate matching known values, credential patterns and file-name classes on every exit (§11.11) | Leak drill per exit |
| 9 | Subprocesses (code generation, conformance testing, preview, anything executing generated or imported code) cannot touch anything outside their per-process scope | Container-grade sandbox; separate OS identity; allow-listed environment; enforced write scope (§6.7) | Escape tests: read another workspace, read the database, write the specification, read the server environment |
| 10 | Corrupt input artifacts degrade gracefully on read **without dropping constraints**; the correctness gate is absolute on write | Per-artifact tolerance decisions; fail-closed constraints; harness gate | Corrupt-each-artifact injection |
| | **Assurance** | | |
| 11 | A flow is **ready for handover** only while no blocking finding remains unwaived, every AI and deterministic node has a contract, and dataflow schemas type-check across every edge — **for its current revision**. Every finding has a witness; every waiver names a human and a justification and is versioned with the flow | Deterministic rule engine on every committed revision; readiness bound to revision; waiver role (§4.4, §4.5, §6.9) | Property tests per rule on generated graphs; edit-after-readiness resets it; waiver without role rejected |
| 12 | **Every figure is traceable and honest:** every profile value carries provenance; every derived figure shows its range and the assumptions behind it; simulations are reproducible (same inputs and seed, same result) and conserve cases | Provenance propagation; seeded deterministic simulation; rendering refuses figures without range and trace (§4.3, §6.10, §6.11) | Analytic test flows; reproducibility and conservation properties; provenance-propagation property |
| 13 | **Every acceptance contract is grounded:** each test case traces to the specification or is marked as an assumption; each contract has a negative case; contracts are tied to node identity and orphaned, never silently kept, when their node changes | Contract validation in the structured error channel; identity anchoring (§4.6, §6.12) | Contract-completeness checks; regeneration orphans contracts; mutation check |
| 14 | **No regulatory output claims more than it covers:** every register and matrix is a draft stating its catalog, version and evaluated obligations; applicability comes only from human answers; export requires a named reviewer's sign-off for the current revision; no output uses verdict vocabulary | Draft lifecycle; reviewer role; human-only applicability; catalog version recorded; export gate (§4.7, §6.13) | Export blocked without sign-off; upstream change resets sign-off; unanswered applicability blocks mapping; output scan for verdict phrasing |

**Further requirements** (not invariants, each with a reason above):

- Run admission, cancellation and rollback are owned by the run harness (§6.1).
- Versioned artifacts and unversioned runtime state live in separate locations; commits stage explicit paths (§6.5).
- Inbox Q&A, waivers and sign-offs are versioned with the artifacts they shaped; no UI action deletes design inputs as a side effect (§6.5, §6.6).
- Chat history is server-authoritative and append-only (§5).
- Output language is a process setting, passed to every agent including code generation (§5).
- Archive import is an untrusted entry point, gated like agent output; imported waivers and sign-offs are not trusted (§5.1).
- Obligation catalogs are authored only by people (§4.7).
- Every human decision is attributed to a named person (§1, Positioning).

---

## 11. Required production capabilities

In priority order:

1. **Identity, authentication, authorization, tenancy.** Real authentication; roles restricting use to users the operator authorizes, including a **waiver role** for governance findings and a **reviewer role** for regulatory sign-off; per-client data segregation; audit trails and authorship keyed to humans. The access model is an operator decision.
2. **Durable orchestration.** A durable task queue and run state machine: restart-safe, multi-worker, with live previews over pub/sub or a stream. Every entry point — including chat — goes through admission; cancellation, disconnect and kill all end in verified rollback. Simulation, conformance and code generation run as tracked jobs under the same rules.
3. **A database supporting concurrent writers, with connection pooling.**
4. **Evaluation harness.** Golden corpora → expected properties, measured flow-authoring reliability, measured quality of every assessment capability, retry statistics fed back into evaluation, and per-role model policy chosen by eval. Shape: §12.5D–H.
5. **Sandboxing for code generation, conformance testing, preview and imported code — launch-blocking** (§13.2.3). Each runs in an isolated sandbox with:
   - an allow-listed environment
   - read-only access to artifacts
   - no access to other workspaces or the database
   - resource limits and a network policy
6. **Model policy as configuration.** Per-role model selection, fallback chains and budget controls as deploy-time configuration; settings computed per model; per-run cost shown alongside the remaining-budget indicator.
7. **Schema versioning and migration.** Flows, profiles, contracts, rule packs and catalogs are long-lived; every schema change ships with a tested migration (§13.2.10).
8. **Observability.** Structured run events, tracing across the router → subagent → flow-authoring → assessment call tree, per-run cost rollups, prompt/skill versioning, rule-pack and catalog versioning, and governance metrics (finding and waiver rates per rule). Run events are scrubbed before storage.
9. **Scale.** A stateless streaming layer for horizontal scale; a shared-storage design for workspaces; history garbage collection; cross-process locking; simulation jobs parallelizable across workers.
10. **Hygiene.** One pinned version of every tool and image; generated or tested architecture docs; duplication linting; dated warning suppressions.
11. **Exit gating everywhere.** One secret gate applied to logs, run events, chat history, version history, generated code, reports and exports. It matches configured and per-request credential values, credential patterns, and classes of credential and environment file names.
12. **Integrity of human inputs.** Versioned inbox, waivers and sign-offs; attributed decisions; server-authoritative append-only chat history; process-level output language.
13. **Catalog and rule-pack management.** Operator-facing workflows to add, version, review and retire rule packs and obligation catalogs; effective-date handling; re-assessment notices when a version changes.
## 12. Contracts, definitions of done, and the test strategy

Agent systems typically end up strong at the unit layer and weak above it: behavioral contracts, orchestration integration, failure injection and evals. Decision-support systems add a second weakness: unmeasured output quality. This section specifies where the investment goes.

### 12.1 The contract stack

Five contract layers, all required:

1. **Artifact schema contracts** — flows, profiles, governance reports, business cases, acceptance contracts, registers, plans, rule packs and catalogs — validated at every boundary: every write path, every import, every export.
2. **Behavioral contracts for agents** — the §10 invariants as *executable checks*: given a scripted model that misbehaves in way X, the system ends in state Y, on every exit path. One test per hazard per exit path, driving the real framework.
3. **Interface contracts** — generated API types; tool signatures (the tool schema *is* the contract the model sees; test it that way); the streaming protocol boundary; **the error contract** (which tool failures and findings become corrective feedback, how attempts are counted and bounded, which failures abort — §6.3, §6.9).
4. **Skill/prompt contracts** — validated skill metadata *and* an effect-level gate: a prompt change is a release, and the eval harness gates it (§12.5D–H). This is the largest surface to contract.
5. **Operational contracts** — SLOs as enforced agreements: run success rate, rollback correctness, cost ceiling per run, latency budgets, simulation job duration — measured, alerted, and enforced.

### 12.2 Definitions of done

- **DoD-code (per change):** unit and contract tests green; translation parity; docs generated or updated; lint- and type-clean. Automated in commit hooks and CI: linter and formatter, strict type checking, frontend type checks and linting, secret scanning, spell checking, translation parity, and tests with warnings as errors and a coverage ratchet.
- **DoD-agent (per agent or prompt change):** the eval suite shows no regression on every affected capability (§12.5D–H); retry-statistics diff reviewed; prompt version bumped.
- **DoD-rules (per rule-pack or catalog change):** property tests and the planted-violation corpus pass; for catalogs, the expert-labelled scenario set shows no loss of recall; affected flows receive re-assessment notices.
- **DoD-release (per deploy):** full suite + property tests + contract tests + smoke test against the built image (all runtime dependencies present, every capability starts) + golden-corpus eval + schema-migration test.
- **DoD-product ("Agentify works"):** defined over a **corpus, not an example**. For each golden spec:
  - the run ends valid-or-verifiably-rolled-back
  - the flow is non-degenerate; coverage is complete; container arity holds; node count is within sanity bounds
  - planted governance violations are found with correct witnesses; clean specs reach readiness without waivers
  - every figure in the business case carries a range and a trace
  - contracts are complete, typed, and fail on deliberately broken components
  - sampled human review rates the flow and its assessments acceptable

  *The single most important DoD; everything else is plumbing.*

### 12.3 The test strategy — an adapted pyramid

| Layer | Purpose | Requirement |
|---|---|---|
| **Unit** | Tools, services, scrubbing, environment construction, layout normalization | One test per §9 hazard at minimum |
| **Property/contract** | Schema invariants; user-edit merge; normalization; rules; simulation; provenance | **High**: every graph invariant and every rule pinned on generated graphs; merge properties with colliding ids; simulation reproducibility and conservation; provenance propagation |
| **Analytic** | Simulation correctness | Flows whose results are computable by hand: linear, branching, rework loop |
| **Orchestration integration** | Agent plumbing with scripted models driving the real framework — no network, deterministic | **Highest**: router dispatch, parallel sections, section-failure isolation, cancel mid-run, concurrent dispatch, the chat path, governance feedback into redesign, contract feedback into code generation |
| **API smoke** | HTTP surface, database, workspace seeding | Redesign flow, version restore, waiver and sign-off workflows, hostile archive import |
| **End-to-end (real model)** | The true pipeline against a real model endpoint | Small nightly canary: one golden spec through intake → specification → flows → assessment → contracts; assert artifact contracts only (never exact content) |
| **Evals** | The *product* gate: is the output good? | **Critical**: golden corpus + rubric + model-as-judge + sampled human anchoring for every capability; block releases on regression (§12.5D–H) |
| **Failure injection / soak** | Invariants hold under hostility | Kill server mid-run; disconnect mid-run; user edits during a run; duplicate concurrent runs (including via chat); corrupt each artifact; fail the commit; plant credentials in every exit; model endpoint 401/403/5xx; edit after readiness; forged waivers and sign-offs in imports |
| **Frontend** | Core UX correctness | Canvas interactions (locks, provenance badges, orphaned-edit surfacing, witness highlighting), readiness indicator, assumptions table, decision attribution, sign-off flow; browser smoke test over the five phases; translation parity in CI |

### 12.4 Build order and release staging

**Foundation (before any assessment capability):**

1. Property tests for the user-edit merge, generating id collisions across regeneration (§12.5A).
2. Scripted-model orchestration integration tests driving the real framework (§12.5B).
3. Failure-injection set for invariants 1–10, covering every exit path (§12.5C).
4. Eval harness MVP for flow authoring (§12.5D).

**Assessment capabilities, in recommended order:**

5. **Node profile and governance engine** (§6.9, §6.10, §12.5E). Cheapest; reuses the validator and the error channel; introduces readiness.
6. **Acceptance contracts and conformance** (§6.12, §12.5F). Makes the handover testable; reuses code generation and the sandbox.
7. **Simulation and business case** (§6.11, §12.5G). Needs mature profiles.
8. **Regulatory mapping** (§6.13, §12.5H). Needs the rule engine, profiles, an operator-supplied catalog, an expert-labelled scenario set, and answers to §13.2.4.

**Then:** frontend interaction tests for every phase; nightly real-model canary.

Steps 6 and 7 are independent and may swap. **Recommended first release:** the foundation plus step 5, so every shipped flow is governed. Release staging is an operator decision.

### 12.5 Behavior contracts

Each block states the **contract the tests must pin** — that is the deliverable. Test code, module names, harness layout and libraries are design decisions.

**A. Property tests for the user-edit merge** (build order #1)

Pins, for any authored flow and any user-edit layer:

- **An empty user layer is the identity:** the merged flow equals the authored flow, every origin is agent-authored, no locks.
- **User-created nodes survive with user provenance**, unless the same layer removes them — *including when a regenerated agent flow contains an item that would collide without namespacing*.
- **A user patch — including a profile override or contract edit — applies only to the content it was made against.** If its target changed or vanished, it is reported as orphaned — never applied to different content, never silently dropped.
- **Removals cascade to touching edges.**
- **Locks never reference ghosts**, and never reference items the flow-authoring agents cannot honor, unless the design defines how incremental runs treat them.
- **The merge always returns a schema-valid flow plus a structured conflict report — it never raises.**
- **The merge is deterministic.**

Generators must produce **collisions between the user layer and a regenerated agent layer** — same ids, same endpoints, vanished parents — and cover edge overlays.

**B. Scripted-model orchestration integration tests** (build order #2)

Pins: *given a scripted model that behaves in a specific way, the run ends in the specified end-state, and the workspace and its history match that end-state exactly.*

Method:

- The **real** agent framework runs; only the model is scripted. A replacement object for the agent does not count (§8.11).
- The real storage layer and rule engine run; commit, rollback, scrub and evaluation are never stubbed.
- Every test asserts both the artifact state and the version history.
- The design exposes a seam for injecting a scripted model.

Cases, each a separate test:

- **Error channel:** a tool returns a structured error; the model corrects; the run ends valid. Attempt accounting matches the error contract.
- **Corrective attempts are bounded** by the error contract, not only the run's request cap.
- **Tool raises an exception:** behavior matches the error contract — never an unhandled crash.
- **Degenerate stop:** the model stops early in from-scratch mode → the run fails; the previous valid flow survives.
- **Gate not called:** the harness gate still enforces every invariant.
- **Governance feedback:** the redesigner's pre-check reports a blocking finding; the model fixes it; the committed flow is assessed without that finding. A model that ignores the finding still commits a valid flow, which is not ready.
- **Cancel between steps:** workspace equals base state, no new revision.
- **Disconnect and runtime cancellation:** same end-state as cancel.
- **Parallel sections:** all three artifacts land, each committed with only its own writes.
- **Section failure mid-run:** its siblings' completed artifacts are intact.
- **User edit during a run:** the user's change is committed alone; a later rollback preserves it.
- **Concurrent admission:** the second run of the same kind for the same process is rejected, from any entry point.
- **Request-limit exhaustion:** clean failure with rollback, not a hang.

**C. Failure-injection suite** (build order #3)

Pins: *each §10 invariant holds under hostility.* Shape: a case table — each case corrupts the world in one way, runs a leg with a scripted model, and asserts the end-state. A new invariant or attack is a new row, not new test code.

Cases:

- **Corrupt flow artifact** → reads don't crash; the agent rebuilds or fails cleanly; corruption is never served downstream.
- **Corrupt user layer** → rendering survives; locks are not silently dropped.
- **Locked item changed in an incremental run** → run fails, verified rollback.
- **Rollback failure** → surfaced loudly.
- **Commit failure** → run marked failed, workspace at base state, no record left "running".
- **Process kill mid-run** → on restart, never a partial flow; no record left "running".
- **Planted credential, once per exit:** generated code, files outside the generated-code location, logs, run events, chat history, version history, reports, archive export. Include known values, pattern-only credentials, and arbitrarily named credential and environment files.
- **Runtime state** → never committed, never deleted by rollback.
- **Sandbox escape** from code generation, conformance testing and preview → refused.
- **Hostile archive import** (invalid flow, embedded credential, decompression bomb, path traversal, malformed manifest, executable content, forged waivers and sign-offs) → rejected or quarantined; imported flows return to *valid*, regulatory artifacts to *draft*.
- **Edit after readiness** (flow, profile, contract, rule pack) → readiness resets.
- **Corrupt rule pack or catalog** → assessment fails loudly; readiness is never granted on a partial evaluation.
- **Property:** text normalization is idempotent and applied on every write path.

**D. Eval harness — flow authoring** (build order #4)

Pins: *a prompt, skill, or schema change cannot merge unless the golden corpus scores at least as well as the last human-reviewed baseline — programmatic dimensions exactly, judged dimensions within judge noise.*

**Scope.** Flow authoring (current-state and redesigned flows). The router's conversational leg needs transcript judging and is deferred (§13.2.14). Later capabilities add their dimensions in E–H.

**Corpus.** Each golden spec holds its input text, an optional golden current-state flow, and an expectations record: the kind of run, validity, coverage and container-arity expectations, node-count bounds, judged dimensions with weights, and — for later capabilities — planted governance violations, expected contract properties, and expected obligations. **Expectations are data, versioned with the corpus, not code.** Target ~10 specs spanning multilingual input, diagram-derived text, and fragmentary operating procedures; the operator supplies them. Start with a deliberately minimal golden case with a checkable outcome — for example, a four-step linear process with one step per execution type, expecting exactly four nodes.

**Rubric:**

- **Programmatic:** validity; coverage; container arity; node-count sanity (per-spec bounds, because a 40-node invoice flow is a failure while a 40-node onboarding flow may be fine).
- **Judged:** current-state faithfulness (every specified step present, in order, nothing invented); archetype correctness (task agent does one verifiable thing; orchestrator distributes but doesn't decide; actor decides with judgment and liability).

**Judge design (starting point):** one call per judged dimension; scores 0.0–1.0 in 0.1 steps with a one-line reason; a harsh scale (1.0 = ship unchanged, 0.5 = real fixes needed, 0.0 = absent or wrong).

**Prompt version:** a hash over everything that shapes output — agent code, prompts, skills, schemas. Over-broad on purpose. Every scorecard records it.

**Gate semantics:**

- A programmatic dimension that passed in the baseline and now fails is a regression.
- A programmatic dimension that failed and now passes is an improvement: reported, not failing, prompting a human-reviewed baseline update.
- A judged score below baseline − ε is a regression.
- Missing baseline entries warn, never fail.

**Harness self-tests:** corpus present and well-formed; canonical golden case undeletable; prompt version stable and change-sensitive; the gate fails on regressions, not on improvements; judged noise within ε tolerated.

**Human-anchoring loop:** ~20 human-scored anchors per judged dimension; a new judge must place ≥ 90% of scores within 0.2 of the human's before it gates anything; ~10% of judged runs re-reviewed, disagreements becoming anchors; baselines updated only after human review, never inside CI.

Not pinned: harness location, rubric weights, corpus format details, the judge model and prompt, the exact ε (start at 0.1), the calibration thresholds.

**E. Node profile and governance** (build order #5)

Pins: *the rule engine flags exactly the violations present, with correct witnesses, deterministically; readiness reflects the current revision; provenance is never lost.*

- **Per-rule property tests:** on generated graphs with planted violations, each rule flags every planted violation and nothing else, with the correct witness.
- **Planted-violation corpus:** 100% recall on blocking rules.
- **Determinism:** same flow, profile and pack → identical findings.
- **Readiness:** an unwaived blocking finding prevents readiness; a valid waiver by the waiver role clears it for that revision only; any change resets readiness.
- **Profile:** branch probabilities sum to 1; every estimated value has a reason; every value keeps its provenance through overrides and regeneration.
- **Eval dimensions:** share of golden redesigns reaching readiness without waivers; waiver rate per rule; judged quality of profile estimates against human-provided values.

**F. Acceptance contracts and conformance** (build order #6)

Pins: *contracts are complete, grounded and discriminating.*

- **Completeness:** every AI and deterministic node has a contract with at least one negative case and stated below-threshold behavior.
- **Grounding:** every test case traces to the specification or is marked as an assumption.
- **Typing:** schema mismatches across edges become findings with the edge as witness.
- **Discrimination (mutation check):** deliberately broken implementations of golden nodes fail their contracts.
- **Conformance:** reports run in the sandbox, give per-node results with reasons, and are versioned with the code and contract revisions they tested; corrective feedback to code generation is bounded.
- **Eval dimension:** judged meaningfulness of test cases.

**G. Simulation and business case** (build order #7)

Pins: *results are correct, reproducible, conservative and honest.*

- **Analytic cases:** linear, branching and rework-loop flows match hand-computed expectations within tolerance.
- **Reproducibility:** same flows, profiles and seed → identical results.
- **Conservation:** every case terminates or is reported stuck.
- **Honesty:** no rendered figure lacks a range and a trace; sensitivity ranking surfaces planted dominant assumptions; the top estimated assumptions become inbox questions.
- **Eval dimension:** judged narrative faithfulness — every claim supported by computed results, nothing beyond them.

**H. Regulatory mapping** (build order #8)

Pins: *obligations are triggered accurately, and no output claims more than it covers.*

- **Expert-labelled scenarios (operator-supplied):** precision and recall of obligation triggering, with recall thresholds agreed with the operator before release.
- **Planted scenarios** must trigger specific obligations.
- **Human-only applicability:** mapping does not proceed on unanswered applicability questions; agent-authored answers are rejected.
- **Assurance:** every output carries its coverage statement and draft label; export is blocked without a current sign-off by the reviewer role; upstream changes reset sign-off; outputs contain no verdict vocabulary.
- **Catalog handling:** schema validation, effective dates, and re-assessment notices on version change.

---

## 13. Where to invest thought and expertise

### 13.1 Settled principles — don't re-derive the why, do justify the how

- **Schema validation pinned by property-based tests**, extended to every invariant, rule and normalization.
- **The closed error-channel topology** (§6.3), with every link code-enforced and reused by every assessment capability.
- **Small, self-validating mutations** over whole-artifact writes.
- **Immutable, per-run versioning** with restore as a new revision and explicit-path commits.
- **An established streaming protocol**, with lifecycle owned by the run harness.
- **Layered user edits with computed provenance**, anchored patches and namespaced identity.
- **One secret gate on every exit.**
- **Rollback as the failure path**, isolated per run and verified.
- **Policy as code**: declarative rules evaluated deterministically, with witnesses.
- **Monte Carlo with sensitivity analysis** for decisions under uncertainty.
- **Contract-based acceptance testing**, including mutation checks to prove tests discriminate.
- **Human sign-off for anything resembling professional advice.**

### 13.2 Open problems

Items 1–4 are **launch-blocking** by default: items 1–3 for any release, item 4 for the regulatory capability. They may only be deferred with operator agreement, and the operator may revise the marking before design sign-off. Items 5–15 follow in priority order.

**Launch-blocking**

1. **Verified rollback on every exit path.** Success, model error, validation failure, cancel, disconnect, kill, storage failure, concurrent runs, and user edits during a run must each end in a committed valid result or a verified return to base state (§6.3 item 4, §10 row 1). Open questions: how runs isolate their writes (per-run working copies, transactional staging, or something else), and how restart recovery identifies a run's base state.
2. **Stable identity between agent and user layers.** Id namespaces, anchored patches and surfaced orphans — for flow items, profile overrides and contract edits (§6.4). Open questions: how orphaned edits are presented, how incremental runs treat locks on user-created items, and how identity survives schema migration (item 10).
3. **Sandbox isolation for generated and imported code.** Isolation, resource limits, network policy, dependency pinning, allow-listed environments, read-only artifact access. Covers code generation, conformance testing, preview and archive import (§6.7).
4. **Regulatory scope and liability** (regulatory capability only). Which catalogs first, who qualifies as reviewer, how catalog maintenance is staffed, and what liability wording exports carry. Without operator answers, regulatory mapping does not ship.

**In priority order**

5. **Evals for subjective artifacts.** What *is* "good" for a process flow, a profile estimate, a contract or a business-case narrative? Needs rubrics, calibrated judges against human anchors, and a corpus spanning messy real-world inputs. Natural further dimensions include human-in-the-loop placement for actors, audit trail and guardrails, and end-to-end versus local optimum; the operator may supply domain review criteria. The only safe route to per-role model policy (§8.1).
6. **The autonomy scale and the rule expression format.** A scale coarse enough for consistent estimates and fine enough for meaningful ceilings; a rule format expressive enough for path predicates (constrained declarative language, graph queries, or something else) yet simple enough for operators to review.
7. **Verifying judgment nodes and the contract formalism.** Task agents have checkable outputs; actors and orchestrators exercise judgment. Which schema formalism suits contracts generally, and which contract shape — behavioral scenarios, judged rubrics, policy constraints — makes judgment nodes testable?
8. **Assumption quality.** How good are agent estimates, and how should they be calibrated? When measured data becomes available (logs, conformance runs, operational metrics), how does it replace estimates without invalidating signed-off artifacts?
9. **Prompt/skill versioning.** Flows and their assessments are long-lived client artifacts built by older prompts. Should artifacts carry their prompt version? Can a flow be re-assessed by newer skills without violating locks or waivers?
10. **Schema evolution.** Every schema version — flows, profiles, contracts, rule packs, catalogs, user-edit layers and their anchors — needs a migration tested like code.
11. **Durable orchestration semantics.** Cancellation across restarts, resumability of half-completed runs, idempotency of side effects, exactly-once commits, admission across every entry point (§11.2).
12. **Multi-tenant security.** Per-tenant key management, workspace isolation under concurrency, audit trails keyed to humans, and a credential model stronger than browser-held API keys.
13. **Cost governance.** Budgets must be *enforced*. Budget-aware model policy needs evals first, or it will silently degrade output quality (§8.1).
14. **The conversational UX contract.** When should the agent ask versus assume? With sensitivity-ranked elicitation, the question becomes measurable: does asking about the top-ranked assumptions narrow the business case most per question?
15. **Observability across the agent tree**, including cost attribution per capability.

---

## 14. Non-negotiable constraints

- **Both design forces** (§1): the reliability contract and no false assurance — including about the product itself.
- **Extension contract:** adding a phase, an artifact type, a governance rule pack, a profile defaults set, or an obligation catalog is a declarative change. No code edit, no workflow edit (§6.2). Do not replace skill discovery, refusal rules or tool scoping with a hardcoded workflow.
- **Models propose, code checks:** governance evaluation, dataflow typing, simulation and obligation mapping are deterministic code (§6.1).
- **People decide, and are accountable:** every waiver, sign-off, applicability answer and override is attributed to a named person and versioned.
- **Translation parity:** one source locale; every key present in every locale the operator specifies, enforced in CI. Prompts are not translated; output language is a process setting applied to every agent (§5).
- **A design-system document governs the frontend**, supplied by the operator. Where code and the design doc disagree, the doc wins — so the doc is kept accurate and reviewed with the code (§8.7).
- **Sandboxing is a security boundary, not hygiene** — for the workspace (file allow-list, allowed roots, path-traversal protection on resolved paths) and for every process that executes generated or imported code.
- **Provider-agnostic model access:** every model call goes through one abstraction (§6.8).
- **No assumed environment or jurisdiction.** Deployment targets, connectivity, data residency, hosting and applicable regulation are operator inputs.
- **Stack is not mandated.** The design document chooses.

---

## 15. Success criteria

1. **Reliability:** ≥ 99% of flow-authoring runs end valid-or-verifiably-rolled-back, measured via run records, where *valid* means every flow invariant holds and the flow is non-degenerate. Zero user-visible "fix the file" moments.
2. **Safety:** no user edit — flow item, profile override or contract edit — ever lost, misapplied or silently dropped by an agent run, including across regeneration. Each §10 invariant pinned by at least one test, including adversarial ones, on every exit path.
3. **Governance:** 100% recall on the planted-violation corpus for blocking rules; every finding witnessed; readiness never granted on a stale revision; waiver rates tracked and reviewed per rule.
4. **Honest quantification:** every business-case figure carries a range and an assumption trace; simulations reproducible and conservative; narrative faithfulness meets its eval threshold.
5. **Verifiability:** every handed-over node has a grounded contract; deliberately broken components fail their contracts; conformance reports accompany every generated prototype.
6. **Assurance:** no regulatory output leaves the system without a current named sign-off and a coverage statement; obligation-triggering recall meets the operator-agreed threshold on the expert-labelled set; every human decision is attributed.
7. **Extensibility:** a new phase with a new artifact type, a new rule pack, and a new catalog each demonstrable as declarations plus documents plus a small test, with no core changes.
8. **Quality:** eval harness green on the golden corpus for every shipped capability; per-role model policy justified by measured output quality (§13.2.5).
9. **Operability:** restart-safe runs, multi-worker safe, admission across every entry point, per-run cost visible and budget-enforced, docs generated or tested.
10. **Security:** every exit gated for secrets; generated and imported code sandboxed; escape and leak drills pass in CI.
11. **Tests:**
    - property tests on the schema, the user-edit merge, every rule, and the simulation
    - scripted-model orchestration tests driving the real framework
    - an eval gate on prompt, rule-pack and catalog changes
    - warnings as errors and a coverage ratchet
    - generated API types

    The DoD-product definition (§12.2) is the acceptance bar for the whole system.
