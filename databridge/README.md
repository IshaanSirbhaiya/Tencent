# databridge — code monorepo

DataBridge Stage-1 (POC). `uv`-managed Python 3.11+ monorepo. **Scaffold only** after
PROMPT 0 — no product code yet; package directories carry `__init__.py` placeholders.

Read `CLAUDE.md` before any session. The shared brain (state, tasks, decisions) is the
sibling repo `../databridge-brain`; start at `../databridge-brain/00-meta/STATE.md`.

## Layout (02_TRD A.2)
```
packages/
  core/      shared types, provenance, config, events   (T01 — next)
  harness/   model abstraction (LiteLLM wrapper)         (T02)
  ingest/    connectors + adapters                       (T03-T05)
  semantic/  schema graph + annotator + confirmation     (T06-T07)
  graph/     knowledge graph (Pillar 1)                  (T08)
  convert/   conversion engine (Pillar 2)                (T09-T10, C01-C24)
  evals/     evaluation suites                           (T11, C24)
apps/
  cli/       POC interface (typer)
  mcp_server/ Stage-2: expose graph to agents
tests/
docs/        the 9 build docs (01-09) — source-of-truth the brain mirrors FROM
config/      models.yaml, sources.yaml (added in T01/T02)
```

## Invariants
- No vendor SDK imports outside `packages/harness` (P2; CI grep rule lands in T01).
- Every emitted value is a `GroundedValue` (P4).
- Closed vocabularies change only via an ADR in the brain repo (P3/P6).
