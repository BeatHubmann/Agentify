---
name: specification
phase: specification
description: Build the three-part specification from uploaded inputs, then synthesize it.
agent_kinds: [spec-section-current, spec-section-desired, spec-section-constraints, spec-synthesis]
---
1. Check that `inputs/` holds at least one document; if not, ask the user to upload one (inbox).
2. Dispatch the three section agents at once, in one turn: `spec-section-current`,
   `spec-section-desired`, `spec-section-constraints`.
3. When all three have ended, dispatch `spec-synthesis`. If a section failed, report it and
   offer to run that section again; do not synthesize from an incomplete set.
