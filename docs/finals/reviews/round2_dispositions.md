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

## 5. Opus and Codex (Act, tools and kit), commit ca014cd unless noted

| Finding | Disposition |
|---|---|
| Opus #1 / Codex #8: no Act rate limit; global proposal eviction | **Fixed.** Per person, 10 drafts or proposals and 10 confirms a minute, then 429 (`action.failed` stage `rate`). At most 5 pending proposals per person, and a new one evicts only that person's oldest. If the store is full of other people's proposals, the answer is a 409 `capacity`. Tests in `test_act_round2.py` |
| Opus #2: ticket links expose restricted ids (rule 4) | **Fixed.** The ticket says "Source N" and links to an opaque `/ib/open/<hmac>`. That link runs Gate 1 and Gate 2 for whoever clicks it: 303 if they can open the source, otherwise the one 404. It writes `doc.viewed` first. Limit: one token per document |
| Opus #5 / Codex #3: confirm doesn't re-check the cited sources | **Fixed.** Confirm re-runs the context check for the confirming user (409 `sources_changed`, without saying which source) |
| Codex #1 (high) / Opus 409-after-timeout: an unknown Jira outcome is reported as "nothing changed" | **Fixed in the backend.** Timeout, 5xx or a malformed success returns 409 `outcome_unknown`. A repeat confirm replays the known result, and no second create is allowed while the outcome is unknown (15 min). No Jira retries. State is in memory only (documented in backend_v1 v3.1). The web copy is in §7 (6de5cd6) |
| Codex #2 (high): cleanup kills unrelated processes | **Partly fixed.** `demo_up.sh` stop and `demo_day.ps1` stop only processes whose command line holds the repo path; any others are reported. PowerShell was not run in the cloud. Not done: recording our own PIDs, and `run_walnut_original.sh stop` (8300/3300) |
| Opus: `check_absent.py` reads `count` | **Fixed:** it reads `documents_containing` (`test_round2_kit.py`) |
| Opus smaller items | **Fixed:** `--fresh` refuses `..` and paths outside `var/`; `fill_template` takes a basename only, won't overwrite, and guards CSV formulas; `*.tok` is ignored; `hy3_check` needs real reply text (Codex 2b #1); the default summary is neutral. The presenter-P docstring was already correct |
| Codex 2b #2: `fill_template` path traversal | **Fixed** (same as above) |
| Codex 2b #3: a failed video re-record overwrites marks | **Fixed (laptop, 30 Oct).** Each take goes to `clips/takes/<scene>/<ts>/`; the current marks are replaced in one rename only after a validated take; `assemble_finals.py` refuses incomplete or old-format marks. Self-tests in both scripts. Old clips must be re-recorded |

## 6. Round-2b Opus (`round2b_opus_unreviewed.md`)

| Finding | Disposition |
|---|---|
| M1: a model draft adds claims its sources never made | **Fixed** (7fa6319). Each draft sentence must take ≥ 80 % of its content words from the facts it is tagged with (CJK counted per character). Otherwise it is dropped, and the draft falls back to the cited sentences. The test uses the review's three sentences |
| M2: MCP output unframed; three identities in one WorkBuddy | **Fixed** (2b339c8). Every source-derived string is framed as data, never instructions, and forged markers are neutralised. `--user-config` no longer writes `ib-compliance`; `--compliance-user-config` writes it alone. Tests prove the frame is present, not that an agent obeys it |
| L3: the mock Jira checks the reporter, unlike real Jira | **Fixed** (2b339c8). The mock checks the caller, and a new e2e test shows the IB re-check alone refuses a revoked permission |
| L4: voice keeps the mic on after Send | **Fixed** (2b339c8). Dictation stops on Send, busy or typing, and late results are dropped. e2e added |
| L5: cheat sheet R/S/Esc wrong; D and 2 missing | **Fixed** (2b339c8), PDF rebuilt |
| L6: passcode sent to any host | **Fixed** (2b339c8): loopback only unless `--allow-remote`, and never plain http to a remote host |
| L7: the video hard-codes "Hy3 not switched on yet" | **Fixed** (2b339c8): the mark follows `/ib/status` |
| Info: mic prompt on stage; dictation needs the network | **Not fixed.** Founder item: grant mic permission on the demo profile once, and don't use dictation in the offline rung |

## 7. Sonnet (UX and a11y), Fable web items and Opus web items (commit 6de5cd6)

| Finding | Disposition |
|---|---|
| Opus: the offline mock says Hy3/TokenHub by default (honesty) | **Fixed.** The mock shows "Quoted from sources (no model)" by default in answers, the DeepSeek fixture, `/ib/status` and Analysis. It names a model only with `NEXT_PUBLIC_IB_MOCK_MODEL=hy3`. e2e no longer covers the model badge |
| Sonnet HIGH: zh Act sheets show English | **Fixed.** The server text is marked `lang="en"`, the fixed label is translated, and one zh line says the text is shown as returned |
| Sonnet HIGH: the tour card covers the Act sheet at 390 px | **Fixed.** The tour offer hides while any dialog is open (e2e) |
| Sonnet MED: ticket `pre` not focusable; confirm button wording; sticky row; dictation aria-pressed | **Fixed.** The description is a focusable named region; the button reads "Create ticket in DBMIG"; the row is sticky at 390 px; dictation toggles once |
| Sonnet MED (rule 4): login chips and starters hint at restricted content | **Fixed.** The notes say only what each person can open, and the refusal starters are removed. Amy's presenter questions are unchanged |
| Fable #4: HUD "Model unknown · Ledger unknown" | **Fixed.** Status is re-read after each sign-in and after P; a failed read keeps the last known dots (e2e) |
| Fable #5: "mlro.tan said", "jdoe:", "(synthetic case …)" | **Fixed** (display only). The chip shows the full name; the parenthesis is dropped from the first sentence. Copied text and passages are unchanged |
| Fable §6: count sentences on the grow page in Stage | **Fixed.** Hidden in Stage (e2e) |
| Opus: Leo cache not cleared on logout | **Fixed.** Cleared on sign-out and persona switch (e2e) |
| Opus: `/present/preflight` has no auth or deploy check | **Partly fixed.** 404 when deployed, or from a non-loopback host without a session cookie. The cookie is checked for presence only. The stage laptop on localhost needs no sign-in |
| Sonnet low: preflight overflows at 390 px | **Not fixed.** Presenter-only page, used at 1280 |
| Codex #1, web side: confirm outcome copy | **Fixed.** 409 `outcome_unknown` says "We could not confirm whether the ticket was created…" and disables Confirm; a 503 after Confirm never says nothing was created |
