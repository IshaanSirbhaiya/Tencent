# Presenter mode spec (for the UI agent)

Build target: `databridge/apps/internal_brain_web`. Purpose: one person (Dev) runs the whole live demo from the keyboard, repeatably, on pre-warmed data. Run sheet: `RUN_SHEET.md`. Priority P0 (D-15), build Saturday morning.

Items marked **PROPOSED** are names or query strings chosen in this spec because the repo has no existing one. Change them freely, but keep the behaviour, and tell the coordinator.

**Stage rules:** only ONE browser window is used on stage (no second pre-opened window); `R` is the recovery key.

## 1. Enabling, and why (WCAG 2.1.4)

Single-key shortcuts are a WCAG 2.1.4 risk. So:
- Presenter keys are **off by default**. They are active only when presenter mode is armed: `NEXT_PUBLIC_IB_PRESENTER=1` at build or start, or `localStorage["ib.presenter"]="1"` (built; `components/presenter/controller.ts`).
- They are ignored whenever focus is in an input, textarea, select or contenteditable, and whenever Ctrl, Alt or Meta is held.
- A small visible HUD (bottom-right, outside the 5% safe margin) shows the armed state, the step number and four status dots: API, model (TokenHub), ledger, WorkBuddy. HUD text is also in the accessibility tree.
- Normal users never see the HUD or any shortcut.

## 2. Keys

| Key | Action |
|---|---|
| `P` | Arm and start. Applies the Stage theme, navigates to Step 1, warms the app (see §5). Pressed again: returns to Step 1 without resetting the sandbox |
| `Space` | Run the next step. The HUD shows what the next step will do. Debounce: ignore a second press within 400 ms |
| `B` | Fallback: full-screen overlay plays the local clip for the current step (A, B or C), muted, from a local file; `B` again returns to live. No network. Target under 300 ms to start, under 3 s end to end |
| `R` | Reset: restores the tamper sandbox copy, clears the ask boxes, re-warms the model, returns to Step 0. Never edits the real ledger |
| `Esc` | Closes the sources sheet and returns focus to the chip that opened it. Normal app behaviour, not presenter-only |
| `L` | (PROPOSED extra) Toggles English and 简体中文 and back. Needed so the driver never uses the mouse |

Everything on the run sheet must be reachable by these keys alone.

## 3. Stage theme

- **Light** theme, **150%** scale (root font size x1.5; body text at least 24 px; answers at least 40 px as the plan asks for projector legibility).
- Navigation hidden; content inside a 1280 x 720 safe area, nothing in the outer 5%.
- Colours keep AA contrast (check with the contrast test). No gradients, no animation beyond a short fade. Respect `prefers-reduced-motion`.
- Footer label always visible: "Demo data: synthetic Company A". When the answer was composed by WorkBuddy, show "composed by WorkBuddy (Hy3) from the Brain's sealed answer" [ONLY IF HY3 LIVE; default: omit the label].
- Theme is applied by a class on `<html>` (PROPOSED: `data-theme="stage"`); `R` and leaving presenter mode remove it.
- The dark theme stays available for the rehearsal test on the venue projector; decide on 2 Nov.

## 4. Pre-minted demo sessions

Presenter mode switches identity by minting a signed demo token for each persona (`IB_DEMO_LOGIN=1`, as the repo does today). Identity comes only from the signed token (repo rule 2). The presenter must not allow typing a name. Personas used: `amy.am`, `mlro.tan` (Rachel), `ken.ext`, `maya.compliance`, `jdoe`.

## 5. Warm-up on `P`

