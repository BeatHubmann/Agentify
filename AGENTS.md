# AGENTS.md

## Process precedence

1. `genesis/` and the `agentic-project-harness` skill govern the project process: proposal → operator sign-off → governed development. No project files are written before the relevant proposal is approved.
2. Superpowers skills are techniques used *within* that process — test-driven development, systematic debugging, verification before completion, code review. Where a Superpowers skill prescribes its own process (brainstorming, writing or executing plans, worktrees, subagent-driven development), this file and the harness take precedence.
3. Design output goes to `ARCHITECTURE.md`, never to `docs/superpowers/`.
4. Subagents are used only when they demonstrably help, never by default.
