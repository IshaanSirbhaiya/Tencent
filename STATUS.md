# STATUS: where the build is

**Last updated:** 15 Oct 2026, 04:10 SGT (submission freeze 10:00 SGT; deadline Fri 16 Oct 23:59 SGT).

This branch is Walnut's entry to the Tencent Cloud AI CAN DO IT Hackathon (FinTech track, Aspire):
the **Internal Brain**. Start at [INTERNAL_BRAIN.md](INTERNAL_BRAIN.md). Everything below the line
further down is the earlier Walnut record, kept as history.

## Done

| Area | State | Evidence |
|---|---|---|
| Internal Brain service (two access checks, grounding, write-ahead audit, one response shape) | Built | `databridge/apps/internal_brain/` |
| Web app: ask, map, compare, audit, sim, guided tour, info pop-ups | Built | `databridge/apps/internal_brain_web/` |
| Original Walnut screens on Company A (graph, brain, evidence, ontology, demo tour) | Running on the same data | `databridge/apps/frontend/`, `scripts/run_walnut_original.sh` |
| WorkBuddy connectors (ask as a person; compliance audit) | Built and shown in WorkBuddy | `docs/tencent/workbuddy/`, `submission/02_workbuddy/` |
| CodeBuddy sessions (6) with saved transcripts | Done | `docs/tencent/evidence/`, `submission/01_codebuddy/` |
| Leak evaluation | 0 leaks in 5,958 adversarial requests; 0 differences in 400 pairs; 0 injection failures; 100% recall | `databridge/evals/internal_brain/RESULTS.md` |
| Automated tests (Internal Brain) | 634 passing | `databridge/apps/internal_brain/tests/` |
| Reviews | Codex tracks 1–6, CodeBuddy security review, Claude Opus/Sonnet/Haiku rounds; every finding fixed or explained | `docs/tencent/SECURITY.md` §9–§13, `docs/tencent/reviews/` |
| Original-code share | 93.2% of today's code lines are unchanged Walnut | `python databridge/scripts/measure_original_share.py` |
| Deck (13 slides, PDF + PPTX) | Done | `submission/07_deck/` |
| Demo video (silent, ~5:50) + voice-over cue sheet and subtitles | Done | `submission/05_video/` |

## Waiting on

| Item | Owner | Note |
|---|---|---|
| Tencent Hy3 through TokenHub | Tencent (account verification) | Until approved the demo answers by quoting sources (same checks). Switch: `IB_FORCE_OFFLINE=0`. |
| Lighthouse server | Tencent (sold out) | Optional; local run is the documented path. |
| Voice-over, Miora cover, form submission, teammates' confirmation | Ishaan | After the freeze |
| Making the repository public (history clean-up first) | Ishaan's OK | |

---

# Earlier Walnut status (history, 16 Aug 2026)

> Read `CLAUDE.md` first (working rules), then this, then `PHASE_PLAN.md`.
> Design authority: `Foundational Research/design/00_SPINE.md` (D1–D14, F1, O1–O3).
> Living record: `wiki/`. Reviews: `wiki/40-reviews/`. Submission: `GOA Submission/`.
>
> **Last updated:** 2026-08-16, end of a very large session. Three agents still in flight (§4).

---

## 1. What this is, in one paragraph

Walnut is **general AI + agent infrastructure**. What is sold is the **evidence substrate**: every
value carries a re-executable proof of where it came from, and anything without one is refused at
construction. Healthcare, finance and government are peer **packs** — the engine knows no domain
(`test_no_domain_branching` enforces it). The graph, the adapter fleet, the orchestrator and the
materialiser are how the substrate is delivered, not what is sold, because each is independently
commoditised as of Aug 2026 (spine §1).

**As of today the core loop actually runs end to end**: a question → the orchestrator picks sources
from their own `describe()` → each adapter's `AdapterShell` proposes one `CapabilityRequest` →
`PolicyEngine` admits or refuses → the Core reads → grounded cells with pointers come back, with
every step recorded on `/trace`.

## 2. Test counts — measured, not estimated

| Package | Tests |
|---|---|
| `packages/adapter` | **668** (was 555 this morning) |
| `packages/graph` | **581** (was 441) — clean, 0 failures |
| `packages/agentdef` | **128** |
| `packages/orchestrator` | **11** (new package) |
| `packages/materialise` | **126** |
| `packages/intake` | 713 |
| `packages/outbox` | 127 |
| `packages/core` | 222 |
| `packages/convert` | 539 |
| `apps/gateway` | ~506 |
| **repo total, last clean measure** | **3,813 passed / 3 skipped / 0 failed** (6m03s) |

Run as `python -m pytest -q -m "not integration"` from `databridge/`. **The marker is required** —
a plain run hangs on an Ollama test. Re-measure on a quiet tree: the total above predates today's
+113 adapter tests and the two new packages.

## 2a. NEW WORKSTREAM (2026-10-01) — MCP-ingested knowledge graph

> **Roadmap + every known gap and finding: `ROADMAP_KNOWLEDGE_PLATFORM.md`** (Phases A–E). Founder
> instructions: unwire hard-coded data (never delete it); Codex = Sol per track + one cross-file Astra
> run at the end (Astra currently unreachable on both the ChatGPT login and the API key).

**Founder decision:** ingestion is via MCP (connect any server through MCPJungle or directly; Walnut
crawls and extracts on its own). Sources: files/folders, a database, Google Drive (SharePoint
deferred). `/graph`, `/evidence`, `/intelligence`, `/brain` run on **one shared graph store**;
`/ontology` (pack `workspace`) is the closed vocabulary. Design guide:
**`KNOWLEDGE_SYSTEM_PRINCIPLES.md`** (repo root) — read it before any ingestion/retrieval work.

**In flight — batch 1 (5 parallel agents, disjoint files):** A shared store + closed vocabulary
(main.py, brain_server.py, extractor.py) · B `packages/mcp_ingest` (new) · C graph traversal/UX
(graph_routes.py, components/graph) · D generic provenance + intelligence (evidence/intelligence
routes + pages) · E infra + synthetic `workspace_demo` corpus + `packs/workspace` + launcher.
**Build plan: `BUILD_PLAN_KNOWLEDGE_PLATFORM.md`** (contracts §5 are frozen; change log §12).
Users define **adapter agents** (manage sources) and **agents** (automate work across sources) in
plain English, on demand or as automations; each agent read-only or read-write with per-action
automation mode. Spec contract landed: `packages/workflow_spec` (17 tests, green).
**In flight — batch 2a (5 more agents):** G `packages/retrieval` (+ harness embeddings) · H
`packages/agent_runtime` · T `packages/automation` (English compiler + trigger engine) · M
`packages/adapter_agents` · O `/ontology` as closed vocabulary.
**Landed:** E (infra/corpus/workspace pack, 21 tests), C (graph traversal, 48 tests), M
(adapter agents, 31 tests), A (shared store + closed vocab; graph 616 passed/1 skipped).
**Integration checklist (coordinator):** (1) register `workflow_spec, mcp_ingest, retrieval,
agent_runtime, automation, adapter_agents` in root `pyproject.toml` pythonpath/testpaths AND
`serve.py _PACKAGES`; (2) A reported gateway **530 passed / 33 failed** mid-batch — re-run on a
quiet tree and attribute each failure to a change before calling any "pre-existing" (rule 7);
(3) `docs/hackathon/SUBMISSION.md:97` quotes "20 Playwright tests" — set from the final spec count;
(4) `test_gateway::test_llm_detect_and_context` asserts the clinic literal header in
`llm.build_context` — make header pack-derived; (5) gateway/graph/mcp suites collide on `conftest`
when run in one pytest call — run per directory or fix; (6) root `conftest.py` now defaults
`WALNUT_GRAPH_DB_PATH=:memory:` + clinic seed on for tests; (7) `start-walnut-kg.ps1` must set
`WALNUT_AGENT_PURPOSE=workspace.agent_run` and
`WALNUT_AGENT_GOLDEN_SET=packs/workspace/evals/agent_contract.jsonl` (coordinator added
`packs/workspace/purposes.yaml` + the shared answer-contract golden set, 10 records, verified to
load); (8) example workflows go in `packs/workspace/workflows/`, NOT `agents/` —
`agentdef.load_pack_agents` reads every `*.yaml` in `agents/`; (9) agentdef should export a
public strict-YAML declaration loader (agent_runtime imports private `_yaml_strict`); (10) DB
write actions need S2 to expose a separate write-capable Postgres connection.
**Also landed:** H (agent runtime, 33 tests), T (English compiler + triggers, 91 tests), O
(`/ontology` owner editor, 42 tests). B (`mcp_ingest`, 57 tests; screen reproduces all 15 expected decisions), D (generic
`/evidence` + `/intelligence`; shared `subject_exposure.py` rule). Coordinator: `workspace`
`about` edge narrowed to non-person targets (else every document is held for human confirmation);
new packages registered in root `pyproject.toml` + `serve.py`; shared Docker stack (demo Postgres
:5433, MCPJungle :8080) left UP for batch 2b.
**Measured 2026-10-01 (all 25 suites, run per directory, `-m "not integration"`): 4,208 passed,
3 failed** → (a) `test_no_domain_branching` — caused by the coordinator's own `ontology_state.py`
naming a domain folder; fixed (structural root lookup), green; (b) SUBMISSION Playwright count —
now "29 … 20 asserted against real servers; 9 not yet run", green; (c)
`test_the_four_step_sequence_verified_then_drift_material` — red since 2026-08-16 (§3.0), assigned to
R. Retrieval (26) and harness (32) were measured while G was still editing — re-run after G lands.
**In flight (2026-10-01, late):** F6 (t07–t09 fixes), P (latency + core patches), AA (adaptive
adapter agents) + Phase C UI: C1 Connect a source, C23 agent + adapter-agent builders (replace
`/builder`), C4 graph trail/agent traversal/person candidates + honest `/trace` `/ontology`
`/materialise`, C5 legacy fakes removed + hard-coded data unwired (never deleted). UI agents run
`tsc` only; coordinator runs the single `npm run build`. **Phase B (end-to-end proof) starts when P
lands.** Also running: **MAT** — Layer 3 wiring (founder: "wire in existing ones, more shapes
later"): PlatformReader over shared graph/facts/passages, materialiser attached via
`platform_runtime`, `POST /materialise` jobs + receipts, the four built shapes tested; Data Agent UI
tab after C23 finishes (same page).
**Codex batch 2 DONE (2026-10-01):**
- **Result:** 12/12 reviews verified genuine; none clean. Round-2 found residual Criticals, incl. a
  subject fuzzy-merge path and same-name subject collapse in `extractor`, policy-off bypass and a
  forgeable principal in adapter-agent drift, host code execution via command sources, retrieval
  read paths writing and non-path doc ids, and graph routes emitting values without pointers.
- **Fix wave running:**
  - **FX1** main.py + platform routes
  - **FX2** real connections
  - **FX3** retrieval
  - **FX4** adapter agents r2
  - **FX5** extractor + graph routes r2
  - **FX6** ontology r2
  - **FX7** evidence + intelligence r2
  - **FX8** builder/sources/graph UI

  Brief: scratchpad `codex2/FIX_BRIEF.md`; dispositions go to `wiki/40-reviews/R-019-*.md`.
- **Also landed:** W2 (English agents now run), MAT (Layer 3 wired; only tabular + chunk_corpus
  built), FED (delegation), AA.

**Now running (latest):**
- **E2E** (Phase B proof; findings → `wiki/40-reviews/E2E-2026-10-01.md`)
- **MAT** (Layer 3 wiring) and **FED** (delegated cross-source queries)
- **W1** (drift hooks into ingest)
- **W2** (compiler↔runtime naming unification; HIGH bug)
- **W4** (auto-drafted DB dialect templates + combined profiling)
- **Codex batch 2** (Sol, 3 at a time, verified): round 2 on t04–t08 + first review of retrieval,
  real connections, platform routes, main.py, sources/graph UI and builder UI

Queued:
- the Data page, once MAT lands;
- Codex on mcp_ingest core / automation / agent_runtime / workflow_spec / materialise / federated,
  once their agents finish;
- one Astra run at the end (blocked: model not accessible).

**Update (P landed):**
- **Ingest speed** (fake-latency bench): 39 s → 22 s cold, 33 s → 0.74 s warm, graph writes 8.8 s → 0.1 s.
- **Writer role applied** to the demo DB: `walnut_agent_writer` INSERT-only on `follow_up_tasks`, verified.
- **`larkspur_db_writer` added** to infra; ingestion REFUSES it (`propose_plan` and `run_ingest`), proven.
- **Drive infra entry updated**; the Drive profile is activated by P.
- **Phase B (E2E) DONE 2026-10-01 — does NOT pass.** 35 findings (3 Critical, 11 High, 13 Med, 8 Low) in `wiki/40-reviews/E2E-2026-10-01.md` + `databridge/var/e2e-20261001T0356/findings.json`. Criticals: F-01 compiled agents cannot run (declaration builder signature mismatch); F-02 approved write recorded 'executed' but nothing written (postgres-mcp errors as text, args unchecked vs input schema); F-03 19 dangling DB edges after merge (report says 0). Also F-05 `NodeKind.PATIENT` in engine = rule-5 violation; F-13/14 live reads skip screen + masking; F-21 extraction ran on gpt-4o not Sonnet. Next: E2E fix wave AFTER FX1–FX8 land (files overlap), grouped by file ownership; founder to approve launch. Delegation (`packages/federated`) is NOT covered by E2E and NOT Codex-reviewed; /brain UI does not route to it.
- **E2E fix wave launched (founder-approved 2026-10-01):** EF1 agent_runtime + automation declaration/classifier (F-01, F-02, F-06, F-07, F-10, F-13, F-15) → `R-020-EF1`; EF2 brain_server + llm.py (F-04, F-05 generalise PATIENT caveat to pack subject kinds, F-21) → `R-020-EF2`; F-14 + F-11 sent to FX4, F-14 + analytics dangling-edge tolerance sent to FX7. **Queued:** F-03 dangling DB edges (mcp_ingest tabular) — starts when W4 lands (file overlap). Each lands → scoped Codex review → fix agent (pipelined). Re-run `scripts/e2e` + `tests/e2e` after the wave.
- **FX8 landed** (R-019-ui-builder-sources-graph: 18 fixed, 1 partial FP; tsc clean; 21 new @unit Playwright tests). Round-3 Codex on the two UI tracks running (pipelined runner: scratchpad `codex3/`, `MANIFEST=<name> bash run_parallel.sh`). FX8 backend handoffs: #1 resume/draft-sync → FX4; #4 answer.py ref→pointer map → EF1; #6 pointerless attributes in graph_routes → FX5. **Queued with F-03 (after W1/W4 land, mcp_ingest):** #2 remove `auto_approve` or require a person's approval (plans.py:775, mapping.py:466); #5 approve_plan refuses plans calling write tools; #3 reject+redact literal secrets at source registration (sources_routes register, connections.py).
- **Landed since:** C1, C4, F6 (all 9 review tracks now fixed), P.
- **Still pending:** pin postgres-mcp.

~~**Pending coordinator steps (blocked on P):**~~ add `larkspur_db_writer` (from
`profiles/postgres/write_connection.example.yaml`) to `infra/mcp_servers.yaml` and apply
`profiles/postgres/agent_writer.sql` to the demo DB ONLY after `connections` gains `purpose:
agent_write` and ingest refuses it; pin postgres-mcp's version. Also after P applies S3's
`tests/fixtures/gdrive/core_extension.patch` (+ camelCase write-tool fix): rename the Drive profile
from `.pending`, and update the `google_drive` infra entry (scope.roots `${WALNUT_GDRIVE_FOLDER_ID}`,
extensions, tools_hint) as proposed in S3's report — roots expansion needs the patch first. Landed since: S4 (147 mcp_ingest
tests), S2 (real postgres-mcp end to end; 163 passed / 6 skipped), F3 (t04: 10/10 real, fixed; 51
tests). Models switched to Opus 5.5 / Sonnet 5.5 with `drop_params` (Claude 5.5 rejects temperature=0).
**In flight — batch 2b (7 agents):** S1 files (PDF/DOCX via MCP) · S2 Postgres (facts, schema
cards, write connection) · S3 Google Drive · S4 generic MCP + MCPJungle + session pooling · P
pipeline latency + core gaps · R orchestrator/verify//brain on real connections · U-api routes +
TriggerEngine hosting. G (retrieval) still finishing. UI refactor deferred until after Codex
reviews (founder).
**Coordinator fix:** a kind deprecated on `/ontology` was
still offered to extraction (deprecation lived only in the gateway state file) — new
`databridge_graph/ontology_state.py` is the single resolver/reader, `extraction_vocabulary`
withholds currently-deprecated kinds of bound packs; 3 tests with reactivation control; ontology
routes 42/42 still green.
**Next:** integrate batch 1 + 2a (register new packages in root pyproject AND serve.py `_PACKAGES`
together — a test asserts they agree) → batch 2b: S1 files · S2 Postgres · S3 Google Drive · S4
generic MCP/MCPJungle (parallel), U routes + builder UIs, R orchestrator/verify//brain on real
connections, X example agents + demo + E2E → batch 3 sequential Codex reviews.

