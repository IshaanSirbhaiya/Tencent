# Docs pass, 30 Oct evening (laptop): change list

Cross-cutting docs brought in line with the round-4 work. Sources: `reviews/round4_dispositions.md`, the three
round-4 reviews, `demo_day/STAGE_QA_30OCT.md`, `rubric/demo_story/PRESENTER_KEYS.md` section 2a, DECISIONS D-34,
`MERGE_PLAN.md`, `app_mode/PLAN_v2.md`, the video docs, `git log 1076517..HEAD`, `git status`.
Not touched (per the brief): SECURITY.md, backend_v1.md, round4_dispositions.md, uploads.md,
deliverables/STATUS.md, code, `video/*`, `app_mode/*`. Nothing committed.

| File | Change |
|---|---|
| `docs/finals/CHANGELOG.md` | New section "30 Oct 2026, evening (laptop)": round-4 reviews (committed) and fixes (working tree): Act v3.2, `/ib/open`, Deliverables B1 to B6/B12/B14, sim/live poll/extraction/Drive/WorkBuddy token, Opus web/kit fixes (preflight gate, role-only chips, key file to the Brain only, proxy open-link check), session queue, wording; upload folder picker (D-34); presenter mouse controls and stage-QA fixes; PID-0 fix; video tooling; Q&A banks; merge plan; app-mode plan v2. Three new "In progress" rows (Demo story, teammate merge, round-4 integration). Test counts untouched, "re-run pending" stated |
| `docs/finals/FEATURE_CATALOGUE.md` | Header note (evening rows are uncommitted, re-run pending); new P1a presenter mouse controls; P1 no longer says "without a mouse"; Deliverables V1 wording and round-4 rows, V2 button label, speaker notes (B14) and formula safety (B1); G2 folder picker marked evening; new section 13: K1 Act v3.2 (Act had no entry before), K2 proxy open-link check, and the planned "Demo story" app mode; contents list; "re-run pending" on the counts |
| `STATUS.md` (root) | Header links (merge plan, plan v2, stage QA, video readiness and Fable review, round-4 reviews and dispositions); new rows: mouse controls, round-4 reviews and fixes, stage QA, Q&A banks, merge plan, app mode (planned); Act, Deliverables, uploads (picker now exists; the old "no folder picker" is removed) and video rows updated; counts marked "re-run pending"; Pending: D-34, Codex review done in part, commit and re-run, app mode and merge |
| `docs/finals/README.md` | Index rows for MERGE_PLAN, app_mode/PLAN_v2 (and its reviews), STAGE_QA_30OCT, video READINESS and review_fable_script, round-4 reviews and dispositions, this change list; DECISIONS now D-01 to D-34; TEST_REPORT row marked re-run pending |
| `docs/finals/rubric/README.md` | Rows 6 and 8: "no mouse" and "keyboard-only driving" reworded to keyboard-driven by default, clickable controls as the fallback; row 8 also drops the 简体中文 flip from the 5:00 (D-32) |
| `docs/finals/rubric/demo_story/README.md` | Line 29 (item 4): the "not touched the mouse" caveat replaced by the truthful rule |
| `docs/finals/rubric/demo_story/RUN_SHEET.md` | Dev's hands (keyboard by default, bar buttons and side buttons as fallback); the 3:12 spoken line is now "Dev drives the whole demo from the keyboard", to say only if true |
| `docs/finals/rubric/demo_story/QA_BANK.md` | Q40: mouse controls are built (`presenter.spec.ts` 23/23, re-run pending); only the app-mode "Demo story" stays [PLANNED] |
| `docs/finals/CLOUD_HANDOFF.md` | New "Laptop session, 30 Oct evening" status block at the top |

## Things the coordinator should know

1. **Branch name.** The checked-out branch is `final_v1.2`; older docs call the build `final_v1.3` (the cloud pointer at `1076517`). I kept both names and said which is which; unify when you push.
2. **Counts** (1,131 backend, 166 web, 41 skipped) are unchanged and marked "re-run pending (final integration)" in CHANGELOG, FEATURE_CATALOGUE (twice), STATUS (twice), README (TEST_REPORT row), QA_BANK Q40 and CLOUD_HANDOFF. Still not marked: `TEST_REPORT.md` itself, `INTERNAL_BRAIN.md` and `ORGANISER_UPDATE.md` quote the old numbers. Search `1,131` when you update.
3. **Login-chip change vs the video recorder.** `video/READINESS_30OCT.md` lists recorder selectors at risk if chips become role-only (`record_finals.cjs` lines about 393 and 394). Chips are now role-only; re-check scene 06 before recording. I did not edit video files.
4. **Not in any doc yet:** the stage-QA fixes are described only from PRESENTER_KEYS.md and my reading of the dispositions; `round4_dispositions.md` lists only stage QA #6 explicitly. If the presenter agent adds rows for #1 to #5 and #7, link them from the CHANGELOG evening section.
5. **Starters flagged, not changed** (round4_dispositions, "#3 starters"): admin.it, maya.compliance, mlro.tan and leo.ops starters ask about content the persona cannot open. Still waiting for a coordinator decision.
6. **Preflight e2e** has a known locator clash when a live `var/demo-day/preflight.json` exists (dispositions, last paragraph); open.
7. `docs/finals/rubric/demo_story/PRESENTER_KEYS.md` line 85 ("no typing and no mouse") is an acceptance-test condition for the keyboard path and is still true; left as is. `DEMO_DAY_RUNBOOK.md:48` ("Do not touch the mouse" while walking on) is cursor hygiene, left as is.
8. No D-35 exists. PLAN_v2 reserves the number for the Demo story; the catalogue and CHANGELOG say so.
