# Review: App mode (mouse) plan, architecture and security seat

Reviewer: Opus (architecture and security seat). 30 Oct 2026, branch `final_v1.2`. Read-only review:
nothing was run or built. I read `PLAN.md`, all of `components/presenter/*`, `components/compare/{cache,params}.ts`,
`CompareView.tsx:120-280`, `AuditConsole.tsx:85-140`, `components/sim/demo.ts`, `components/shell/{nav,Providers,nextPath}.ts(x)`,
`lib/prefs.ts`, `lib/api.ts` (demo login), `app/layout.tsx`, `app/(user)/layout.tsx`, `app/present/preflight/page.tsx`,
`internal_brain/databridge_ib/{app.py:205-250,config.py:80-115}`, `databridge/scripts/demo_day.ps1` and the root `.cmd` files.

`web/` = `databridge/apps/internal_brain_web/`.

**Verdict: GO WITH CHANGES.** The architecture is right: the story stays data in `script.ts`, the actions are
imported unchanged, and the plan touches no presenter file. Fix H1 and H2 before building. Fold the Medium
items into the build. Re-estimate the effort at 10 to 12 h.

---

## 1. Findings

| # | Severity | Plan § | Issue | Recommended change |
|---|---|---|---|---|
| H1 | **High** | §9, §1, §13 | **The kit change touches the stage path.** (a) "Map `--app` to `-App` in all five `.cmd` files" edits `Start Walnut Demo.cmd` and `Reset Walnut Demo.cmd`. §1 says `Start Walnut Demo.cmd` is not in scope, and the §13 rollback says it "was never changed". (b) `last-mode.txt` makes Reset depend on state. Run app mode at the booth, then press Reset before the 5:00, and Reset reopens `/demo` in a maximised normal window instead of the fullscreen presenter (`Reset-Demo` → `Start-Live`, `demo_day.ps1:514`). | Drop `last-mode.txt`: Reset always reopens the presenter, as it does today. Only the new `Start Walnut Demo (app mode).cmd` passes `-App`. Do not edit the four existing `.cmd` files, and drop the offline app mode (Q4). If app mode is needed after a Reset, double-click the app-mode button again: `start` is "safe to run twice" (`demo_day.ps1:15`). Add the check `git diff --quiet -- "Start Walnut Demo.cmd" "Reset Walnut Demo.cmd" "Start Walnut Demo (offline).cmd" "Stop Walnut Demo.cmd"`. |
| H2 | **High** | §4 (history), §5 | **Browser Back and Forward race the setup and the session.** (a) The plan disables the pill's buttons while a setup runs, but the browser's Back still works. The setup keeps running, and its next `go()` pushes a new entry, so it undoes the user's Back. (b) On Back or Forward the page mounts **before** the persona re-check finishes. Example: Forward from chapter 4 (Rachel) to chapter 5 mounts `/dev/journey/<req>` or `/dev/audit` under the old session. It shows "Request not found" (the correct rule-4 shape, but it looks broken) or an audit error. After the sign-in the page does not fetch again, because nothing re-runs its effects. | (a) The guided runner listens for `popstate`. When it fires during a setup, it bumps the guided generation (the `live()` checkpoint stops the setup) and calls `callPresenter('compare-cancel')`. (b) Re-check the persona only for chapters whose page depends on the main session: 5 to 8 (`/dev/journey`, `/dev/audit`). The Compare chapters use slots `b`/`c`, and the slides need no session. When the persona differs, sign in, then remount the page: a hard `location.replace(sameUrl)` is simplest, and `/dev/*` keeps no client cache worth saving. Announce "Signed in as Maya for this chapter" in the live region. Add e2e tests for both (a) and (b) (§3 below). |
| M1 | Medium | §5 `signInMain` | "Serialised by one promise chain" is ambiguous. A private chain would race the shell's persona switch and Sign out (`claimSession`, `session.ts:37`) and the presenter's warm-up and D. | Import `serialSession`, `markSessionOwned` and `sessionOwner` from `presenter/session.ts`. That is import only, so working rule "no presenter edits" still holds. Mirror `controller.ts:80-100`: if `sessionOwner()` changed since the setup began, stop (the shell's choice wins). Never override a persona the user picked mid-setup. |
| M2 | Medium | §4 rule "setup never adds an extra history entry" | The rule breaks on cold chapter 5. The setup runs step 2's `go(compare)` and then step 5's `go(journey)`: two pushes. Back from the journey lands on a Compare page labelled `gd=5`. | Adapter rule: **the first `go()` of a setup pushes (or replaces, for a deep link); every later `go()` in the same setup replaces.** Append `gd` with the `URL`/`URLSearchParams` API, not string concatenation (`/present` has no query; Compare URLs do). |
| M3 | Medium | §3 item 5, §11 test 8 | **End demo lands on `/dev`, not the Ask page.** Visiting any `/dev/*` page saves `mode='dev'` for good (`Providers.tsx` route-mode effect, `lib/prefs.ts:104-113`). After chapters 5 to 8, `MODE_HOME[mode]` is `/dev`. | Store the mode in `ib.guided` when the demo starts. End demo calls `switchMode(startMode)` (it pushes `MODE_HOME[startMode]`). Test 8 asserts the exact path after a run through chapter 8. |
| M4 | Medium | §8 rule 4 | **The speaker cues hold exactly what rule 4 hides.** `STEPS[].say` for `compare-person` says "On the right, a company that does not exist". For `compare-people` it says "Rachel… gets the case". If the overview or the pill shows `say`, it states which company exists and which content is restricted. Also, the deny-list unit test scans only `guided.ts`, but the card titles come from `presenter.ts` (`step.<id>`, `presenter.ts:141-154, 326-339`). | Never render `say` in app mode; render only the `step.<id>` titles and the `guided.ts` lines. The chapter 3 card line must not say which company is real. Run the deny-list test against the **rendered** `/demo` page and the pill (en and zh), and add every `say` string to the list as a "must not appear" substring. Match `STR` as a whole word (`\bSTR\b`), so it does not catch "STRING". |
| M5 | Medium | §7 server gate | The `/demo` page reads `process.env.IB_DEPLOYED` and calls `notFound()`. Without `export const dynamic = 'force-dynamic'`, Next may pre-render the page at build time and fix the result then. The preflight page sets that flag (`preflight/page.tsx:9`). The gate is load-bearing: demo login **can** run on a deployment with a passcode (`config.py:83-86`), so `IB_DEPLOYED` is the only thing that keeps `/demo` off a deployment. | Set `force-dynamic` on the page, or put the check in a server component that reads `headers()`. Put the page in `app/(user)/demo/page.tsx`, so it gets the shell from the route-group layout (`app/(user)/layout.tsx`) instead of wrapping itself. Unit-test the gate function with `IB_DEPLOYED=1`. |
| M6 | Medium | §14 | **The estimate is optimistic.** The plan covers an adapter with generations and session rules, an overview, a responsive pill with a live region and padding, a dialog, WorkBuddy, menu wiring, en and zh strings, 13 e2e tests including 16 axe scans (2 pages/states × 2 themes × 2 sizes × 2 languages), the kit, shortcuts and docs. Earlier presenter work here needed several review rounds (the "round-4 Opus" notes in `session.ts` and `controller.ts`). | Plan for **10 to 12 h plus one fix round**. To fit a weekend: drop last-mode and offline app mode (H1), drop the corner swap (collapse only, Q6), leave out the palette (Q7), and trim the axe matrix to light+dark × 1280+390 in en, plus one zh pass. |
| L1 | Low | §5 model line, §13 | (a) "Model: Tencent Hunyuan (live)" is hard-coded. `refreshHealth` sets `live` whenever `models.active` (a chain exists, `app.py:216-222`), whatever the provider (rule 8). (b) `refreshHealth()` writes the presenter store (`setPresenter({health})`, `controller.ts:241`). That contradicts §13 "never writes the presenter store". | Read `analyticsApi.status()` directly in the guided module. Show `models.provider` (or `active_chain[0]`) when active; otherwise show the honest "not live" line. |
| L2 | Low | §3 item 4 | `WorkBuddyCard` is not pill content. It is a `fixed z-40` overlay placed for the presenter bar (`bottom-[calc(…+7.5rem)]`, `WorkBuddyCard.tsx`). | Render it unchanged as an overlay above the pill and include it in layout test 13, or write a small guided panel that reuses `Q_WORKBUDDY` and the `pr.wb.*` strings. Do not edit the presenter file. |
| L3 | Low | §13 "two runners" | The pill hides while the presenter is armed, but a guided setup that is already running continues. Ctrl+Shift+P and then P during a guided setup gives two runners. `localStorage['ib.presenter']='1'` arms the presenter on every load (`store.ts:123`), so the pill would never show in that browser. | When `armed` becomes true, bump the guided generation and mark the guided state inactive. Note the `localStorage` case in the runbook. |
| L4 | Low | §4 deep link | `/demo?step=n` changes the session and makes asks that are written to the ledger on a plain GET, and also straight after sign-in through `?next=`. It is loopback only and needs demo login, so the risk is small. But a link can sign the viewer in as Maya without a click. | Run the deep link only when the viewer is signed in. Otherwise, or as a simpler rule, open the overview with that chapter's **Open** button focused. Test 6 then clicks Open. |
| L5 | Low | §9 kit | The presenter and app mode share `$BrowserProfile`. `Close-DemoBrowser` kills any browser started with that profile, so opening app mode closes the presenter window, and the other way round. That behaviour is fine (one demo browser), but nobody has written it down. | Say it in `Show-Verdict` and in the runbook. Compute the start path once after `param`: `$StartPath = $PresentPath; if ($App) { $StartPath = '/demo' }`. Then `Start-Live`, `Start-Offline` and `Reset` all follow the same path without the inline `$(if …)` repeated three times. When diffing the dry-run transcripts, strip the timestamp header line (`demo_day.ps1` main block). |
| L6 | Low | §2 | The line references have already drifted, because the other agent's edits landed. `ctx` is at `controller.ts:60` (not 57), `runStep` at 137 (not 129), `go` at 68-73, `refreshHealth` at 229 (not 218). The presenter now has mouse support: bar buttons and side buttons (`controller.ts:266-314, 433-451`) and browser-history ◀ handling (`:453-479`). | Re-cite by function name. Item 2 of the "what exists" list should name the presenter's new mouse support, because it changes the Q1 answer. |
| L7 | Low (pre-existing) | §8 recordings | `public/clips/*` ships in **every** build, deployments included. So clip A (Rachel's answer, synthetic) is served to anyone at `/clips/…`, signed in or not. App mode does not change this. | Out of scope here. Record a follow-up to keep clips out of deployed builds. |
| L8 | Low | §11 config | The other side configs (`a11y-shell`, `act`, `grow`, `deliverables`) have **no `webServer` block** and take their port from an environment variable (`GROW_PORT ?? 3127`). Port 3132 is free. | Follow the same pattern: `GUIDED_PORT ?? 3132`, `NEXT_DIST_DIR=.next-guided`, no `webServer`. The `testIgnore` line in `playwright.config.ts` is a shared file, so the coordinator lands it. |

What is sound and should stay:
- Steps are looked up by id, which is safe with `setGrowSteps` splicing `STEPS` in place (`script.ts:279-291`).
- The `StepCtx` adapter instead of editing `controller.ts`.
- Using `callPresenter` as is.
- The request id: the Compare cache's `right.res.request_id` for `SPEC_PEOPLE`. Read the cache **first** and the store second; the cache is fresher in this tab.
- `gd` is ignored by `parseSpec` and `sameSpec`. `CompareView.tsx:165` drops `gd` only when the user changes the spec, and then the "back to step n" chip is the correct result.
- Gating on `demo.server`, not `demo.on`.
- The guided state lives in `sessionStorage` and holds only a step number.
- Identity still comes only from `/auth/demo-login` (rule 2). Rules 1, 3 and 5 are untouched.

## 2. History and URL design: verdict

The design is sound once H2, M2 and M3 are fixed:
- Real URLs with a `gd` marker.
- A deep link that replaces itself.
- Browser Back restores the page and never re-runs the action.

One choice to state explicitly: the pill's **Back** runs `step.back ?? go(stepUrl)` (the presenter's ← semantics, so test 3's "no new ask" holds). It does **not** run the action, so Back to chapter 4 does not re-open the source sheet. Pill Back pushes a new entry, so history grows like a paginator: A2 A3 A2. That is acceptable, but if you prefer, the pill's Back can call `history.back()` when the previous entry is chapter n-1. The cleaner rule is to keep the push and document it.

Revisiting `/dev/audit?tamper=1` runs the sandbox tamper again on every visit (`AuditConsole` `autoTamper`). It only ever touches a copy, so this is fine, as the plan says.

## 3. Test coverage: additions

The plan's 13 tests are good. Add these:
1. **Back during a setup** (H2a): open chapter 2, call `page.goBack()` before "Done", and assert the URL stays where the browser put it and no later push happens.
2. **Forward into a `/dev` chapter with another persona** (H2b): chapter 4, then Next to 5, then Back, then Forward. The journey page shows the waterfall, not "Request not found", and `whoami` is Maya.
3. **No speaker cues** (M4): `/demo` and the pill, en and zh, contain none of the `STEPS[].say` strings and none of the deny-list words (run on the rendered text).
4. **End demo after chapter 8** (M3): the URL is the start mode's home.
5. **Presenter armed**: arm with Ctrl+Shift+P during a guided setup. The pill disappears and the setup stops (L3).
6. **Server gate** (M5): a unit test of the gate with `IB_DEPLOYED=1`.
7. **Kit**: the four existing `.cmd` files are unchanged (`git diff --quiet`). The `start -DryRun` transcript without `-App` matches today's, ignoring the timestamp header.

## 4. Answers to the review questions

1. **Stage role.** Booth, rehearsal and walk-up tool, and the backup for a laptop that is not the stage laptop. **Not** the 5:00 path: the presenter now has bar buttons, mouse side buttons and browser-Back handling (`controller.ts:266-314, 433-479`), so Dev already has a mouse on stage. Reword D-35 as "the Guided demo is the walk-up and booth path; the presenter (keys and mouse) stays the stage path". Yes, we need both, for different rooms.
2. **Name.** "Demo story" is safer. It shares no word with the "guided tour" (`Tour.tsx`), so it cannot be mistaken for it. "Guided demo" is acceptable if the icons differ.
3. **Adapter or export.** Use the adapter now, importing `session.ts` (M1). Look at a parameterised `ctx` export after Demo Day, once `controller.ts` stops changing.
4. **Kit.** No last mode, and no offline app mode (H1). One app-mode button.
5. **Recordings.** Do not gate by persona. Any demo-login viewer can switch to Rachel from the user menu, so hiding the button would hide nothing and suggest a protection that does not exist. Keep the label "Recording · not live". Log L7 as the real follow-up.
6. **Dragging.** Collapse only for Demo Day. The corner swap is optional. No free drag.
7. **Entry points.** The user menu only, plus the kit's start URL. Leave out the palette and the Settings card.
8. **Numbering.** "Step n of 11", with the HUD number in small text on the cards (matching the run sheet) and in the pill tooltip.
9. **Stage theme.** No toggle. `setStage` writes the presenter store and `<html>` and belongs to the presenter.
10. **Gating.** The current proxy is fine for UI. The real enforcement is the backend: `/auth/demo-login` answers 404 when demo login is off (`app.py:236-237`). No backend change before Demo Day. The server-side `IB_DEPLOYED` check must be dynamic (M5), because demo login can be on in a deployment.
11. **End demo.** Keep the session, and restore the start mode (M3).
12. **Cold chapter 5.** Running Compare first is acceptable, with one history entry (M2) and the "asking the question first" note. If that Compare fails, fall back to the journey list instead of a failed step.
