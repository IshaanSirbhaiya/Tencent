# App mode plan: judge-impact review (Fable seat)

Reviewed 30 Oct 2026 against the working tree on branch `final_v1.2`. Read-only: nothing was run. I
read `PLAN.md`, the rubric (`rubric/README.md`), `reviews/round3_fable_judges.md` and its dispositions,
`RUN_SHEET.md`, `FOUNDER_TODO.md`, and the presenter code the plan builds on. The facts are checked in
`review_haiku.md`. This file judges the plan's value to the judges, not its correctness.

## 0. Verdict

1. **Keep the guided demo off the stage.** The 5:00 already has a mouse path. The presenter bar has
   clickable buttons: back, next, skip, recording, start and reset (`PresenterBar.tsx:165-185`). The
   mouse's back and forward side buttons also drive it while it is armed (`controller.ts:433-465`). So a
   second runner on stage adds nothing the room can see. It does make two claims false: the spoken line
   "Dev has not touched the mouse all demo" (`RUN_SHEET.md:63`), and the rubric's Demo row ("Presenter
   mode, no mouse") and UX row ("Keyboard-only driving"). Moving the 5:00 to it scores **−0.5 to −1** on
   Demo & Storytelling.
2. **As a tool for the judges' hands-on time, it is worth about +0.5 to +1 in total**, at best. It helps
   a judge who walks up after the pitch and says "show me the tamper again". It does not move HCD: that
   score is held by the empty fix log and the hallway test (round 3: HCD 6, 8 with a real tester line).
3. **Timing is the main risk.** The plan builds on "Saturday or Sunday". Saturday 31 Oct is the hallway
   test and the laptop checks. Sunday is rehearsal and the Monday freeze. 6 to 8 hours of new code plus
   review fixes lands in the same 48 hours that still have to produce the tester line, the proof-slide
   numbers and three timed runs. A guided-demo bug found at the booth costs more than the feature earns.
4. **Recommendation: build a cut-down version (about 3 to 3.5 hours) only after the hallway test notes
   are in.** It should be for hands-on only, with no kit changes, as in the ranked list in §4. If
   Saturday slips, skip it entirely. Instead, give a judge the presenter window: its bar is already
   clickable. Add one line to the runbook saying so.
5. **D-35's wording should change.** Instead of "the primary mouse path", write: "Guided demo (`/demo`)
   is for judges' and visitors' hands-on time. The 5:00 stays on the keyboard presenter, whose bar is
   also clickable."

## 1. Does it raise the three scores?

| Criterion (10 points each) | Round-3 score after its fixes | On stage (guided demo drives the 5:00) | Hands-on only (booth, after the pitch) | Why |
|---|---|---|---|---|
| **6 Demo & Storytelling** | 9 | **−0.5 to −1** | **+0 to +0.5** | The story, the timing and the "two proofs" do not change. The room cannot see whether Dev clicks a pill or presses Space. It can see an address bar, tabs and a "Setting up…" spinner in a maximised normal window, instead of the full-screen Stage theme. The keyboard-only claim is part of how we sell criteria 6 and 8. Losing it, plus the cost of rewriting `RUN_SHEET.md:63`, nets a small negative. At the booth, "open any chapter, watch its recording, go back" lets a judge replay the tamper or the identical refusals without us driving. That is a real but small plus |
| **2 Human-Centered Design** | 6 (8 with the tester line) | 0 | 0 | HCD is scored on evidence that real people used the product and we changed it (`FIX_LOG.md`, `fixlog.json`). A demo harness is not that evidence, and the testers' tasks are product tasks, not the pitch story. **Net negative if it takes Saturday hours from the hallway test** (worth +2 on this row) |
| **8 UX & Accessibility** | 8 | −0.5 (the "keyboard-only driving" claim is gone) | +0 to +0.5 | The plan's accessibility work is good: WCAG 2.2 Focus Not Obscured, a single-pointer alternative to dragging, live region, reduced motion, 简体中文. A judge who tries the browser's Back button and sees it work will notice. But criterion 8 judges the product (Ask, Compare, Settings, axe), which is already 8. The pill is a demo harness, and judges rarely score the harness |
| 10 Overall quality and impression | 9 | 0 | −1 to +0.5 | The upside is "they built a self-serve tour of their own pitch". The downside, if any chapter fails at the booth, is "This step did not finish" in front of a judge, or a persona switch they did not ask for ("why am I Maya now?") |

