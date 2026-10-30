# Round 2 claims and consistency review (partial)

Scope: read-only check of judge-facing docs against the code, as of HEAD e9d3dd2. Run cut short by a laptop time limit. Items marked "not checked" were not reviewed.

Issue count: 17 verified issues. Not yet checked: docs/tencent/SECURITY.md §15, AI_USAGE_MAP (beyond row 11), TENCENT_TOOLS_REPORT, the submission READMEs, handouts_src, QA_BANK, JUDGE_QUESTION_BANK, and link checks across all files.

| # | file:line | Current text | Problem | Exact replacement |
|---|---|---|---|---|
| 1 | STATUS.md:43 | "Being built now: Act ..., live graph growth, the one-click demo kit, CodeBuddy CB7 to CB9 and new WorkBuddy tools ... Not committed" | Wrong. CB7–CB9 and WorkBuddy tools are in 9afd1bd; demo kit in a1ccc6c; live growth in 1079bd2. Only Act is uncommitted (databridge_ib/actions.py is untracked or modified in the working tree). | "In progress, not committed: Act (draft a reply / follow-up ticket) and the new demo video." |
| 2 | STATUS.md:25 | "WorkBuddy connectors ... Built at submission, unchanged" | Stale. New MCP tools (answer receipt, what I can open, org summary, tamper demo) were added in 9afd1bd. | "Built; new MCP tools added 30 Oct (9afd1bd); live WorkBuddy test 33/33." |
| 3 | STATUS.md §1 Built table | Act, live-ingestion kit, printable handouts, video tooling, Miora cover, persona chips absent | Built items missing from the table. | Add rows: Act (uncommitted, see 1), live-ingestion kit (6c868bb), printable handouts (f0f84bd), video tooling (215e31c), persona chips (ec502cd). |
| 4 | STATUS.md:19 | "Presenter mode (10-step keyboard script ...)" | script.ts STEPS has 11 steps (title through close); 14 when the live-growth steps are on. | "Presenter mode (11-step keyboard script; 14 with the live-growth steps)" |
| 5 | STATUS.md:7 | "As of commit `f0f84bd`" | Stale. HEAD is e9d3dd2. | "As of commit e9d3dd2." |
| 6 | docs/finals/rubric/ai_innovation/AGENTIC_ROADMAP.md:6 and AI_USAGE_MAP.md:30 | "Act is Built, with tests" | Code and tests exist (actions.py, test_act.py 19 tests, test_act_e2e.py 4 tests) but are uncommitted. CHANGELOG and STATUS say so. | "Act is built in the working tree with tests (19 unit, 4 e2e); not yet committed." |
| 7 | docs/finals/rubric/research/A_impact_feasibility.md:138, :320, :356; C_ai_interaction_innovation.md:241; D_technical_responsible_ai.md:17, :109, :450 | "733 backend tests" | Stale. Current count is 754 (commit 7bf3246). Act tests are not in either count. | "754 backend tests (commit 7bf3246)" |
| 8 | docs/finals/rubric/demo_story/PRESENTER_KEYS.md §1 (line 14), §5 (line 46), and REHEARSAL_CHECKLIST.md:48 | "four status dots: API, model (TokenHub), ledger, WorkBuddy" | HUD has three dots: API, model, ledger (controller.ts refreshHealth, ~lines 205–218). No WorkBuddy dot. | "three status dots: API, model, ledger" |
| 9 | docs/finals/rubric/demo_story/PRESENTER_KEYS.md §2 (Esc row) | "Esc closes the sources sheet and returns focus to the chip" | controller.ts:315–316 makes Esc toggle the HUD hidden. Sheet close may be a separate component handler (not verified). Keys ArrowLeft (back), S (stage), D (deliverables), 2 (open citation 2), ? (legend) are missing from the doc. | "Esc: hide or show the HUD. ArrowLeft: back one step. S: stage theme. D: deliverables. 2: open citation [2]. ?: key legend." (also confirm sheet-close owner) |
| 10 | docs/finals/rubric/demo_story/RUN_SHEET.md:2.35–2.40 heading row | "Tencent, live" | Hy3 is not live (STATUS.md:40). The step is an off-app WorkBuddy beat (script.ts offApp: true). | "Tencent, off-app" |
| 11 | docs/finals/rubric/demo_story/RUN_SHEET.md:2.20–2.28 spoken line | "I change one decision in a copy of the log, from 'forbidden' to 'verified'" | script.ts:155 says "denied to allowed". Two wordings for the same tamper. | Pick one and match script.ts. Suggested: "I change one word in a copy of the log, denied to allowed." |
| 12 | docs/finals/rubric/demo_story/RUN_SHEET.md §2 (whole sheet) | No mention of the three live-growth steps | script.ts inserts grow, grow-post, grow-ask after compare-person when demo features are on. An extra Space press changes the count and the run sheet is silent. | Add a line after 0:50: "If live growth is on, Space x3 (grow, post, ask both) runs here: see PLAN §11." |
| 13 | docs/finals/rubric/demo_story/RUN_SHEET.md §2 vs PRESENTER_KEYS.md §6 | RUN_SHEET says step 2:20 "Space runs the sandbox tamper, then Space runs Verify" | script.ts orders verify (2:10) before tamper (2:20), and each step only navigates to a query string. The sheet and PRESENTER_KEYS §6 (steps 0–11, with 3b/5b/6b) do not match the 11 real step ids. | Replace PRESENTER_KEYS §6 table with the real ids from script.ts (title, compare-people, compare-person, citation, journey, verify, tamper, workbuddy, proof, business, close), and mark it as the live order. |
| 14 | docs/finals/TEST_REPORT.md and FEATURE_CATALOGUE.md:20, :529; README.md:331 | "124 passed, 0 failed, 38 skips" | act.spec.ts (5 tests) exists and is not in the count. voice.spec.ts is excluded and noted (correct). | Add: "act.spec.ts (5 tests) and voice.spec.ts (2) are not in this count; re-run pending." |
| 15 | docs/finals/rubric/responsible_ai/README.md:26 | link to `TEAM.md` | Not verified to exist at that path. | Check the target. If it is missing, point to the team list in STATUS.md or remove the link. |
| 16 | README.md:19 and INTERNAL_BRAIN.md:33, :369 | Hy3 wired, "not run live" | Consistent with STATUS.md:40 and the code. No fix needed. Recorded as checked. | none |
| 17 | Names and tagline | "Walnut DataBridge", "Team Walnut", "Ishaan · Dev · Mehul · Aaruni", "One connected brain for your business.", "Answers you can prove." | Consistent across the slides (i18n/presenter.ts:143–145), the run sheet and STATUS. COVER_MIORA_PROMPT uses "DataBridge by Walnut", which the brief allows. No fix needed. | none |

