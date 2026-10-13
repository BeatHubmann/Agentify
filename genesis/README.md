# Agentify — genesis package

> **Purpose:** the specification package for building **Agentify**, a tool that redesigns business processes so AI agents can execute them — and delivers each redesign governed, quantified and testable.
> **Audience:** a frontier LLM (or its human operators) designing and building the system from scratch.
> **Contents:** `IMPLEMENTATION-PROMPT.md` (the tasking layer — use as the opening instruction) + `PRODUCTION-BRIEF.md` (the canonical specification context).

This package is **standalone**: everything needed to design the system is in these files. The only other inputs are the operator inputs listed in `IMPLEMENTATION-PROMPT.md`.

**Neutral by construction.** The brief describes mechanisms by what they must achieve. It names no vendors, libraries, languages, runtimes, jurisdictions, or deployment environment. Every name, technology and environment is chosen in the design document or supplied by the operator.

## What Agentify delivers

From written process material, Agentify produces a current-state flow and an agentic redesign, then makes the redesign defensible:

- **Governed:** deterministic policy checks with witnesses and waivers
- **Quantified:** a simulated business case with ranges and traced assumptions
- **Testable:** per-node acceptance contracts, verified against generated code
- **Obligation-aware:** a draft regulatory register for qualified human review

Two design forces govern everything: the **reliability contract** and **no false assurance** (both §1). Models propose, code checks, and people decide and are accountable.

## How to read the brief

| First | Section | Why |
|---|---|---|
| 0 | `IMPLEMENTATION-PROMPT.md` | The assignment: deliverables, non-negotiables, inputs, deviation protocol |
| 1 | §1–§2 Mission, reframe | The two design forces, the positioning, and the product's reason to exist |
| 2 | §4 Domain model | The grammar, schema, node profile, artifact lifecycle, and the governance, contract and obligation concepts |
| 3 | §9–§10 Hazards, invariants | The payload: what goes wrong in systems of this kind, and what must always hold |
| 4 | §6 Reference architecture | Mechanisms and their requirements: foundation (§6.1–§6.8) and assessment (§6.9–§6.13) |
| 5 | §7–§8 Recommended defaults, anti-patterns | Judgment context, with reasons |
| 6 | §11–§13 Capabilities, contracts and tests, open problems | Where expertise is needed; §12.4 stages the build; §13.2 lists launch-blocking problems first |
| 7 | §12.5 Behavior contracts | What the tests must pin, stated as prose |

## Ground rules for consuming this package

1. **The invariants (§10) and hazards (§9) are the payload.** They must survive in some form. A design that loses an invariant has failed, whatever it adds.
2. **"Must" is a requirement; "recommended" is a default.** Defaults are a starting point, not a ceiling. Where you know an approach that meets the requirement better, propose it and state why in the design document.
3. **The behavior contracts (§12.5) are the deliverable for the tests; their shape is yours.** Test code, module layout, harness structure and libraries are design decisions.
4. **Terms are descriptive.** Choose your own names. Don't assume any runtime environment the operator hasn't stated.
5. **Build the foundation first.** The assessment capabilities read the validated flow; they are only as trustworthy as the reliability contract beneath them (§12.4).
6. **This package exists to be argued with, not obeyed.** Disagree explicitly, with reasons, through the deviation protocol in `IMPLEMENTATION-PROMPT.md`.

## Non-goals of this package

Stack, model providers, gateway arrangement, deployment environment, locales, access model, jurisdictions and obligation catalogs, frontend scope, and release staging are decided by the operator or in the design document. The brief records them as open decisions.
