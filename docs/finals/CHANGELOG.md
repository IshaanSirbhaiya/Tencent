# Walnut DataBridge: changelog

**Answers you can prove.**

Every change since the 16 Oct 2026 submission, newest first, grouped by area. Each line links its commit
on `final_v1.0`. What each feature does and how it is tested: [FEATURE_CATALOGUE.md](FEATURE_CATALOGUE.md).

All 44 commits after the submission (`acb18f0`), up to `f0f84bd`, were made on 30 Oct 2026 (SGT). Times are SGT.

## In progress

Being built now. **Not committed, not counted in any number on this page, not claimed anywhere else.**
Each line moves to the right area below when it lands.

| Work | What it will be | What exists in the tree today |
|---|---|---|
| **Act** | Draft a reply, or a follow-up Jira ticket, from an answer; never revealing anything the asker cannot open | Uncommitted `databridge_ib/actions.py`, `tests/test_act.py`, `tests/test_act_e2e.py`, mock Jira create (`mock_platforms/tests/test_mock_jira_create.py`) |
| **Watch the brain grow** | The knowledge graph grows live as documents arrive; add a document and see it appear | Committed: the web proxy allows `GET /ib/graph/changes` ([1079bd2](https://github.com/IshaanSirbhaiya/Tencent/commit/1079bd2)) and a 7 MB body for `POST /sim/upload-file` ([8e53a0b](https://github.com/IshaanSirbhaiya/Tencent/commit/8e53a0b)). The backend routes are not committed yet (uncommitted `databridge_ib/live.py`). Research: [live_growth/RESEARCH.md](live_growth/RESEARCH.md) (uncommitted) |
| **One-click demo kit** | "Start Walnut Demo" and a preflight check | Research only: [demo_day/LIVE_DEMO_RESEARCH.md](demo_day/LIVE_DEMO_RESEARCH.md) (uncommitted) |
| **Tencent tools upgrade** | CodeBuddy sessions CB7 to CB9; new WorkBuddy MCP tools; a WorkBuddy GUI test | Uncommitted prompts and transcripts in `docs/tencent/evidence/`, [WORKBUDDY_GUI_TEST.md](rubric/ai_innovation/WORKBUDDY_GUI_TEST.md), edits to the WorkBuddy guide |
| **New demo video** | The finals walk-through, narrated by the team | Uncommitted script and recorder in [video/](video/VIDEO_SCRIPT_FINALS.md); raw clips and cards in `submission/finals_video/` |
| **Everyday company content** | The world gains an employee handbook, Finance Ops, an IT helpdesk project, three public Slack channels and a People folder (leave, expenses, payday, holidays, onboarding, VPN), open to every employee, plus People-only salary bands. Append-only: every earlier document, fact and question answer is unchanged | Uncommitted `fixtures/enterprise/workplace.py`, regenerated `world.json` / `manifest.json` / `graph_rows`, `tests/test_workplace_layer.py`; extractive tie-break fix in `databridge_ib/narrate.py` with `tests/test_narrate_determinism.py`; eval re-run 30 Oct 11:27 SGT: 0 leaks in 7,374, 0 of 400 pairs differ |
| **Live Hy3** | Hy3 answers through TokenHub | Waits on TokenHub activation (a person). `demo_up.sh` will switch it on by itself once [`hy3_check.py`](../../databridge/scripts/hy3_check.py) gets an answer |
| **Hallway test** | 5 people × 20 min; results into the fix log and the proof slide | Protocol ready ([HALLWAY_TEST_PROTOCOL.md](rubric/hcd_ux/HALLWAY_TEST_PROTOCOL.md)); Sat 31 Oct |

## 30 Oct 2026: the finals build

### Demo and Presenter

| Commit | Time | Change |
|---|---|---|
| [f0f84bd](https://github.com/IshaanSirbhaiya/Tencent/commit/f0f84bd) | 10:16 | Printable handouts in `submission/finals/`: A4 leave-behind with QR, judge cards per rubric criterion, presenter cheat-sheet, with a generator that reads the numbers from the repo; `eval_summary.json` now records 124 e2e tests (it said 38) |
| [baf8a07](https://github.com/IshaanSirbhaiya/Tencent/commit/baf8a07) | 09:52 | Backup clips A (Compare, Ken, source), B (journey, Verify, tamper) and C (in-app stand-in for WorkBuddy) in `public/clips/` for the **B** key; status table in [BACKUP_PLAN.md](rubric/demo_story/BACKUP_PLAN.md#recorded-clips-status-30-oct-2026) |
| [82c12e7](https://github.com/IshaanSirbhaiya/Tencent/commit/82c12e7) | 06:24 | Proof slide: source paths wrap instead of overlapping |
| [edb0dee](https://github.com/IshaanSirbhaiya/Tencent/commit/edb0dee) | 06:05 | Bulletproof demo path: generation-bound presenter actions, serialised session changes, safe reset, Compare runs report real success, stale request ids invalidated, cancellable asks, honest SSE status line; Stage polish (one-row HUD, hidden chrome, `/present` landing, full names, rounded slide figures, Hy3 lines only when live, tamper progress) |
| [708d902](https://github.com/IshaanSirbhaiya/Tencent/commit/708d902) | 04:11 | Compare (two people; one person, two questions; "Identical responses" marker), Presenter mode with the Stage theme and keyboard script, `/present` slides |

### User mode

| Commit | Time | Change |
|---|---|---|
| [1bc6b4c](https://github.com/IshaanSirbhaiya/Tencent/commit/1bc6b4c) | 09:47 | Voice dictation in the Ask composer: browser speech, English / 简体中文, never auto-sends, accessible, hidden when unsupported (`voice.spec.ts`) |
| [ac94931](https://github.com/IshaanSirbhaiya/Tencent/commit/ac94931) | 04:42 | Interactive tours: User "Try it" (sign in, ask, open a source, Ken's identical refusals, checklist) and Developer (pipeline, journey, graph, trust, analytics / Verify); spotlight and "Do it for me"; English / 简体中文 |
| [b14f101](https://github.com/IshaanSirbhaiya/Tencent/commit/b14f101) | 03:45 | User mode: persona sign-in, Ask with live stage events, cited answers, sources sheet, honest refusals, History, Settings, English / 简体中文 |
| [8fe6b68](https://github.com/IshaanSirbhaiya/Tencent/commit/8fe6b68) | 03:04 | Foundation: tokens, primitives, shell with User / Developer modes, i18n, route skeletons, API client, graph and QR dependencies |

### Developer mode

| Commit | Time | Change |
|---|---|---|
| [ca72003](https://github.com/IshaanSirbhaiya/Tencent/commit/ca72003) | 04:45 | Live QA fixes: robust graph layout fallback, readable Slack / Jira versions, citation attachment for live answers, audit `?latest=1` and full window, waterfall seal ticks, Compare live-region status |
| [71456de](https://github.com/IshaanSirbhaiya/Tencent/commit/71456de) | 04:04 | Mission control (live pipeline), request journey (compliance waterfall and employee receipt), end-to-end Analytics, `/value` ROI calculator |
| [06bc7c7](https://github.com/IshaanSirbhaiya/Tencent/commit/06bc7c7) | 03:52 | Mock: the document panel takes authors and links from the graph mock, so both agree |
| [70b6fdc](https://github.com/IshaanSirbhaiya/Tencent/commit/70b6fdc) | 03:52 | `/dev/graph`: permission-aware WebGL knowledge graph (Sigma): explore, search to focus, one-hop expand, node panel with passage and lineage, table view, compare two people |
| [a1c0547](https://github.com/IshaanSirbhaiya/Tencent/commit/a1c0547) | 03:48 | Audit console (plain-English search, hash-chain Verify, sandbox tamper demo), Simulator, Trust centre; `/audit` and `/sim` redirect to `/dev` |
| [7041b4d](https://github.com/IshaanSirbhaiya/Tencent/commit/7041b4d) | 03:43 | `/dev/brain` Walnut engine view (graph, time slider, provenance, ontology) behind a locked-down read-only proxy; dev and start bind to 127.0.0.1 |
| [c140561](https://github.com/IshaanSirbhaiya/Tencent/commit/c140561) | 03:05 | Per-area mock handler registry; Wave-1 agent rules ([AGENT_RULES.md](contracts/AGENT_RULES.md)) |

### Backend (Internal Brain)

| Commit | Time | Change |
|---|---|---|
| [7bf3246](https://github.com/IshaanSirbhaiya/Tencent/commit/7bf3246) | 06:19 | Final-review fixes: graph edges from authorised current versions; SSE gate events held until sealed; caps after live auth; one deadline through read-through; tamper lock safety; map via the authz helper; trace failure ledgered; Host allowlist on the Brain and the mock; trace sync TTL; graph re-search after denials; edit-record flips a decision; neutral fixture disclaimer. Fresh full eval 0 / 5,958 and 0 / 400. **754 tests pass** (`test_final_review_fixes.py`) |
| [8582f4b](https://github.com/IshaanSirbhaiya/Tencent/commit/8582f4b) | 05:03 | Simulator reset runs a full reconciliation (revocations undone at once); offline plain-English audit search resolves document titles; plain-English window notes; `limit` no longer echoed as a filter |
| [e9ce768](https://github.com/IshaanSirbhaiya/Tencent/commit/e9ce768) | 03:36 | Round 2: sandbox tamper-demo endpoint, SSE stage events (`/ib/ask/stream`), Hy3 for plain-English audit search with rule fallback, silent-revocation seeding, fresh full eval, SECURITY §15 |
| [9826de0](https://github.com/IshaanSirbhaiya/Tencent/commit/9826de0) | 03:08 | Web proxy: unbuffered pass-through for `/ib/ask/stream` |
| [405d34b](https://github.com/IshaanSirbhaiya/Tencent/commit/405d34b) | 03:07 | Permission-aware `/ib/graph` (+ `openable`), `/ib/doc`, `/ib/status`, trace as a public receipt, version-bound authz helper, stats flows / window / cache, ledgered failures, replay seeder, eval summary, proxy allowlist |
| [e5406b9](https://github.com/IshaanSirbhaiya/Tencent/commit/e5406b9) | 02:26 | Stage timings, `/audit/stats`, `/audit/journey`, own-request `/ib/trace` for Developer mode |

### Security

| Commit | Time | Change |
|---|---|---|
| [8e53a0b](https://github.com/IshaanSirbhaiya/Tencent/commit/8e53a0b) | 10:13 | Proxy: a 7 MB body cap for `POST sim/upload-file` only (for the live file ingest demo, in progress); every other route keeps 64 KiB |
| [1079bd2](https://github.com/IshaanSirbhaiya/Tencent/commit/1079bd2) | 10:07 | Proxy allowlist: `GET /ib/graph/changes` for the main session and Compare slots (for live growth, in progress) |
| [7bf3246](https://github.com/IshaanSirbhaiya/Tencent/commit/7bf3246) | 06:19 | Host allowlist (`TrustedHostMiddleware`) on the Brain and the mock platforms; SSE gate events held (see Backend) |
| [bc8414c](https://github.com/IshaanSirbhaiya/Tencent/commit/bc8414c) | 05:23 | Web proxy refuses a non-loopback Host (DNS rebinding) and cross-site GETs; the engine snapshot is readable only by its own persona (compliance no longer) |

### Accessibility and i18n

| Commit | Time | Change |
|---|---|---|
| [ca7a2ff](https://github.com/IshaanSirbhaiya/Tencent/commit/ca7a2ff) | 06:06 | English starter questions tagged `lang="en"` in 简体中文 (WCAG 3.1.2) |
| [01d8ac7](https://github.com/IshaanSirbhaiya/Tencent/commit/01d8ac7) | 06:05 | From the final reviews: top bar reflow at 130% text; 简体中文 skip link and roles; Developer-mode English note; 24 px chips; unique landmarks; one live region; safe `?next` deep links with a session timeout; readable journey seals; audit year, i18n and tamper banner; speaker labels; `safeHref`; graph layout cleared on persona change |

### Tests and evaluation

| Commit | Time | Change |
|---|---|---|
| [ddadaa1](https://github.com/IshaanSirbhaiya/Tencent/commit/ddadaa1) | 06:40 | e2e results written up: [TEST_REPORT.md](TEST_REPORT.md) |
| [810ddbb](https://github.com/IshaanSirbhaiya/Tencent/commit/810ddbb) | 06:40 | Full e2e suite ported to the finals UI: **124 passed, 0 failed**, 38 env-gated skips (three consecutive runs); tour offer moves to the top on phones; dead `AnswerPanel` / `SourcesPanel` removed |

### Scripts

| Commit | Time | Change |
|---|---|---|
| [8b69cbd](https://github.com/IshaanSirbhaiya/Tencent/commit/8b69cbd) | 09:43 | `hy3_check.py` (is Hy3 live on TokenHub?); `demo_up.sh` turns live Hy3 on by itself when it answers |
| [dda470b](https://github.com/IshaanSirbhaiya/Tencent/commit/dda470b) | 03:46 | `demo_up.sh` starts the whole Demo Day stack (mock, Brain, seed, engine, web) on localhost |

### Docs, rubric and plan

| Commit | Time | Change |
|---|---|---|
| [fdbdec2](https://github.com/IshaanSirbhaiya/Tencent/commit/fdbdec2) | 09:47 | Miora prompt for the new cover ([COVER_MIORA_PROMPT.md](COVER_MIORA_PROMPT.md)); the brief lists the cover and narration tasks |
| [1da9dae](https://github.com/IshaanSirbhaiya/Tencent/commit/1da9dae) | 06:24 | Morning brief rewritten for the finished build |
| [4c79687](https://github.com/IshaanSirbhaiya/Tencent/commit/4c79687) | 06:19 | Backend test count 754 in the docs |
| [fcb6cab](https://github.com/IshaanSirbhaiya/Tencent/commit/fcb6cab) | 05:22 | Claims-consistency pass from the final Haiku review (built vs pending, honest Hy3 and deployment wording, broken paths, demo cast, run sheet aligned with the app) |
| [d090131](https://github.com/IshaanSirbhaiya/Tencent/commit/d090131) | 04:57 | Docs for judges: README quick start, INTERNAL_BRAIN 10-minute walk-through, STATUS, ARCHITECTURE §8, finals index, organiser update |
| [cb619d5](https://github.com/IshaanSirbhaiya/Tencent/commit/cb619d5) | 04:43 | Morning brief progress update |
| [8923ebd](https://github.com/IshaanSirbhaiya/Tencent/commit/8923ebd) | 03:49 | Rubric: graph / document claim row updated now both are built and tested |
| [ba20db3](https://github.com/IshaanSirbhaiya/Tencent/commit/ba20db3) | 03:09 | Morning brief and organiser update draft |
| [4403694](https://github.com/IshaanSirbhaiya/Tencent/commit/4403694) | 02:52 | Rubric index mapping each criterion to its deliverables ([rubric/README.md](rubric/README.md)) |
| [bf4a1d3](https://github.com/IshaanSirbhaiya/Tencent/commit/bf4a1d3) | 02:52 | Responsible-AI pack (system card, OWASP LLM red team, framework crosswalk and more), technical pack, CI workflow |
| [f174b6c](https://github.com/IshaanSirbhaiya/Tencent/commit/f174b6c) | 02:50 | Impact, feasibility, ROI calculator spec, AI usage map, innovation one-pager |
| [9a731e1](https://github.com/IshaanSirbhaiya/Tencent/commit/9a731e1) | 02:47 | HCD / UX evidence templates, demo story pack, competitor recon, Opus plan review |
| [8e479bc](https://github.com/IshaanSirbhaiya/Tencent/commit/8e479bc) | 02:39 | Research, rubric research, five plan reviews, plan v2 and decisions ([PLAN.md](PLAN.md), [DECISIONS.md](DECISIONS.md)) |

## 16 Oct 2026: the submission (baseline)

Last submission commit: [acb18f0](https://github.com/IshaanSirbhaiya/Tencent/commit/acb18f0) (one-file
conversation history PDF). At submission: 639 Internal Brain tests and 38 web e2e tests passed; the
leak evaluation gave 0 leaks; deck, video, cover and CodeBuddy evidence are in
[submission/](../../submission/README.md). The backend rules (two gates, identity from the token,
write-ahead ledger, one response shape) are unchanged since then.
