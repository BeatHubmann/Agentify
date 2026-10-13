# Implementation prompt — Agentify

> **How to use:** hand this directory to the implementing model with this file as the opening instruction (verbatim). `README.md` explains the package; `PRODUCTION-BRIEF.md` is the context. This file is the tasking layer — it points into the brief rather than repeating it.

---

## The task

You are designing and building **Agentify** — an agentic process-redesign tool that consultants use on client engagements (mission and product surface: brief §1, §5). It captures how a process works today, redesigns it so AI agents can execute it, and makes the redesign defensible: governed by deterministic policy checks, quantified by a simulated business case, made testable by per-node acceptance contracts, and mapped to regulatory obligations for qualified review.

Your inputs are the two other files in this directory, plus the operator inputs listed under *Inputs not in this package*. You are building the system **from scratch**.

Begin with `README.md`, then read `PRODUCTION-BRIEF.md` in full before producing anything.

## Clean-room build

- **Build from this specification alone, in a clean, empty repository.**
- **Choose every name, technology and file layout yourself**, and justify the choices in the design document.
- **Assume no runtime environment** (operating system, hosting model, network access, model provider, locales, jurisdiction) that the operator has not stated. Where an operator decision is missing, ask; don't fill the gap with a default.
- **Treat recommended approaches as defaults, not ceilings.** Where an established approach meets a requirement better, propose it and say why.

## The two design forces

1. **Reliability contract (§1):** the flow-authoring agents reliably transform a written process specification into a validated, renderable process flow — **every time, with no manual correction of tool failures**. Failure is handled by rollback, never by asking the user to fix files. "Validated" means every flow invariant holds and the flow is non-degenerate, checked by code after the run. Schema validity alone is not enough. §12.2's DoD-product defines this over a corpus, not an example.
2. **No false assurance (§1):** every figure carries its range and assumption trace; every finding carries its witness; every regulatory output states its coverage and stays a draft until a named, qualified human signs it off. The system reports; it never issues verdicts. This applies to the product's claims about itself as well: no claim about speed, effort or quality without measured data.

## First deliverable: a design, not code

Produce a design document, `ARCHITECTURE.md`, **before** writing implementation code. It must at minimum:

1. **Map every §10 invariant to the code mechanism that enforces it.** A mechanism that depends on the model calling a tool, obeying a prompt, or the UI behaving does not count as enforcement.
2. **Show each invariant holds on every exit path:** success, model error, validation failure, user cancel, client disconnect, process kill, storage failure, concurrent runs on one process, and user edits made while a run is active.
3. **Define the artifact lifecycle (§4.4):** the states, what gates each transition, and how readiness is bound to revisions.
4. **Name the §9 hazards** you design against from day one, and how.
5. **State which §13.2 open problems you solve at launch and which you defer**, with reasons. Problems marked launch-blocking may only be deferred with operator agreement.
6. **List every point where your design departs from the brief's requirements or recommended defaults**, with reasoning (see *Deviations*).
7. **Define your module/service layout, data model, artifact contracts, and naming** (§12.1 layers 1–3) precisely enough that a second engineer could implement against them — including the node profile, governance rules and findings, acceptance contracts, simulation inputs and outputs, and obligation catalogs.
8. **Define the error contract:** how a tool failure or governance finding reaches the model, how corrective attempts are bounded and counted, and which failures abort a run (§6.3, §6.9).
9. **Propose the formalisms the brief leaves open:** the autonomy scale and rule expression format (§13.2.6), the contract schema formalism and the contract shape for judgment nodes (§13.2.7), and the simulation model (§6.11).
10. **Define the test strategy.** Contract tests drive the real agent framework with scripted models, never a replacement object for the agent, and never stub out the components they claim to cover — commit, rollback, secret scan, rule evaluation (§8, §12.3, §12.5).

Present the design and wait for operator sign-off before generating the system.

## Non-negotiables

- **All fourteen §10 invariants**, in some form.
- **§14 constraints:**
  - **Extension contract:** adding a phase, an artifact type, a governance rule pack, a profile defaults set, or an obligation catalog is a declarative change, not a code edit (§6.2).
  - **Both design forces**, including no false assurance.
  - **Models propose, code checks:** governance evaluation, dataflow typing, simulation and obligation mapping are deterministic code.
  - **Translation parity** for whatever locales the operator specifies.
  - **Sandboxing as a security boundary**, for workspaces and for every process that executes generated or imported code, including conformance testing.
  - **Provider-agnostic model access:** every model call goes through one abstraction (§6.8).
- **Every exit is gated for secrets:** logs, version history, generated code, run events, chat history, reports, and exports (§6.7, §11.11).
- **Measured, never assumed:** the reliability contract and every capability's quality are measured by the eval harness (§12.5D–H).

## What you may change

Everything else: stack, module layout, every API shape the brief describes, rule and catalog formats, the simulation method, the eval corpus format and rubric details, release staging, and any recommended default (§7) — deliberately and with reasons.

## Deviations

Where your judgment conflicts with a requirement or default in the brief (especially §8, §9, §10), you may overrule it — but only by stating the disagreement explicitly, with reasoning, in your design document. These requirements address hazards common to agent systems that write artifacts and to decision-support systems that quantify and assess; deviating silently risks reintroducing them.

## Build order

§12.4 is the recommended sequence and release staging; you may re-sequence with stated reasons. The foundation (reliability contract, user-edit identity, verified rollback, eval harness) comes before the assessment capabilities that read from it. §12.5 states the behavior contracts as prose.

## Inputs not in this package

- **Frontend design system — operator input.** Supplied before any UI work (§14). Until then, do no UI work beyond what the backend contracts require.
- **Golden corpus — operator input.** §12.5D defines its shape. The operator supplies anonymized process material, including a minimal first golden case with a checkable outcome.
- **Obligation catalogs — operator input.** Versioned, human-maintained, with jurisdiction, effective dates and source citations per obligation (§4.7). Agents never author catalog entries.
- **Expert-labelled regulatory scenarios — operator input.** Needed to measure obligation triggering (§12.5H) before the regulatory capability ships.
- **The initial governance rule pack.** Propose it in the design document; the operator approves the blocking rules before they gate anything (§6.9).
- **Prompt and skill texts.** Writing them is your job; the brief gives their contracts, not their copy.

## Operator decisions required before implementation begins

- [ ] Stack pin — or unconstrained
- [ ] Model providers, and whether model calls go through a private gateway (authentication style, budget reporting)
- [ ] Deployment targets and environment: local, hosted, on-premises; any offline or air-gapped requirement; data residency
- [ ] Access model: who may use the system, roles (including the waiver role and the qualified reviewer role), tenancy (§11.1)
- [ ] UI and output locales
- [ ] Frontend in scope for the first release, or backend-first — and, if in scope, the design-system document
- [ ] Release staging of the assessment capabilities (§12.4)
- [ ] Access to anonymized process specifications for the eval corpus
- [ ] Jurisdictions, the first obligation catalog, reviewer qualifications, and liability wording for regulatory exports (§13.2.4)
- [ ] Business-case conventions: currency, period, and whether to model staffing and queueing (which needs staffing data)
- [ ] Which §13.2 open problems are launch-blocking vs. deferred, beyond items 1–4
- [ ] Scope of optional capabilities (§5.1): AI-maturity assessment, demo sessions, process archive import/export, live preview
- [ ] Code-generation engine: a specific coding-agent tool, or a pluggable executor
- [ ] Redesign coverage reference: the agent-authored current-state flow, or the current-state flow including user edits
- [ ] Design-document sign-off (the gate between design and code generation)

