# Demo story (mouse-driven app mode): plan v2

Status: **plan v2. Nothing is built.** Written 30 Oct 2026 on branch `final_v1.2`. It replaces
[`PLAN.md`](PLAN.md) (v1, kept as written) after the four-seat review:
[`review_opus.md`](review_opus.md) (architecture and security), [`review_sonnet.md`](review_sonnet.md)
(UX and accessibility), [`review_haiku.md`](review_haiku.md) (facts and cites),
[`review_fable.md`](review_fable.md) (judge impact). **Codex was not run on this plan** (budget); the
review is four seats, not five. Codex joins the post-build reviews only if budget allows (§9).

Paths under `databridge/apps/internal_brain_web/` are shortened to `web/`. Cites are `file:line` as of
30 Oct, re-checked against the Haiku review; `controller.ts` is still moving, so its cites also name
the function.

---

## 0. What the founder asked for (30 Oct)

1. A **separate**, single-click way to run the Demo Day story like a normal website: driven by the
   mouse, the browser's Back and Forward work, things close with a mouse click.
2. The existing keyboard presenter stays **exactly as it is**, as the fallback. No edit to
   `web/components/presenter/*`, `/present?present=1`, or the four existing launcher `.cmd` files.
3. Built **Saturday 31 Oct or Sunday 1 Nov**, after today's submission work and after the hallway
   test. It must never slow the main track (hallway test, proof-slide numbers, three timed runs).

---

## 1. Resolved decisions

