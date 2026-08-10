# PRD-08 — Frontend, Dashboard & Visualisation Layer
# Deep specification · Companion to 01_PRD.md through 07_PRD_HARNESS_AND_EVALS.md
# Position in build order: LAST — after backend systems are proven by their evals.
# Two distinct deliverables with different lifespans:
#   PHASE A — Demo Layer (disposable, 2–3 days, investor/pilot-facing)
#   PHASE B — Product UI (Stage 2/3, the real surface wrapping the same APIs)
# Rule inherited from the whole system: the frontend renders backend truth; it never
# computes, infers, or transforms. Every pixel showing a value shows its provenance.

---

## 1. Why a Frontend at All (and why LAST)

The backend is the product; the frontend is how three audiences believe it:

| Audience | What they must believe | The view that convinces them |
|---|---|---|
| Investor | "scattered data → unified brain → grounded training data, automatically" in 90 seconds | the Demo Flow (§4): before/after + provenance-linked SFT view |
| Pilot customer (technical buyer) | "it annotated MY schema correctly and I stayed in control" | confirmation UI + schema graph + eval scorecard on THEIR data |
| Daily user (Stage 3) | "I can run, review, and trust conversions without reading logs" | the operational dashboard (§6) |

It is built last because every view is a thin window onto contracts that already exist — AnnotatedSchema, the graph store, PipelineEvents, EvalResults, artifacts. Building UI before those contracts stabilise means rebuilding it; building after means the UI is mostly *rendering*, which is exactly the cheap, low-risk position a small team wants.

**The one non-negotiable design idea across every view: provenance is the aesthetic.** Other tools show outputs; DataBridge shows outputs *with their receipts*. Hover any value → see its SourcePointer. Click any answer sentence → see the entities/edges that support it. This is the visual differentiator because it IS the product differentiator (P4 made visible).

---

## 2. Architecture

```
┌────────────────────────────  BROWSER  ────────────────────────────┐
│  Next.js (App Router) + Tailwind + shadcn/ui                      │
│  React Flow (schema & knowledge graph) · Recharts (eval/cost)     │
│  TanStack Query (server state) · zustand (view state only)        │
└──────────────────────────────┬────────────────────────────────────┘
                               │ REST + SSE (no business logic above this line)
┌──────────────────────────────▼────────────────────────────────────┐
│  apps/api  — FastAPI gateway (NEW, thin)                          │
│  wraps the SAME functions the CLI calls (apps/cli imports them    │
│  from packages/*; the API imports the same — one brain, two       │
│  mouths). Endpoints:                                              │
│   GET  /sources · /schema/{id}/annotated · /graph/subgraph        │
│   GET  /graph/ask?q=&as_of=        (SSE: retrieval → cited answer)│
│   GET  /artifacts · /artifacts/{id}/manifest                      │
│   GET  /evals/latest · /events/stream    (SSE: live pipeline)     │
│   POST /annotations/{id}/review    (confirmation actions)         │
│   POST /convert  {ConversionRequest}                              │
│  Auth: none in demo profile (localhost) · session+RBAC Stage 3    │
└──────────────────────────────┬────────────────────────────────────┘
                               │ in-process imports
                    packages/{semantic,graph,convert,evals,core}
```

**Decisions & tradeoffs:**
- *FastAPI gateway over direct DB access:* the UI must never query Neo4j/DuckDB directly — the gateway enforces the same refusal rules (unconfirmed schemas unusable, airgapped restrictions) the CLI enforces. One enforcement point or none.
- *SSE over WebSockets:* every live view (pipeline events, ask-streaming) is server→client only; SSE is simpler, proxies cleanly inside customer VPCs, and degrades to polling. WebSockets add bidirectional complexity nothing here needs.
- *Next.js + shadcn over a heavier platform:* the team already ships React; shadcn gives accessible primitives without design-system investment; everything renders fine on a laptop inside an airgapped VPC (no CDN dependencies — fonts and assets bundled, a P1 detail that bites at the worst demo moment).
- *No state duplication:* TanStack Query caches server responses keyed by the same content hashes the backend emits; zustand holds only ephemeral view state (selected node, open panel). The frontend holding business state is how UIs drift from truth.

