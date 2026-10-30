# Cloud handoff: Walnut DataBridge finals build (written Fri 30 Oct 2026, ~11:45 SGT)

**Read this first in any new or cloud session.** It is the full end-to-end context. The founder
(Ishaan) is away with no internet. Continue the work here; when he is back, the local session pulls
this branch and carries on.

## 1. The competition and the goal

- **Event:** Tencent Cloud AI CAN DO IT Hackathon Singapore 2026, FinTech track (sponsor Aspire).
  Team Walnut is a **Top-2 FinTech finalist**; 10 finalists out of 150+ teams.
- **Brief:** "The Internal Brain: context-aware enterprise knowledge system with RBAC, security
  logging & audit trail".
- **Demo Day: Tue 3 Nov 2026,** Marina Bay Sands. **5 min presentation + 1 min Q&A.** Mandatory
  rehearsal Mon 2 Nov 16:00–17:00.
- **Rubric,** 10 criteria × 10 points: Impact, Human-Centered Design, AI Interaction, Technical
  Execution, Feasibility, Demo & Storytelling, Innovation, UX & Accessibility, Responsible AI,
  Overall.
- **Judges:** Tencent WorkBuddy/CodeBuddy and RTC experts, AISG, and the five sponsors. They want
  Tencent products shown live.
- **Product:** "Walnut DataBridge" (walnut.sg calls it "DataBridge by Walnut"); "Internal Brain" is
  its ask product. Headline: "One connected brain for your business". Tagline: "Answers you can
  prove."
- Updated materials to the organisers by **Fri 30 Oct 23:59**. The email is in
  `docs/finals/ORGANISER_UPDATE.md`; the founder sends it.

## 2. Hard rules (never break them)

From `CLAUDE.md` and `docs/tencent/SECURITY.md`:
1. Filter before the model (Gate 1 index pre-filter, then a live Gate 2 source check; any error
   means deny).
2. Identity only from the signed token.
3. Write-ahead audit ledger; a ledger failure means 503.
4. **Restricted ≡ non-existent:** never show an asker hidden counts, denial reasons, or anything
   they can't open.
5. Every value has a source pointer, with the grounding wall.
6. No per-domain branching in the engine.
7. Synthetic data only.
8. Never claim more than a test proves.

The founder's working rules:
- **Maximum parallelism.**
- **Research → plan → 5-seat review** (Sonnet, Haiku, Opus, Fable, plus Codex when available)
  **→ build → 5-seat review → fix.**
- **Docs always live:** update FEATURE_CATALOGUE, CHANGELOG, README, INTERNAL_BRAIN, STATUS and the
  rubric index with every landing, and commit and push continuously.
- **Branch versioning:** `final_v1.0` frozen (verified), `final_v1.1` the integrated finals work,
  `final_v1.2` this WIP snapshot. Cut `final_v1.3` and so on at the next major change. Never commit
  to `Tencent` (it is the original 16 Oct submission, also tagged `submission-2026-10-16`).
- Never `git stash`, `git checkout --` or `git reset --hard`. Never push to walnut-upstream; only
  `IshaanSirbhaiya/Tencent`.
- Plain, sourced, numbered docs (like the DataBridge deck); no hype words.

## 3. State of the work

### Done, committed and pushed (on final_v1.1 / final_v1.2)

- **Backend:** developer views (stats, journey, trace receipt, graph, doc, status, SSE stages,
  tamper demo, the Hy3 audit parser), simulator reconcile, the version-bound authz helper, Host
  allowlist. **754 tests at the last full run.**
- **Eval:** full eval re-run, 0 leaks / 5,958, 0/400 differential, 0 injection failures.
- **Web:**
  - User mode;
  - Compare;
  - Presenter (10-step keyboard script, Stage theme, slides);
  - Developer mode: Mission control, Journey, Knowledge graph, Walnut engine, Analytics, Audit with
    the sandbox tamper demo, Simulator, Trust centre, Value calculator;
  - tours, voice dictation, preflight board. **124 e2e passing.**
- **Evidence pack** for all 10 criteria (`docs/finals/rubric/README.md`); the demo story, run sheet,
  Q&A banks, handouts, backup clips A/B/C.