**Expected value:** about +0.5 if it is built and works, and −1 to −2 if it is built at the expense of the
hallway test or if it fails at the booth. Round-3 items 7 and 10 (the hallway test, Hy3, the WorkBuddy GUI
retest, `B` at every step) are still worth +2 to +4. Those need the founder and the laptop, not a builder,
so the guided demo does not compete with them for **builder** hours. It **does** compete for founder
review time and Saturday's laptop.

## 2. Stage or hands-on?

**Hands-on only.** The reasons, in order of weight:

1. **The fallback is already mouse-capable, in the same window.** If Dev freezes on keys, he clicks ▶ on
   the bar he is already looking at. A separate app-mode launcher means a separate browser window with
   its own clean profile and sign-in. Switching mid-pitch is a 30-second-plus recovery that the run
   sheet's ladder does not have.
2. **A normal window looks worse on a projector.** The plan replaces `--start-fullscreen` with
   `--start-maximized` in a window with an address bar and Back/Forward buttons. The Stage theme
   (larger type, chrome hidden, safe area) is not part of app mode (Q9 leaves it open).
3. **Browser Back on stage is a hazard, not a feature.** A stray side-button press or trackpad swipe
   re-opens a page whose own URL parameters run again (`/dev/audit?tamper=1` re-runs the tamper on
   remount). The armed presenter takes over those buttons on purpose (`controller.ts:434-438`).
4. **Two runners mean two rehearsal paths.** RUN_SHEET §3 wants three timed runs between 4:40 and 5:00 on
   one path. A second path halves the practice on each.
5. **The words change.** `RUN_SHEET.md:63` must be deleted, and the rubric rows 6 and 8 rewritten. Hard
   rule 8 does not allow keeping a claim the stage contradicts.

On the booth side, two points matter. App mode lets a judge drive without learning keys. It also signs
the right personas in for them, which the user-menu persona switch already allows (plan §8). Both are the
right fit for "judges' follow-up".

## 3. Demo Day risks (3 Nov, 5:00 + 1:00 Q&A)

| # | Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|---|
| 1 | The build overruns into Sunday and the Monday freeze, so the keyboard path gets less rehearsal and fewer review fixes | Medium-high (6-8 h estimate plus a 5-seat review on a weekend that already has the hallway test) | High | Time-box it to the cut-down version (§4), starting only after the hallway notes are sent. If it is not green by Sunday 18:00, do not ship it: delete the route and keep the plan |
| 2 | A booth chapter fails cold ("This step did not finish") in front of a judge. Cold chapter 5 runs a live Compare first (about 3 s, live model or fixture); chapters 6 to 8 sign Maya in | Medium | Medium | Rehearse every chapter opened cold, once, on the stage laptop. Keep "Watch recording" one click away on every card. Test 5 (every chapter cold) is the one e2e test not to cut |
| 3 | Kit edits to `demo_day.ps1`, the file the 5:00 launch depends on, made while another agent is editing it | Low-medium | High (the stage launcher) | **Cut the kit work.** At the booth, open `http://127.0.0.1:3100/demo` in a normal browser tab. No `-App`, no new `.cmd`, no shortcut, no last-mode file |
| 4 | Naming confusion: the tour's own UI already says "Guided tour" (`lib/i18n/tour.ts:10,17`) and its toast offers "Take the tour" on `/`. A "Guided demo" item in the same menu reads as the same thing | Medium | Low | Call it **"Demo story"** (Q2), with a different icon. "End demo story" lands on `/`, where the tour offer toast can appear: dismiss it for that tab, or accept it |
| 5 | Rule 4 at the booth: a judge signed in as Amy opens chapter 2 and sees Rachel's answer, or watches clip A (Rachel's case) | Low (demo sign-in already lets anyone pick Rachel; clips are synthetic and labelled) | Medium if a judge asks "so anyone can see the case?" | Keep the plan's content-free cards and deny-list test. Each card should say who it signs in ("Opens as Rachel, the MLRO"), so the persona switch is announced, not silent. That also answers the question before it is asked |
| 6 | The stage laptop runs the presenter armed in one window and someone opens `/demo` in another tab of the same profile | Low | Medium (two runners) | The plan hides the pill while armed. Also: never open `/demo` in the stage profile before the 5:00. It is a booth-only tab, opened after |
| 7 | Hy3 wording: the overview's model line says "live" when it is not | Low | High (rule 8, [HY3]) | The plan's default is the honest wording. The line reads the same `models.active` as the HUD dots. Keep it |

## 4. Ranked list: what to build, and what to cut if time is short

