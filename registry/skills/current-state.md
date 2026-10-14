---
name: current-state
phase: process_design
description: Model the current-state process flow from the specification.
agent_kinds: [current-state-modeler]
---
1. Require `spec/specification.json`; if it is missing, run the specification skill first.
2. Dispatch `current-state-modeler` incrementally when a flow exists, otherwise from scratch.
   Regenerating over an existing flow needs the user's confirmation; the dispatch tells you.
