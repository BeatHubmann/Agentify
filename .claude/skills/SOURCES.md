# Vendored skills

Third-party agent skills, copied into this repository at a pinned commit and reviewed before adoption. They are techniques used within the process in `AGENTS.md`; where one conflicts with `AGENTS.md`, the harness or the approved documents, those win. No plugin, hook or install script from these projects is used. An update is a reviewed re-copy at a new commit, recorded here.

| Skill | Upstream | Commit | License | Copied | Local changes |
|---|---|---|---|---|---|
| `humanizer` | [blader/humanizer](https://github.com/blader/humanizer) | `225a6f39ac85f76ee48dbad772ea4abe4ed6c9d8` | MIT (`humanizer/LICENSE`) | `SKILL.md` | None |
| `ponytail-review` | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | `84740e3d344b69b4a133201fa519eb37a62a2b2a` | MIT (`ponytail-review/LICENSE`) | `skills/ponytail-review/SKILL.md` | None |
| `ponytail-audit` | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | `84740e3d344b69b4a133201fa519eb37a62a2b2a` | MIT (`ponytail-audit/LICENSE`) | `skills/ponytail-audit/SKILL.md` | None |
| `ui-ux-pro-max` | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | `56ea9cf174ed86786187daa9de0e6247cb0747e4` | MIT (`ui-ux-pro-max/LICENSE`) | `.claude/skills/ui-ux-pro-max/` without `scripts/tests/` | `SKILL.md`: script paths `${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/` → `.claude/skills/ui-ux-pro-max/` (run from the repository root) |

## Not adopted (reviewed 2026-10-15)

- **ponytail core skill, plugin and hooks.** The hooks inject an always-on mode at every session start, subagent start and prompt. Its guidance on tests ("trivial changes need none") and on types ("keep values in the form the platform gives you") conflicts with the TDD and distinct-type rules and with the evidence rule in `AGENTS.md`.
- **ui-ux-pro-max's other bundled skills** (`design`, `design-system`, `brand`, `banner-design`, `slides`, `ui-styling`). `design` sends prompts to Google Gemini and MuAPI, outside the EU inference boundary (A§1, OD-3 and OD-4); the rest are not needed for the frontend.

## Use in this project

- `humanizer`: prose in `ARCHITECTURE.md`, `VERIFICATION.md`, PR descriptions and commit bodies. It never adds facts.
- `ponytail-review`, `ponytail-audit`: report-only code review techniques, alongside the Superpowers review skills. Findings are evidence for the operator, not authority.
- `ui-ux-pro-max`: local, offline UI/UX guidance (Python standard library over bundled CSV data). The frontend still waits for an operator-approved design-system document (A§1, OD-11). Its output is a recommendation; no client or project data goes into queries.

## Checking a copy against upstream

```sh
git clone https://github.com/<owner>/<repo> /tmp/upstream && git -C /tmp/upstream checkout <commit>
diff -r /tmp/upstream/<copied path> .claude/skills/<skill>   # expect only LICENSE and the local changes above
```