| # | Topic | Decision | From |
|---|---|---|---|
| R1 | **Name** | **"Demo story"**, zh **演示流程**. The tour's own UI label is already "Guided tour" (`web/lib/i18n/tour.ts:10,17`), so "Guided demo" is one word away from it (working rule 6: never shadow). Different icon from the tour. Code names follow: module `web/components/demo-story/`, strings `web/lib/i18n/demoStory.ts`, tab state `sessionStorage['ib.demostory']`, URL marker `ds=n` (v1's `gd`) | Opus Q2, Sonnet, Haiku 23, Fable Q2 |
| R2 | **Role** | For the **booth and judges' hands-on time** (and rehearsal on a laptop that is not the stage laptop). **The 5:00 stays on the keyboard presenter.** Said honestly: the presenter already works with the mouse. Its bar has clickable back, next, skip, recording, start and reset buttons (`PresenterBar.tsx:165-185`), and the mouse's side buttons drive it while armed (`controller.ts:433-451`, handler; browser-history ◀ handling `:453-479`). The Demo story is not a "mouse fallback for the stage"; the stage already has one | Opus Q1, Sonnet Q1, Fable §0, §2 |
| R3 | **D-35 wording** | "**D-35 (30 Oct). Demo story (`/demo`) is a separate, mouse-driven way to run the Demo Day story, for judges' and visitors' hands-on time and the booth.** It runs the same 11 steps from `script.ts`, one chapter at a time, in a normal browser window where Back and Forward work. **The 5:00 stays on the keyboard presenter, whose bar buttons and mouse side buttons already work with the mouse.** The presenter and `Start Walnut Demo.cmd` are unchanged and remain the fallback. Off on any deployment (`IB_DEPLOYED`)." D-34 is taken (`DECISIONS.md:41`), so D-35 is the next free number; re-check before writing | Fable §0.5, Opus Q1, Haiku 54 |
| R4 | **Entry points** | (a) The URL `http://127.0.0.1:3100/demo`, typed at the booth (works from Phase 1). (b) One **"Demo story"** item in the user menu (`PersonaMenu`, between Settings `ShellControls.tsx:251-256` and Sign out `:258-261`), shown only when `DEMO_COMPARE && demo.server`. (c) Phase 3 only: the new `Start Walnut Demo (app mode).cmd` and its desktop shortcut. **Not** the command palette, **not** a Settings card, **not** a link from the tour | Opus Q7, Fable Q7; Sonnet F11 partly (see disposition) |
| R5 | **Numbering** | On screen: **"Chapter n of 11"** (zh "第 n 章，共 11 章"), n = 1..11. The HUD number appears as visible small text "HUD 02" on the cards and on the pill's title row (not a tooltip: F1). The pill is never on screen with the presenter bar (it hides while armed, §5.4), so "Chapter 3 of 11" and the bar's "02 / 10" never meet. In docs: "chapter n (HUD hh)" everywhere | Opus Q8, Sonnet Q8, Haiku 61, Fable Q8 |
| R6 | **Exit restores the starting mode** | The button is **"Exit demo story"** (zh 退出演示流程; matches the presenter's "Exit"; F17). When the story starts, the current mode (`user` or `dev`) is stored in `ib.demostory`. Exit calls `switchMode(startMode)`, which opens `MODE_HOME[startMode]` (`web/lib/prefs.ts:113`); without this, any chapter 5 to 8 visit leaves the tab in `dev` (`modeForPath`, `prefs.ts:104-111`) and Exit would land on `/dev`. The session is kept (no sign-out). Exit announces "Demo story closed. You are still signed in as <persona>." and sets a per-tab "dismissed" flag | Opus M3, Q11; Sonnet F17, F8 |
| R7 | **Recordings** | Labelled **"Recording · not live"** (`pr.clip.badge`) inside the dialog body. **Not gated by persona**: any demo-login viewer can already switch to Rachel from the user menu, so hiding would protect nothing and suggest a protection that does not exist; the clips are synthetic. Each card and the dialog name who the clip shows ("Shows: Amy and Rachel"). The dialog has one visible line, **"What this recording shows"**, a content-free sentence per clip in en and zh (WCAG 1.2.1). `<video controls muted>`; **no autoplay** when `prefers-reduced-motion` or `data-motion="reduce"` (muted autoplay otherwise; WCAG 2.2.2) | Opus Q5, Sonnet F4, Q5, Fable Q5 |
| R8 | Adapter, not export | App mode uses its own `StepCtx` adapter and imports from the presenter (`script.ts`, `bus.ts`, `session.ts`); it never edits a presenter file. A parameterised `ctx` export is looked at after 3 Nov | all four seats, Q3 |
| R9 | Kit | Phase 3 only, and only a **new** `.cmd`. No last-mode file, no offline app mode, no change to Reset, the four existing `.cmd` files byte-identical | Opus H1, Fable rank 11 |
| R10 | Not built | Free drag, corner swap, "Stage view" toggle, command-palette entry, backend `demo_login` field | Opus Q6/Q9/Q10, Sonnet F13, Fable rank 12 |
| R11 | Cold chapter 5 | Runs chapter 2's Compare first ("Setting up: asking the question first", announced). If that Compare fails, the failed state offers Try again, Skip, Watch recording | Opus Q12, Sonnet Q12, Fable Q12 |
| R12 | Stop rule | If Phase 1 is not green by **Sunday 1 Nov 18:00**, it does not ship: remove the route and the two shared lines (§10); the plan stays. Phase 2 and 3 have the same deadline | Fable risk 1 |

---

## 2. Disposition of every review finding

Key: **A** accepted as written; **C** accepted with a change; **R** rejected. "P1/P2/P3" is the build
phase that carries it (§4 to §6). "Out" means out of scope, recorded as a follow-up.

### 2.1 Opus (architecture and security)

| # | Finding (short) | Disp. | Phase | Reason / what changes |
|---|---|---|---|---|
| H1 | Kit change touches the stage path (`.cmd` mapping in all five files; `last-mode.txt` makes Reset reopen `/demo` before the 5:00) | **A** | P3 | Only the new `Start Walnut Demo (app mode).cmd` passes `-App`. No `--app` mapping anywhere, no last-mode, no offline app mode, Reset untouched. `git diff --quiet` on the four existing `.cmd` files is a Phase 3 exit test |
| H2 | Browser Back/Forward race the setup and the session ((a) setup pushes after the user's Back; (b) `/dev` page mounts under the old persona and never refetches) | **A** | P2 | `popstate` during a setup bumps the generation and calls `callPresenter('compare-cancel')`; persona re-checked only for chapters 5 to 8; on a change, sign in then `location.replace(sameUrl)`. Phase 1 states the interim behaviour honestly (§4.6) |
| M1 | Private promise chain races the shell's persona switch and Sign out | **A** | P1 | Adapter imports `serialSession`, `sessionOwner`, `markSessionOwned` from `presenter/session.ts:17,24,29` (import only) and mirrors `controller.ts` `signInMain` (`:73-97`): if `sessionOwner()` changed since the setup began, stop; the shell's choice wins |
| M2 | Cold chapter 5 pushes two history entries | **A** | P2 | Adapter rule: the first `go()` of a setup pushes (replaces for a deep link), every later `go()` in the same setup replaces. The marker is added with `URL`/`URLSearchParams`, not string concatenation. Note: the rule is about 5 lines in `runner.ts`; the builder may pull it into Phase 1 if it is free, otherwise Phase 1 lives with one extra Compare entry after a cold chapter 5 (§4.6) |
| M3 | Exit lands on `/dev` after chapters 5 to 8 | **A** | P1 | R6 |
| M4 | Speaker cues (`STEPS[].say`) say what rule 4 hides; the deny-list scanned only `guided.ts`, not the rendered titles | **A** | P1 | `say` is never rendered. The deny-list runs on the **rendered** `/demo` page and pill, en and zh, and includes every `say` string as a must-not-appear substring; `\bSTR\b` whole-word. Chapter 3's card line does not say which company exists |
| M5 | Server gate pre-rendered at build time without `force-dynamic`; `IB_DEPLOYED` is the only thing keeping `/demo` off a deployment (demo login can run deployed with a passcode, `config.py:83-86`) | **A** | P1 | `web/app/(user)/demo/page.tsx` with `export const dynamic = 'force-dynamic'` (as `preflight/page.tsx:9`), gets the shell from `app/(user)/layout.tsx`. Pure `gate.ts` tested with `IB_DEPLOYED=1` |
| M6 | Estimate optimistic (10 to 12 h) | **C** | all | Not one 10-12 h block: three phases, 3.5 h + 3 h + 2 h, each gated on the previous one being green, with R12's stop rule. Cuts per Opus: no last-mode, no offline, no corner swap, no palette, trimmed axe matrix |
| L1 | "Tencent Hunyuan (live)" hard-coded; `refreshHealth()` writes the presenter store | **A** | P1 | Read `/ib/status` directly in the Demo story module (no `refreshHealth`). When `models.active`, show the provider the status names (confirm the field, `app.py:216-222`, at build time); otherwise "Model: not live. Answers are quoted from the sources, with the same checks." Never "Hy3 wrote" ([HY3]) |
| L2 | `WorkBuddyCard` is a fixed overlay, not pill content | **C** | P1 | A small Demo story panel reuses `Q_WORKBUDDY` and the `pr.wb.*` strings, plus **Show newest record** (`/dev/audit?latest=1&ds=8`). The presenter file is not touched or rendered |
| L3 | Arming the presenter during a setup gives two runners; `localStorage['ib.presenter']='1'` arms on every load | **A** | P1 | When `armed` turns true: bump the generation, mark the story inactive, hide the pill. Runbook line on the `localStorage` case |
| L4 | `/demo?step=n` signs in and asks on a plain GET | **A** | P2 | Deep link runs only when signed in; otherwise the overview opens with that chapter's **Open** focused. The deep link itself is Phase 2 (history work) |
| L5 | Shared `$BrowserProfile`: app mode closes the presenter window and the reverse; repeated inline `$(if …)` | **C** | P3 | Compute `$StartPath` once after `param`. Used only by `Start-Live` (Start-Offline and Reset are not changed, R9). Say the one-browser behaviour in `Show-Verdict` and the runbook. Dry-run diffs strip the timestamp header |
| L6 | Cites drifted; the presenter now has mouse support | **A** | — | §8 and R2 |
| L7 | `public/clips/*` ships in every build, deployments included | **Out** | — | Pre-existing, not changed by app mode. Follow-up: keep clips out of deployed builds (after 3 Nov) |
| L8 | Side configs have no `webServer`, port from env | **A** | P1 | `demo-story.config.ts`: `DEMOSTORY_PORT ?? 3132`, `NEXT_DIST_DIR=.next-demostory`, no `webServer`; coordinator adds it to `testIgnore` (`playwright.config.ts:11`) |
| Opus §2 | Pill Back = presenter's ← semantics (`step.back ?? go(url)`, no action re-run) | **A** | P1 | Phase 1 pill Back pushes (paginator history, documented); Phase 2 refines it per F6 |
| Opus §3 | Seven extra tests | **A** | P1–P3 | Listed in each phase's exit tests |

### 2.2 Sonnet (UX and accessibility)

| # | Finding (short) | Disp. | Phase | Reason / what changes |
|---|---|---|---|---|
| F1 | Title cannot fit a 22 rem, 56 px pill; tooltip is hover-only | **A** | P1 | ≥768 px: three rows, about 88 px. Row 1 "Chapter 3 of 11 · HUD 02", Collapse. Row 2 the title, wrapping to two lines, no tooltip. Row 3 Back, **Next** ("Next: <title>" as its accessible name), Watch recording, More. `--demostory-h` set from a ResizeObserver, not assumed. 390 px: one-row bar, title on expand |
| F2 | Moving focus to `<main>` after Next hurts keyboard and switch users | **A** | P1 | Focus stays on the activated button. `aria-disabled` (not `disabled`) while busy, as `PresenterBar.tsx` does. Chapter changes announced through the shared `announce()` (`ui/announce.tsx`), no second live region. A skip link "Skip to demo story controls", visible on focus |
| F3 | Slides have no shell, so no reserved padding there | **A** | P1 | On `/present/*` the pill defaults to the 40 px chip; the user can expand it. Layout test covers the slide footer. 390 bar gets `padding-bottom: env(safe-area-inset-bottom)` |
| F4 | Video-only clip needs a text alternative; autoplay | **A** | P1 | R7 |
| F5 | Focus return breaks when the dialog opens from a menu item | **A** | P1 | **Watch recording** is its own pill button (≥768), not in More. At 390, where it is in More, `onCloseAutoFocus` focuses the More trigger |
| F6 | Pill Back and browser Back disagree | **A** | P2 | When the previous history entry is chapter n-1 (`ds` check), pill Back calls `history.back()`; otherwise push |
| F7 | Wrong person's answers shown for a moment on Back | **A** | P2 | "Switching to Maya…" in the pill, announced; a failed `switchTo` shows the failed state, never a blank |
| F8 | `ds` in the URL with no story state is undefined | **C** | P1 + P2 | P1: Exit sets a per-tab "dismissed" flag; a `ds` page with no state shows only the chip "Demo story: open chapter n" (one click re-runs the setup), and nothing after Exit. P2: a shared `ds` link activates the full pill when the gate passes |
| F9 | "Disabled while busy" contradicts "Next twice supersedes" | **A** | P1 | `aria-disabled` while busy, clicks ignored; a **12 s timeout** moves a stuck setup to the failed state (Try again, Skip, Watch recording). The generation counter stays for Overview, Exit and (P2) history |
| F10 | First and last chapter not specified | **A** | P1 | Chapter 1: Back `aria-disabled`. Chapter 11: Next becomes **Finish**, opens the overview, announces "Demo story complete" |
| F11 | Judge alone has no obvious start; add palette and tour link | **C** | P1 | Accepted: one primary **Start at chapter 1** button above the cards. Rejected: palette entry and the tour link (they edit `CommandPalette.tsx` and `Tour.tsx`, two more shared surfaces, for a booth where we type the URL; Opus and Fable say user menu only) |
| F12 | Next should be bigger on stage | **A** | P1 | Next 44 px high, filled primary; others 36 px; ≥8 px gaps |
| F13 | Corner swap and drag not needed | **A** | — | Corner swap and drag cut (R10). Collapse kept (F3) |
| F14 | `z-40` collides with the dialog scrim, presenter bar, WorkBuddyCard | **A** | P1 | Pill at **`z-[45]`**: above page content and other `z-40` overlays, below dialog content (`z-50`, `dialog.tsx:58,93`), the clip overlay (`z-[60]`) and the tour (`z-[70]`/`z-[80]`). Under a Radix modal the pill is inert (outside pointer events off), so it shows but cannot be clicked: accepted. Haiku and Fable proposed `z-30`; rejected because `z-30` would sit under the `z-40` overlays and the scrim |
| F15 | `<nav>` is the wrong landmark | **A** | P1 | `<section aria-label="Demo story">`, buttons in `role="group"`; icon-only 390 buttons carry the target title in `aria-label` |
| F16 | zh and 200 % zoom overflow untested; motion | **A** | P1 | Layout test adds zh at 200 % and 390 px. Transitions use `motion-safe:` and honour `data-motion="reduce"` |
| F17 | "End demo" sounds like sign-out | **A** | P1 | R6 ("Exit demo story", announcement) |
| Q11 | Restore the persona signed in at the start | **R** | — | Another session switch at Exit is a surprise of its own; the announcement says who is signed in. Revisit after 3 Nov |
| Q10 | `demo_features` on but demo login off: Open fails | **A** | P1 | Covered by the failed state (sign-in error or the 12 s timeout) |
| Armed edges | Pill hidden when armed by `?present=1` after mount; Exit from the presenter brings back the chip, not the full pill; no runner while armed | **A** | P1 | L3 plus the chip-after-exit rule; one e2e test |

### 2.3 Haiku (facts and cites)

The 14 corrections and its "apply in order" list (`review_haiku.md` §5):

| # | Correction | Disp. | Where it lands |
|---|---|---|---|
| H-1 (row 54) | D-34 is taken (`DECISIONS.md:41`); the decision is D-35 | **A** | R3 |
| H-2 (row 44) | `Start-Offline` uses `$MockUrl` (`:552`), not `$LiveUrl`; `Show-Verdict` prints `$Base$PresentPath` (`:396`) | **A** (moot for offline) | Offline app mode is dropped (R9), so `Start-Offline` is untouched. `Show-Verdict`'s `-App` line uses `$Base` (P3) |
| H-3 (rows 27, 11) | `signInMain` via `serialSession`/`sessionOwner`; `callPresenter` gets the liveness function as its 4th argument (`bus.ts:40`, as `controller.ts:76`) | **A** | P1 adapter (M1) |
| H-4 (row 33) | Cold journey pushes twice | **A** | P2 (M2) |
| H-5 (row 25) | `WorkBuddyCard` cannot go inside the pill unchanged | **A** | P1 (L2) |
| H-6 (rows 50, 55) | `PRESENTER_KEYS.md` path; RUN_SHEET:63, rubric README rows 6 and 8, `demo_story/README.md:29` | **A** | §8, §7 |
| H-7 | `controller.ts` drifts and others | **A** | §8 |
| H-8 (rows 37, 35) | Live-region example names the wrong step; scrim is `z-40` | **A** | The example is now "Chapter 4 of 11: Source [2] on Rachel's answer" (`step.citation`, HUD 03). z-index per F14 |
| H-9 (row 15) | Two `PresenterRoot` mounts (`app/layout.tsx:36-37` and `app/present/layout.tsx:14`, one renders via `useIsOwner`, `PresenterRoot.tsx:32-51`) | **A** | The pill is mounted **only** at the root `app/layout.tsx`, so it needs no owner guard |
| H-10 (row 17) | `refreshHealth` writes `health` into the presenter store | **A** | Not used (L1); the store is never written |
| row 13 | Compare's rewrite drops the marker | **A** | When the user changes the spec, the pill falls back to the chip; that is correct |
| row 24 | Failed UI is in `PresenterBar.tsx:136-140,177-178,288-290`; `pr.failed` names keys | **A** | The Demo story has its own failed strings in `demoStory.ts` |
| row 34 | Back to `/dev/audit?tamper=1` re-runs the sandbox tamper | **A** | Reworded: "history never re-runs the script action; the page's own URL parameters still apply" (copy only, as the presenter's ←) |
| row 42 | Exit to `/` on a clean profile shows the tour offer toast (`Tour.tsx:464`) | **A** (accept the toast) | Harmless; dismissing it would edit `Tour.tsx`. Noted in the runbook |
| row 47 | "four buttons" text at `demo_day.ps1:4,23`; RUNBOOK lines 4 and 15 | **A** | P3 docs |
| row 59 | RUN_SHEET 0:00 says `/compare` open, `script.ts` step 00 is the title slide | **Out** | Upstream run-sheet inconsistency; flag to the RUN_SHEET owner. This plan follows `script.ts` |
| row 61 | Three numberings mixed | **A** | R5 |

### 2.4 Fable (judge impact): verdict and ranked list

| Item | Disp. | Reason / what changes |
|---|---|---|
| §0.1 Keep it off the stage (moving the 5:00 costs −0.5 to −1 on Demo & Storytelling, breaks `RUN_SHEET.md:63` and rubric rows 6, 8) | **A** | R2 |
| §0.2 Worth about +0.5 to +1 as a hands-on tool; does not move HCD | **A** | Sets the priority: it never takes hours from the hallway test |
| §0.3 Timing is the main risk | **A** | Phases, Saturday start only after the hallway notes, R12 stop rule |
| §0.4 Cut-down 3 to 3.5 h, hands-on only, no kit | **C** | Phase 1 is that cut-down (3.5 h) but includes the pill (founder decision). The kit returns only as Phase 3, optional, as a new `.cmd` |
| §0.5 D-35 wording | **A** | R3 |
| Risk 2: rehearse every chapter cold once on the stage laptop | **A** | Phase 1 exit, a founder/laptop step (not builder hours) |
| Risk 5: each card says who it signs in ("Opens as Rachel, the MLRO") | **A** | P1 card line |
| Risk 6: never open `/demo` in the stage profile before the 5:00 | **A** | Runbook line |
| Rank 1: no change to the 5:00; runbook line "the presenter bar's ◀ ▶ work with the mouse; hand a judge this window" | **A** | Phase 0: doc only, 5 min, can land any time (it is true today) |
| Rank 2: strings, content-free lines, deny list | **A** | P1 |
| Rank 3: adapter by id, generations, persona table, `serialSession`, cold journey with `replace` | **C** | P1, except push-then-replace in P2 (M2 note) |
| Rank 4: `/demo` overview, gates, cards, Watch recording | **A** | P1 |
| Rank 5: e2e 5, 7, 10, 11, 12 + `tsc` + `presenter.spec.ts` | **A** | P1 exit tests |
| Rank 6: minimal pill (below Fable's cut line; `z-30`, no collapse) | **C** | Moved above the line into P1 by founder decision, with F1 to F3 fixes, `z-[45]`, collapse chip kept (slides need it) |
| Rank 7: user-menu link | **A** | P1 (R4) |
| Rank 8: WorkBuddy off-app panel and Show newest record | **C** | P1, minimal panel (L2), 0.2 h |
| Rank 9: persona re-check on Back/Forward, marker handling, e2e 3, 4, 6 | **C** | P2, only if P1 is green |
| Rank 10: collapse chip, corner swap, "back to chapter n" chip, 390 bar, 1920 test | **C** | Chip, 390 bar and 1920 bbox test in P1 (needed for F3 and the axe pass at 390); corner swap rejected |
| Rank 11: kit cut entirely before Demo Day | **C** | P3, optional, a **new** `.cmd` only (R9); the four existing files byte-identical |
| Rank 12: palette, Stage view, free drag | **A** | R10 |

---

## 3. Shared design (all phases)

**3.1 Chapters.** The same 11 steps as `script.ts` (`STEPS`, ids at `script.ts:92,104,115,126,140,159,167,175,185,193,201`), looked up **by id**, never by index (`setGrowSteps` splices `STEPS`, `script.ts:279`). Grow steps are never chapters.

| Ch. (HUD) | Step id | Main persona ensured before the action | Page | Clip |
|---|---|---|---|---|
| 1 (00) | `title` | Rachel `mlro.tan` (the action signs in, `script.ts:98`) | `/present` | A |
| 2 (01) | `compare-people` | Rachel | `/compare` Amy vs Rachel, `compare-run` | A |
| 3 (02) | `compare-person` | Rachel | `/compare?mode=person`, Ken | A |
| 4 (03) | `citation` | Rachel (action signs in, `:133`) | Compare, `compare-open-citation` slot `c`, n 2 (self-sufficient, `CompareView.tsx:253-275`) | A |
| 5 (04) | `journey` | Maya `maya.compliance` (action signs in, `:145`) | `/dev/journey/<Rachel's req>` | B |
| 6 (05) | `verify` | Maya (Demo story signs in first; D-05) | `/dev/audit?verify=1` | B |
| 7 (06) | `tamper` | Maya | `/dev/audit?tamper=1` (sandbox copy) | B |
| 8 (07) | `workbuddy` | Maya | `/dev/audit` + WorkBuddy panel | C |
| 9 (08) | `proof` | kept | `/present/proof` | none |
| 10 (09) | `business` | kept | `/present/business` | none |
| 11 (10) | `close` | kept | `/present/close` | none |

**3.2 Adapter** (`web/components/demo-story/runner.ts`). Implements `StepCtx` (`script.ts:27-45`):
`go` = router push (marker `ds=n` added with `URL`); `call` = `callPresenter(name, arg, t, live)`
(`bus.ts:40`); `signInMain` = `serialSession` + `sessionOwner` check + `markSessionOwned`
(`session.ts:17,24,29`), mirroring `controller.ts` `signInMain` (`:73-97`); `req(p)` = Compare cache
first (`cachedResult(SPEC_PEOPLE).right.res.request_id`), presenter store `reqs[p]` second (read
only); `reveal` = the same wait-and-scroll loop as `controller.ts:99-113`, `behavior: 'auto'` under
reduced motion. A generation counter as `controller.ts:33-46`. It never writes the presenter store.

**3.3 Gating.** Server: `web/app/(user)/demo/page.tsx`, `dynamic = 'force-dynamic'`, `notFound()` when
`IB_DEPLOYED === '1'` or `DEMO_COMPARE` is off, via a pure `gate.ts`. Client: chapters only when
`useDemoFeatures().server === true` (`demo.ts:104`); `demo.server`, not `demo.on`, on purpose (clean
profile, v1 §7). Pill and menu item: same condition. The backend stays the real enforcement:
`/auth/demo-login` is 404 when demo login is off (`app.py:236-237`).

**3.4 Rule 4.** Cards and pill show only `step.<id>` titles (`presenter.ts` en 141-154, zh 326-339)
and content-free lines from `demoStory.ts` (mechanism only; who it opens as; who the clip shows).
`/demo` fetches nothing but `/ib/status`. The deny list (scanned on the rendered page, en and zh):
every `STEPS[].say` string, `\bSTR\b`, "suspicious", "可疑", "money-laundering", "a company that does
not exist", case ids from the fixture manifest (derived, not typed: working rule 4). Company names
are **not** on the list: the existing titles name both companies and say neither is real or fake.

---

## 4. Phase 1 (about 3.5 h, Saturday after the hallway test)

Start only after the hallway-test notes are sent. Nothing in Phase 1 touches `components/presenter/*`
or the kit.

**4.1 Work**

| # | Work | Files | Est. |
|---|---|---|---|
| 1.1 | Strings en/zh: overview, card lines, clip descriptions, pill, failed state, announcements, WorkBuddy panel | `web/lib/i18n/demoStory.ts` | 0.4 h |
| 1.2 | Chapter data (persona per id, clip "shows" line), tab store `ib.demostory` (step, start mode, dismissed), adapter (§3.2), cold chapter 5 precondition, 12 s timeout, armed → stop | `web/components/demo-story/{chapters,store,runner}.ts` | 1.0 h |
| 1.3 | Overview: server gate (`force-dynamic`), client gate, model line (L1), **Start at chapter 1**, 11 cards (title, "HUD hh", line, "Opens as …", **Open**, **Watch recording**) | `web/app/(user)/demo/page.tsx`, `demo-story/{gate.ts,Overview.tsx}` | 0.6 h |
| 1.4 | Pill: F1 layout, F2 focus, F3 chip on slides, F9/F10, F12, F15, `z-[45]`, reserved padding var, skip link, hidden while armed, chip after presenter exit, WorkBuddy panel | `demo-story/{Pill,WorkBuddyPanel}.tsx` | 0.8 h |
| 1.5 | Recording dialog (R7, F5) on `components/ui/dialog.tsx` | `demo-story/RecordingDialog.tsx` | 0.2 h |
| 1.6 | Shared lines, landed by the coordinator: mount in `app/layout.tsx` (next to `<TourOverlay/>`), menu item in `ShellControls.tsx`, `testIgnore` in `playwright.config.ts` | 3 shared files, one line each | 0.1 h |
| 1.7 | E2E spec and config, exit tests below | `web/tests/e2e/demo-story.{spec,config}.ts` | 0.4 h + run |

**4.2 Exit tests** (mock mode, `demo-story.config.ts`, `DEMOSTORY_PORT ?? 3132`, `.next-demostory`, no
`webServer`). All must pass for Phase 1 to be "green".

| # | Test | Source |
|---|---|---|
| E1 | User menu shows "Demo story"; it opens `/demo` with 11 cards in `script.ts` order, titles from `step.<id>`, "HUD hh" visible | v1 1 |
| E2 | **Every chapter opened cold** from the overview: URL and `ds`, page, and `whoami` match §3.1 (the test not to cut) | v1 5, Fable risk 2 |
| E3 | Chapter 2: both answers done, pill "Chapter 2 of 11", `whoami` Rachel | v1 2 |
| E4 | Pill Next → chapter 3 (Ken); pill Back → chapter 2 with no new `/api/ib/ask` | v1 3 |
| E5 | Focus stays on Next after a click; `aria-disabled` while busy; a double click runs one setup; a hung ask reaches the failed state at 12 s with Try again, Skip, Watch recording | F2, F9, Sonnet Q10 |
| E6 | Chapter 1 Back is `aria-disabled`; chapter 11 Next reads Finish and opens the overview with "Demo story complete" | F10 |
| E7 | Recording dialog from the card and from the pill: badge in the body, "What this recording shows" line, video or "not recorded" fallback, Close, Esc, focus returns to the opener; no autoplay with `reducedMotion: 'reduce'` | v1 7, F4, F5 |
| E8 | Run through chapter 8, Exit: URL is `MODE_HOME[startMode]` (`/` when started in user mode), pill gone, state cleared; `page.goBack()` then shows no pill | M3, F8, Opus §3.4 |
| E9 | Gating: `/ib/status` routed to `demo_features: false` → no menu item, `/demo` not found; Node-side test of `gate.ts` with `IB_DEPLOYED=1` → not allowed | v1 10, M5 |
| E10 | **Rule 4 deny-list scan of the rendered page**: signed in as Amy, `/demo` and the pill on every chapter, **en and zh**, contain no deny-list entry (§3.4); `/demo` makes no `/ib/ask` request | v1 11, M4 |
| E11 | Presenter armed (Ctrl+Shift+P) during a setup: pill leaves the DOM and the setup stops; presenter Exit brings back the chip | L3, Sonnet |
| E12 | Presenter unchanged: `?present=1` lands on `/present`, `P` starts at HUD 00 (smoke copy); plus the full `presenter.spec.ts` once under the main config | v1 9 |
| E13 | **axe** (`expectAccessible`, `helpers.ts:30`) on `/demo` and on a chapter with the pill expanded and as the chip: light and dark × 1280x860 and 390x844 in en, plus one zh pass | v1 12, M6 trim |
| E14 | Layout: at 1280 and 1920 the pill box does not intersect Compare's Ask button, the citation chips, Verify, the slide headline or the slide footer (slides show the chip); at 390 no horizontal scroll; zh at 200 % zoom at 390 | v1 13, F3, F16 |
| E15 | `npx tsc --noEmit`, then `git diff --quiet -- tsconfig.json` (restore that one file only if rewritten); `git diff --quiet -- databridge/apps/internal_brain_web/components/presenter` (no presenter edits) | v1 §11, R8 |

Plus one founder step on the stage laptop: open each chapter cold once against the live stack (not a
builder step; Fable risk 2).

**4.3 Docs when Phase 1 lands** (only claims E1–E15 prove; hard rule 8): §7 rows marked P1.

**4.4 Review.** 5-seat review after build: Sonnet, Haiku, Opus, Fable, and Codex only if budget allows
(sequential, on stdin, checked with `databridge/scripts/verify_codex_output.py`). One fix round.

**4.5 Phase 1 user flow.** Type the URL or use the user menu → `/demo` → Start at chapter 1 or Open →
chapter set up, pill shows → Next/Back/Watch recording/Overview/Exit. Booth guidance: use the pill.

**4.6 Phase 1 limits, said honestly.** Until Phase 2: (a) browser Back during a setup may be undone by
the setup's next navigation; (b) browser Back or Forward into chapters 5 to 8 does not re-check the
persona, so a `/dev` page can show "Request not found" or an audit error until the chapter is opened
again from the pill's chip; (c) a cold chapter 5 leaves one extra Compare entry in history, unless M2's
rule was pulled forward; (d) pill Back pushes, so history grows like a paginator; (e) no deep link
(`/demo?step=n`). The runbook says "drive with the pill; the browser buttons are for the presenter's
own window".

---

## 5. Phase 2 (about 3 h, only if Phase 1 is green and reviewed)

**5.1 Work** (all in `demo-story/runner.ts` and `Pill.tsx`; no shared files)

1. **Supersede the setup on history** (H2a): a `popstate` listener; during a setup it bumps the
   generation (the `live()` checkpoint stops it, no further navigation) and calls
   `callPresenter('compare-cancel')`.
2. **Re-check the persona for chapters 5 to 8 only** (H2b): on a `ds` change to 5–8, if `whoami` is not
   Maya, show and announce "Switching to Maya…" (F7), sign in through `serialSession`, then
   **reload**: `location.replace(sameUrl)` so the `/dev` page refetches. Compare chapters use slots
   `b`/`c`; slides need no session. A failed sign-in shows the failed state.
3. **First navigation pushes, later ones replace** (M2) for every setup, including cold chapter 5.
4. **Pill Back agrees with browser Back** (F6): `history.back()` when the previous entry is chapter
   n-1, otherwise push.
5. **Deep link** `/demo?step=n` (L4): signed in → run the setup and **replace** itself; signed out →
   overview with chapter n's Open focused.
6. **Shared `ds` link** (F8): with the gate passing and not dismissed, a `ds` page activates the full
   pill.

**5.2 Exit tests**

| # | Test | Source |
|---|---|---|
| E16 | Back during a setup: open chapter 2, `page.goBack()` before Done; the URL stays where the browser put it, no later push, the ask is cancelled | H2a, Opus §3.1 |
| E17 | Chapter 4 → Next 5 → Back → Forward: the waterfall shows (not "Request not found"), `whoami` is Maya, "Signed in as Maya for this chapter" announced | H2b, F7, Opus §3.2 |
| E18 | Browser Back after pill Next: pill follows the URL; `history.length` unchanged by the persona check | v1 4 |
| E19 | Pill Back then browser Back agree | F6 |
| E20 | Cold chapter 5 adds exactly one history entry; Back from the journey does not land on a `ds=5` Compare page | M2, Haiku 33 |
| E21 | Deep link `/demo?step=6` signed in: lands on `/dev/audit?verify=1&ds=6`, Back goes to the page before `/demo`; signed out: overview with Open focused | v1 6, L4 |
| E22 | Sign-in failure during the re-check (route `/auth/demo-login` to 500): failed state with Try again, never blank | F7 |
| E23 | axe on a chapter reached by Forward while "Switching to…" shows, light and dark, 1280 | F7, M6 |
| E24 | Phase 1 tests E1–E15 still pass | regression |

**5.3 Docs and review.** §7 rows marked P2. 5-seat review (Codex if budget). One fix round.

---

## 6. Phase 3 (about 2 h, optional; only if Phase 2 is green, and before R12's deadline)

**6.1 Work**

1. **`demo_day.ps1`**: re-read the file right before editing (another agent edits it). Add
   `[switch]$App` to `param` (`:27-37`); compute `$StartPath` once after `param` (`/present` or
   `/demo`); `Start-Live` (`:476`) uses it. `Open-DemoBrowser` (`:267-297`): with `-App`, the URL as a
   plain argument (not `--app=`, `:271`) and `--start-maximized` instead of `--start-fullscreen`
   (`:278`), same clean profile; without `-App` byte-for-byte the same command line. `-App` messages:
   the `Ok "... app window ..."` line (`:295`), no F11 hint (`:296`), `Show-Verdict` (`:396`, using
   `$Base`) prints "Demo story: …/demo" and "Keyboard presenter: …/present?present=1" and one line in
   place of `Show-Keys` (`:85-95`), and says the two windows share one browser profile (opening one
   closes the other, L5). Header help (`:6-25`) documents `-App`; fix "four" at `:4` and `:23`. ASCII
   only, PowerShell 5.1 syntax. **Not changed:** `Start-Offline` (`:552`), `Reset-Demo` (`:514`),
   `Test-SignIn`, ports, warm-up.
2. **New root file `Start Walnut Demo (app mode).cmd`**: a copy of `Start Walnut Demo.cmd` that runs
   `demo_day.ps1" start -App %ARGS%`, with its own title and comment. No `--app` mapping in any other
   file. **No last-mode file, no offline app mode, no change to Reset.**
3. **Desktop shortcut**: one row in `New-Shortcuts` (`:570-580`),
   `Start Walnut Demo (app mode)` → the new `.cmd`, a `shell32.dll` icon not in {137, 131, 238, 12}.
   `Make Desktop Shortcuts.cmd` is not edited (it only calls the script).

**6.2 Exit tests**

| # | Test |
|---|---|
| E25 | Parse check: `[System.Management.Automation.Language.Parser]::ParseFile` reports no errors |
| E26 | `start -App -DryRun -NoWarm`: the browser line shows `http://127.0.0.1:3100/demo`, no `--app=`, no `--start-fullscreen` |
| E27 | `start -DryRun -NoWarm` without `-App` matches today's transcript (timestamp header stripped) |
| E28 | **The four existing launchers are byte-identical**: `git diff --quiet -- "Start Walnut Demo.cmd" "Reset Walnut Demo.cmd" "Start Walnut Demo (offline).cmd" "Stop Walnut Demo.cmd"` (and `Make Desktop Shortcuts.cmd`) |
| E29 | `shortcuts -DryRun -Target <scratch folder>` lists the five `.lnk` names |

Never `start`, `stop` or `reset` for real while 3100/8100/8200/8300 are in use; `-DryRun` only.

**6.3 Docs and review.** §7 rows marked P3. 5-seat review (Codex if budget).

---

## 7. Docs to update when it lands

Every line is written only after the phase's tests pass (hard rule 8). Style: plain, numbered,
sourced, as the DataBridge deck.

| File | Change | Phase |
|---|---|---|
| `docs/finals/DECISIONS.md` | D-35 with R3's wording (check D-35 is still free) | P1 |
| `docs/finals/FEATURE_CATALOGUE.md` | §3 Presenter (P1–P4 at 210-247): "P5 Demo story (`/demo`, mouse, hands-on)": where, who, tests E1–E15 | P1, updated P2, P3 |
| `docs/finals/CHANGELOG.md` | One line under "In progress" (`:14`) until committed | each |
| `docs/finals/rubric/README.md` | Row 6 (Demo & Storytelling) evidence: add "Demo story for judges' hands-on time (`/demo`)". Row 8 (UX & Accessibility) evidence: add "mouse-driven Demo story, axe-checked (E13)". **The criteria text "Presenter mode, no mouse" and "Keyboard-only driving" stays**: it is about the 5:00, which is unchanged | P1 |
| `docs/finals/rubric/demo_story/README.md:29` | Add: "The Demo story (`/demo`) is a booth tool and is not used in the 5:00, so the line stays true." | P1 |
| `docs/finals/rubric/demo_story/PRESENTER_KEYS.md` (exists; v1's path was wrong) | One line: the presenter bar also works with the mouse; for hands-on, the Demo story | P1 |
| `docs/finals/demo_day/DEMO_DAY_RUNBOOK.md` | Phase 0: Fable rank 1 line (hand a judge the presenter window; its bar is clickable). P1: short "Demo story (booth)" section: URL, pill, Phase 1 limits (§4.6), never open `/demo` in the stage profile before the 5:00, the `localStorage['ib.presenter']` case, the tour toast after Exit. P3: the sixth button and the shared-profile note; fix "four buttons" (lines 4, 15) | P0, P1, P3 |
| `docs/finals/rubric/demo_story/RUN_SHEET.md:63` | **No change.** Only if the Demo story is ever used on stage, which is not planned; then line 63 must go in the same commit | — |
| RUN_SHEET 0:00 vs `script.ts` | Flag to the RUN_SHEET owner (Haiku row 59); not this plan | — |

---

## 8. Corrected cites (Haiku review)

| Thing | v1 cite | Correct cite (30 Oct) |
|---|---|---|
| private `ctx(gen)` | `controller.ts:57` | `controller.ts:60` |
| `ctx.go` (always push) | `:63-68` | `controller.ts:66-71` (`host?.push` at `:70`); `PresenterRoot.tsx:75` maps it to `router.push` |
| `runStep` export | `:129` | `controller.ts:137` |
| controller `signInMain` | `:75-92` | `controller.ts:73-97` (via `session.ts`) |
| `reveal` loop | "12 lines" | `controller.ts:99-113` |
| generation counter | `:31-45` | `controller.ts:33-46` |
| `refreshHealth` | `:218` | `controller.ts:229`; writes presenter store `health` (`:241`) |
| keys guard (unarmed) | `:264` | `controller.ts:330` |
| failed state | `controller.ts:291` (that is `goBack`) | `PresenterBar.tsx:136-140,177-178,288-290`; `pr.failed` at `presenter.ts:80` |
| window-focus reload | `:369` | `controller.ts:426-430` |
| presenter mouse support | (absent) | bar buttons `PresenterBar.tsx:165-185`; `controller.ts:266-314`; side buttons `:433-451` (taken only while armed, `:444`); browser ◀ `:453-479` |
| session queue | (absent) | `session.ts:17` `serialSession`, `:24` `sessionOwner`, `:29` `markSessionOwned`, `:37` `claimSession` |
| `callPresenter` | `bus.ts` | `bus.ts:40`, signature `(name, arg, timeoutMs, live)` |
| host install / armed return | `PresenterRoot.tsx:74`, `:103` | `:74` (effect), armed early return `:108`, ClipOverlay at `:121,:130`; second mount `app/present/layout.tsx:14`, `useIsOwner` `PresenterRoot.tsx:32-51`; grow needs `demo.on` `:67` |
| ClipOverlay store read | `:16-18` | `ClipOverlay.tsx:15-18` |
| preflight gate | `preflight/page.tsx:57` | `force-dynamic` `:9`; `IB_DEPLOYED` in `allowed()` `:93`; `notFound()` `:99` |
| `defineMessages` | `presenter.ts:355` | `presenter.ts:382` |
| dialog stacking | "z-50" | content `z-50` `dialog.tsx:58,93`; scrim `z-40` `dialog.tsx:19` |
| user menu | `ShellControls.tsx:252-261` | Settings `:251-256`, Sign out `:258-261`, persona switch `:214-250` |
| `MODE_HOME` | — | `lib/prefs.ts:113`; `modeForPath` `:104-111` |
| tour label / toast | — | `lib/i18n/tour.ts:10,17`; offer toast `Tour.tsx:464` |
| `Start-Offline` URL | `$LiveUrl` | `$MockUrl` (`demo_day.ps1:552`); moot, not edited |
| `Show-Verdict` | `:396` | prints `$Base$PresentPath` (`:396`); `Show-Keys` `:85-95` |
| `Test-SignIn` passcode | "being edited" | landed, `demo_day.ps1:118-121` |
| **`PRESENTER_KEYS.md`** | `docs/finals/PRESENTER_KEYS.md` "(if present)" | **`docs/finals/rubric/demo_story/PRESENTER_KEYS.md`** (exists) |
| RUN_SHEET line | `RUN_SHEET.md` | `docs/finals/rubric/demo_story/RUN_SHEET.md:63` |
| decision number | "D-34 next free" | D-34 taken (`DECISIONS.md:41`); **D-35** |
| test helpers | — | `expectAccessible` `tests/e2e/helpers.ts:30`; `testIgnore` `playwright.config.ts:11` |
| live-region example | "Step 3 of 11: Source [2]…" | "Chapter 4 of 11: Source [2] on Rachel's answer" |

---

## 9. Review process per phase

Build → 5-seat review of the built code (Sonnet UX/a11y, Haiku facts and cites, Opus architecture and
security, Fable judge impact; Codex only if budget allows, run sequentially on stdin and checked with
`verify_codex_output.py`) → one fix round → the phase's exit tests green → docs (§7) → the next phase
may start. Parallel builders only on disjoint files (working rule 1); the coordinator lands the three
shared lines in §4.1 row 1.6. No commit until the founder says so.

---

## 10. Risks and rollback

| Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|
| The build eats hallway-test or rehearsal time | Medium | High | Starts only after the hallway notes; phases gated; R12 stop at Sunday 18:00. Builder hours, not founder hours, except the one cold-run check |
| A change breaks the keyboard presenter (the 5:00) | Low | High | No presenter edits (E15 diff check); E12 and the full `presenter.spec.ts`; Phase 3 off by default and the four launchers byte-identical (E28) |
| Two runners at once | Low | Medium | Pill hides and the setup stops when armed (E11); runbook: no `/demo` in the stage profile |
| Session race with the user-menu switch or Sign out | Low | Medium | `serialSession` + `sessionOwner` (M1) |
| A chapter fails cold at the booth | Medium | Medium | Failed state with Watch recording one click away; 12 s timeout; E2; one cold run per chapter on the stage laptop |
| Back/Forward shows a `/dev` page under the wrong persona | Medium (P1) / Low (P2) | Medium | P1: documented limit, use the pill (§4.6). P2: re-check + reload (E17) |
| Rule 4 slip on a card or the pill | Low | High | Content-free strings, no `say`, rendered deny-list scan en and zh (E10) |
| `/demo` reachable on a deployment | Low | High | `force-dynamic` server gate + `gate.ts` test (E9); backend 404 on demo login when off |
| Pill covers content | Medium | Low | Reserved padding, chip on slides, collapse, E14 |
| Name confusion with the tour | Low | Low | "Demo story", own icon (R1) |
| Kit edit collides with the other agent's `demo_day.ps1` work | Low | High | Phase 3 optional, re-read before edit, parse check and dry-run diff (E25–E27) |
| Clips served on deployed builds | Pre-existing | Low | Out of scope (L7), follow-up after 3 Nov |

**Rollback** (any phase, minutes): delete `web/app/(user)/demo/`, `web/components/demo-story/`,
`web/lib/i18n/demoStory.ts`, the e2e spec and config; remove the mount line in `app/layout.tsx`, the
menu item in `ShellControls.tsx` and the `testIgnore` entry. Phase 3: delete the new `.cmd` and the
shortcut row; the `-App` switch is inert without its `.cmd`, or revert `demo_day.ps1` to the
pre-Phase-3 version. The keyboard presenter and the four launcher `.cmd` files were never changed, so
they are the fallback throughout.

---

## 11. Effort summary

| Phase | When | Hours | Gate to start |
|---|---|---|---|
| 0 | Any time | 0.1 (doc line) | none |
| 1 | Saturday 31 Oct, after the hallway test | about 3.5 + review fixes | hallway notes sent |
| 2 | Saturday evening or Sunday | about 3 + review fixes | Phase 1 green and reviewed |
| 3 | Sunday, optional | about 2 + review fixes | Phase 2 green; before Sunday 18:00 |

Total about 8.5 h plus fix rounds, against the reviewers' 10 to 12 h for v1's full scope: the saving is
the cuts in R9 and R10, and every phase can stop without leaving anything half-built.
