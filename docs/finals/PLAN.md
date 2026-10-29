# Finals build plan: Walnut DataBridge for Demo Day (v1)

Branch `final_v1.0`. Written 30 Oct 2026, 03:40 SGT. Reviewed by Sonnet, Haiku, Opus, Fable and Codex
before the build starts. Decisions are in [DECISIONS.md](DECISIONS.md). Research is in
[research/](research/), and the per-criterion rubric research and deliverables are in [rubric/](rubric/).

## 0. The goal in one line

Win Demo Day (Tue 3 Nov, 5 min + 1 min Q&A). The product must look like a polished enterprise product
(Linear / Vercel / Palantir grade), work live with Tencent AI, and give judges something concrete for
every one of the 10 criteria.

## 1. Fixed facts and dates

| What | When (SGT) |
|---|---|
| Updated project link and description to Cindy (organisers) | **Fri 30 Oct 23:59**, ready for the user by 20:00 |
| Build complete, all tests green | Sun 1 Nov 18:00 |
| Final multi-model and Codex review, fixes | Sun 1 Nov 18:00 – Mon 2 Nov 10:00 |
| Freeze, rehearsal script, backup video | Mon 2 Nov 12:00 |
| Mandatory rehearsal | Mon 2 Nov 16:00–17:00 |
| Demo Day | Tue 3 Nov, finalists from 15:35 |

The rubric (10 × 10 points): Impact & Relevance · Human-Centered Design · AI Interaction ·
Technical Execution · Feasibility · Demo & Storytelling · Innovation & Creativity · UX & Accessibility ·
Responsible AI & Ethics · Overall Quality & Judge's Impression.

What the organisers said:
- Judges are Tencent WorkBuddy/CodeBuddy and RTC experts, AISG, and the five sponsors.
- **Show Tencent products live.**
- Pitch like a business.
- Skip the background.

## 2. Hard rules (unchanged, they are the product)

1. **Filter before the model.** Gate 1 and Gate 2 run before any model call, and any error means deny.
2. **Identity comes only from the signed token.**
3. **Write-ahead audit.**
4. **One response shape for the asker.** "Restricted" looks exactly like "does not exist". The new UI
   must **never** show the asker a count of hidden documents, a "something exists but is hidden" note,
   or Gate-2 reason codes. Some research suggested both; we reject both (see D-07).
5. **Every value on screen comes from the backend.** No invented numbers. Charts aggregate on the
   server (`/audit/stats`), not in the browser.
6. **Synthetic data only.**
7. **Never claim more than a test proves.**

## 3. What we are building

One Next.js app, `databridge/apps/internal_brain_web`, rebranded **Walnut DataBridge**, with two modes
and a persistent switch in the top bar.

### 3.1 User mode (simple, ChatGPT/Claude-like)

| Page | What it does |
|---|---|
| `/login` | Persona picker as sign-in: cards with name, role and "can open" line; radio-group keyboard behaviour; language and theme; passcode field when deployed |
| `/` (Ask) | Greeting, one composer, scope pill ("Answers only from what *you* can open"), 4 persona-aware starter cards, privacy one-liner |
| Answer view | Question bubble. Staged status line while the request runs (checking access → searching your sources → re-checking live → writing → verifying citations), driven by elapsed time and replaced by the real result. Answer with inline `[n]` chips. Source row with platform icons, freshness and version. Classification pill. Honest refusal (one constant text). Copy, feedback (Helpful / Wrong source / Out of date, ledgered later if time). Follow-up chips. "How this was answered" one-line disclosure → opens the Developer journey |
| Sources sheet | Right sheet: title, platform, container, version, "checked live at", highlighted passage; Esc returns focus |
| `/history` | My questions, grouped by day; opens the answer journey |
| `/compare` | Same question, two people, side by side (kept, restyled) |
| `/settings` | Language (English / 简体中文), theme, text size, reduce motion, high contrast, replay tour, Developer mode switch, privacy notice |

### 3.2 Developer mode (the brain, for technical judges)

