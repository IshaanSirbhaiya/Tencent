# Claims audit (final, 30 Oct 2026 evening)

Scope: README.md, INTERNAL_BRAIN.md, STATUS.md, `docs/**` (not `docs/finals/reviews/**`; research notes are only annotated), handout sources, slide JSON, the video script and cues, and UI strings (`lib/i18n`, `components/trust`, `components/presenter`). Branch `final_v1.4-wip`, with `origin/final_v1.3` and `origin/final_v1.2-cloud` merged in. README "The platform underneath", README "Built with Tencent" and `docs/PLATFORM.md` were not touched (another agent owns them; see "Left for others").

Verified facts used: clean-checkout cloud run on the lock `final_v1.3`: Internal Brain 1,274 passed / 0 failed / 4 skipped (1,278 collected); web e2e 185 / 0 / 42; mock platforms 159; fixtures 47; `tsc` clean. Leak eval: 0 leaks in 7,374 requests (3,687 per path), 0 of 400 P2, 0 hard failures in 1,152 injection-relevant requests, recall 100%. CI: `gh run list` shows 17 successful runs, 0 failed (24 were cancelled by newer pushes), latest run 40990413962. CodeBuddy: nine sessions (CB1 to CB9). WorkBuddy live test 33 of 33 (`docs/finals/rubric/ai_innovation/workbuddy_live_test.md`). Hy3 via TokenHub: wired, not run live. Stand-in model: only on `final_v1.4`, off by default.

## Counts by category

| Category | Fix rows |
|---|---|
| 1 False negatives (said not done, now done) | 51 |
| 2 False positives (claims beyond the proof) | 37 |
| 3 Stale numbers stated as current | 11 |
| 4 Names (stand-in model named by its provider) | 4 |
| 4b Build-tool mentions trimmed | 33 |
| 5 Consistency | 7 |
| **Total** | **143** |

One row can stand for several occurrences (the review-seat relabelling rows count each file once). File:line is the line before the edit.

## Fix table