- **Tencent tools:** CodeBuddy CB1–CB9 (CB7–CB9 on the finals code), the WorkBuddy MCP connector
  upgrade (live test 33/33), the Miora finals cover (`submission/06_cover/finals/`).
- **Demo day kit:** `Start/Stop/Reset/Start (offline) Walnut Demo.cmd`, `demo_day.ps1`,
  `demo_preflight.py`, the runbook and the founder checklist.
- **Video:** script, side-by-side plan, recorder, assembler, 6:08 test cut. **On hold** until the
  founder's teammates' updates; the founder records narration later.

### In this WIP snapshot (final_v1.2): finish, review, test, then integrate

Each has its own STATUS file written at checkpoint. Read it first.

| Track | Status file | Notes |
|---|---|---|
| **Act** (draft a reply / follow-up ticket with confirm) | `docs/finals/contracts/backend_v1.md` v3 | Built and tested (24 IB + 17 mock tests, 5 e2e); in the round-2 review now |
| **Deliverables** (cited table / chart / analysis; P1 summary and slides) | `docs/finals/deliverables/STATUS.md`, PLAN.md, review_*.md | Kill switch `IB_DELIVERABLES`; Opus review findings 1–5 are MANDATORY (number-span verifier, source-only checks, scale/locale rejects, PPTX formula guard, label binding) |
| **Live brain growth + add-document + absence proof + engine refresh** | `docs/finals/live_growth/STATUS.md`, PLAN.md | DEMO-ONLY toggle `IB_DEMO_FEATURES=1` plus the Settings "Demo features" switch; apply plan fixes L-1..L-6 |
| **File-upload ingest** (docx/xlsx/pptx/pdf?/csv/md/WhatsApp) | `docs/finals/contracts/uploads.md` | Security rules (a)–(e); demo-only flags |
| **World expansion** (HR, expenses, onboarding …) | `docs/finals/WORLD_EXPANSION_STATUS.md` | HIGH RISK: must be append-only and verified (the 4 demo moments, full eval 0 leaks); otherwise keep HEAD's world |
| **Live-ingestion kit** | `docs/finals/live_kit/LIVE_INGEST_KIT.md` | Done; the "after" half is unverified until the features land |

### Round-2 review (all changes since commit 7bf3246): act on these in the cloud