| Page | Data | Role needed |
|---|---|---|
| `/dev` Mission control | KPI tiles, pipeline diagram with live per-stage p50, source freshness, ledger head, model status | Any; org numbers need `compliance_officer` (otherwise a lock card that explains RBAC, a feature in itself) |
| `/dev/journey/[req]` Request journey | Waterfall: accept → Gate 1 → Gate 2 → seal → compose (model, grounding) → release, with timings, the model used, citations, and the 3 hash-chained ledger records (seq, hash) | Own requests via `/ib/trace`; any request for compliance via `/audit/journey` (adds considered set and Gate-2 verdicts with reason codes) |
| `/dev/graph` Knowledge graph (permission-aware) | New `/ib/graph`: the signed-in person's permitted documents, containers, people and cross-platform links. Grows one hop at a time from a search or from an answer's citations, with count badges, a legend, a node side panel (Overview · Passage · Lineage · Why I can see it · Versions) and **View as**: a second persona in a compare slot, with nodes they cannot open greyed out and locked | Any employee |
| `/dev/brain` Walnut engine | The Walnut evidence engine's entity graph of Company A, its time slider, the provenance chain to the exact row and the ontology, through a new `/api/walnut/*` proxy to the gateway (:8300). Restyled into the new design system; the old :3300 app stays as it is | Any (dev token on the gateway; local only) |
| `/dev/analytics` End-to-end analysis | `/audit/stats`: requests over time; answered/refused/failed; funnel asked → considered → Gate 2 allowed → released → cited → answered; Gate-2 verdicts by reason; per-platform coverage; latency p50/p95 and per-stage; model mix and fallback rate; channel (UI vs WorkBuddy); grounding drops; access changes; reveals. Plus the evaluation panel (0 leaks / 5,958, 0/400, injection, recall) from a generated `eval_summary.json`, and test counts | `compliance_officer` |
| `/dev/audit` Audit console | Existing NL audit search, reveal-with-reason and Verify chain, restyled; row → journey | `compliance_officer` |
| `/dev/sim` Platform simulator | Existing (revoke channel, edit runbook, restrict page, reset), restyled | `admin_it` |
| `/dev/trust` Trust centre | Responsible-AI evidence in the app: the controls, AI Verify / MAS FEAT / OWASP LLM mappings and live proofs (tamper demo, restricted ≡ non-existent, injection) linking to the rubric folder | Any |

### 3.3 Teaching and demo

- **Interactive tour** in each mode (Radix popovers, step counter, Skip, keyboard). User mode: 3 steps in
  about 45 seconds. Developer mode: 5 steps.
- **Presenter mode** (`?present=1` or a hidden shortcut). A floating script bar with the next demo step
  and one-click actions: sign in as X, ask the scripted question, open journey, view as Ken. Large text
  for projectors, plus a reset.
- **Warm demo history**: `scripts/seed_demo_activity.py` asks about 300 real questions from the fact
  manifest, across all personas, through the real pipeline, so analytics and audit are populated. Each
  is labelled `channel="replay"`, `synthetic_replay=True`, and the UI says so.

### 3.4 Live AI

- Hy3 via TokenHub when the account is activated (the user does this in the morning; TokenHub currently
  answers 402 / 401006 for every model).
- The model id becomes configurable (`IB_MODELS`, already supported).
- Model status appears in Mission control, and every answer shows its model.
- The deterministic fallback stays and is labelled honestly.

## 4. Design system

- **Look:** Linear/Vercel restraint plus Palantir density for Developer mode.
  - Near-neutral greys and one accent.
  - Semantic colour only for platform identity and permission state (full, partial, locked).
  - Tabular numerals; Inter (bundled locally); 13–14 px dense in Developer mode, 15–16 px in User mode.
  - Dark default (projector-tested); light theme at AA.
  - Motion 120–200 ms, respecting reduced motion.
  - No neon, gradients, glass or emoji.
- **Components:** shadcn-style primitives on Radix (Dialog/Sheet, Popover, Tabs, Tooltip, DropdownMenu,
  Switch), cmdk command palette, a TanStack Table for audit/journey tables, and Recharts for analytics.
- **Graph:** React Flow (`reactflow` 11.11.4, already proven in the repo) for the one-hop subgraph, with
  a deterministic layout (d3-force with a fixed seed). See D-04, pending the Opus graph research.
- **Language:** EN and 简体中文 dictionaries in `lib/i18n/` for User mode and navigation. Developer mode
  stays English. `lang` is set on `<html>`.
- **Accessibility:** WCAG 2.2 AA throughout:
  - keyboard paths and visible focus, 24 px targets;
  - the answer area is a `role="log"` live region that announces once;
  - an accessible name for every control;
  - axe clean in both themes and both modes;
  - text size 100/115/130 %, reflow at 320 px;
  - reduced motion and a high-contrast mode.

## 5. Backend work (Python, `apps/internal_brain`)