Build from the top and stop when time runs out. Everything below the line is cut first. Hours are taken
from the plan's §14, scaled to the cut-down scope.

| Rank | Item | Hours | Why this order |
|---|---|---|---|
| 1 | **Do nothing to the 5:00.** Add one runbook line: "The presenter bar's ◀ ▶ buttons work with the mouse; hand a judge this window if they want to drive." Change D-35's wording (§0 item 5) | 0.1 | Zero risk. It already covers "a judge wants to click through" |
| 2 | i18n strings (en, zh), content-free card lines, and the deny-list unit check | 0.5 | Rule 4 first. Everything else depends on these strings |
| 3 | The `StepCtx` adapter by step id: generation counter, persona table, `serialSession` from `session.ts` (see `review_haiku.md` row 27), and the cold-journey precondition with `replace` | 1.5 | The core of "open any chapter". It is the only new logic |
| 4 | The `/demo` overview: server gate (copy preflight's `force-dynamic` and `IB_DEPLOYED`), client gate on `demo.server`, 11 cards with **Open** and **Watch recording** (a Radix dialog) | 1.0 | This is the whole hands-on value. With only ranks 2 to 4, a judge picks a chapter, sees it set up, and comes back to `/demo` with the browser's Back button |
| 5 | E2E tests 5 (every chapter cold), 7 (recording modal), 10 (gating), 11 (rule 4), 12 (axe at 1280, light and dark, en and zh); plus `tsc` and one `presenter.spec.ts` run | 1.0 | The checks that stop a booth failure and a rule-4 slip |
| — | *Cut line for a 3-to-4-hour budget* | | |
| 6 | A minimal pill: "Chapter n of 11", Next, Back, Overview, End. Bottom right, `z-30`, with reserved padding. No collapse, no corner swap | 1.0 | Nice for a judge, but `/demo` plus the browser's Back button covers it |
| 7 | User-menu link ("Demo story") | 0.25 | Before this exists, a URL typed at the booth is enough |
| 8 | The WorkBuddy chapter's off-app panel and "Show newest record" | 0.5 | At the booth, WorkBuddy is shown in its own window anyway. The card can just say "open WorkBuddy" |
| 9 | Re-checking the persona on browser Back/Forward, the `gd` marker on every page, and e2e tests 3, 4 and 6 | 1.0 | The most intricate part (history, persona races) for the least judge value. Without it, Back is a plain page view and the persona stays as it is |
| 10 | Collapse chip, corner swap, "back to step n" chip, the 390-px bar form, the 1920 layout test (13) | 1.0 | Polish. 390 px matters for the product, not for a booth laptop |
| 11 | **Kit:** `-App`, the new `.cmd`, the `--app` mapping in five `.cmd` files, the shortcut row, the Reset last-mode file, offline app mode | 0.75 + rehearsal | **Cut entirely before Demo Day.** It edits the 5:00's launcher in freeze week, adds a sixth button to a runbook built around "use the lowest rung that works", and the booth needs only a URL. Revisit after 3 Nov |
| 12 | Command palette entry (Q7), "Stage view" toggle (Q9), free drag (Q6) | — | Not needed |

## 5. Answers to the plan's review questions (Fable view)

1. **Q1 (stage role):** a booth and follow-up tool only. The 5:00 stays on the keyboard presenter, whose
   bar already covers the "Dev wants a mouse" fallback. We do not need both runners on stage.
2. **Q2 (name):** "Demo story". The tour is already labelled "Guided tour" in the UI.
3. **Q3 (adapter or export):** the adapter, now. Revisit the export after 3 Nov.
4. **Q4 (kit):** none before Demo Day (rank 11).
5. **Q5 (recordings and rule 4):** keep the clips available. They are synthetic, static, labelled
   "Recording · not live", and already reachable with `B`. Say on the card which personas the clip shows.
6. **Q6 (dragging):** neither drag nor corner swap for now. Reserved padding is enough at 1280.
7. **Q7 (entry points):** the user menu only (or just the URL at the booth).
8. **Q8 (numbering):** "Chapter n of 11" for judges, with HUD `hh` in small text so the team can map it
   to the run sheet.
9. **Q9 (stage theme):** no toggle.
10. **Q10 (gating):** `DEMO_COMPARE && demo_features` is enough for a booth tool. Do not change the
    backend in freeze week.
11. **Q11 (end demo):** keep the session.
12. **Q12 (cold chapter 5):** run the Compare first. For a judge, seeing the real request journey is the
    point. Announce it as "Asking the question first", and offer the recording on failure.