---

## 3. PHASE A — The Demo Layer (build first, keep disposable)

Scope: ONE single-page app, three panels, wrapping three backend calls. Budget: 2–3 days. It may be thrown away when Phase B lands — write it accordingly (no abstractions, no tests beyond smoke, yes hardcoded fixture IDs).

```
┌─────────────────────────────────────────────────────────────────┐
│  DataBridge · demo                                  [fixture ▾] │
├───────────────┬─────────────────────────────────────────────────┤
│ PANEL 1       │  PANEL 2 — THE BRAIN                            │
│ SOURCES       │  React Flow force-graph of the knowledge graph  │
│ ─────────     │  nodes coloured by EntityType, edges by         │
│ ● postgres    │  confidence; time-slider sets as_of → expired   │
│   12 tables   │  edges fade out (the bi-temporal wow-moment)    │
│ ● documents   │                                                 │
│   10 files    │  [ ask the brain… ____________________ ⏎ ]      │
│ ● codebase    │  → streamed answer; every claim is a chip that  │
│   1 repo      │    highlights its supporting nodes/edges        │
│               ├─────────────────────────────────────────────────┤
│ ingest ▶      │  PANEL 3 — GROUNDED OUTPUT                      │
│ (SSE ticker   │  left: generated SFT conversation               │
│  of pipeline  │  right: source rows/spans                       │
│  events —     │  hover a fact in the chat → its source row      │
│  "annotating  │  flashes; hover a row → its uses highlight.     │
│  table 7/12") │  Footer: E2 100% · 0 fabrications · n pairs     │
└───────────────┴─────────────────────────────────────────────────┘
```

