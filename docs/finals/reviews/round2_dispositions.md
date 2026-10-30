# Round-2 review dispositions (cloud session, Fri 30 Oct 2026)

What happened to every finding in `round2_opus_act_tools_kit.md`, `round2_codex_act_tools_kit.md`,
`round2_sonnet_ux.md`, `round2_haiku_claims.md`, `round2_fable_judges.md` and
`round2b_opus_unreviewed.md`. "Fixed" means fixed with a test unless the row says it is wording only.
Commits are on `final_v1.2` (then `final_v1.3`).

## 1. Test suites before the fixes (cloud, Linux, `IB_FORCE_OFFLINE=1`)

| Suite | Result | Note |
|---|---|---|
| IB backend | 1040 passed, 0 failed (12 min 32 s) | 13 upload-reader failures on a fresh install: `cv2` was missing from the lock. Fixed by pinning `opencv-python-headless==5.0.0.93` (90393b2) |
| Mock platforms | 151 passed | |
| Fixtures (enterprise) | 47 passed | |
| Web `tsc --noEmit` | clean | |
| Web e2e (mock, 3101) | 139 passed, 4 failed, 39 skipped | 1 failure was the "Six → Nine CodeBuddy" change landing mid-run (test updated). The other 3 were re-run after the web fixes (§4) |

## 2. Fable (judges)

| # | Finding | Disposition |
|---|---|---|
| 1 | Grow steps on the Space path stalled the run | **Fixed** (90393b2). The grow steps need demo features AND `?grow=1` on this tab; `?grow=0` turns them off. Default script is 11 steps (00–10). e2e: `presenter.spec.ts` "the grow beat stays off the Space path…" |
| 2 | One number set | **Fixed** (d7e461e). Current claims say 0 / 7,374, 0/400, 0/1,152; dated history keeps its numbers. Nine CodeBuddy sessions; cheat sheet S = Stage theme; handout PDFs rebuilt. Left: two sample labels in `app/dev/styleguide/page.tsx` (see §4) |
| 9 | Deliverables: Table only, behind D | P0 Table and Chart confirmed; P1 Slides (.pptx) built with the formula guard (1701515). Model kinds still say "Needs the model" |
| 3, 6, 7, 10 | Hallway test, WorkBuddy GUI and clip C, B at every step, Hy3 | Founder or laptop only. Listed in `CLOUD_HANDOFF.md` "Questions for Ishaan" |

## 3. Haiku (docs and claims)

All wording, fixed in d7e461e: STATUS "not committed" rows, WorkBuddy state, the Built table,
"10-step" changed to 11 steps (00–10), the "As of" branch, Act state, three HUD dots, Esc and the missing keys,
"Tencent, off-app", tamper wording, the grow line in RUN_SHEET, PRESENTER_KEYS §6 ids, and the e2e
count note. H15–H17 needed no change. H7 (stale test counts) is done in the final docs pass.

## 4. Deliverables (Opus mandatory items 1–5)

All five are done and tested. The table is in `docs/finals/deliverables/STATUS.md` §5.2. Item 4 (the PPTX
formula guard, T15) was built in this session and fails when the guard is removed.
