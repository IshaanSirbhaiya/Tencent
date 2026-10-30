# Video kit readiness, Fri 30 Oct (before the evening recording)

Checked against the app in the working tree on branch `final_v1.2`. Read-only on the app: nothing was recorded, no
window opened, no server touched, nothing committed. Both self-tests pass after the edits:
`node docs/finals/video/record_finals.cjs --self-test` and `python docs/finals/video/assemble_finals.py --self-test`
(the second now also compares every number the script says with `eval_summary.json`).

Overall: **green to start recording**, with the founder items below and two "look at the first take" items.
Every selector the recorder uses was matched against the app source (`data-testid`s, ids, i18n strings); none
points at UI that has gone.

## Per scene

| Scene | Status | Notes |
|---|---|---|
| 00 Camera intro | green | Same file as the first video (`submission/05_video/voice/intro_final.mp4`). |
| 01 Title (card) | amber | Text is right; re-run `record_finals.cjs cards` (cards are PNGs baked from the old run). |
| 02 Amy vs Rachel | green | `/compare?a=amy.am&b=mlro.tan`, `#cmp-a/#cmp-b/#cmp-q`, "Ask both" and the Q-V string all exist and match `lib/personas.ts`. Compare columns use the compact answer card, so the new Act and Deliverables rows do not appear. |
| 03 Ken identical | green | `/compare?mode=person&p=ken.ext`, `#cmp-q1/#cmp-q2`, `identical-marker`, `refusal-card` exist. |
| 04 Source live | amber | Selectors exist (the "Velquarry" starter is Rachel's first suggestion; `citation-chip`, `sources-sheet`, "Checked live at", Esc). It is the one non-compact Ask page, so the new Act and Deliverables rows now sit under the answer: check on the first take that the pointer and scroll still land well. Chinese shows on user screens only (D-32), so the narration and label changed from "the whole app" to "the screens you ask from". |
| 05 Journey | green (order) | Needs Rachel's "restricted" request in Recent requests, which scene 02 creates: record 02 first. |
| 06 Graph | amber | Three counts that go stale as the world grows were made generic. Still reads "Ken can open N" (line 393) and "Second person" (394), which come from persona names and i18n. |
| 06b Brain grows | amber (optional) | The Add sheet has landed (`/dev/graph?add=1`); selectors now use its real test ids. Needs demo features on (D-24) and writes a real note into the mock Slack of the shared stack: record it last, reset after. `?grow=1` is a presenter opt-in and does not affect this scene. |
| 07 Analytics | green | `analytics-kpis`, `replay-notice`, "Live only" exist. Record after 02 to 05 so Live only has rows. |
| 08 Audit | green | `#nl-question`, Interpret, Verify audit chain, tamper-demo strings, "Your real audit log was not modified.", "Chain broken at entry" all in `lib/i18n/audit.ts`. |
| 09 Trust centre | green | `trust-controls` and "Proved by" (matched case-insensitively) exist. |
| 10 Engine | green | `engine-*` test ids exist; the "932 entities" text match is now any count. |
| 11 CodeBuddy (card) | amber | The card said "Six CodeBuddy sessions"; the recorder now writes "Nine" (from `video_constants.json`). The PNG on disk is stale until the cards are re-run. Narration now says nine. |
| 12 WorkBuddy (stills) | amber | Real 15 Oct captures, labelled as such. D-30 changed the compliance connector's configuration since; say nothing about it over these stills. |
| 13 Hy3 status | green | `model-status` exists; the overlay follows the live status. Record with `FINALS_HY3=0`. Default narration is the "quoted from sources" line; the Hy3-live line is tagged [ONLY IF HY3 LIVE]. |
| 14 Proof (slide) | amber | The slide is the app's own (reads `eval_summary.json`, not stale). The overlay pill now reads the counts from the same file. Re-run the cards so the PNG matches. |
| 15 Business, 16 Close | green | The app's own slides. |

## Recorder selectors at risk (file:line, `record_finals.cjs`)

None is broken today. These depend on text another agent may change (login chips to role-only text, persona names):

- :393 `getByText(/Ken can open \d+/)` and :394 `getByText('SECOND PERSON')` in scene 06. The name comes from the
  persona `name` (`lib/personas.ts:49`, used in `components/graph/CompareView.tsx:23,106`); the caption from
  `lib/i18n/graph.ts:78-82`. If persona names become role-only there, scene 06 loses its anchor (it times out on that
  hover step; the label at :392 does not depend on it).
- :321 starter filtered by "Velquarry" (Rachel's first suggestion, `lib/personas.ts:111`): changing her suggestions breaks scene 04.
- :354 Journey row filtered by `mlro.tan` and "restricted": uses the raw handle in "Asked by"
  (`components/journey/RecentRequests.tsx`), so role-only login chips do not affect it.
- :337-343 language menu by aria-label `Language: ...` and radio names (`components/shell/ShellControls.tsx:58`).
- :525-528 `ms.getByRole('list')`, `ms.locator('p').last()`, "Extractive only|Live models" in scene 13
  (`components/analytics/SystemStatus.tsx`): structure-dependent; looks fine, check in the first take.
- :402-412 scene 06b: `graph-main-addsheet-text` and `-post` (`components/sim/AddDocumentSheet.tsx`, `GraphExplorer.tsx:558`).

The recorder never uses the login chips (it signs in through `/api/ib/auth/demo-login`), the presenter script, the
`L` flip or `?grow=1`. So the 11-step script, D-26, D-32 and D-33 change no scene except the Chinese wording.

## What I changed

- `record_finals.cjs`: CodeBuddy card count comes from the new `video_constants.json` (Nine, was Six); the graph,
  engine and Ken-label counts are no longer hard-coded (867 / 932 / 24); the Chinese label says "the user screens";
  06b uses the Add sheet's real test ids, waits for "Posted to Slack" and closes the sheet before switching mode.
- `assemble_finals.py`: reads `eval_summary.json` and `video_constants.json`; the proof pill is built from them
  ("Plus 1,278 backend and 185 browser tests, none failing"; 1,278 collected, 1,274 pass, 4 skipped, from the 30 Oct evening cloud run); `check_script_numbers()` compares the spoken
  numbers (7,374 / 400 / 1,274 / 185 / nine) with the evidence and flags 5,958, 754, 124, 976, "six CodeBuddy" and a
  stale or non-zero summary; it runs in `--self-test` and on every cue-sheet write. It accepts `[ONLY IF HY3 LIVE]`
  (and the old `[if Hy3 is on]`).
- New `video_constants.json` (CodeBuddy sessions 9; the old video, 6:28).
- `VIDEO_SCRIPT_FINALS.md`: scene 14 now says seven thousand three hundred and seventy-four requests (it said 5,958)
  and "none failing" (was "all passing"; 41 browser tests are skipped); scene 11 says nine sessions; scene 04 no longer
  says "the whole app also speaks Chinese"; the Hy3 line is tagged [ONLY IF HY3 LIVE]; branch name.
- `narration_cues.md` and `narration.srt` regenerated from the script (`--cues-only`). No 754, 124, 5,958 or 976 remains
  in `docs/finals/video/` or `submission/finals_video/`.
- `VIDEO_PLAN.md`: pre-record checklist at the top (reset, Hy3 status, display, Do Not Disturb, tell the founder, scene
  order, expected length 6:08, or about 6:36 with 06b, against 6:28); `submission/finals_video/README.md`: branch name.

## Video length limit (checked 30 Oct)

No length limit for the finals video was found in `docs/finals/*`, `docs/finals/ORGANISER_UPDATE.md`, `PLAN.md` or the
finalist notes (D-01 says only that the old video stays unless the new UI is ready). The only stated range is
**5 to 8 minutes**, for the 16 Oct submission form (`docs/tencent/SUBMISSION.md:253`). Amber: the main cut in
`VIDEO_PLAN.md` ("The new timeline") is therefore planned at about 7:55, the short cut at about 6:03 and the full cut
at about 9:15 (over 8:00; use it only if no limit applies). Ask the organisers to confirm.

## New timeline: what is built (30 Oct, evening)

Built, no recording done: recorder scenes `17_act`, `18_deliverables`, `19_upload`, `21_extras` (tour offer,
dictation button, value calculator, presenter landing) and a rewritten `06b_brain_grow` (Compare view: Jordan posts,
Ken's feed must stay empty or the take is refused); the Miora card (`20_miora.png`, made by `record_finals.cjs cards`);
in the assembler, `--cut short|main|full` (main is the default; other cuts add `_short` / `_full` to file names),
the new order (audit before graph and analytics), optional rows for every new scene and the teammate slots
`S1_voice`, `S2_jit_ui`, `S3_dots`, `--cues-only --plan` for cue sheets before anything is recorded; in the script,
every scene with cuts, on screen, caption, proof and voice-over, **[MAIN]** / **[FULL]** extras, and TODO
placeholders for the slots. Cuts: short 6:18, main 7:56 (with all slots), full 9:43 (after the Fable script review of 30 Oct: title 9 s, Act as Amy then Jordan, scene 21 re-cut, 13 to 16 longer). Both self-tests pass and now
also check the cut totals, the script sections and that TODO lines are never read; `node --check` and `py_compile` pass.

New scenes use `data-testid` selectors only (no visible text), because ActSheet, DeliverSheet and AddDocumentSheet
copy is being edited. Exceptions that already existed: the mode-switch "User" text and the "Compare two people"
text in scenes 06 and 06b's return to User mode; scene 21 uses the landing page's `main` element.

Risks for the first take of the new scenes (nothing could be checked without a browser):
- 17 Act: assumes Jordan's first starter gives a released answer with the Act row, and that the ticket proposal
  is allowed for him; it writes a mock Jira ticket. Any other state (`act-unavailable`, `act-error`, ...) fails the take.
- 18 Deliverables: assumes the table kind is offered (`deliver-table` row button) for that answer; `deliver-table` and
  `deliver-chart` are both a row button and a view element, so the recorder scopes them to `deliver-sheet`.
- 19 Upload: recorded as Leo (`leo.ops`), not Jordan, who may write to no folder (round-5 Opus #1). Sets the file on
  the hidden input and picks **Pricing & Offers** by its label (Leo writes there, Ken cannot read it; checked in
  `world.json`), never by position; the take fails if that folder is not listed. Writes a file into the mock Drive.
- 06b: needs demo features on; `graph-left-add`, `graph-left-addsheet-*`, `graph-left-live-item`, `graph-right-live`.
- 21: the tour offer only shows if the stack has not been marked dismissed in this browser profile; the scene removes
  `ib-tour-offer` and reloads; the mic button needs the browser's speech API (Chromium headless may not have it, then
  the take fails cleanly: drop the dictation beat or record it headed).
- Tight spoken lines (about equal to the picture): scene 21 (main), scene 11 (main), scene 03 and 17 (full).

Needs the teammates: names, routes, on-screen strings and tests for voice, just-in-time UI and dots (the script has
TODO lines; the rows are in the assembler and skipped until a clip exists).

## Still needs the founder (or a teammate)

1. **Tell the founder before the stack is used**, and choose the quiet window: the recorder asks real questions as five
   personas on the shared stack (it adds ledger rows), and 06b writes a note into the mock Slack.
2. **Re-record every scene and the cards.** The clips and PNGs in `submission/finals_video/` are the old test takes (the
   assembler refuses old-style takes by design). Then Ishaan's voice-over from the new `narration_cues.md`.
3. **Teammates' updates:** the login-chips change (check scene 06 still shows "Ken can open N" in the first take); any
   Hy3 go-live (then `FINALS_HY3=1`, re-shoot 02, 04, 05, 13, assemble with `--hy3`); anything that changes the WorkBuddy
   story (the stills are 15 Oct captures).
4. Scene 11 is tight for the voice (about 12 s of speech for 13 s of picture): read it briskly, or add a second to its
   `SEGMENTS` row.
5. Not checked (needs a browser and the stack, both off limits): how scene 04 looks with the Act and Deliverables rows,
   and the 06b flow end to end.