| file:line | issue | category | fix |
|---|---|---|---|
| `STATUS.md:11` | 'nothing merged' is stale: final_mehulupdates merged on final_v1.4 (cddd878, e47069a) | FN | fixed |
| `STATUS.md:28` | 'not committed / re-run pending' is done: committed 751a687, in the 30 Oct cloud e2e run | FN | fixed |
| `STATUS.md:36` | v3.2 'working tree, not committed' is committed (cb7b1a1) | FN | fixed |
| `STATUS.md:38` | Deliverables round-4 fixes 'working tree' are committed | FN | fixed |
| `STATUS.md:38` | 'not committed' is stale | FN | fixed |
| `STATUS.md:39` | upload folder picker 'not committed' is committed | FN | fixed |
| `STATUS.md:45` | round-4 fixes 'not committed' are committed; also review-seat model names (build-tool trim) | FN | fixed |
| `STATUS.md:46` | stage-QA fixes 'working tree' are committed | FN | fixed |
| `STATUS.md:48` | 'nothing merged' stale | FN | fixed |
| `STATUS.md:74` | review-seat name trimmed (DEV_PROCESS.md keeps the list) | BT | fixed |
| `STATUS.md:75` | 'commit the round-4 fixes and run everything again' is done | FN | fixed |
| `STATUS.md:77` | 'demo_day.ps1 not run' is done (FOUNDER_TODO laptop checks) | FN | fixed |
| `STATUS.md:76` | pending row said the merge had not started; the first branch is merged on final_v1.4 | FN | fixed |
| `STATUS.md:61` | STATUS carried no CI result; CI is green (17 runs, 0 failed) | FN | fixed |
| `docs/finals/FEATURE_CATALOGUE.md:8` | header said round-4 fixes were 'in the working tree and not committed yet'; they are committed and verified on final_v1.3 | FN | fixed |
| `docs/finals/FEATURE_CATALOGUE.md:238` | presenter.spec 're-run pending' is done | FN | fixed |
| `docs/finals/FEATURE_CATALOGUE.md:239` | 'Not committed yet' is committed | FN | fixed |
| `docs/finals/FEATURE_CATALOGUE.md:588` | 're-run pending' is done | FN | fixed |
| `docs/finals/FEATURE_CATALOGUE.md:643` | 're-run pending' is done | FN | fixed |
| `databridge/apps/internal_brain_web/components/trust/data.ts:37` | 're-run pending' is done: the eval was re-run on the finals pipeline 30 Oct and again in the cloud | FN | fixed |
| `databridge/apps/internal_brain_web/components/trust/data.ts:75` | 're-run pending' is done | FN | fixed |
| `databridge/apps/internal_brain_web/components/trust/data.ts:247` | 'From the 14 Oct run; re-run pending' is stale (re-run done 30 Oct) | FN | fixed |
| `databridge/apps/internal_brain_web/lib/i18n/tour.ts:78` | 'a real company' in a synthetic-only demo | FP | fixed |
| `databridge/apps/internal_brain_web/lib/i18n/tour.ts:205` | '真实存在的公司' in a synthetic-only demo | FP | fixed |
| `databridge/apps/internal_brain_web/lib/i18n/tour.ts:48` | tour implied a model writes every answer; Hy3 is not live | FP | fixed |
| `databridge/apps/internal_brain_web/lib/i18n/tour.ts:175` | zh tour implied a model writes every answer | FP | fixed |
| `submission/finals/handouts_src/build_handouts.mjs:92` | handout said keyboard-only; D-32/PRESENTER_KEYS say keyboard-driven by default | CO | fixed |
| `submission/finals/handouts_src/html/judge_cards.html:83` | built HTML source said keyboard-only | CO | fixed |
| `docs/finals/rubric/technical/COMPLETENESS_CHECKLIST.md:11` | 'not yet run green on GitHub': CI is green, 17 runs, 0 failed | FN | fixed |
| `docs/finals/rubric/technical/COMPLETENESS_CHECKLIST.md:27` | CI row: 'Not yet run green' is false; branch list stale | FN | fixed |
| `docs/finals/rubric/technical/README.md:17` | CI branch list stale | FN | fixed |
| `docs/finals/rubric/responsible_ai/README.md:31` | 'Re-run pending' is done | FN | fixed |
| `docs/finals/rubric/responsible_ai/README.md:32` | 'Re-run pending' is done | FN | fixed |
| `docs/finals/TEST_REPORT.md:42` | 're-run pending' is done | FN | fixed |
| `README.md:183` | 'to become final_v1.3' is stale: final_v1.3 exists as the verified lock | FN | fixed |
| `INTERNAL_BRAIN.md:334` | 'to become final_v1.3' is stale: final_v1.3 exists as the verified lock | FN | fixed |
| `docs/finals/TEST_REPORT.md:3` | 'to become final_v1.3' is stale: final_v1.3 exists as the verified lock | FN | fixed |
| `STATUS.md:57` | 'to become final_v1.3' is stale | FN | fixed |
| `STATUS.md:9` | 'to become final_v1.3' is stale | FN | fixed |
| `docs/finals/TEST_REPORT.md:9` | 'to become final_v1.3' is stale | FN | fixed |
| `docs/finals/README.md:21` | 'nothing merged' stale | FN | fixed |
| `docs/finals/README.md:22` | review-seat model names trimmed (file names unchanged) | BT | fixed |
| `docs/finals/video/VIDEO_PLAN.md:203` | 'six sessions' in the scene-14 row; the card says nine | SN | fixed |
| `docs/finals/README.md:7` | 'to become final_v1.3' is stale | FN | fixed |
| `docs/finals/README.md:48` | review-seat model names trimmed (file names unchanged; DEV_PROCESS.md keeps the list) | BT | fixed |
| `docs/finals/README.md:55` | review-seat model names trimmed | BT | fixed |
| `docs/finals/rubric/demo_story/PRESENTER_KEYS.md:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 12 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `docs/finals/rubric/demo_story/RUN_SHEET.md:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 2 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `docs/finals/rubric/demo_story/QA_BANK.md:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 2 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `submission/finals/handouts_src/build_handouts.mjs:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 2 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `docs/finals/FEATURE_CATALOGUE.md:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `docs/finals/video/READINESS_30OCT.md:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `databridge/apps/internal_brain_web/components/presenter/ClipOverlay.tsx:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `databridge/apps/internal_brain_web/components/presenter/PresenterRoot.tsx:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `databridge/apps/internal_brain_web/components/presenter/controller.ts:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 4 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `databridge/apps/internal_brain_web/components/presenter/deliver.ts:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 4 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `databridge/apps/internal_brain_web/components/presenter/session.ts:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `databridge/apps/internal_brain_web/components/presenter/script.ts:various` | review-seat model labels (Fable/Opus/Sonnet/Haiku) in round-N tags: 1 occurrence(s) | BT | relabelled "round-N review #k"; file names and dated history untouched |
| `docs/tencent/DEV_PROCESS.md:7` | single build-tools statement added (Tencent tools first; WorkBuddy 33/33 verified in workbuddy_live_test.md) | BT | fixed |
| `docs/tencent/DEV_PROCESS.md:28` | implied the whole engine (incl. retrieval) answers; the Brain uses core, harness, convert and the grounding wall only | FP | fixed |
| `docs/tencent/ARCHITECTURE.md:242` | said the Brain uses packages/retrieval by import; it keeps its own index (index.py:1-13) | FP | fixed |
| `INTERNAL_BRAIN.md:411` | implied the whole evidence engine (incl. retrieval) is used; index.py:1-13 | FP | fixed |
| `docs/finals/rubric/demo_story/QA_BANK.md:26` | 'reused the Walnut evidence engine' overstated; the Brain keeps its own index, does not use packages/retrieval | FP | fixed |
| `STATUS.md:15` | review-seat model names trimmed in link labels | BT | fixed |
| `docs/finals/FEATURE_CATALOGUE.md:551` | review-seat model names trimmed; pointer to DEV_PROCESS | BT | fixed |
| `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md:52` | name-drop trimmed; pointer to the single build-tools statement | BT | fixed |
| `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md:12` | 'all six CodeBuddy sessions' stated as the current count; nine now | SN | fixed |
| `docs/finals/rubric/responsible_ai/README.md:42` | model name-drop replaced by pointer to the single statement | BT | fixed |
| `docs/finals/rubric/responsible_ai/AI_VERIFY_SELF_ASSESSMENT.md:67` | name-drop trimmed | BT | fixed |
| `docs/finals/rubric/responsible_ai/OWASP_LLM_REDTEAM.md:4` | name-drop trimmed | BT | fixed |
| `docs/finals/rubric/responsible_ai/OWASP_LLM_REDTEAM.md:42` | review-seat label trimmed | BT | fixed |
| `docs/finals/rubric/responsible_ai/SYSTEM_CARD.md:167` | name-drop trimmed | BT | fixed |
| `docs/finals/rubric/technical/COMPLETENESS_CHECKLIST.md:24` | name-drop trimmed | BT | fixed |
| `docs/finals/rubric/technical/EVIDENCE_CARD.md:78` | name-drop trimmed | BT | fixed |
| `docs/finals/FOUNDER_TODO.md:25` | name-drop trimmed | BT | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:534` | name-drop trimmed | BT | fixed |
| `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md:4` | pointer to the single build-tools statement | BT | fixed |
| `docs/finals/rubric/responsible_ai/README.md:7` | pointer to the single build-tools statement | BT | fixed |
| `databridge/apps/internal_brain_web/components/presenter/script.ts:189` | "Tencent is inside the product" overstates (Hy3 not live; WorkBuddy is a front door) | FP | fixed |
| `docs/finals/rubric/demo_story/RUN_SHEET.md:61` | "Tencent is inside the product" overstates (Hy3 not live; WorkBuddy is a front door) | FP | fixed |
| `docs/finals/rubric/demo_story/QA_BANK.md:63` | name-drop replaced; DEV_PROCESS.md keeps the list | BT | fixed |
| `docs/finals/rubric/research/A_impact_feasibility.md:138` | research note states 1,131/166 as current | SN | fixed |
| `docs/finals/rubric/research/C_ai_interaction_innovation.md:241` | research note states 1,131/166 as current | SN | fixed |
| `docs/finals/rubric/research/D_technical_responsible_ai.md:96` | research note states 1,131/166 as current | SN | fixed |
| `docs/finals/rubric/demo_story/QA_BANK.md:33` | 'CodeBuddy to build' read as the whole build (Q18, both variants) | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:418` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals_video/narration.srt:267` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals_video/narration_short.srt:203` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals_video/narration_full.srt:319` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals_video/narration_cues.md:120` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals_video/narration_cues_short.md:95` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals_video/narration_cues_full.md:133` | narration "connectors to every source" overclaims; the platform has connectors to many sources | FP | fixed |
| `submission/finals/handouts_src/build_handouts.mjs:56` | 'a real customer' in a synthetic-only handout | FP | fixed |
| `submission/finals/handouts_src/build_handouts.mjs:68` | 'CodeBuddy built and reviewed the code' overstates its share of the build | FP | fixed |
| `submission/finals/handouts_src/html/leave_behind_A4.html:72` | 'a real customer' in a synthetic-only handout | FP | fixed |
| `submission/finals/handouts_src/html/leave_behind_A4.html:84` | 'CodeBuddy built and reviewed the code' overstates its share of the build | FP | fixed |
| `submission/finals/handouts_src/build_handouts.mjs:19` | QR/link pointed to the frozen final_v1.0; the organiser email links final_v1.3 (built HTML/PDF need a rebuild) | SN | fixed |
| `submission/finals/handouts_src/build_handouts.mjs:79` | handout footer named final_v1.0 | SN | fixed |
| `README.md:126` | 'a real company' in a synthetic-only demo | FP | fixed |
| `docs/finals/video/record_finals.cjs:333` | on-screen caption 'a real customer' in a synthetic-only demo | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:184` | on-screen caption 'a real customer' in a synthetic-only demo | FP | fixed |
| `docs/finals/video/VIDEO_PLAN.md:19` | 'a real customer' in a synthetic-only demo | FP | fixed |
| `docs/finals/CHANGELOG.md:30` | stand-in model named by its provider | NM | fixed |
| `docs/finals/CHANGELOG.md:30` | stand-in model named by its provider | NM | fixed |
| `docs/finals/CHANGELOG.md:30` | 'not merged' is stale | FN | fixed |
| `docs/finals/CHANGELOG.md:30` | stand-in model named by its provider | NM | fixed |
| `docs/finals/CHANGELOG.md:30` | stand-in model named by its provider | NM | fixed |
| `docs/finals/CHANGELOG.md:35` | 'to become final_v1.3' is stale | FN | fixed |
| `docs/finals/CHANGELOG.md:29` | 'Plan only, nothing merged' is stale | FN | fixed |
| `docs/finals/CHANGELOG.md:52` | 'being merged (work in progress)' is stale | FN | fixed |
| `docs/finals/MERGE_PLAN.md:6` | 'suites not yet run' is stale: the e2e suite has run on v1.4 | FN | fixed |
| `docs/finals/MERGE_ANALYSIS_final_mehulupdates.md:4` | 'suites have not been run' is stale | FN | fixed |
| `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md:56` | 'demo_up.sh should refuse to start ... not changed here' is done (7645420) | FN | fixed |
| `README.md:185` | README Numbers had no CI result; CI is green (17 runs, 0 failed) | FN | fixed |
| `INTERNAL_BRAIN.md:336` | INTERNAL_BRAIN had no CI result; CI is green (17 runs, 0 failed) | FN | fixed |
| `README.md:26` | README did not say how final_v1.4 differs from the lock | CO | fixed |
| `STATUS.md:3` | STATUS did not say how final_v1.4 differs from the lock | CO | fixed |
| `docs/finals/README.md:7` | finals README did not say how final_v1.4 differs from the lock | CO | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:316` | 'She never sees the answer itself' contradicts 'cannot read answers by default' and 'reveal needs a reason' | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:418` | 'connectors to every source' (there are four) and the platform list did not say it is not part of answering | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:171` | 'is a criminal offence' stated as universal law; the 16 Oct line said 'can be' | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:388` | 'It's an MCP agent' (WorkBuddy is the host; our server is the MCP server) | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:380` | 'an MCP agent' (WorkBuddy is the host) | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:53` | 'an MCP agent' | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:582` | 'an MCP agent' | FP | fixed |
| `docs/finals/video/VIDEO_SCRIPT_FINALS.md:3` | video script header named final_v1.2 | CO | fixed |
| `docs/finals/video/VIDEO_PLAN.md:29` | 'an MCP agent' | FP | fixed |
| `docs/finals/video/VIDEO_PLAN.md:157` | 'an MCP agent' | FP | fixed |
| `docs/finals/rubric/README.md:25` | 'axe in every e2e test' overclaims; axe runs in the specs that call it | FP | fixed |
| `docs/finals/rubric/README.md:25` | 'axe in every e2e test' overclaims; axe runs in 12 of 31 spec files | FP | fixed |
| `docs/finals/rubric/README.md:20` | model-off wording only; model-ON variant added | CO | fixed |
| `docs/finals/video/VIDEO_PLAN.md:36` | spoken backend count 1,274 vs the script's 1,278 (1,278 collected, 1,274 pass, 4 skipped) | SN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:67` | spoken backend count aligned with the script (1,278 collected; 1,274 pass, 4 skipped) | SN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:232` | spoken backend count aligned with the script | SN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:90` | 'Today's test assembly is 6:08' is stale | FN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:92` | planned cut lengths stale | SN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:108` | '6:08 in the test assembly' is stale | FN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:231` | model-OFF wording only in the 16 Oct side-by-side table | FN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:231` | model-OFF wording only | FN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:248` | line is no longer in the script | FN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:249` | quoted line differs from the script | FN | fixed |
| `docs/finals/video/VIDEO_PLAN.md:250` | quoted line differs from the script | FN | fixed |