**The 90-second investor script this layout serves:**
1. Click *ingest* → the SSE ticker shows the system working through real sources (10s).
2. The graph blooms in Panel 2; drag the time-slider to show "what we believed in March" (20s — the moment generalist tools can't replicate).
3. Type one question; the answer streams WITH claim-chips lighting up graph nodes (25s — "every claim has receipts").
4. Panel 3: the generated training conversation with fact↔row highlighting (25s — "training data with zero hallucination, provably").
5. Footer evals: "and the system grades itself on every run" (10s).

**Implementation notes:** the fact↔row linking is free — Subsystem C's output already carries `facts_used` + SourcePointers; the demo just renders the join. The time-slider is a single `as_of` param the graph endpoint already accepts. Nothing in the demo computes; that is why it's 2–3 days.

---

## 4. PHASE B — Product UI (Stage 2/3)

Five views, in build order matching backend readiness:

### 4.1 Confirmation Workspace (replaces the TUI; T19)
The 25-minute-per-30-tables KPI from PRD-06 §7 carries over and is measured the same way (review-time telemetry per item). Layout: left rail = review queues by confidence band (mandatory → spot-check → auto-approved sample); centre = the per-column card (name, dtype, stats, redacted samples, hints, model answer + description, second-model vote, single-key accept / vocabulary-picker edit); right rail = the table's position in the schema graph (mini React Flow) so the reviewer never loses the forest. Label candidates get their own tab rendering derivation SQL with plausibility-check results inline. The workspace refuses to mark a schema `confirmed_by` while mandatory items remain — same type-level rule, now with a disabled button explaining why.

### 4.2 Schema & Brain Explorer
Two linked graph canvases sharing one component: (a) schema graph — tables as nodes sized by row_count, FK edges labelled with relationship names from Stage C, role-coloured; (b) knowledge graph — the Panel-2 demo view grown up: type filters, confidence floor slider, corroboration-count visual weight, entity drawer (attrs, source_pointers list with deep-links to source viewers), and the as_of time-slider as a permanent fixture. Search = the same hybrid retrieval endpoint; results pulse in the canvas.
*Tradeoff owned:* force layouts collapse beyond ~2k visible nodes — the explorer always renders a SUBGRAPH (seed + N hops, filters) and says so; "show everything" is a non-goal, not a missing feature.

### 4.3 Conversion Studio
The `plan → confirm → execute → artifact` lifecycle as a surface: pick source + target → the proposed MappingDraft renders as a two-column field-mapping diagram (source columns ↔ target fields, coercion chips on each edge, `unmappable` fields flagged red with the proposer's reasoning) → sample preview table (the 50-row validator output, quarantine rows highlighted with reason codes) → confirm → execution progress via SSE → artifact card (manifest, schema hashes, replay button). Drift refusals render as the structured diff report, not a stack trace.

### 4.4 Eval & Cost Dashboard
EvalResults over time: per-suite pass/fail board, slice drill-downs (the E1 confusion matrix as a heatmap; calibration reliability diagram per PRD-06 §4), E6 parity gap trend (cloud vs airgapped lines), E7 cost-per-operation trend with the regression threshold drawn in. The scorecard CI posts to PRs and this dashboard read the same JSON — one source, two renderings.

### 4.5 Pipeline & Audit View (Stage 3)
The PipelineEvent stream made navigable: per-run timelines, model_map per step, ladder/retry telemetry, budget consumption, fallback activations flagged. Click any output value anywhere in the UI → its lineage chain renders as a small DAG ending at SourcePointers. This view is the RBAC/audit deliverable (T22) wearing a UI.

---

## 5. Design System (small, strict)

- **Provenance affordance everywhere:** any rendered value derived from data gets the same hover/click affordance (dotted underline → pointer popover). One component (`<Grounded value={gv}/>`), used or the PR is wrong.
- **Confidence has exactly one visual language:** a 3-stop colour scale (auto/spot/review bands) used identically in TUI parity, graph edges, annotation cards, mapping chips. Investors and users learn it once.
- **Dark-first, projector-tested:** demos happen on projectors and in dim rooms; the palette is chosen against that reality, with a light theme as a toggle, not the default.
- **Empty states teach:** every view's empty state explains what backend step produces its data and offers the command/button — the UI doubles as onboarding.
- **Numbers are never restyled into vagueness:** eval scores, costs, gaps render as the exact figures the backend emitted. No traffic-light-only summaries hiding the number (the sceptical-buyer principle applied to pixels).

---

## 6. Evaluation of the Frontend Itself

| Check | Method | Gate |
|---|---|---|
| Render-truth parity | snapshot tests: API fixture → rendered values byte-equal to fixture values (no client-side math) | 100% |
| Provenance coverage | DOM audit script: every `data-grounded` value has a resolvable pointer popover | 100% |
| Demo script timing | scripted Playwright run of the §3 flow on fixture data | < 2 min end-to-end, zero errors |
| Confirmation KPI | telemetry on pilot reviews | ≤ 25 min / 30 tables (Phase B) |
| Airgapped serve | build served from the VPC container, network-denied CDN check | zero external requests |
| Accessibility floor | axe-core CI pass on the five Phase-B views | no critical violations |

---

## 7. Failure Modes Register

1. **UI computes a number the backend didn't** (a "quick" client-side aggregate) → render-truth snapshot gate + code review rule: TanStack Query data is read-only; any derivation belongs in the gateway.
2. **Graph view oversells** (force layout implies structure that's just physics) → explorer always labels the active subgraph query; layout seed is fixed per subgraph so the same query renders the same picture twice (investors notice nondeterminism).
3. **Demo divergence** (Phase A drifts from real backend behaviour as backend evolves) → the demo runs against the real API on the real fixture in CI weekly; red demo = fix or retire it before it lies in a meeting.
4. **Confirmation gamification backfires** (speed telemetry pressures rubber-stamping) → same mitigation as PRD-06 ¶9.3: the KPI is a ceiling for friction, not a target for reviewers; per-item floor flags survive into the UI.
5. **CDN/external assets in airgapped rooms** → bundled assets + the network-denied CI check (§6); discovered in CI, never in a bank's demo room.