| # | Item | Status |
|---|---|---|
| B1 | Per-stage timings in `answer.released.stage_ms` | **done** (e5406b9) |
| B2 | `GET /audit/stats` (compliance, ledgered, counts only) | **done** |
| B3 | `GET /audit/journey/{req}` (compliance, ledgered, no answer text) | **done** |
| B4 | `GET /ib/trace/{req}` (own requests only; no considered set, no Gate-2 reasons) | **done** |
| B5 | `GET /ib/graph?focus=&q=&hops=` permission-aware subgraph (Gate 1 readable set + live Gate 2 on the returned documents, like the map); nodes = documents, containers, people, linked objects; edges = contains, authored, links, mentions; capped, with "+N more" counts that count only permitted neighbours | todo |
| B6 | `GET /ib/doc/{id}`: passage, version, lineage (connector, last sync, authz check) for a document the asker can open (Gate 1 + Gate 2 live), else the same 404 | todo |
| B7 | `scripts/seed_demo_activity.py`: warm, labelled replay history | todo |
| B8 | `scripts/eval_summary.py`: RESULTS.md numbers to JSON for the analytics page; re-run the eval after the pipeline edit | todo |
| B9 | Audit time windows anchored correctly for the seeded world (the "last 30 days" issue from the inventory) | todo |
| B10 | Model id/status: `/health` reports the provider chain and whether the key is configured (never the key) | todo |

Tests: unit and route tests for each item. The full IB suite must stay green: 645 now.

## 6. Work split (parallel agents on disjoint files)

The coordinator (main session) owns:
- `package.json`;
- `tailwind.config.ts`, `app/globals.css`, `app/layout.tsx`;
- `components/shell/*` (the shell and mode switch);
- `lib/api.ts`, `lib/types.ts`, `lib/i18n/index.ts`;
- the proxy allowlist;
- `PLAN.md` and `DECISIONS.md`.

Agents may add files only inside their own folders, and request shared changes from the coordinator.

| Wave | Agent | Model | Owns | Starts after |
|---|---|---|---|---|
| 0 | Foundation | Opus | design tokens, primitives in `components/ui/*`, shell, mode switch, i18n scaffold, routes skeleton | plan review |
| 1 | Backend | Opus | B5–B10 + tests | plan review (independent of the web) |
| 1 | User mode | Sonnet | `app/(user)/*`, `components/user/*`, `lib/i18n/user.*` | Wave 0 |
| 1 | Graph | Opus | `app/dev/graph/*`, `components/graph/*` | Wave 0 + B5/B6 contract |
| 1 | Engine | Opus | `app/dev/brain/*`, `components/engine/*`, `app/api/walnut/*` | Wave 0 |
| 1 | Analytics + Mission control + Journey | Sonnet | `app/dev/(page, analytics, journey)/*`, `components/analytics/*`, `components/journey/*` | Wave 0 |
| 1 | Audit + Sim + Trust | Sonnet | `app/dev/(audit, sim, trust)/*`, `components/audit/*`, `components/trust/*` | Wave 0 |
| 2 | Tour + Presenter + Compare | Sonnet | `components/tour/*`, `components/presenter/*`, `app/compare/*` | Wave 1 |
| 2 | Rubric deliverables | Opus + Sonnet | `docs/finals/rubric/*` | rubric research |
| 2 | Tests | Sonnet | `tests/e2e/*` (update the 38, add new ones with axe) | Wave 1 |
| 3 | Review | Sonnet, Haiku, Opus, Fable, Codex | read-only | build complete |

Codex is used for the plan review and the final review only (its usage is limited). If it hits a limit,
work that doesn't depend on it carries on.

## 7. Demo story (draft; the rubric research refines it)