Files in `docs/finals/reviews/`:
- `round2_opus_act_tools_kit.md` (security/correctness, all code since 7bf3246);
- `round2_codex_act_tools_kit.md` (Codex);
- `round2_sonnet_ux.md` (UX/a11y);
- `round2_haiku_claims.md` (docs/claims);
- `round2_fable_judges.md` (judge impact and run sheet).
Any file missing means that seat didn't finish before the laptop went offline: re-run that seat in
the cloud (except Codex, which needs the founder's laptop). Fix every high and medium finding with a
test; record the dispositions in `docs/finals/reviews/round2_dispositions.md`.

## 3b. Live snapshot of the tracks in flight at hand-off (30 Oct, ~12:00 SGT)

What is on disk in this commit, file by file, so nothing depends on chat history. Agents were told to
stop at a clean step and write their STATUS file; if a STATUS file below is missing or says
"PLACEHOLDER", the agent was cut off: inspect the files listed and finish from there.

| Track | Files in this commit | Tests in this commit | State at hand-off |
|---|---|---|---|
| Act | `databridge_ib/actions.py`, app.py `/ib/act/*` routes, `connectors/jira.py`, mock `jira.py`/`state.py`/`common.py`, web `ActSheet.tsx`, `AnswerCard.tsx` act row, `lib/i18n/act.ts`, `lib/mock/extra/act.ts` | `test_act.py`, `test_act_e2e.py`, mock `test_mock_jira_create.py`, e2e `act.spec.ts` (+`act.config.ts`, not in the 124 count) | Built and green before the review; round-2 findings below still to fix |
| Deliverables | `databridge_ib/deliverables/` (`spec, build, evidence, verify, export, model, prompts, routes, service`), web `DeliverSheet.tsx`, `presenter/DeliverOverlay.tsx`, `presenter/deliver.ts`, `lib/i18n/deliver.ts`, `lib/mock/extra/deliver.ts` | `test_deliverables.py`, `test_deliverables_verify.py`, `test_deliverables_eval.py`, e2e `deliverables.spec.ts`, `deliverables.live.spec.ts` | Mid-build. Run its tests first; check the Opus review items 1–5 against `verify.py` one by one |
| Live growth / add-document / engine refresh | `live.py`, `engine_refresh.py`, `sim.py`, `bootstrap.py`, `config.py`, web `graph/LiveFeed.tsx`, `graph/useLiveChanges.ts`, `engine/EngineRefreshButton.tsx`, `sim/AddDocumentSheet.tsx`, `sim/DemoFeatures.tsx`, `sim/demo.ts`, settings page | `test_live_growth.py`, `test_sim_add_document.py`, `test_demo_features.py`, e2e `grow.spec.ts` (+`grow.config.ts`) | Mid-build. Must stay OFF unless `IB_DEMO_FEATURES=1` AND the viewer turns it on (D-24) |
| Uploads | `uploads.py`, `extract_files.py`, `connectors/drive.py`, mock `uploads.py` | `test_uploads.py`, `test_extract_files.py`, mock `test_mock_drive_upload.py` | Contract in `contracts/uploads.md`; backend hardening in progress |
| World expansion | `fixtures/enterprise/workplace.py`, `generate.py`, `world.json`, `manifest.json`, `graph_rows/*` | `test_workplace_layer.py`, `test_generator.py`, `test_fintech_layer.py` | Verified append-only (see `WORLD_EXPANSION_STATUS.md` §1). The eval re-run on the new world is the remaining gate |
| Narration determinism | `narrate.py` | `test_narrate_determinism.py` | Small fix; run with the suite |

Not committed on purpose: `submission/finals_video/walnut_databridge_finals_SILENT.mp4` and
`submission/finals_video/clips/` (74 MB test cut; the video is on hold).

### Round-2 findings already in (summaries; full tables in the files)

**Haiku, docs and claims** (`reviews/round2_haiku_claims.md`, partial): 16 fixes, all wording. Top ones:
- STATUS.md says CB7–CB9, the kit, the WorkBuddy tools and live growth are "not committed". They are
  (9afd1bd, a1ccc6c, 1079bd2).
- AGENTIC_ROADMAP/AI_USAGE_MAP say Act is "built, with tests". It was uncommitted until this commit;
  update to the final state.
- PRESENTER_KEYS and REHEARSAL_CHECKLIST say four HUD dots; the HUD has three.
- RUN_SHEET 2:35 says "Tencent, live"; make it "Tencent, off-app" (Hy3 is not live).
- Stale "733 backend tests" in rubric/research A, C, D; the current count is 754+.
- "10-step" presenter script: script.ts has 11 steps (14 with live growth).
- RUN_SHEET lacks the 3 optional grow steps.
- PRESENTER_KEYS §6 ids don't match script.ts.
- Tamper wording differs between RUN_SHEET and script.ts.

**Sonnet, UX and a11y** (`reviews/round2_sonnet_ux.md`):
- HIGH: 简体中文 Act sheets show English ticket type, description and draft body. Use `lang="en"` on
  those blocks, or make the API follow the UI language.
- HIGH: the "New here?" tour card covers the Act sheet at 390 px. Hide it while any dialog is open.
- MED: the ticket description `pre` is not focusable (axe scrollable-region-focusable). Add
  `tabIndex=0` and `role="region"`.
- MED (rule 4): login chips and starters hint at restricted content ("no FinCrime case access", "Q3
  breach report"). Trim them or say on stage that it is a demo device.
- MED: the confirm button should name the project; make the confirm row sticky at 390 px; check the
  dictation `aria-pressed` double-toggle.
- Low: preflight page overflows at 390 px (presenter-only).

**Fable, judges** (`reviews/round2_fable_judges.md`). Score about 77/100 now; about 83 once in-flight
items land; 87–89 with Hy3 live and a real hallway line. Top items, by points per hour:
1. **Stage blocker:** with demo features on, the presenter has 13 steps. Space #3 landed on "The brain
   grows" (18.6 s to settle), and the next Space did not advance. Gate the grow steps on `?grow=1` or a
   key, never on the Space path (`PresenterRoot.tsx:63`, `script.ts`).
2. **One number set everywhere:**
   - 7,374 (run sheet, QA bank, eval_summary.json) vs 0/5,958 (leave-behind, judge cards, cheat
     sheet, CHANGELOG);
   - slides.json says "Six CodeBuddy sessions" (it is nine);
   - the cheat sheet says S = Slides (S is the Stage theme).
   Then rebuild the handout PDFs.
3. Hallway test (founder, Sat). Otherwise delete the `[TESTER]` line.
4. The HUD reads "Model unknown · Ledger unknown" after P (401 before sign-in). Fetch status after
   `signInMain`.
5. Rachel's hero answer shows a "mlro.tan said" chip and "(synthetic case FC-2026-0147)" inline; the
   grow answer has a raw "jdoe:" prefix. Use the person's name and strip the parenthesis.
6. WorkBuddy GUI retest and a real clip C (laptop only).
7. Key B showed "This step has no recording" on a step with clip A (HEAD aborted). Check on the
   laptop.
8. Act as a prepared Q&A beat (key A), not a 5:00 step.
9. Deliverables: ship Table only (no model) behind D; model kinds stay "Needs the model".
10. Founder items: Hy3, then the eval; QA Q23/Q7; print.
Also: cover eyebrow "DATABRIDGE BY WALNUT" vs product "Walnut DataBridge"; FIX_LOG template empty;
grow page sentence "Ken can open none of the items on the left" on the stage screen (rule 4 risk);
ArrowLeft after a stuck step dropped to /present with no HUD.

**Deliverables agent checkpoint** (full detail in `deliverables/STATUS.md`):
- Passing: verify 69, deliverables 21, eval T16, act 19; tsc clean; e2e mock 4.
- Full IB suite NOT confirmed: 2 unidentified failures in the first quarter. Step 1 is to rerun and
  fix them.
- Docs not written: SECURITY §15, backend_v1 v4, FEATURE_CATALOGUE, AI_USAGE_MAP, D-25, QA_BANK Q10.
- P1 (Summary, Slides/.pptx with the T15 formula guard) untouched.

**Live growth agent checkpoint** (full detail in `live_growth/STATUS.md`):
- Done: live_growth 11/11, sim_add_document 29/29, demo_features 11/11, tsc clean, grow e2e 6/6.
  Live check passed: the note shows in 3.6 s; Ken sees nothing; engine refresh 34 s; 429 on a repeat;
  reset clean.
- **Full IB suite: 1036 passed, 2 failed.** Both are in `test_connector_drive.py`
  (`test_markdown_and_csv`, `test_crawl_full_then_quiet`): `connectors/drive.py` (the uploads change)
  returns nothing for the CSV/Markdown fixture. **These are the "2 unidentified failures" the
  Deliverables note mentions. Fix first.**
- Left:
  - decide whether `/sim/upload-file` is unmounted when the flag is off (today it answers 401, not the
    unknown-route 404);
  - make `tests/test_app_routes.py` loop over `app.iter_routes()` (it skips included routers today);
  - Fable item 1 (keep the grow steps off the Space path);
  - a reset step in the rehearsal checklist; a PROPOSED line in PRESENTER_KEYS.
- Laptop-only: `demo_up.sh stop/status` can't see the ports on this laptop (netstat is empty); use a
  health-probe-based stop.

**World expansion checkpoint** (full detail in `WORLD_EXPANSION_STATUS.md`): VERIFIED and kept.
- Append-only, record by record against HEAD.
- 464 pre-existing questions give byte-identical answers; the 4 demo moments pass.
- **Full eval on the new world:** 0 leaks / 7,374, 0/400 P2, 0/1,152 injection, recall 100%.
  `eval_summary.json` regenerated (e2e 124).
- **Fixed a real determinism bug** in `narrate.py` (tie-break by set order), with
  `test_narrate_determinism.py`.
- **New counts:** 34 people, 17 groups; 231 pages; 261 issues; 628 threads / 3,133 messages; 87
  files; 1,207 docs (1,204 indexed); 72 facts; 546 questions. Docs updated to them.
- **Next:**
  - re-run the full IB suite and put the count in STATUS and FEATURE_CATALOGUE (they still say 754);
  - on the laptop, `demo_up.sh start --fresh`, then refresh `live_kit/ABSENCE_PROOF.txt`;
  - re-run the eval if `databridge_ib` changes again.
- **Number consistency (Fable item 2):** the canonical numbers are now 7,374 / 400 / 1,152. Replace
  every "5,958" and "976" in the handouts, slides.json, CHANGELOG and ORGANISER_UPDATE, then rebuild the
  handout PDFs.
**Opus and Codex:** were still running at hand-off. If their files are missing, re-run Opus in the
cloud on `git diff 7bf3246..final_v1.2 -- databridge/`; Codex waits for the laptop.

**Uploads checkpoint** (full detail in `contracts/uploads.md` §8):
- **Tests:** uploads 53, extract_files 21, mock 151, connector_drive 65.
- **The two Drive failures are FIXED:** a header-only CSV is now indexed.
- **Full IB suite:** run 2 had no failures at 90%. Re-run to confirm.
- **Lock file:** `python-pptx==1.0.2` and `pypdfium2==5.14.0` are now added to
  `requirements-lock.txt` (the coordinator did this at hand-off). Check that `XlsxWriter` (a python-pptx
  dependency) installs; pin it if pip complains.
- **Hardening done:** reader worker process with a 15 s kill, XXE refusal, per-format isolation,
  WhatsApp parser, CSV rows as citable lines, filename fix.
- **Gaps:** no OCR; WhatsApp phone numbers not masked; one file at a time; no PDF sample in
  `make_files.py`.

**Opus round-2 checkpoint** (`reviews/round2_opus_act_tools_kit.md`, PARTIAL). Fix with tests:
1. **MED:** Act has no rate limit, and the 500-proposal store is global, so evicting the oldest drops
   other people's proposals. Add a per-person 429 (`action.failed` stage `rate`) and a per-person cap
   (`actions.py:435-441`, `app.py:478-505`).
2. **MED (rule 4):** ticket description links expose restricted doc ids, channels and spaces to the
   project's browsers. Route links through an IB redirect that runs Gate 1 and Gate 2 for the clicker
   (`actions.py:404-407`).
3. **MED (honesty):** offline mock labels say Hy3/TokenHub unless `NEXT_PUBLIC_IB_MOCK_MODEL=extractive`;
   DeepSeek fixture, mock `/ib/status` and mock deliverables ignore the switch. Make extractive the
   default everywhere (`lib/mock/fixtures.ts:106-110`, `mock/extra/analytics.ts:667-678`,
   `mock/extra/deliver.ts:133`).
4. **MED:** `live_kit/check_absent.py --sim` reads `count`; the endpoint returns
   `documents_containing` (`check_absent.py:157`).
5. **LOW:** confirm doesn't re-run `context()` on the cited docs (`actions.py:459-499`).
6. **Smaller:**
   - the 409 "nothing changed" message after a timeout;
   - the default summary echoes the question;
   - the presenter P key makes ledgered asks, but its docstring says it doesn't;
   - the Leo answer cache isn't cleared on logout;
   - `/present/preflight` has no auth and no `IB_DEPLOYED` check;
   - the `demo_up.sh` `--fresh` guard accepts `var/../..` (reject `..`);
   - `fill_template.py --name` path traversal and CSV formula injection;
   - the video's hard-coded test counts;
   - `.gitignore` missing `*.tok`.
7. **NOT reviewed yet (do next):**
   - the MCP tools (`mcp_server.py`, `audit_mcp_server.py`, `docview.py`);
   - `ModelChain.draft` grounding;
   - `jira.creatable` / `create_issue`;
   - the Act web components;
   - voice;
   - `demo_day.ps1`, `demo_preflight.py`, the `.cmd` launchers.

**Codex round-2 (`reviews/round2_codex_act_tools_kit.md`, added after the cloud move).** 8 findings:
0 critical, 2 high, 5 medium, 1 low. Codex hit its usage limit partway, so it is partial.
- **HIGH:** if the Jira create response is lost, a retry can create a duplicate issue. Use an
  idempotency key, or look the issue up before retrying.
- **HIGH:** the Windows cleanup in the demo kit can force-kill unrelated python/node processes. Kill
  only the PIDs listening on our ports.
- **MED:** confirm does not re-check access to the cited docs (same as Opus #5).
- The rest are in the file.
- **Round 2b (partial):**
  - the proxies are OK;
  - `hy3_check.py` treats an empty JSON reply as success;
  - `fill_template.py` path escape (same as Opus).
- **Not reviewed:** secret leakage into generated files (handouts, conversation PDF, workbuddy test,
  video scripts); `check_absent.py` and `make_files.py`; the presenter and `personas.ts`. This is a
  Codex TODO for the laptop.

**Scripts/video/evidence review (a4b24bb):** the agent may not have written
`reviews/round2_scripts_video_evidence.md` before hand-off. If it is missing, fold that scope into
the Opus re-run.

## 4. What to do next, in order

1. **Make each WIP track pass its own tests,** then the FULL suites:
   - IB backend (from `databridge/`):
     `PYTHONPATH="apps/internal_brain:packages/core:packages/harness:packages/adapter:packages/retrieval" python -m pytest apps/internal_brain/tests -q -p no:cacheprovider`
     (on Linux use `:` separators; the expectation is 754+ green);
   - the mock platforms: `PYTHONPATH=apps/mock_platforms python -m pytest apps/mock_platforms/tests -q`;
   - the fixtures: `python -m pytest fixtures/enterprise/tests -q`;
   - web: `cd apps/internal_brain_web && npm ci && npx tsc --noEmit && npx playwright install chromium && npx playwright test` (it builds the mock and runs on 3101).
2. **Fix all round-2 review findings.**
3. **Finish Deliverables P0,** then live growth, then uploads. Each runs its 5-seat review
   (Sonnet/Haiku/Opus/Fable in the cloud; Codex later locally) before it is turned on.
4. **World expansion:** only if it can be fully verified, including the full eval
   (`python -m evals.internal_brain.run` with `IB_FORCE_OFFLINE=1`, then `scripts/eval_summary.py`).
5. **Docs pass:** FEATURE_CATALOGUE, CHANGELOG, README, INTERNAL_BRAIN, STATUS, the rubric index,
   SECURITY §15, backend_v1.md, the test counts, FOUNDER_TODO.
6. **Commit small and often to final_v1.2** (cut final_v1.3 for the next major change) and push.

## 5. Cloud limits (what you cannot do here)

- **No TokenHub key** (it lives only in the git-ignored `databridge/.env.ib.local` on the laptop).
  Run everything with `IB_FORCE_OFFLINE=1`. Hy3 is not live anyway (TokenHub 402 until the founder
  activates it).
- **No Codex.** Leave a Codex review TODO for when the laptop is back.
- **No live demo stack** on the laptop ports (3100/8100/8200/8300). Use the in-process TestClient
  tests, and run the web e2e in mock mode.
- **No screen or WorkBuddy GUI work,** no video re-record (on hold anyway), no founder-only tasks.

## 6. The founder's pending tasks (see `docs/finals/FOUNDER_TODO.md`)

- Activate Hy3 in TokenHub.
- Send the organiser email before 23:59 today.
- Hallway test (Sat).
- Narration, after the video re-record.
- Stage roles and rehearsal.
- Windows settings, shortcuts and handout printing (Mon).
- The WorkBuddy GUI test (needs his OK for screen control).

## 7. When the founder is back (the local session)

`git fetch origin && git switch final_v1.2` (or the latest `final_v1.x` the cloud cut) and pull.
Then:
- run Codex on everything the cloud built;
- restart the demo stack (`Start Walnut Demo.cmd`; `--fresh` only if the world changed and is
  verified);
- do live QA;
- do the WorkBuddy GUI test;
- re-record the video when the founder says so.
