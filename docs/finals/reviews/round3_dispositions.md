# Round-3 review dispositions (cloud session, Fri 30 Oct 2026)

Round-3 seats reviewed Deliverables, live growth and uploads, which landed after round 2:
`round3_opus_security.md`, `round3_sonnet_ux.md`, `round3_haiku_claims.md`, `round3_fable_judges.md`.
Codex is the fifth seat. It runs on the laptop (see `CLOUD_HANDOFF.md`, Questions for Ishaan).

## 1. Opus (security): 1 medium, 7 low, 3 info. Commit 7a8f709

| Finding | Disposition |
|---|---|
| M1 the `.pptx` guard misses `{=` and link prefixes | **Fixed.** `ptext` quotes a leading `{` and link prefixes. The chart workbook is written with XlsxWriter's formula and link conversion off. T15 now plants `{=cmd…}`, `external:` and `mailto:` and finds no `<f>` and no hyperlink in the embedded xlsx |
| L1 table headers unchecked | **Fixed.** Same check as cells, so the screen and the file agree |
| L2 DTD check bypass (UTF-16, renamed parts) | **Fixed.** Any XML-like member is scanned, NUL-stripped (UTF-16/32); other encodings are refused. Binary members are not scanned: a real `.pptx` holds a printer `<!DOCTYPE plist>`. SECURITY §15.12 says exactly that |
| L3 `/sim/upload-file` always mounted | **Fixed.** Mounted only with demo features and demo sign-in, never when deployed; the rate limit is checked first. This makes D-24 and backend_v1 v4 true as written |
| L4 engine refresh race | **Fixed.** The slot is claimed under a lock: one "started", one build |
| L5 successful source writes not ledgered | **Fixed.** `ingest.sync` records ("requested" with byte length and SHA-256, then "written" with the doc id); 503 if the ledger is down |
| L6 slow `clean_text` | **Fixed.** Linear comment stripping; a 5 MiB unclosed `<!--` runs in under 2 s |
| L7 route-auth test skips included routers | **Fixed.** It walks every route with demo features off and on; no route is open without a session |
| I1 kill switch after body validation | **Fixed**: 404 when off |
| I2 change-feed ledger rows unbounded | **Fixed**: at most one new-window row per person per 5 s |
| I3 build exception leaves `requested` unmatched | **Fixed**: `deliverable.failed` and 503 |

## 2. Sonnet (UX and a11y): 3 high, 9 medium, 8 low. Commit 19603c8

All three high and all nine medium are **fixed**: focus return on the Add sheet; the engine refresh announces
state changes only; the form clears after a post; zh `lang="en"` on Deliverables; one citation chip per row and no stacked modal; i18n for the chart type and copy headings; Regenerate focus; 390 px feed; the "Live updates stopped" state; the engine poll tolerates a single failure. L1 (rule-4 wording), L4, L5 and L7 fixed. L2, L3, L6 and L8 partly (the folder is still an id field: no folder-list route exists).

## 3. Haiku (claims): 5 high, 9 medium, 8 low. Commit 7e05cfa, with test counts in the final docs pass

| # | Disposition |
|---|---|
| 1, 7 | QA Q18/Q7 no longer read as Hy3 live |
| 2 | AI_USAGE_MAP: 1,047 capture requests |
| 3, 10, 13 | Test counts: set from the final cloud run (STATUS, README, FEATURE_CATALOGUE, the handouts) |
| 4 | Dated notes on the 16 Oct submission and the 14 Oct planning files; the history itself is not rewritten |
| 5, 15 | Now true in code (Opus L3): the upload route is unmounted when off. LIVE_INGEST_KIT notes all three switches |
| 6, 8, 9, 11, 12, 14, 16–22 | Fixed as worded (see commit) |

## 4. Fable (judges): 79/100 now. Commits d8f8de7 and earlier

| # | Disposition |
|---|---|
| 1 | **Fixed.** `Space` drives the grow walk-up: the Add sheet is presenter-owned, `grow-post` closes it, and a signed-out `?grow=1` tab picks up the steps after sign-in. `Esc` never hides the HUD once started; `H` does (D-33). Keys-only e2e |
| 2 | **Fixed.** `Esc` on the D panel restores the previous session; the journey back-path re-signs Maya. e2e |
| 3 | One test count and the organiser link: final docs pass and ORGANISER_UPDATE |
| 4 | Done: every track committed and the suites re-run on the committed tree |
| 5 | **Fixed.** Analysis with no model says "Needs the model (Hy3). It is not connected on this build…" (en, zh) |
| 6 | **Fixed.** QA_BANK walk-ups for Act and grow, rehearsal lines, runbook keys. The stage timing is TO FILL (laptop) |
| 7, 10 | Founder or laptop (hallway test, Hy3, WorkBuddy GUI, B at every step) |
| 8 | **Done.** The L flip is cut (D-32) |
| 9 | Partly: the upload folder is a labelled id field (no folder-list route). The live rehearsal is laptop-only |