| Time | Beat | Screen | Rubric |
|---|---|---|---|
| 0:00–0:20 | Hook: "Every fintech wants an AI brain. None can let it read everything." | Title in the app | Story, Impact |
| 0:20–1:20 | Jordan asks the DB migration question. A cited answer from 3 platforms arrives; click `[2]` and the passage is highlighted, checked live | User mode | AI, HCD, UX |
| 1:20–2:00 | Same question as Ken (contractor): the polite constant refusal; then the Q3 breach report real vs made up, identical | Compare | Responsible AI, Innovation |
| 2:00–3:00 | Switch to Developer mode: the journey waterfall of Jordan's answer (Gate 1, Gate 2, Hy3, grounding, 3 sealed records); the graph grows from the citations; View as Ken greys the private channel | Dev | Technical, Innovation |
| 3:00–3:40 | Maya: analytics (funnel, 0 leaks / 5,958), then Verify chain → green; the tamper demo flips it red | Dev analytics/audit | Feasibility, Responsible AI |
| 3:40–4:20 | Tencent live: the same question from WorkBuddy (MCP connector), Hy3 named on the answer; CodeBuddy built and reviewed it | WorkBuddy | AI Interaction |
| 4:20–5:00 | Business: 7 min/week break-even, US$1.3M/yr base case, pilot plan; close line | Title | Impact, Feasibility |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Hy3 not activated in time | Honest extractive fallback; the label says so; one Hy3 answer recorded the moment it works |
| Venue network | Laptop runs everything; hotspot only for TokenHub; backup video; presenter reset |
| Too much scope | Wave 0 and the User-mode answer path are P0; Engine restyle and Trust centre are P1; artifacts and voice are P2 |
| A rebuild breaks proven flows | The 38 e2e tests are kept or ported; backend suite green on every commit |
| The UI leaks counts or denials | Rule 4 in every agent brief; a review item; e2e asserts on the refusal shape |

## 9. v2: changes after the five plan reviews (30 Oct, 05:30)

Reviews: [Sonnet](reviews/plan_sonnet.md) · [Haiku](reviews/plan_haiku.md) · [Opus](reviews/plan_opus.md) ·
[Fable](reviews/plan_fable.md) · [Codex](reviews/plan_codex.md). Each accepted change is a decision in DECISIONS.md.

**Demo story:** replaced by Fable's run sheet (reviews/plan_fable.md §3.2). It opens live on Compare
(Amy vs Rachel on Velquarry), then Ken: real vs made-up, identical. Then the passage checked live, the
journey (as Maya), and tamper on a sandbox copy. Tencent live through WorkBuddy at 2:35, then proof,
business and the close. Tagline: "Answers you can prove."

**Priorities (cut line Sat 31 Oct 22:00):**
- **P0, the demo path:**
  - Presenter mode with a Stage theme;
  - Compare with "same question / same person";
  - Ask and answer, Sources sheet;
  - Journey as compliance;
  - Audit with Verify and sandbox tamper;
  - the WorkBuddy beat and the Hy3 activation;
  - B9 audit clock;
  - a fresh eval re-run;
  - axe on every page.
- **P1:**
  - the graph: Sigma galaxy plus one-hop focus, and View-as as two independent graphs side by side;
  - Analytics and Mission control;
  - the Trust centre;
  - 简体中文 for User mode;
  - History and Settings;
  - SSE stage events.
- **P2:** `/dev/brain` engine embed (thin, compliance or Jordan only, read-only allowlist), the tours,
  cmdk extras.

**Security changes (Codex and Opus):**
- `/ib/trace` becomes a public-safe receipt:
  - citations re-authorised live; no `released`;
  - coarse stages without ms; total latency rounded; no global seq;
  - ownership-constrained lookup; ledgered.
- Full ms waterfalls live only in the compliance journey.
- One version-bound read-through helper for graph, doc and trace.
- The graph is an induced graph of authorised documents only, with no hidden-neighbour counts.
- "View as" is two independent graphs side by side; no union, diff or greyed placeholders.
- Stats and journey failures are ledgered.
- The stats window is `since ≤ t < now`.
- Replay failures keep their replay label.
- A head-keyed stats cache.
- The `/api/walnut` proxy has a closed GET-only allowlist: no `/brain/query`, no `/brain/ingest`. It is
  gated to sessions whose view the engine graph was built for (jdoe) or to compliance.

**New backend items:**
- **B11** `POST /audit/tamper-demo` (compliance): runs the `scripts/tamper_demo.py` scenarios on a
  temporary COPY of the live ledger and returns each verify report. The real chain is never touched.
- **B12** `POST /ib/ask/stream` (SSE): real stage events (accepted, gate1, gate2, sealed, composing,
  grounded); the answer text is sent only after grounding passes (D-08 holds).

**Graph stack:** Sigma 3.0.3 + graphology 0.26 + @react-sigma/core 5.0.6 for the knowledge graph (WebGL;
about 900–1,300 permitted nodes per person). React Flow 11.11.4 for small pipeline diagrams. Recharts
2.12.7 (already pinned).

**Human evidence:** a 5-person hallway test on Saturday, with the user and teammates (protocol in
rubric/research/B). We publish the real SUS and the fix log; nothing is invented.

**Mock contracts:** every new endpoint gets a mock handler, so pages work in `dev:mock` and against the
real backend.