Top 10 (by judge impact):
1. STATUS.md:43 says committed work is uncommitted (item 1).
2. STATUS.md:25 WorkBuddy tools "unchanged" (item 2).
3. AGENTIC_ROADMAP.md:6 and AI_USAGE_MAP.md:30 call uncommitted Act "Built" (item 6).
4. PRESENTER_KEYS.md §1 says four HUD dots; HUD has three (item 8).
5. RUN_SHEET.md heading "Tencent, live" while Hy3 is not live (item 10).
6. Stale 733 counts in rubric/research A, C and D (item 7).
7. STATUS.md:19 "10-step" script, actually 11 (item 4).
8. RUN_SHEET omits the optional live-growth steps (item 12).
9. PRESENTER_KEYS §6 step list does not match script.ts ids (item 13).
10. Tamper wording differs between RUN_SHEET and script.ts (item 11).

Files read: STATUS.md, README.md (grep), INTERNAL_BRAIN.md (grep), docs/finals/rubric/demo_story/RUN_SHEET.md, PRESENTER_KEYS.md, databridge/apps/internal_brain_web/components/presenter/{controller.ts, script.ts, slides/Slides.tsx}, lib/i18n/presenter.ts, git log 7bf3246..HEAD, git status (read-only). No git changes and no other edits were made.
