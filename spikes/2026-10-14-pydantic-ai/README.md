# Spike: Pydantic AI semantics (2026-10-14)

Exploratory code, not product code. Nothing under `spikes/` is imported by Agentify.

**Question:** which Pydantic AI 2.56.0 behaviors does the error contract and run harness rely on, and do they hold? See `ARCHITECTURE.md` A§10.3 and A§28 (S1.x).

**Run:** in a throwaway environment with `pydantic-ai-slim[bedrock,ag-ui]==2.56.0` on Python 3.13:

```
PYDANTIC_AI_NO_BANNER=1 python -I spike_semantics.py
```

No model or network is used; every check drives the real framework with a scripted `FunctionModel`.

**Output:** `results-2026-10-14.txt`. Re-run at the versions pinned at scaffolding before relying on the results.