## Found, not fixed (decision or action for the founder)

| file:line | issue | category | fix |
|---|---|---|---|
| `submission/finals/leave_behind_A4.pdf` | Built PDF shows 1131 backend tests and 166 browser tests; the QR code and footer point to `final_v1.0`. The source (`handouts_src/build_handouts.mjs`) now links `final_v1.3` | 3 | founder: rebuild (`node build_handouts.mjs`); not rebuilt on request |
| `submission/finals/judge_cards.pdf` | Built PDF shows "166 browser tests" and the old "Keyboard-only demo" wording | 3, 5 | founder: rebuild |
| `submission/finals/presenter_cheatsheet.pdf` | No stale counts found; built 30 Oct 05:32, before the evening presenter changes, so check its keys against `PRESENTER_KEYS.md` | 3 | founder: rebuild with the others |
| `submission/finals/handouts_src/html/*.html` | Built HTML carries the old QR code (`final_v1.0`); text fixes were applied by hand, the QR needs a rebuild | 3 | founder: rebuild |
| `submission/finals_video/cards/11_codebuddy.png` | Card says "Six CodeBuddy sessions" (READINESS_30OCT.md line 28); `14_proof.png` and the other cards predate the 1,278 / 185 label | 3 | founder: re-run `record_finals.cjs cards` |
| `README.md` "The platform underneath" (lines 8 to 26) | Lists `retrieval`, the federated engine, adapter agents and the graph as what Internal Brain "runs on"; the Brain keeps its own index (`databridge_ib/index.py:1-13`) and imports only `core`, `harness`, `convert` and the gateway grounding wall | 2 | other agent (told not to be edited here) |
| `docs/PLATFORM.md` | Same point, "which parts the Internal Brain uses" | 2 | other agent |
| `docs/tencent/SUBMISSION.md:234`, `docs/tencent/VIDEO_SCRIPT.md:159`, `docs/tencent/deck/make_deck.py:233-240` | "all six sessions", 639 + 38 tests, 5,958 requests, 976 injection requests: the 16 Oct submission as filed (SUBMISSION.md says so in its header). Not changed | 3 | founder: leave as filed |
| `docs/finals/CHANGELOG.md` commit rows, `CLOUD_HANDOFF.md`, `WORLD_EXPANSION_STATUS.md`, `FOUNDER_TODO.md:31`, `PLAN.md`, `research/03-05` | Old counts (1,131; 166; 154; 5,958; 976; 181/1/42) quoted as dated history, each with a dated note or a commit; not changed | 3 | none (history) |
| `docs/finals/TEST_REPORT.md` | No pass count is recorded for `final_v1.4` (only screens refreshed by a Playwright run on it) | 1 | founder: run the clean-checkout suites on `final_v1.4` before it is quoted anywhere |
| `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md` W7 | "WorkBuddy GUI retest: not run yet" is still true (needs the stage laptop) | 1 | none (true limit) |
| `docs/finals/rubric/hcd_ux/*` | Hallway test and `[TESTER]` line pending until Sat 31 Oct: true | 1 | none (true limit) |
| `docs/finals/video/*`, `submission/finals_video/narration*` | The cue sheets were regenerated (`--cues-only --plan`) from the script after the story fixes; both self-tests pass. If narration was already recorded from the old cues, re-read: lines for scenes 05 (Maya), 10 (platform), 12 (WorkBuddy), 3 (Amy) changed | 5 | founder |