## 2b. GOAL RUN (founder, 2026-10-01) — "a completed, working system"
Scope: fixes + E2E works + ONE new page (Activity/transparency, extends /trace — rule 9) + /brain routes cross-source questions to delegation. Everything else is done.
Codex: ~2 rounds per part, every output verified, every finding fixed/dismissed with reason. **REVISED 2026-10-01 (founder):** every area gets EXACTLY 2 reviews, then stop — no round 4 anywhere; the round-3 findings already in hand (UI, retrieval) are fixed but not re-reviewed. Priority order: areas at 0/1 first. **E2E re-run as soon as EF1+EF2+EF3 land** (not at the very end), then again after the last fixes. Pipelined: a track's review starts when its fix agent lands; its fix agent starts when the review verifies.
Runner: scratchpad `codex3/` — `ROUND=<n> [DISPO_<track>=<file>] python make_bundles.py <name> <tracks…>` then `MANIFEST=<name> GROUP=2 bash run_parallel.sh`.
Parts & rounds (tick as done):
- [ ] federated (FED)  f1 → FXF-c fixed 14 + 2 partial (R-021-federated; federated 19→43 tests, routes 5) → f2 11/11 fixed by FXF2 (federated 43→65, routes 5→10). DONE = 2 reviews. `/federated/ask` now needs WALNUT_FEDERATED_GRANTS (principal→connections|*) for non-dev users; person anti-links refused (planner offers candidate semi-link); structural candidate flags (answer.py side → RTF). Founder: #1 per-principal source directory for /federated/ask; #16 per-call deadlines in facts store + gateway   | also a live E2E of /federated/ask
- [x] materialise: 2 reviews; FXM2 fixed 9 + 1 partial of f2 (224 tests; 14/14 mutations killed; exact structural pointers, WAL-aware pins, plans must pin all sources, captured text re-derived + get_meta, leak check covers Absent). Founder: packs declare facts identifier classes (F7)? Absent in prior-row/count columns report-only (F5)? Cross-track: count_query facts_dir → FXI4; extracted-text hash → FXI2. History: f1 → FXM-c fixed 11 + 1 FP (R-021-materialise; 159→203 tests; principal isolation, identifier disclosure needs separate approver [none configured → refused], pins enforced, 409 DATASET_WITHHELD on failed/missing pointer or leakage checks, plan_verification) → f2 QUEUED (Codex limit). Founder/DPO: who may approve identifier disclosure (platform_runtime ~517 `identifier_disclosure_approver=`). BREAKING: Materialiser.run(plan, *, by).  | also a live E2E of a tabular + chunk_corpus build
- [x] Data page         2 reviews; f1 9/9 + f2 6/6 fixed (16 @unit, tsc clean). Server leakage-gate → FXM-c.
- [x] UI builder + sources/graph  3 reviews; r3 16/16 fixed by FX8b-c (38 @unit, tsc clean). NO round 4. Backend halves with EF1 (done), FX4-c, EF3-c, FX5-c; gateway secret validation → EF3-c (c) + FX1-c route. Backend halves: ungrounded-approval → EF1; resume/sync gated on approved plans → FX4; mapping auto_approve + unclassified tools → EF3; structural graph values w/o pointers → FX5 (sent)
- [x] workflow_spec + workspace pack: 2 reviews; WSF fixed 16/16 (+F-28; workflow_spec 72→129, pack r021 111). save_to = declared action only; deep-immutable spec; `unknown` provenance. Founder: D1 derivations coverage for active specs; D3 rename `plans` or carry plan digest+approval id (breaking).
INT1 DONE (8/8: frontend auth headers everywhere; 58 ungated routes gated + route-list gate test; null labels; `unknown` producer; tokens from record; contract spec_digest; get_meta readers; decision_refused in Activity; gateway 849). INT2 DONE (federated live reads screened before capture; AA requires issued Approver). Left: retrieval/__main__.py records corrupt capture skips; /evals/* gating → FXP; AA live test DSN fixture → EF3-c.
**FINAL INTEGRATION PASS list (after all agents land):** frontend: graph-api.ts `label: string|null`; produced_by `unknown` badge; `activity_routes.run_item` read record tokens/cost_usd; adapter_agents registry.spec_digest → contract `spec_digest` (digests change once); E2E `s5_golden.py` assert new `outcome` fields + re-bind agents to new golden digest; docs: BUILD_PLAN §5.6 larkspur-db→larkspur_db + illegal `source.query` action; SUBMISSION.md Playwright figures; frontend /evals page handles 404 honestly; main.py: FXB2 items (R-020 'Codex f2 (final)'); frontend other_label nullable; **gateway must pass principal OBJECTS to mcp_ingest approvals** (FXI3: sources_routes.py:708 approve_plan(principal=…, vouch_read_tools=…); platform_runtime IngestFacade.approve_plan(principal=…); test_platform_routes FakeIngest + monkeypatch plans._live_tools + tools_digest) — likewise for mapping/dialect approvals after FXI4; platform_runtime.py:215 use runner.run_dir_for (FXI1); adapter_agents approve_adaptation for a plan must call plans.approve_plan(principal=) (FXI1 — else next sync refused); gateway sources_routes.py:723 + platform_runtime.approve_mapping pass principal= (FXI4); adapter_agents adapt.py:1163 must call plans.approve_plan (not write YAML) + adapt._apply approve_mapping(principal=<verified>) — DEFAULT CHOSEN: auto_additive = delegated approval by the principal who activated the agent (recorded delegated_by + policy digest), additive in-scope changes only; founder may overrule; postgres.sql.yaml add read_only_session; normalise CRLF→LF in plans.py/build.py; remaining fails to chase: test_aa_drift_tabular KeyError 'region', test_s1_integration_files pointer KeyError 'start'; D2 runtime check deployment screen pack == spec.screen.pack.
- [x] agent_runtime: 2 reviews; RTF fixed 14/14 + 4 coordinator items (runtime 116→179; 30/30 mutation reversions fail their tests). Approver only via registered authenticator; CLI approve needs --dev-os-principal; masked-only captures; decision_refused logged. Founder: ground every capitalised name in drafted prose (would refuse most free-text drafts)? Follow-ups: federated run_live screen + AA is_issued_principal → INT2; ToolResult.structured → EF3-c; Activity decision_refused → INT1.
- [x] automation (see EF4). Earlier history: EF1-c LANDED (9/9 fixed, R-020-EF1; runtime 86→106; shared classifier classify.py; citation_refs). Codex review DONE (runtime 8C/5H/1M; automation 3C/5H/3M) = 2nd review for both → split: EF4 = automation DONE (9 E2E + 11/11 Codex, automation 157→188; R-020-EF4. Founder: separate candidate-selection step vs write approval; per-request internal tool grants; run every busy slot vs coalesce. Follow-ups: activity_routes read record tokens/cost_usd; `aliases: [crm]` on larkspur_db → EF3-c connections) ; RTF = agent_runtime review + F-27,F-29,F-30,F-33 hdr,F-34 — RTF waits for EF4's release message (forward it).
  - Founder decision (EF1): 'executed' = non-error reply captured; postgres-mcp INSERT returns 'No results' so no proof of effect — add a read-back query after writes?
- [ ] mcp_ingest: EF3-c DONE (13 fixed incl. F-03 dangling edges re-pointed + truthful count, F-18, F-23, F-08+aliases, F-16, F-31, F-33, auto_approve inert, fail-closed tool classes + vouch_read_tools, secret_scan; mcp_ingest 390/2 skip; live PG green). F-19 deferred (HMAC key holder; hash raw email pre-mask?; where confirmed links live). Codex f1 DONE (runner 12, build 14, sources 14, tabular-drift 19 = 59; verified) → FXI1 DONE (11 fixed + 1 FP hardened; 31 tests, 12/12 reversions caught; binding.json resume refusal, append-only attempts/, safe paths, scrubbed ledgers, crash-safe outbox, per-source lock) ; FXI4 DONE (19/19; mcp_ingest 514/0; 14/14 mutations; mapping+dialect approvals principal-only w/ digest log, read-only probe allow-list, held prose never published, writer SQL → pack YAML) ; FXI3 DONE (14 + D4 + redact; mcp_ingest 520/0; 16/16 mutations; approve_plan principal-only + live tool re-list + re-classify + append-only log; package-level command/SSRF policy for origin=remote; per-connect DNS/redirect recheck; list_changed journalled). Founder: refuse ${VAR:-default} passwords once manifest defaults removed?; register_connection default origin; secrets via MCPJungle only if it resolves ${VAR}. ; FXI2 DONE (14; mcp_ingest 521/0; 24/24 mutations; review rows need authenticated approval of exact content; overrides via approve_override log; credential rules engine-enforced; anchors cite listing records). Founder: who holds/rotates a key to sign stored approvals (plans/mappings/overrides are append-only lines anyone with write access could add)?; hold rows of PK-less tables? → **INT3 LAUNCHED (backend integration: principal objects everywhere, delegated auto_additive, FXI2 cross-track, main.py FXB2 items, CRLF, D2, chase failures, all suites)** → then mcp_ingest Codex f2 (brief scratchpad codex3/MI_BRIEF.md; shared approval helper plans.require_authenticated_approver by FXI3) → f2.
- [x] brain_server + llm.py: 2 reviews; FXB2 fixed 7/8 of f2 (#4 doc identity = founder). Healthcare seed moved to packs/healthcare/demo_seed.yaml via graph pack_seed.py; no pack text in the system prompt; id-addressed subjects served as candidates. main.py follow-ups (demo_seed_sources, extraction_route=, dropped_sentences/subject_mentions, llm_routing) → final integration pass. History: FXB fixed f1 (10) + llm r2 #1,#3 (mcp_server 107→127; guard 11→25 AST-based; GroundedNarrator drops ungrounded answers; subjects never selected by name; read-only reader mode=ro). llm.py = 2 reviews DONE. brain_server f2 RUNNING. Deferred: seed hook in packs/graph (healthcare seed); doc-identity rule. main.py bits → FXP.
  - earlier: EF2-c LANDED (F-04, F-05 generic subject caveat, F-21; mcp_server 98→107; guard now scans apps/) → Codex review RUNNING (brain_server 1st; llm.py 2nd) → fix → 2nd for brain_server.
  - Doc drift: packages/intake test_submission_figures fails because the e2e spec count grew (57→78+) — update docs/hackathon/SUBMISSION.md figures in the final pass.
  - EF4 released agent_runtime to RTF (F-27/29/30/33/34 DONE by EF4; runtime 116/116).
- [x] main.py + platform routes: 2 reviews; FXP fixed 17/17 of r2 (+6 coordinator items; gateway 969/0; 28/28 mutations killed). /evals/e1 + /evals/brain unwired (404) → frontend /evals page needs honest 'not available' (final UI pass). Founder: D1 LLM narration 'grounded w/ stated scope' vs per-claim pointers (llm.py contract change); D2 command profiles vs exe allow-list; D3 single-process gateway vs file lock per source. D4 connect-time DNS/redirect → FXI3. History: FX1-c DONE (15 fixed, 2 FP, 1 partial; gateway suite 835/0; every /brain + /convert + /artifacts gated; command sources off over HTTP unless allow-listed; activate route; register_gateway_authenticators()) → Codex r2 QUEUED (07:13). W1 boot hook wired (platform_runtime install_adapter_hooks) + W1 AA diff applied (AA 94/94). Activity routes REGISTERED in main.py by coordinator after founder's explicit 'yes add it' (/activity, /activity/automations, /activity/runs/{id}; fail closed like every disclosing route; activity tests 16/16). INT1 launched: frontend auth headers (brain-api.ts, api.ts), export_routes auth + route sweep, graph label null, `unknown` badge, activity tokens, adapter spec_digest. Founder: demo command allow-list (npx = any code); revoke mapping approvals on connection replace?; per-claim pointers in llm.py (contract change).
- [ ] FX1–FX7 tracks — areas already at 2 reviews: no r3
  - [x] extractor + graph routes: 2 reviews; FX5-c done (graph 655→682, graph routes 56→71; subjects never merged, every returned value pointer-backed/withheld/structure). Follow-ups: frontend lib/graph-api.ts `label: string|null` (lines 73,193) → final UI integration pass; build.py id_only+char_offset → W1-c. Founder: `producers` table so each graph version keeps its run_id?
  - [x] ontology: 2 reviews; FX6-c done (10/11 fixed, ontology tests 65→93; guardrail fails closed, crash-consistent approve/rename, fsynced audit). Founder #9: auto-approve toggle owner-only (require_owner + WALNUT_ONTOLOGY_OWNERS)? Watch: intake /ask identity tests flapped 401 vs 422 during concurrent auth edits (FX1-c) — confirm in final full run.
  - [x] evidence + intelligence: 2 reviews; FX7-c done (15/16 + F-14 + F-03 analytics; ledger is the only release authority; as_of snapshots; editable text_sha256 no longer proof). Founder: must every producer (incl. /intake/upload) mint doc ids as hashed full path? Follow-up: text digest field on SourcePointer (packages/core).
  - [x] adapter agents + drift policy: 2 reviews; FX4-c done (9/10 + F-14, F-11, draft/resume, F-24, plan-gated sync; adapter_agents 94/94 incl. live PG). Gateway activate route + trust_principal_type boot registration → FX1-c. Founder #6: should always_ask be un-narrowable for removed/type_changed/tool_signature_changed too (workflow_spec models.py:421)?
  - [x] live connections (verify/orchestrator): 2 reviews; FXV fixed 11/11 of r2 (gateway 850→964; finance estate moved to fixtures/packs/finance/fixture_fleet.yaml — rule 5; reads need WALNUT_FEDERATED_GRANTS; writes need explicit entitlement; no false VERIFIED; errors → ERR- refs). Founder: one grant list for /federated + /orchestrate + /evidence/verify, or per surface? mutation also require WALNUT_DEV_AUTH?
  - [x] retrieval: 3 reviews; r3 14/14 fixed by FX3b-c (retrieval 43→58, harness 36→37). NO round 4. Founder: approval record retrieval verifies before admitting review-band content; multi-host builds in scope? (needs lease service)
  - FX3 retrieval LANDED (17/17 real, fixed; retrieval 26→43, harness 33→36; `R-019-retrieval.md`) → r3 running. Defaults taken (safe, revisit later): approved review-band overrides stay OUT of passages until retrieval verifies an approval record; no chunk-shrink floor. Cost: each publish re-reads the store's captures.
- [ ] ACT: FXA f1 HALF DONE — trace side fixed (owner-only traces, WALNUT_AUDIT_ROLES trace_admin, ledger-cover rule, bounded, identical 404s; trace tests 22→40). **activity_routes.py backend half NOT applied: permission classifier refused FXA's edit ('Modify Shared Resources'). Draft patch scratchpad/fxa/edit_act.py (untested). Needs founder permission.** Residual risk until then: /activity ownership fails open (cross-principal visibility). Founder: who holds trace_admin / activity_operator; scheduled-run traces admin-only vs owner= at 4 call sites.
  - earlier: ACT (Activity & trace) LANDED (3 tabs: per-request / timeline / automations + delegation tree; 15 backend tests, 8 @unit, tsc clean). main.py registration → FX1-c. Package gaps sent: refused approvals logged → RTF; plan assumptions in federated run record → FXF2. Open: workflow agents have no owner; tokens from RunRecord (EF4 added) not yet read by activity_routes.run_item. Codex f1+f2 queued (after 07:13).
**W4 LANDED** (dialect draft→probe→approve; 8 templates: postgres verified, sqlite/duckdb tested, 5 drafts `unverified`; combined profiling 92→24 statements, 8.17→1.94 s; 24 offline + 3 live tests; mcp_ingest 286 pass/2 skip/5 load-flaky, pass alone). Open: crawl.py (W1) still resolves dialects by name, not the approved per-connection `<source>.dialect.yaml`.
EF3 LAUNCHED (tabular/plans/mapping first; runner/crawl/connections after W1 lands — message it). Queued agents: EF3 (mcp_ingest: F-03, F-08, F-11 part, F-16, F-18, F-19, F-23, F-31, F-33 report, auto_approve #2, approve_plan write tools #5, secrets #3) after W1+W4;
EF4 (automation compiler + agent_runtime lows: F-09, F-22, F-26, F-27, F-28, F-29, F-30, F-32, F-33 run header, F-34) after EF1;
BRAIN (/brain/query routes multi-source questions to /federated/ask + brain page shows table/citations) after FX1 + EF2.
Coordinator lines: main.py `register_activity_routes(app)` after FX1 lands.
Finish order (founder-agreed): (1) all offline suites IN PARALLEL per directory (minutes); (2) E2E re-run PARALLELISED (files+DB ingest concurrently, golden set concurrently, agents concurrently; timings measured per step) — target ~15 min vs 36; (3) fix → repeat both. Finish: npm run build once → launch → re-run scripts/e2e WITHOUT workarounds + FED + MAT scenarios → tests/e2e → fix what fails → re-run → final explanation (docs + wiki + this file).

**05:01 all 16 agents stopped (founder terminal interrupt); 05:08 relaunched as continuation agents (`<name>-c`) reading `scratchpad/resume/<NAME>.md` (original brief + edits + last notes) + `CONTINUE_BRIEF.md`; id map `scratchpad/resume/agents.tsv`. Killed Codex f2 workflow-spec/workspace-pack re-running. EF3-c still waits for W1-c before runner/crawl/connections.**

**05:37 Codex (ChatGPT login) hit its USAGE LIMIT until 07:13.** Verifier caught it (rc=1, 'usage limit' body) — f1-brain-llm and r2-n-real-connections NOT reviewed; re-run after 07:13 (or with the API key if founder approves). Review queue meanwhile: brain-llm f1+f2, real-connections r2, main+platform r2 (after FX1-c), materialise f2 (after FXM-c), mcp_ingest core/sources/drift f1+f2 (after W1-c+EF3-c), ACT f1+f2. Areas already at ≥2 (adapter agents, ontology, extractor, graph, evidence, intelligence, retrieval, UI, Data page, workflow_spec, pack, delegation, runtime, automation) get NO more reviews.

**~06:40 founder approved using the OpenAI API key for Codex.** Separate CODEX_HOME = scratchpad `codex_home_apikey/` (ChatGPT login in ~/.codex untouched; key piped from .env, never printed). gpt-5.6-sol works on the key. Run with `CODEX_HOME=<that> MANIFEST=… bash run_parallel.sh`. Batch api1 running: brain-llm f1, activity f1, real-connections r2, main-legacy r2, platform-routes r2, materialise f2.

**api1 batch DONE (6/6 verified genuine):** brain-llm f1 (10), activity f1 (8), real-connections r2 (13), main-legacy r2 (9), platform-routes r2 (8), materialise f2 (10). Fixers: FXB (brain_server+llm.py), FXV (verify/orchestrator fleets; finance-in-gateway rule-5), FXP (main+platform r2), FXM2 (materialise r2), FXA (activity; waits for INT1 release of activity_routes/components/activity). Still need a 2nd review after fixing: brain_server, activity, mcp_ingest (x2).

**~09:00 INT3 DONE — ALL OFFLINE SUITES GREEN** (each dir separately: adapter 668, adapter_agents 106, agent 300, agent_runtime 182, agentdef 128, automation 188, convert 539, core 222, evals 23 (2 live-Ollama deselected), federated 67, graph 824/1skip, harness 37, ingest 10/2skip, intake 712 + **1 fail = SUBMISSION.md Playwright count doc**, materialise 201, mcp_ingest 548/2skip, orchestrator 11, outbox 127, retrieval 63, semantic 32, workflow_spec 129, gateway 981, mcp_server 137, root tests 25/1skip, mcp_ingest integration 15/1skip (Drive OAuth)). Delegated auto_additive implemented. CRLF→LF in 136 files.
**mcp_ingest Codex f2 DONE (runner 15, build 7, sources 15, tabular-drift 18; verified) → FXJ1–FXJ4 final fixers (brief codex3/MI_BRIEF2.md; mutation-checked; delegation kept + hardened). After them: every area has 2 reviews.** **Running now:** E2E2 (parallel E2E re-run, no workarounds, + FED/MAT/adaptation/security; will message before `npm run build`); mcp_ingest Codex f2 (4 tracks); INT4 DONE (GET/approve/reject held adaptations, owner-only, 409/404/422; builder 'Held changes'; /evals honest; gateway 986, tsc clean). Open: agents with no recorded creator can't be decided from the app; rejection not final in engine (approve after reject allowed) — make rejection final? Frontend is free → E2E2 may build when it asks. Pending founder: Activity backend patch permission.

**~10:40 E2E RUN 2 DONE — still not passing.** 24 findings (0 Critical, 8 High, 9 Med, 7 Low) in wiki/40-reviews/E2E-2026-10-01-run2.md. Run-1: 25 fixed, 4 changed, 3 still failing (F-11, F-17, F-19). PASS: ingest, traversal, event trigger, adapter agent, timings, adaptation (delegated), security. PARTIAL: connectivity, answering, traceability, delegation, materialise, frontend (28/43 live specs). FAIL: pointers 100%, briefing, read-write loop. Wall ~7.7 min driving (vs 36).
Fix wave (brief codex3/E2E2_BRIEF.md): RTF2 DONE (N-05 refs→ptr ids; N-06 replayable derivations; N-11 schema-shaped args + failure cap + wrap-up; N-12 outcome block; N-13 five FP causes; runtime 217; 24/24 mutations), AUTO2 DONE (N-04 delegate agent compiles from the run's exact sentence; N-21; fed:<sha1> ids; automation 207), FED3 DONE (N-02 root cause: documents delegate never offered a row's own node / co-mentioned nodes; replay now returns C-1001 = 11100; zero-match links → coverage 'uncertain'; N-03 answers fed:<sha1>, carry links, @today; N-19 tokens; federated 87). E2E3 notes: synthetic data has no past-due open invoice on 2026-10-01 → ask with an explicit date window or 'open' status; customers must be re-mapped (business key), MAT3 DONE (N-07 model shown judged schema + logged near-miss repair + one redraft; offline planner asks PlanNeedsInput instead of guessing; N-08 <stem>.pointers.jsonl role 'pointers'; N-09 test-only withhold helper; materialise 256). E2E3: labelled request must name the outcome field; synthetic estate all recorded same day → 0 mature rows. Founder: unlabelled tabular extract shape (closed-vocab ADR)?, BRAIN DONE (/brain/query routes cross-source/aggregate → federated under caller grant, rules first then router; context now includes neighbour attributes w/ pointer tags, question-ordered attrs, deduped passages; x-3 refusal fixed; delegated-answer UI; N-15; N-18 agent owner recorded + approve/reject owner-or-operator (WALNUT_OPERATORS); gateway 1007); FXJ1 N-01; FXJ2 N-17; FXJ3 N-10,N-22; FXJ4 N-16 — FXJ4 DONE (18/18 f2 + N-16 business-key rule; mcp_ingest 654/0; subjects get no merge key; live run needs re-mapping of customers + re-approval of contacts/employees). AA3 launched (adapter_agents: _evolve columns, SEALED delegation record at activation, approved_drifts, rejection final). FXJ1 N-01 DONE. FXJ3 DONE (15/15 f2 + N-10 + N-22 + ClickHouse; 49 tests, 21/21 mutations; manifest DSNs plain ${VAR} + start script sets synthetic pw; WALNUT_SOURCE_REGISTRATION=operator; WALNUT_MCP_DECLARATIONS). FXJ2 DONE (7/7 f2 + N-16 business-key join both orders + N-17 + graph_ids; signed+chained override log (WALNUT_APPROVAL_KEY); mcp_ingest 666/0; 23/23 mutations). AA3 DONE (sealed hash-chained delegation record at activation, DELEGATION_NOT_SEALED; rejection final; adapter_agents 118/0; 14/14 mutations). SWEEP DONE (34 violations / 24 unsure / 11 unwired fakes all unreachable; today's new tests recall nothing; top: /intake silent gold fallback, clinic prompt in agent/frame.py, DATASET_TODAY fixed date, clinic wording in agent/render + intelligence CONSOLE_NOTES, packs.ts bundled fallback, connector catalogue in Python, hardcoded models in mcp_ingest/llm.py, /intake+/outbox registered for every pack). **HC1 DONE** (9 priorities; 19/19 mutations; /intake gold only when no model + labelled, model failure → 503; agent wording from packs/<pack>/agent_wording.yaml; deployment_today(); 'Registry unavailable' honest state; connector catalogue + monitors in pack YAML; /intake+/outbox mounted only when pack declares; models from models.yaml). Founder: audit #21 identity deny-list from pack YAML (union over packs)? **HC2 LAUNCHED** for the rest (frontend #14-16,18-19,22-23, card offline_gold, impact 'estimated', export_routes, mcp_server, dispatcher, test literals). Earlier: HC1 launched to fix them (packages/intake, agent, outbox, intake/intelligence/connector/outbox routes, mcp_ingest llm.py, packs.ts, brain-layout.ts). Roadmap: move packages/intake → packs/healthcare. **INT5 LAUNCHED** with the queue below + SWEEP violations (forward). **INT5 queue** also: /trace resolve ?run=; packages/agent 'clinic' wording under every pack (rule 5); add brain_routing task class to models.yaml; runner uses DocBuild.graph_ids; packages/graph keep label on merge + carry new canonical id + public _resolve_existing; start script + gateway conftest set WALNUT_APPROVAL_KEY(_FILE) for demo; ADR for node-kind id_pattern; owners must re-approve round-1 unsigned overrides; test_activity_routes 2 fails (federated records now carry tokens — FED3); s5_golden reads record.outcome; **(after FXJ2/RTF2/FED3/MAT3/BRAIN/AA3):** platform_runtime pass origin='remote', advertise=False (2 gateway fails); GET /sources shows connection_problems(); mapping.py + screen.py adopt hash-chained approval log; tabular/pipeline.py pass plan=; runner treats 'tool changed' as hold; **postgres-mcp rejects INT3's read_only_session statement → live DB probe integration test fails — fix before E2E3**; dialects ClickHouse/DuckDB/Snowflake/BigQuery/SQL Server not approvable (founder table in R-022-sources). Then E2E3. **Founder approved (2026-10-01):** (A) SWEEP read-only hardcode audit running → violations fixed in INT5; (B) E2E3 adds a 'random requests' scenario: ~15 fresh unseen requests (6 questions incl. cross-source/nonexistent/vague, 4 dataset requests incl. payments + one fine-tuning that must refuse honestly, 5 agents/automations incl. read-write, schedule, event, unconnected tool) graded: correct cited answer OR sensible question OR honest refusal; invented answer / crash / fake 'done' = FAIL. Multi-turn checked NOT built → roadmap. N-23 frontend specs after. N-18 Activity side: founder APPROVED (2026-10-01) → ACT2 DONE (activity 36 tests, 24/24 mutations; owner-or-operator fail-closed, allow-list redaction, bounded reads + cursor, namespaced joins, identical 404s; gateway 1060/1 — graph_routes_r2 versions test → INT5's graph merge change). Activity Codex f2 DONE → ACT3 fixed 11/11 (28/28 mutations; gateway 1085/0). **EVERY AREA NOW HAS 2 CODEX REVIEWS.** Founder: pointer-less releases = integrity defects?; per-principal Activity role?; reject reasons/coverage sentences as codes only? Cross-track released(pointer_id) at intelligence_routes:689 + orchestrator:322 → INT5. Founder: operators see all or only ownerless?; backfill owners of pre-existing agents or re-save?; drop activity_operator role or map to WALNUT_OPERATORS?; can a person who starts someone else's agent keep seeing that run?

**~16:00 INT5 + HC2 DONE — offline suites green before E2E run 3.** INT5 (wiki/40-reviews/INT5-integration.md): adapter 668, adapter_agents 118, agent 317, agent_runtime 217, agentdef 128, automation 207, convert 539, core 222, evals 23 (2 Ollama deselected), federated 87, graph 830/1s, harness 37, ingest 10/2s, materialise 232, mcp_ingest 701/2s (incl. live :5433), orchestrator 12, outbox 130, retrieval 63, semantic 32, workflow_spec 129, intake 718 + 1 known fail (SUBMISSION.md Playwright count), mcp_server 137, root 25/1s, tsc clean. 17/18 mutations killed (1 survives by construction). Fixes: live DB probe trusts DB `transaction_read_only`; app-activated agents get sealed delegation; HTTP registration origin=remote; mapping.py uses hash-chained approval log (pre-INT5 mapping approvals no longer honoured); ToolChanged → hold TOOL_CHANGED_PENDING_REPLAN; label change needs own pointer; released() carries pointer_id in orchestrator + /intelligence/ask. HC2 (wiki/40-reviews/HC2-hardcode-fixes.md): gateway 1099/0 fail; 26/26 mutations killed; ReadSourceBanner (offline_gold honest label), /impact minutes labelled estimated, pack nouns in UI, engine strings de-domained, literal test counts derived. Deferred: `_sgd` field name (#15) and checkbox path list (#16) need intake_routes fields. **Next: E2E run 3 (brief scratchpad codex3/E2E3_BRIEF.md) — launched.**

**~17:45 E2E RUN 3 DONE — not passing yet; 14 findings (0 Critical, 4 High, 6 Med, 4 Low)** in wiki/40-reviews/E2E-2026-10-01-run3.md (run dir var/e2e-20261001T0836). Run-2 FAILs now PASS: pointers 575/575 + 0 dangling + 8 cross-source; briefing 58 s, 11/11 citations, masked; read-write loop (CRM write lands, 409 on re-approve, re-ingested). Golden 10/10; adaptation, withheld 409, owner-only Activity, security all PASS. Run-2: 14 fixed, 2 mostly, 3 changed, 2 still failing (N-06, N-07). Random requests 11/15 PASS. High: P-01 /brain "0 open invoices" (live 1); P-02 delegate.ask malformed; P-03 labelled tabular POINTER_CHECK_FAILED; P-04 follow-up drafts never written. Frontend Playwright void (P-14, harness/env) — must re-run. Fix wave (brief codex3/E2E3_FIX_BRIEF.md, no Codex per founder): FED4 (P-01,08,11), RT4 (P-02,04,12), MAT4 (P-03,10), ING4 (P-05), GW4 (P-06,07,09,13); HC3 (docs + #15/#16) running. **Founder decision (2026-10-01): run 3 is the LAST E2E round for this goal — no re-run.** Next: when all fix agents land → one parallel offline-suite pass → wiki/STATUS updated → proceed. P-01..P-13 fixes are verified offline only (not live); frontend build/Playwright (P-14) not validly run in run 3 — carried as an open item.**

**~19:20 RUN-3 FIX WAVE DONE + FINAL OFFLINE PASS GREEN.** Dispositions wiki/40-reviews/R-024-{FED4,RT4,MAT4,ING4,GW4}.md + HC3-docs-and-deferred.md; every fix test-first + mutation-checked (FED4 9/9, RT4 8/8, MAT4 5/5, ING4 3/3, GW4 all, HC3 10/10). Fixed offline (NOT re-verified live, by founder decision): P-01..P-13. Final parallel pass (scratchpad final_suites_1853): gateway 1132, mcp_server 137, adapter 668, adapter_agents 118, agent 317, agent_runtime 230, agentdef 128, automation 213, convert 539, core 222, evals 23 (2 Ollama deselected), federated 97, graph 833/1s, harness 37, ingest 10/2s, intake 719 (SUBMISSION count fixed → 0 known failures), materialise 251, mcp_ingest 710/2s, orchestrator 12, outbox 130, retrieval 63, semantic 32, workflow_spec 129, root 25/1s, tsc clean. 3 tests failed only under the 23-suite parallel load (graph file-lock 120 s timeout, mcp_ingest MCP session start timeout, adapter_agents seal test) and pass alone — load flakes, not regressions.
OPEN after this goal: P-14 frontend build + Playwright never validly run live; ac-004 city attribute not cited in llm.build_context; doc-before-DB short names not re-pointed until doc rebuild; masked DB cell in a dataset still POINTER_CHECK_FAILED (needs policy); row-id rule in 3 places (build.py, verify_fleet.py, materialise reader) — unify; compiler forces drafts human_required even for "tell me after"; federated service.py:558 could pass question= to check_answer; `_sgd` fields deprecated, remove later. Human demo steps: re-map follow_up_tasks (no invoice_no canonical id), re-ingest, re-approve pre-INT5 mapping approvals.

**~20:00 Founder CANCELLED the healthcare-pack move (HCP0 stopped; it had changed no files) — the general system + `workspace` pack is the product; healthcare stays as-is (roadmap item kept). E2E run 4 (final, once only; brief codex3/E2E4_BRIEF.md) launched.** ~19:40 (superseded): (1) move healthcare fully into its pack; (2) then ONE more E2E run (run 4) and ONE fix wave — once only.** HCP0 (foundation: dependency map, ADR, engine-side generic primitives + registries + pack-declared plugin loader, split plan) running; brief scratchpad codex3/HCPACK_BRIEF.md. Then stage-B parallel tracks per codex3/HCPACK_SPLIT.md → full offline pass → E2E run 4 (incl. frontend build + Playwright, P-14) → single fix wave → final offline pass.

**~20:45 E2E RUN 4 (FINAL live run) DONE — core passes live; 11 findings (0 Critical, 1 High, 4 Med, 6 Low)** in wiki/40-reviews/E2E-2026-10-01-run4.md (run dir var/e2e-20261001T1255). Run-3: 11 fixed + N-06 fixed, P-12 mostly, 3 changed, 0 still failing. Live: founder's cross-source question complete + correct (LA-007 11100, LA-012 15300) via /federated and the English-built delegating agent; drafts written; CRM write loop; 573/573 pointers, 0 dangling, 8 cross-source; dataset pointers 150/150 + 6/6; owner-only Activity + approvals; security refused; follow_up_tasks no longer keyed on invoice_no. Random 13/15. **Frontend first valid run: 33 pass / 8 fail (spec mismatches) / 2 skip of 43.** Open: Q-07 High (labelled tabular: immature labels + empty features; DB nodes timestamped at ingest instant), Q-01 catalogue empty during sync, Q-02 compiler grants execute_sql runtime refuses, Q-03 account owner not in context, Q-09 rejected drift re-bundled; lows Q-04/05/06/08/10/11. **FINAL fix wave (once only; brief codex3/E2E4_FIX_BRIEF.md): MAT5 (Q-07), FED5 (Q-01,06), RT5 (Q-02,05,08), GW5 (Q-03,04,11), AA5 (Q-09), SPEC5 (Q-10). Then one offline pass → wrap-up. No further live E2E.**

**~22:10 GOAL RUN COMPLETE — final fix wave landed + final offline pass green.** Dispositions wiki/40-reviews/R-025-{MAT5,FED5,RT5,GW5,AA5,SPEC5,COORD}.md. Fixed offline (no further live E2E by founder decision): Q-01 (root cause: pid-only temp name in mcp_ingest count.py → per-call name + Windows retry, coordinator), Q-02, Q-03, Q-04, Q-05, Q-06, Q-07 (High: maturity at T_d+window, rejected_unlabelled, empty columns refused), Q-08, Q-09 (rejection final; changes_digest; per-change baseline), Q-10 (specs; + real a11y defect in add-source-form.tsx fixed), Q-11. Final pass (scratchpad final2_suites_2147): gateway 1155, mcp_server 137, adapter 668, adapter_agents 121, agent 317, agent_runtime 254, agentdef 128, automation 214, convert 539, core 222, evals 23 (2 Ollama deselected), federated 106 (1 timing test failed only under 23-suite load; passes 3/3 alone), graph 833/1s, harness 36/1s, ingest 10/2s, intake 719, materialise 257, mcp_ingest 711/2s, orchestrator 12, outbox 130, retrieval 63, semantic 32, workflow_spec 129, root 25/1s, tsc clean. Nothing committed.
CARRIED (roadmap/open): business-time column at ingest (valid_from = updated_at) + repeated captures for label history; frontend shows new receipt keys + sends changes_digest on approve; specs + a11y fix unverified live; nl_query no-LLM phrasing of owner; resolve_by_plan can resolve a rejected step-hold; per-change baseline → mcp_ingest/drift.py; row-id rule in 3 places; guardrail refusal wording is clinic-specific for every pack (move pack.py/clock.py/guardrails.py into an engine package, wording from pack YAML); deployment.yaml still binds finance+government+healthcare (founder: workspace-only?); multi-turn; conversational fine-tuning shape.

**2026-10-02 ~01:40 FINANCE LIVE DRY RUN DONE + GENERAL FIX WAVE RUNNING.** Committed all work on branch `implementation-prototype-completed` (08bebb2). FIN1 dry-ran the finance estate through live MCP ingestion side-by-side (:8100/:3100, var/finance/, DB finance_demo in walnut_demo_pg; launcher start-walnut-finance.ps1; guide FINANCE_LIVE_GUIDE.md). Works end-to-end via API: 10,128 nodes, 99,975/99,975 pointers, 372 cross-source entities, questions/delegation/agent/corpus OK. 19 findings in wiki/40-reviews/FIN-live-dry-run-2026-10-02.md (3 High: FIN-01 one email fails the mailbox source, FIN-02 DB plan not approvable in UI, FIN-03 adapter agent needs screen.yaml) + coordinator C-1..C-5 (FK edges dropped, 1,000-row cap, no cross-DB link proposals, Intelligence cards withhold >5,000 nodes, clinic wording under other packs). Lesson: four E2E runs on one small estate checked correctness of what's in the graph, never completeness vs source. **Founder decisions:** all fixes GENERAL (tests on ≥2 estates); FIN-04 party → ORGANISATION (mergeable on declared business key / exact legal name, human-approved rule) vs PERSON (never merged). Fix wave (brief codex3/FIN_FIX_BRIEF.md): TAB6 (C-1,C-2,C-3,FIN-05,FIN-14, completeness accounting), SCR6 (FIN-01,FIN-03 default screen,FIN-17 injection screening), GRAPH6 (FIN-07,FIN-04 + ADR), UI6 (FIN-02 vouch UI,C-4,FIN-16,FIN-19,intake card), ANS6 (FIN-08,11,12,15, finance purpose/golden), PACK6 (C-5/FIN-09: helpers → engine package, pack wording), MAT6 (FIN-13,FIN-10, completeness E2E checks + LESSON). **Next: offline pass → reset finance state (move, don't delete) → founder watches the rebuild from empty.** Roadmap: frontend cleanup added.

**2026-10-02 ~16:30 Finance fix wave committed (2db23ed). Founder is rebuilding finance live (:8100/:3100, fresh var/finance; old state in var/finance_backup_20261002-1558). Three founder features running in parallel (brief codex3/FEAT8_BRIEF.md): AAX: an adapter agent per source drives setup (sources auto-listed in the Builder adapter section; "Set up" → plan → approve → discover → model-explained mapping → person approves → build → keeper). PLX: approve-all / run-all on Sources, per-plan logic, separate vouches, writes never batch-approved. ONE: one-time read/write task on Intelligence (same compiler/runtime, no trigger, writes held, Activity record, no persistent agent). Then sequential Codex reviews (one per track, verified), then fix.** Roadmap added: Builder one-time task type (now being built as ONE), optional whole-graph overview, per-source adapter agent design (now being built as AAX MVP), Intelligence ask/subgraph at any size, one company-merge policy for all packs.

**2026-10-02 ~19:00 FEAT8 review plan (founder):**
1. Codex round 1 per track: ONE, PLX and AAX all FAILED review (ONE 1 Critical: outbox approvals trusted a client staff string; PLX 3 High; AAX 1 Critical: document discovery built before the mapping gate, plus 6 High). Fix agents FX-ONE, FX-PLX and FX-AAX are running; DSD is still building.
2. After fixes: Codex round 2 per track (ONE, PLX, AAX, DSD).
3. ONE cross-section Codex review for consistency (schemas, contracts, auth and approval patterns, ids, pointers across tracks).
4. Fix.
5. E2E run (both estates; completeness checks).

Dataset scope (founder): Walnut provides data plus per-column "known when" metadata. ML engineers own cleaning, splits, class imbalance and leakage decisions. Roadmap priority: core correctness first; entity resolution next (a necessity).

**Founder instruction (2026-10-02 late):** do NOT run E2E yet. After FX-AAX lands, run the cross-section Codex review, fix its findings, run the tests and commit. Then start the website for the founder to go through manually. If the founder says nothing for 30 minutes after the site is up, kill all app ports and run the E2E (both estates, with completeness checks).

**2026-10-03 E2E run 5 launched** (brief: scratchpad codex3/E2E5_BRIEF.md). FEAT8 plus the consistency work is committed (8f7e282), with all suites green when run sequentially. The founder's 30-minute manual window passed with no message, so the app was stopped and E2E5 started: both estates (workspace, then finance), the completeness checks, entity identity, ONE/AAX/PLX/DSD, Activity consistency, security, random requests and frontend Playwright. One estate and one process at a time (memory). Old unsealed approvals must be re-approved once.

**2026-10-03 E2E RUN 5 DONE: neither estate fully passes.** There are 24 findings (0 Critical, 4 High, 13 Medium, 7 Low) in wiki/40-reviews/E2E-2026-10-02-run5.md.
- **Workspace:** every new feature worked live, with 527/527 pointers and the security probes refused. Its High is W-07, a regression: the founder's cross-source question loses Corriedale because cross-source joins weakened (W-02). Frontend 36/45.
- **Finance:** isolated nodes 33% → 3.4%; no row cap; the mailbox completes; 17 injections held; 579,140/579,140 pointers; count cards at 50k nodes; Fairmont = 1 node; the delegation matches Postgres in 29/29 groups. Highs:
  - F-03: FK edges are not re-resolved after a later build (2,251 missing);
  - F-04: HARD CONSTRAINT 1 — 72 person contacts merged by name, because `contact` is not declared a subject;
  - F-10: the raw dataset over 24k transactions is refused (bad size gate, time filter dropped).

Fix wave (brief codex3/E2E5_FIX_BRIEF.md; targeted tests only, because of memory):
- IDN: F-04 plus an engine person guard, W-02/W-07, F-08;
- TABX: F-03, F-11, F-01 per-cell screen, F-02 perf, W-01;
- BRN: W-03, W-04, F-05 latency/vague, F-06, F-07;
- DSX: F-10, W-10, W-11;
- SETX: W-05, W-06, W-08, F-09;
- ACT2: F-12, W-09, W-12.

Then: all suites run sequentially by the coordinator, Codex review per track, fixes, commit.

**2026-10-03 R-029 fix wave LANDED (uncommitted), with targeted tests green per track.** Dispositions are in `wiki/40-reviews/R-029-{IDN,TABX,BRN,DSX,SETX,ACT2,TIDY2}.md`.

TIDY2 then closed the tracks' cross-track items:
- The UI8 test now asserts per-row holds.
- **Reads never write:** the approval key is never created implicitly. A write with no key refuses `APPROVAL_KEY_UNAVAILABLE`; `provision_approval_key()` is the explicit step, and the launchers call it.
- Identity-rules refusals use `MACHINE_ACTOR` plus `error_ref`.
- There is one gateway mechanism constant.
- Run triggers carry the full actor.
- **Graph readers by index** in `databridge_graph.query`, proven identical on copies of both estates. On finance, `/brain/neighbors` went from 6–8 s to 0.5 s, `path` from 8 s to under 1 s, and linking from 40 s to 0.2 s warm.
- Composite node indexes.
- No server paths in `report.json` or the discovery manifest.
- The facts file is hashed once per snapshot.
- The fs-corpus-under-finance "0 nodes" was traced: the ontology mismatch and AMBIGUOUS document kind are expected, but a real defect was found and fixed: a document is now rebuilt when its anchor kind changes.
- D-004 addendum.

Mutation: 28 of 29 killed; 1 is equivalent, with the reason given.

**NEXT (exact):**
1. Coordinator runs every suite sequentially.
2. One Codex review per R-029 track, verified with `verify_codex_output.py`.
3. Fix, then commit.
4. E2E run 6 re-checks the planner repair round (BRN) and the finance documents re-anchored after `document_kind` is set (needs one forced re-sync: old state has no `anchor_kind`).

**2026-10-05 PAUSED by the founder: read `RESUME_HERE.md` first.** The R-029 Codex round-1 fixes are done, and every suite passed when run sequentially (3 trivial failures fixed). They are NOT committed and have not had Codex round 2. The next steps are in RESUME_HERE.md §4.

**2026-10-07 D-005 PROPOSED** (`wiki/20-decisions/D-005-locate-then-fetch-and-learning-identity.md`): locate (search index + thin graph) → fetch (adapter agents, facts store) → resolve (identity layer: concept synonyms, keys, confirmed aliases, scored candidates, human review; "found in A and B; not confirmed" answers; every decision a sealed label). The 0.92 fuzzy merge becomes a proposal. Build phases P1–P6 after P0 (commit R-029 + Codex round 2). Awaiting the founder's §12 decisions. `semantica` reviewed at code level: ideas only, not a dependency.

## 3. START HERE — the next actions, in order

> **`PHASE_PLAN.md` now ends with a FORWARD PLAN (Phases 7–12)** covering the same work in more
> depth, each phase naming what a user could not do before it and what proves it afterwards.
> Read that for shape; read this for state.

0. **RESOLVED 2026-10-01 (agent R; `wiki/40-reviews/R-018`).** Root cause: verify re-checks
   admission as the verifier under `pointer_verification`→`audit`, which the citing principal's role
   never held; neither hypothesis below was right. Fix: verify re-checks under the purpose the read
   ran under — no grant widened. Kept below for history.
   ~~**FIRST: `test_verify_routes.py::test_the_four_step_sequence_verified_then_drift_material`
   is RED.**~~ It asserts `'forbidden' == 'verified'`, and it **breaks the `/evidence` demo path**
   (Verify → mutate → `DRIFT_MATERIAL`), so it is user-visible, not merely red.

   **What is established.** `finance.pointer_verification` is declared in
   `packs/finance/purposes.yaml` and **no role in `fixtures/packs/finance/roles.yaml` grants it**.
   No grant ⇒ policy denies ⇒ `verify()` answers `FORBIDDEN`. That is the mechanism, and it is a
   **purpose/role binding gap in pack YAML — the fix does not belong in the engine.**

   **What is NOT established: why it started failing today.** Two candidates, and picking the wrong
   one wastes a session:
   - **`rolemodel.py`'s loader excludes every conditional grant** — found independently by the
     adapter-ingest agent, which hit the same class of failure on the government estate
     (`adapter_service`/`ingest` compiles to `allow == {}`). `fixtures/packs/finance/roles.yaml`
     contains **2 conditional grants**. If the verification grant is one of them, this is the cause
     and it is an **engine gap**, not a config gap.
   - A genuine pre-existing config mismatch, as the auth agent concluded.

   **Check the conditional-grant path first** — it is the cheaper test and it would also explain the
   government failures, which makes it the more likely single cause.

   *(Correction, recorded because the reasoning is more useful than the conclusion: I first called
   this fallout from today's untrusted-decides-access fix, on the reasoning that `FORBIDDEN` means
   "policy now denies what it once allowed" and that fix was expected to break estate grants. That
   was wrong — the purpose is simply ungranted. I also, separately, was right to distrust the first
   "pre-existing" label on five other tests today, which were mine. **"Pre-existing" is a claim to
   verify in both directions**: an agent cannot see who caused a failure, and neither can a
   coordinator who assumes their own last change is responsible.)*

1. **Collect the in-flight agent (§4)** and re-run `packages/graph`. Two are working
   **hard-constraint-1 findings** and their verdicts matter more than anything else outstanding.
2. **Work codex batch 2's remaining reviews** — `phase4-agentdef.md` (8 Critical) and
   `phase4-materialise.md` (7 Critical) have **no agent assigned**. Triage guidance is in
   `wiki/40-reviews/R-005-adapter-fleet-triage.md`.
3. **D12 — Actions as the only write path.** Bigger than it looks: the append-only ledger with its
   `evidence_no_update`/`evidence_no_delete` triggers lives **only** in `packages/intake` (the
   healthcare pack). There is no generic engine ledger. So: lift the ledger into the engine, put
   `ActionType` invocation in front of it, add a declared reversal path, and leave the pack
   supplying policy rather than mechanism. **This is also what makes the Connector Builder's write
   toggle stop refusing.**
4. **The finance graph built BY AGENTS.** Government's was deterministic. Unblocked now by the
   finance seed map and the merge fix.
5. **Per-field trust in `estate_seed.py`** — see §7.
6. **Codex batch 3** over the gateway and frontend, still entirely unreviewed.
7. The fixture defect in §7, then the smaller items in §6.

**Deferred until after the founder pushes to GitHub — do not start:** the Skill runtime registry
and AgentTeams integration.

## 4. In flight — NOTHING. All agents have landed.

| Agent | Owns | Working |
|---|---|---|
| ~~estate seed + merge~~ | — | **DONE — `R-010`.** 11 fixed, 2 dismissed with reasons. |
| ~~reverse index + store~~ | — | **DONE — `R-011`.** All 11 findings fixed, **none dismissed.** |
| ~~growth + attestation~~ | — | **DONE — `R-012`.** See below. |

**Batch 2's graph reviews all landed** — `R-010`, `R-011`, `R-012`; `packages/graph` was clean at
**567 passed, 1 skipped, 0 failed** before the current wave started. Summaries below.

| Agent | Owns | Working |
|---|---|---|
| ~~adapter-fleet ingest~~ | — | **DONE.** See §4d. `packages/graph` **581 passed, 0 failed**. |
| ~~agentdef findings~~ | — | **DONE — `R-013`.** All 12 findings, **98 -> 128 tests**. |
| ~~materialise findings~~ | — | **DONE — `R-014`.** 11 fixed, 2 dismissed, **103 -> 126 tests**. |
| ~~gateway auth~~ | — | **DONE — `R-015`.** Impersonation closed. **518 passed, 1 failed** (see §3.0). |

**`R-012` closed both hard-constraint findings, and the second was worse than reported.**
`enforce_attestation`'s `src_id` defaulted to `checked.candidate_id`, so the binding check compared
a value with itself and was **always true** — omitting `src_id` now raises. Both production call
sites already passed it explicitly, so it was never exploitable; it mattered because the function is
public and `__all__`-exported. And `require_trusted_evidence=False` did not merely *waive* the
untrusted-evidence check, it **disabled it outright** — so the "four structurally unwaivable"
triggers were never four. `EVIDENCE_UNGROUNDED` and `EVIDENCE_UNTRUSTED` are now in
`STRUCTURAL_ESCALATIONS` (**six triggers**) and the toggle is deleted: a switch beside a
"structurally unwaivable" trigger was never structural. It also fixed a YAML injection via
unescaped `proposed_by`/`src`/`dst`, and an attestor check that accepted `"AI"` and `"system"`.
Two items are **acknowledged, not closed**, with reasoning: a TOCTOU when a pack's subject list
grows after a decision, and `GrowthLedger.record` trusting an externally-constructed
`GrowthDecision`.

**Pre-triage for the seed agent, so it is not lost:** finding 2
(`UpsertOutcome.requires_human_selection` permits `False`) is **very likely a false positive** — the
reviewer conflated two types that share a field name. `ResolutionResult.requires_human_selection` is
`Literal[True]` and is what the hard constraint is about; `UpsertOutcome`'s field means "this upsert
touched a subject kind", and `False` on an address is correct and informative. **Finding 1 is the
real question.**

**`R-011` — reverse index and store, all 11 fixed.** The two load-bearing properties survived and
got stricter: the honest-answer set gained a third state (`Answer.UNAVAILABLE`, so a corrupt
`attrs_json` can no longer render as a clean `KNOWN`), and ledger-bound records went from
flag-protected to **fully immutable** — a second `record_emission` against an already-bound
`(kind, ref)` is refused outright.

Three worth knowing:
- **`ix_ptr_binding_natural` was UNIQUE and should not have been**, so `INSERT OR REPLACE` silently
  **evicted a different `source_rowid`'s binding**. Silent data loss in the index whose entire job
  is to know what it has seen. Existing stores self-heal on open via `DROP INDEX IF EXISTS`.
- **`cascade` mutated by node id, not by version** — it could touch a sibling open version sharing
  the same id. Now version-scoped, and the whole mutation phase runs in one transaction with
  rollback.
- **The migration rewrote historical `attrs_json` in place.** Narrowing moved to a read-time
  helper. One caveat recorded and not fixed: `node_sheet.py` and `clinic_history.py` read
  `attrs_json` directly and would see raw legacy values on a hypothetical pre-migration store.

No performance regression — the 46k-scale equivalence suite still passes inside its 60 s bound.

**`R-010` — estate seed + merge. 11 fixed, 2 dismissed with recorded reasons, and the verdict on
the identity finding is the most valuable output of the whole batch.**

The reviewer claimed `_resolve_existing` "automatically merges subject records". The agent's verdict:
**the broad claim does not hold, but a real defect existed at a different locus.** `merge.py`'s
id-match and canonical-match logic is sound — never-fuzzy-merge for subjects, the A4 grounding check
— and its one dependency is that `incoming.id` is genuinely 1:1, which `merge.py` cannot verify
itself. That trust was breakable **upstream**: `_build_node`'s lossy `slug()` normalisation let two
different subjects' rows collide onto one node id and be **silently fused before `upsert_node` was
ever consulted**. Fixed at that locus with a `raw_keys` guard that refuses rather than fuses, proved
end to end by `test_the_pipeline_end_to_end_never_fuses_two_different_subjects_on_a_slug_collision`.

**A review names a problem; it does not get to name the locus either.** Finding 2 was dismissed as
predicted (two types sharing a field name) and pinned with a regression test.

Also fixed: `resolve_incoming` emitting false provenance on `label`/`matched_on` (now `derived`
pointers); a fabricated fallback label; malformed `valid_from` silently defaulting (now skip and
count); `upsert_edge` never invalidating attribute changes; `upsert_node` losing existing attrs on
supersede and dropping new ones on corroborate; `read_sql_insert` dropping single-line multi-tuple
INSERTs; `read_json_pages` masking malformed collections as empty; dotted-path columns ignored;
`read_fixed_width` masking bad bytes; and path traversal via string-prefix containment (now
`Path.is_relative_to`).

**A regression I caused and then fixed.** Binding all three packs broke 5 tests that hardcoded
`("healthcare",)` — and three agents in a row reported them as "pre-existing failures in a sibling's
file", because they predated *their* work. They were mine. `test_default_binding_is_declared_not_
guessed` now **reads `packs/deployment.yaml`** and asserts the loader returns what is declared,
which is what a test called "declared, not guessed" should always have done; the never-fuzzy-merge
tests are scoped with `deployment("healthcare")`; and `test_a_retired_vocabulary_member_is_no_longer_
accepted` is scoped too, because `person` is a legitimate **government** subject kind and the rule
under test is "a kind THIS pack retired is refused", not "the string `person` is always invalid".

**Two lessons, both now in `CLAUDE.md`:** derive a test's expectation from the declaration, and
**"pre-existing" is a claim to verify, not accept** — an agent can only see that a failure predates
its own work, not who caused it.

## 4c. Two findings that escaped their agent's scope — pick these up

**A real bug in `packages/adapter`'s generic derivation-replay `verify()` path**, found by the
materialise agent while fixing F2 and **left unfixed because it is outside its file set**:
`verify()` misbehaves for **compressed-aggregate pointers** (pointers carrying a `predicate`). The
agent worked around it at its own layer by excluding predicate-bearing pointers from the verified
sample, **and disclosed the exclusion in the receipt** rather than hiding it. That is the right
call at its layer and the wrong place for the fix. **Owner: whoever next touches
`packages/adapter`.** The receipt disclosure should be removed once the underlying path is correct.

**`SCHEME_VERSION` bumped 1 → 2 in materialise** (new fields entered `plan_hash`: `bundle_version`,
`materialiser_version`). The golden `plan_hash` test was re-pinned. Per the standing rule, a golden
hash that moves is an alarm — here the move was **correct**, because the whole finding was that
things which change what is emitted were sitting outside the identity.

## 4a-2. `R-015` — the impersonation hole is closed

New `apps/gateway/databridge_gateway/auth.py`: a bearer token checked against a directory from
`WALNUT_API_TOKENS`, exposed as **one FastAPI dependency (`require_principal`) that every disclosing
route takes**. It never reads identity from a body; `401` for missing/unknown, `503` if no directory
is configured at all, and **no default principal, ever**. `AskBody.on_behalf_of` is **deleted** —
the authenticated identity now selects the adapter `Principal`.

**Gated:** all `/orchestrate/*`, `/evidence/verify*`, `/evidence/impact`, `/evidence/trace/{id}`,
all four `/graph/*`, both `/trace*`, all four `/intelligence/*`. **Deliberately not gated:**
`/evidence/verify/lattice` — static engine vocabulary, no estate data, same class as `/health`.

**Demo:** `WALNUT_DEV_AUTH=1`, default off, logged loudly at boot, set only for the gateway's own
process by `start-databridge.ps1`. Verified live: 401 without a token, **422 when a body tries to
smuggle `on_behalf_of`**, 200 with the dev token.

The threat model is written into `auth.py`'s docstring and is explicit about what it does *not*
defend against (a leaked token, same-host environment access, the network layer). The existence
oracle was closed mostly *by the gate itself* — an anonymous prober cannot reach either route now —
and the residual 403/422 inconsistency was harmonised to 403.

**The frontend now needs a fetch wrapper** attaching the bearer token to every gated call, and the
orchestrator persona picker must switch **tokens** rather than send `on_behalf_of`. Documented in
`R-015`, **not implemented** — the UI will be broken against the gated routes until it lands.

## 4d. Adapter-fleet ingest — the graph can now be built BY THE FLEET

New `packages/graph/databridge_graph/adapter_ingest.py` bridges any Core's `resolve()` output into
the row shape the builder already consumes. A dataset declares `reader: adapter` in `seed_map.yaml`
and the caller supplies a producer backed by a real `PostgresCore` or `DocumentCore`. **Opt-in and
per-dataset** — default calls are byte-for-byte unchanged, verified by a full unbounded government
build after all edits: **46,346 nodes / 52,747 edges, exact match to the pinned baseline**.

**The per-field trust gap (§7) is closed where wired.** `_pointer()` now prefers a row's own
`AdapterRow.pointer_for(column)` over the dataset-level stamp. Proven on finance's new
`cb_facility_adapter` dataset: `interest_basis` stamped `Trust.TRUSTED` and `notes` stamped
`Trust.UNTRUSTED` **on the same node**, from `sources.yaml`'s real per-field declaration. Previously
`cb_facility` had to **omit `notes` entirely** to avoid mislabelling it.

**Three refusals were pinned as passing tests rather than routed around** — this is the most useful
part of the result, because each names a real gap:
1. `finance.graph_build` is deliberately unbound in production config.
2. Government's `adapter_service`/`ingest` role compiles to `allow == {}` — **`rolemodel.py`'s
   loader excludes every conditional grant.** A real engine gap.
3. `auditor`/`audit` *does* grant `legacy_records.LEGACY-MASTER` unconditionally but is refused on
   an **entity-name mismatch** (`LEGACY-MASTER` vs `DocumentCore`'s reader-derived `legacy_master`)
   — the `grant_list` loader does not reconcile the two.

**Still on direct read, with reasons:** government's 17 `permits.*`/`benefits.*` postgresql datasets
(no SQLite-attach loader exists for them, unlike finance's `load_finance_estate`), and
`gis_parcels`/`public_registry` (`csv`/`json_api` kinds — **no Core in the fleet serves them**).

**It also caught a `test_no_domain_branching` violation in its own work** and fixed it correctly:
which purpose/role admits each adapter-routed read moved out of Python and into each dataset's
`reader_options` in `seed_map.yaml` — data, not code.

**Phase 7 is unblocked.** `resolve_incoming()` is untouched and still generic; no cross-source links
were bound; and **`local_key` → node recoverability is unaffected regardless of reader**, pinned by
a test. That was the hinge.

## 4b. THE GRAPH IS A FAN, NOT A WEB — highest-value work queued next

Measured on the real government graph, not estimated:

| | |
|---|---|
| edges per node | **1.14** (46,346 nodes / 52,747 edges) |
| nodes with degree 1 | **94.6%** — 3,785 of 4,000 sampled. Degree 2: 173. Degree 3: 31. |
| biggest hubs | `zoning_code` lookup rows at degree ~250–275 — *reference data, not entities* |
| `case_event` (15,000), `legacy_record` (7,943), `address_point` (4,913), `case_file` (1,200), `address` (1,170) | touched by **exactly one edge kind each** |
| `parcel` (3,000) | 4 edge kinds — the only well-connected kind |

So the surface renders hub-and-spoke bursts, not a knowledge graph, and the founder said so on sight.
**This is structural, not a rendering problem:** `estate_seed.py` binds rows→nodes and foreign
keys→edges, so each row gets one node and one edge to its parent. Nothing links across sources.

### The fix, and the estate already ships its scoring key

**The same person appears in six government sources as six unconnected nodes.**

- `fixtures/packs/government/entity_resolution_truth.csv` — **9,870 appearances of 511 distinct
  people across 6 sources** (`legacy_records` 7,360, `public_registry` 1,221, `permits_registry`
  533, `benefits` 306, `case_files` 250, `correspondence` 200). Columns
  `gt_person_id, source, local_key, name_as_recorded, id_as_recorded, traps`, where **`traps` names
  the planted difficulty per row** — `name.uppercase`, `dob.day_month_swapped`, `addr.abbreviated`,
  `legacy.rec_type_BN`.
- `entity_resolution_negatives.csv` — **41 pairs that must NOT link**, e.g. *"identical full name,
  19–31 years apart — the classic parent/child collision"*.

So this can be **scored**, exactly as the sanctions join was (12/12 hits, 6/6 traps). That is the
difference between adding edges and proving they are the right ones.

**The mechanism exists.** `resolve_incoming()` in `estate_seed.py` — the generic, pack-YAML-driven
join built for 3G — is currently wired for one finance source. Cross-source linking is the same
capability applied across six.

**The constraint that shapes it:** merging two *subject* records is reserved for a named human.
Links must be `about`-shaped, candidate-shaped or attested — **never a fused node**. The sanctions
join got this right by writing `screening_result` nodes and never touching a `party`.

**Also queued with it:** bind the foreign keys the seed map skipped. `case_event` having exactly
one edge kind means real relationships in the estate are simply not bound yet.

**Blocked only on the adapter-fleet ingest agent**, which owns `estate_seed.py` and the seed maps
right now. It has been briefed to keep `resolve_incoming()` generic and to flag it if its change
makes `local_key`→node recovery harder — that mapping is the hinge this workstream turns on.

## 4a. Codex batch 3 — the gateway, never reviewed. **28 Criticals.** Triage: `R-016`

Five scoped reviews, all genuine **first time** (the LF-manifest fix worked). They collapse into six
root causes, and the largest is the most serious finding of the whole review programme.

**The caller asserts their own identity.** `orchestrator_routes.py:174` reads
`principal = fleet.principal_for(body.on_behalf_of)` — a POST with `{"on_behalf_of": "fcc-3"}`
returns compliance-officer disclosure **to anyone**. Reported independently by **4 of the 5
reviews**. `PolicyEngine` is correct and is not the problem; it faithfully enforces the grants of
whatever principal it is handed, and **nothing authenticates the claim**. Everything else in this
system — per-field trust, four cell states, compile-time refusal of untrusted scope columns, the
attestation wall, the append-only ledger — is downstream of *who is asking*, and that was the one
input taken on trust. Same shape as batch 1's root cause: enforced everywhere except at the
boundary where the claim enters. **Fix in flight (`R-015`).**

The other five, in `R-016`: `Transformed` missing from the cell contract in two places (O1b
resurfacing — a trace that omits the state renders a partial disclosure as whole); values
transformed in serialisation while keeping the original pointer; denials dropped rather than
represented as `Withheld`; request-driven unbounded work on paths that escaped `/graph`'s bounded
discipline; and internal detail in responses including a deliberate 403-vs-404 existence oracle.

**Take the trace findings as seriously as the auth one** — graph-trace #3/#4/#5 say an empty or
unrelated ledger append falsely covers a release, an unknown policy decision renders as permitted,
and releases are fabricated into citations with no source-pointer check. The trace is what the
product shows a reviewer to prove the loop behaved. A trace that overstates is worse than no trace.

## 5. What landed 2026-08-16

**The loop closed.** `class Orchestrator` did not exist this morning and nothing constructed an
`AdapterShell` outside tests — the system could be inspected but not *asked* anything. Now:
`packages/orchestrator`, two adapters wired over the finance estate, measured live — **375 grounded
cells** for a credit analyst, a real **`POLICY_PURPOSE_MISMATCH`** refusal for the same question
against the mailbox, and that mailbox question answered for a compliance officer with every cell
`trust: untrusted`. `/trace` shows a real run.

**3G incremental join — the decentralisation claim, previously ZERO coverage.** 8 sources → 2,420
nodes / 1,260 edges in 4.99 s; the 9th (`sanctions_watchlist`) adds 195 nodes / 30 edges in
**0.90 s with no rebuild**; **12/12 true hits claimed, 6/6 near-name traps correctly left
unlinked**; no pre-existing node closed or superseded (strict per-id assertion). The join is a
**generic** `resolve_incoming()` in `estate_seed.py` driven by a pack's `resolve:` block — the first
draft put screening logic in `finance_seed.py` and `test_no_domain_branching` caught it.
**No automatic identity merge ever happens:** screening writes only `screening_result` nodes.

**`merge.py` was quadratic and is not any more.** `upsert_edge` materialised every open edge on each
insert (52,747 edges ≈ 1.4B comparisons); a full government build reached 38k of 46,346 nodes in 35
minutes and never started edges. Two partial indexes plus a lean candidate projection → **full build
in 5.6 minutes**, proven behaviour-identical by an equivalence test replaying an identical operation
sequence against a verbatim copy of the pre-fix code.

**Reverse pointer index, then emission registration.** 435,798 pointer rows → 339,483 distinct
pointers in 44.8 s; `impact_of` 0.18 s; 47 µs write overhead; **11 mutations, all 11 detected**.
The three stores that bypassed it (`intake/ledger.py`, `materialise/`, `outbox/store.py`) are now
wired, so one drifted source reaches **7,350 pointers through a real emitted dataset** rather than
an honest empty list.

**All four adapter Cores reviewed and closed** — 58 Criticals triaged in `R-005`, dispositions in
`R-006`–`R-009`, `packages/adapter` 555 → **668, 0 failures**. The root cause five reviews found
from five different angles (**untrusted content deciding access**) was closed **once, centrally**, in
`PolicyEngine._compile`: no adapter call site needed changing, because all four Cores obtain their
`Admission` through `admit()`.

**Also:** the finance seed map (5 of 9 sources bound, 4 held out with named reasons); the estate
loader consolidated from three divergent copies; a product README + Apache-2.0 LICENSE; all three
packs bound by default; `/graph`; the Connector/Adapter Builder; and a rebuilt `/ontology` with a
`packs/common` starter library, deprecate-never-delete, and attested kinds visually separated.

## 6. Not built — the honest list

1. **D12 / Actions** — see §3.3.
2. **The finance graph built by agents.**
3. **`packages/agentdef` and `packages/materialise` review findings** — 15 Criticals, unassigned.
4. **The gateway and frontend have never been reviewed at all.**
5. **Four things the GOA docs now claim** that are not built: OpenTelemetry GenAI conformance, a
   persisted trace, PostgreSQL 19 + SQL/PGQ, and DBOS. They carry no figures and no file paths, so
   they read as design — but they are on the list.
6. Croissant emission; the two O1b-blocked materialisation shapes; conformal certificates;
   crystallisation metering; `purpose_bindings.yaml` declares only finance; `trusted_case_bindings`
   (both estates have grants that are dormant only because an unrelated check fires first).
7. **`AdapterShell` is wired by the orchestrator** — but only for the two adapters its fleet builds.
   The document and MCP Cores still have no live caller.

## 7. Findings that need a decision or a follow-up

**Per-field trust vs per-dataset pointers.** `estate_seed.py` grounds pointers at the **dataset**
level while `sources.yaml` declares trust **per field**. The finance seed map's answer was to
**omit** untrusted columns rather than mislabel them (pinned by
`test_no_untrusted_field_is_emitted_as_a_trusted_attribute`). That is correct, and also a gap: the
graph is missing real data it should be carrying *with an untrusted label*. The fix belongs in
`estate_seed.py`.

**Fixture defect — two ground-truth files disagree.** `expected_counts.json` says
`entities.canonical = 1368`; `entity_clusters.json` says `entity_count = 1152` and genuinely holds
1,152 clusters (plus `entities_visible_in_3_or_more_sources = 377`). **Cite the scoring key: 1,152,
and lead with 377.** Make the fixture self-consistent. Related: a raw `find` returns 452/967 files
where the manifests say **445/963**, because the manifests list only *generated* files — the
manifest is the citable number, and the government README's "959" is simply wrong.

**Sparse node kinds.** `case_file` nodes carry **zero attributes**, so clicking one in `/graph`
shows an empty evidence panel and looks broken. `legacy_record`, `parcel`, `payment` and
`case_event` are rich (5–9 attrs, each with its own pointer). Fill them in or hide the kind.

## 8. Gotchas — things future-you would otherwise rediscover painfully

- **Codex fails silently in FOUR shapes now**: banner-only; an articulate refusal that reads exactly
  like a review; concurrency truncation; and **dying mid-response right after its opening line**.
  Every output MUST go through `scripts/verify_codex_output.py`. Batch 1 failed all five in
  parallel and succeeded sequentially.
- **Write codex manifests with LF.** A `\r` made `cat "$prompt"` resolve a filename with a trailing
  carriage return, so codex ran with an **empty prompt** and reviewed from the bundle header alone.
- **A full-suite run while agents are writing is not a result.** Two agents reported "pre-existing
  failures" today that were a third agent's half-written file; one vanished on re-run.
- **Discovery belongs in the launcher, not a route.** Auto-detecting the graph DB inside
  `graph_routes.py` broke 14 tests — a route that hunts for a file overrides the store a process
  explicitly attached, and makes every test depend on whatever sits in `var/graph`. It lives in
  `serve.py::_default_graph_db()`.
- **Derive test expectations; do not hardcode them.**
  `test_the_sink_policy_covers_the_whole_trusted_sink_inventory` hardcoded two sink names and broke
  when the inventory correctly grew to five. A hardcoded expectation turns a deliberate widening
  into a red test, which trains the next reader to edit the number rather than ask why it moved.
- **`GroundedValue.pointer` is not Optional.** Cells are `GroundedValue | Withheld | Absent |
  Transformed`; a denial is `Withheld` with a reason code, never an absence.
- **A golden hash that moves is an alarm, not a chore.** Three times the move was correct.
- **`sources.yaml` is authoritative over `ADAPTER_AGENT_SPEC.md`'s tier claims** — the spec guessed
  optimistically three times and implementation corrected it every time.
- **Never run tree-wide git operations** while agents run; the tree is shared.
- **Read the working test before writing a new one.**

## 9. The frontend — read this before touching it

**The founder used the app and was unhappy, twice.** The graph tab was rebuilt three times in one
day. The settled position:

- **`/graph` is a verbatim port of `/brain`'s rendering** — `ObsidianNode`, `FloatingEdge`, the exact
  force constants — on `/graph`'s bounded data layer. **Do not restyle it.** Colour resolves through
  `brain-layout.ts`'s `NODE_STYLE` (a declared hex always wins), with a hue-ring fallback for
  undeclared kinds; all 30 government kinds now have hand-picked hexes.
- **One deliberate deviation, unresolved:** `/graph`'s canvas is **dark**; `/brain` today renders
  **light** (the app went light-first in Phase 5b). The founder asked for "Obsidian type" and asked
  whether light mode was the problem. **Ask before changing it either way.**
- **`/brain` is untouched and still reachable** under Pack → "Knowledge graph". It was never
  removed; a second tab with the same name was added above it, which is what read as removal.
- Nav is `Build` (Builder · Ontology) / `Graph` (Knowledge Graph · Trace · Provenance) / `Use`
  (Intelligence · Data). Schema mapping, Convert and Eval dashboard are gone. **Builder is ONE tab
  with two internal tabs** (Agent Builder, Adapter Builder) — MCP config is a **modal inside the
  Adapter Builder**, because MCP connectors are only reachable through an adapter.
- **Four surfaces are still declared only by `packs/healthcare/ui.yaml`** (`/frontdesk`, `/intake`,
  `/brain`, `/impact`). Finance and government declare none, so switching packs empties that
  section — which is why the platform reads as a healthcare product. `/intelligence` was promoted
  out of it today; the rest should follow or be removed.

## 10. Open — needs a human decision

- **O1 capture-on-read vs data minimisation** (spine §9). Needs counsel. **Do not put the
  minimisation claim in a customer deck until answered.**
- **O1b policy language** — core can represent a transformed disclosure; `egress_ceiling` is still a
  scalar and cannot *say* "transformed only", so two finance roles still reach nothing.
- **O2 / O2a** — EU AI Act dates unverified against EUR-Lex.
- **The MCP catalogue** is vendored-and-pinned (a server fetched at runtime is one nobody reviewed;
  41.6% of audited public MCP servers vanish within three days). Whether live registry search is
  wanted is a deliberate trade the founder has not made.
- **The dark/light graph canvas** (§9).

## 11. GOA Submission — context recorded nowhere else

`GOA Submission/` holds the handbook and **15 drafted documents**, all rewritten today.
**`_SHARED_FACTS.md` is the single source for every number** — §7a verified figures and the
fabricated list, §8a measured reverse-index numbers, §9b the voice rule, §9a-1 the evidence rule.

**The repository is NOT being submitted this round.** §10 requires no executable code, so the docs
describe the finished product in the present tense. **The one register that does not move is
measurements** — a number, a sample output or a log line must correspond to something actually run,
because those are defended in person at the semifinal, where §11 *does* require code and repository
access.

**Both mandatory clauses are met**: §9.1 asks for "at least a list of core Skills" (14 supplied with
all nine fields); §10 asks the design to *explain how* it maps to AgentTeams (a five-row mapping with
per-row migration cost). The Skill runtime registry and AgentTeams integration are semifinal items.

**Nine fabricated figures were found and corrected today** — including an invented covenant breach —
plus two that were simply wrong (1,368 entities → 1,152/377; 7 purposes → 8). The rule that catches
this whole class: **a file path is an evidence claim, not a design claim** (`_SHARED_FACTS.md`
§9a-1).