1. One throwaway ask per persona used, to warm caches and the model call.
2. Check the four status dots: API, model, ledger, WorkBuddy reachability (the last may be "unknown").
3. Ask timeout: if the model call exceeds 4 seconds, serve the deterministic extractive answer (the product's own behaviour, repo rule 5). No dead air.
4. No token streaming (D-08). The staged status line may show (D-20 SSE stage events if built): accepted, checked, composing, verified. Text appears only after grounding passes.

Acceptance test: from a cold browser, `P` then `Space` x N (N = steps in §6 that are in-app) completes the in-app part in under 100 seconds with no typing and no mouse, and `B` at any step plays that step's clip within 3 seconds.

## 6. Steps: exact app action and URL

Route names for Developer pages come from the plan (`/dev/journey/<req>`, `/dev/audit`). The Compare route `/compare` exists today; the query strings below are **PROPOSED**.

Questions (exact strings, from `lib/personas.ts`):
- Q-V: `Why has Velquarry Trading's account been restricted, and what should I tell the customer?`
- Q-B: `Why has Bayfront Spice Traders' account been restricted, and what should I tell the customer?`

| Step | Trigger | Run sheet time | URL | App action |
|---|---|---|---|---|
| 0 | `P` | before 0:00 | `/compare?mode=people&left=amy.am&right=mlro.tan` | Stage theme on. Two columns labelled Amy (Account manager) and Rachel (MLRO). Boxes pre-filled with Q-V but not submitted. HUD ready |
| 1 | `Space` | 0:07 | same | Both columns submit Q-V at once, each with its own persona token. Staged status line, then both verified answers. There is no classification pill in the app. Save Rachel's `req_id` in presenter state for step 5 |
| 2 | `Space` | 0:45 | `/compare?mode=person&person=ken.ext` | Mode "same person, two questions". Both columns are Ken. Left submits Q-V; right submits Q-B. Both show the same refusal card. Optional (P1): a line "Responses identical" computed from the two user-visible refusal bodies only. It must never state or imply anything about hidden documents (D-07). Hold |
| 3 | `Space` | 1:10 | `/compare?mode=people&left=amy.am&right=mlro.tan` | Returns to step 1's results (restored, not re-asked). Focus on Rachel's answer. `Space` itself opens citation [2], i.e. the **sources sheet** (no second key) |
| 3b | (none; same `Space` as step 3) | 1:12 | same, sheet open | Sheet shows: document title, the cited passage highlighted, "checked live" age, version. `Esc` closes it and returns focus to chip [2] |
| 4 | `Space` | 1:40 | `/dev/journey/<req>` where `<req>` is Rachel's `req_id` from step 1 | Session switches to `maya.compliance`. Journey opens in under 2 seconds: waterfall with real milliseconds per stage, model name, three ledger records with sequence and hash. Full ms waterfall shows only to the compliance role (D-16) |
| 5 | `Space` | 2:10 | `/dev/audit` | Opens the audit view as Maya |
| 5b | `Space` | 2:15 | `/dev/audit` | **Verify** on the live ledger. Green: intact |
| 6 | `Space` | 2:20 | `/dev/audit` | **Tamper**: calls the compliance endpoint `POST /audit/tamper-demo` (B11), which changes one decision ("forbidden" to "verified"; the backend scenario is being changed to exactly this) in a temporary **copy** of the ledger and returns the verify report. The real chain is never edited (D-19) |
| 6b | `Space` | 2:25 | `/dev/audit` | **Verify** on the tampered copy. Red: "broken at entry N" with the exact sequence number. Ishaan or Mehul reads the number off the screen |
| 7 | `Space` | 2:35 | HUD card "WorkBuddy" | App stays open. HUD says "Switch to WorkBuddy". Dev switches window (Alt+Tab) to the WorkBuddy window (**off-app**; see §7) |
| 8 | `Space` | 3:12 | `/dev/audit` | Back in the app. Shows the real ledger (not the sandbox copy). The newest row, channel `workbuddy-mcp` (Jordan), appears when the window regains focus (Alt-Tab back; `?latest=1`), not on a key. Then `L` flips to 简体中文 and `L` back (3 s) |
| 9 | `Space` | 3:20 | `/present/proof` (PROPOSED) | **Proof slide** |
| 10 | `Space` | 3:50 | `/present/business` (PROPOSED) | **Business slide** |
| 11 | `Space` | 4:30 | `/present/close` (PROPOSED) | **Close slide** |
| Q&A | none | 5:00 | `/dev/journey/<req>` | Driver goes back to the journey page and leaves it up |

Note: the run sheet's Ken step happens before the passage step. The tamper in step 6 writes only to the sandbox copy, so the WorkBuddy beat in step 7 writes a real record to the real chain afterwards (D-19), which step 8 shows.

## 7. The WorkBuddy beat (off-app)

- Not controlled by the app. WorkBuddy is a separate window, already open, warm, configured as Jordan with the Internal Brain connector (`scripts/workbuddy_setup.py`).
- Aaruni starts the action by pasting the one exact prompt used everywhere (it is Jordan's canonical question in `personas.ts`): `What's the status of the database migration project and were there any blockers raised in Slack last week?`
- Expected: a cited answer stitched from Jira, Slack and Confluence.
- Step 8 then shows the new ledger row. This is the live proof that WorkBuddy used the same gates.
- If the network fails: `B` plays clip C.

## 8. Slides

Three full-screen slides rendered by the app so the Stage theme and fonts match. Space advances; `B` is not needed. The content is in `RUN_SHEET.md` and `LEAVE_BEHIND.md`.

**Proof slide** (`/present/proof`):
- Three large numbers, each with its source in small type:
  - `7,374` adversarial requests, `0` leaks (`databridge/evals/internal_brain/RESULTS.md`).
  - `400` paired questions, `0` differences (same file).
  - A tester line, taken verbatim from `FIX_LOG.md`. If no real row exists, **do not render this line**.
- One CodeBuddy line, only the facts in the repo.
- Numbers are read from `results.json` at build time if possible, so they cannot drift from the evidence. If not, hard-code and re-check on 3 Nov morning.

**Business slide** (`/present/business`):
- One number: about US$1.3M a year, labelled "estimate".
- Four inputs on the slide (default case in the repo research: 600 staff, 90 minutes saved per person per week, US$31 an hour, 46 weeks). Confirm against the one-pager.
- One Tencent stack line [ONLY IF HY3 LIVE for the Hy3 part; default: "WorkBuddy, CodeBuddy, TokenHub (Hy3 wired, live run pending); a Lighthouse deploy kit, no live instance"]. State only what is live ([HY3] check).

**Close slide** (`/present/close`):
- The tagline in large type: "Answers you can prove."
- "Walnut DataBridge" and the team: Ishaan, Dev, Mehul, Aaruni.
- A QR code to the GitHub repo: `https://github.com/IshaanSirbhaiya/Tencent` (the repo's `origin`). Check that it is public on 2 Nov and that the QR scans from 3 metres. Add the short URL in text under the QR (accessibility).
- The ask: "One pilot team at one regulated company."

## 9. Guardrails (do not violate)

- No hidden-count UI and no "something exists but is hidden" text, anywhere in presenter mode (D-07).
- Refusal cards are the real product response, not a presenter-written string.
- Recorded clips are labelled "recording" in the HUD and in the overlay corner.
- English and 简体中文 only (D-22).
- Presenter mode never writes to the real ledger except through ordinary asks.
- Presenter state (current step, `req_id`) is client state only; nothing about it goes to the server.

## 10. Test list for the UI agent

1. Armed off: pressing P, B, R, L or Space does nothing special.
2. In a text field: keys type normally.
3. Steps 0 to 11 in order, using only keyboard.
4. `R` after step 6b restores a clean sandbox; step 6 repeats.
5. `B` at each step plays the right clip and returns.
6. Step 4 opens in under 2 seconds with the correct `req_id`.
7. Axe and contrast pass on the Stage theme.
8. With the model timeout forced, the extractive answer appears and the run is not blocked.