### Provider names that remain, by design (identifiers, not claims)

The stand-in model appears by its provider's name only as code identifiers, which docs must quote to be accurate: `databridge_ib/claude.py`, env var `IB_CLAUDE`, test names `test_claude_*` and `test_restricted_documents_never_reach_claude_or_the_page`, the file `claude_spend.json` (FEATURE_CATALOGUE.md lines 706 to 717), `MERGE_ANALYSIS_final_mehulupdates.md:75`, DECISIONS D-41, and the guard word lists in `video/assemble_finals.py` and `record_finals.cjs`. Renaming them is a code change: founder's call. DECISIONS D-38 says the Ask look is "like ChatGPT or Claude" (a design reference for the product, not the stand-in). README.md ~431 and `databridge/README.md` (original platform's optional keys) were left as instructed.

## Build-tool mentions

Founder decision (30 Oct): keep them, trim gently.

- **One statement:** `docs/tencent/DEV_PROCESS.md`, new section "Build tools: the one statement". Tencent tools first (CodeBuddy: nine recorded sessions, two security reviews that found real bugs; WorkBuddy: MCP connectors, 33 of 33 checks, verified in `workbuddy_live_test.md` and `evidence/workbuddy_live_test.py`; Miora: the cover; Hunyuan Hy3 / TokenHub: wired, not live). Then the other assistants, named once each (Claude as the coding assistant; Codex as a read-only reviewer; Claude Opus, Sonnet, Haiku and Fable 5.1 as independent review seats). No usage ratio is stated and no tool's role is hidden.
- **Pointers added** from `docs/finals/rubric/ai_innovation/TENCENT_TOOLS_REPORT.md` and `docs/finals/rubric/responsible_ai/README.md`.
- **Trimmed** (rows marked 4b above): "round-N Fable/Opus/Sonnet/Haiku #k" tags became "round-N review #k" in PRESENTER_KEYS, RUN_SHEET, QA_BANK, FEATURE_CATALOGUE, READINESS, the script, the handout source and presenter code comments; link labels in STATUS, `docs/finals/README.md` and FEATURE_CATALOGUE became "code review", "UX" and so on (file names unchanged); "Claude built the UI" and "two Claude rounds" became "other AI assistants" / "further AI review rounds" with a pointer.
- **Kept on purpose:** `docs/finals/reviews/**` and its file names, commit history, `docs/tencent/SECURITY.md` and DEV_PROCESS tables (dated review records), DECISIONS, CLOUD_HANDOFF, app_mode and deliverables review files (history). Remaining name-drops there are listed by `grep -rniE "opus|sonnet|haiku|fable|claude" docs`.

## Retrieval package (coordinator note)

The Internal Brain keeps its own index and does not use `packages/retrieval` (`databridge_ib/index.py:1-13`). Fixed in `docs/tencent/ARCHITECTURE.md` (section 7 closing), `docs/tencent/DEV_PROCESS.md` (Foundation row), `INTERNAL_BRAIN.md` ("What's inside") and QA_BANK Q11. The video script already says the engine, adapter agents and federated engine are "not used to answer". The `PYTHONPATH` lines that list `packages/retrieval` (EVIDENCE_CARD, MERGE_PLAN, deliverables STATUS) are test setup and were left.

## Video story fixes (coordinator list)

Items 1 to 9 of the storytelling review were applied: Maya's answer line, "connectors to Slack, Confluence, Jira and Drive" plus "none of it answers a question", "can be a criminal offence", WorkBuddy as the host of our MCP server, 1,278 spoken everywhere (1,274 pass, 4 skipped, written once), `final_v1.4` in the header, VIDEO_PLAN length and model wording, rubric README model-ON variant and the axe claim. Scenes were not restructured.
