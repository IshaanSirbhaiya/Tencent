# App mode (mouse): plan for the "Guided demo"

Status: **plan, for the 5-seat review. Nothing is built yet.** Written 30 Oct 2026 on branch
`final_v1.2`. Build window: Saturday or Sunday, after the reviews (founder instruction, 30 Oct).
It must not slow the other work. Proposed decision: **D-35**, "Guided demo in app mode is the primary
mouse path; the keyboard presenter stays as the fallback".

Every claim about existing code below cites `file:line` as of 30 Oct. `demo_day.ps1` is being edited by another agent (the passcode JSON in `Test-SignIn`), so its line numbers will move by a few lines; the function names are the stable reference. Paths under
`databridge/apps/internal_brain_web/` are shortened to `web/`.

---

## 1. Goal and user

**Goal.** A second way to run the same 11-step Demo Day story (RUN_SHEET §2, HUD 00 to 10) that
works like a normal website: you click, you use the browser's Back and Forward buttons, and you close
things with the mouse. It needs no presenter keys. One double-click starts it.

**Users.**

| Who | When | What they need |
|---|---|---|
| The founder or any teammate driving | Rehearsal, booth walk-ups, judges' follow-up, a demo on someone else's laptop | Click through the story without learning `P`, `Space`, `B`, `R` |
| A judge or visitor at the booth | After the pitch | Open any chapter on its own, go back, watch a recording |
| Dev on stage | Demo Day 5:00 | Unchanged: keeps the keyboard presenter. App mode is his fallback only if the founder chooses it (review question Q1) |

**Not in scope.** No change to the keyboard presenter's behaviour, its keys, `/present?present=1`,
or `Start Walnut Demo.cmd`. No new backend route. No change to the 5:00 script or its words.

---

## 2. What exists today (the facts the plan builds on)

1. **The story is data.** `web/components/presenter/script.ts` holds `STEPS`: 11 steps with `id`,
   `url`, `clip` and an `action(ctx)` (ids at lines 92, 104, 115, 126, 140, 159, 167, 175, 185, 193,
   201). Actions only use the `StepCtx` interface (`script.ts:27-45`): `go`, `call`, `signInMain`,
   `req`, `reveal`.
2. **The runner is the keyboard controller.** `controller.ts:57` builds the private `ctx()`;
   `runStep(i, dir)` is exported (`controller.ts:129`). `ctx.go` is always `router.push`
   (`controller.ts:63-68`), through a `host` that `PresenterRoot` installs on every page
   (`PresenterRoot.tsx:74`), even when presenter mode is not armed.
3. **The grow steps are opt-in only** (`?grow=1`, `script.ts:262-275`) and splice themselves into
   `STEPS` (`setGrowSteps`, just below `growOptIn`). App mode must address steps **by id**, never by index.
4. **Request ids for the journey step are remembered only while armed**:
   `rememberRequest` returns early when `!state.armed` (`store.ts:120-123`). An unarmed run of step 4
   would open the journey list, not Rachel's request.
5. **Compare results are cached per tab** (`web/components/compare/cache.ts`), including each side's
   `res.request_id`, so a later step can find Rachel's request without asking again.
6. **Page handlers are reachable from anywhere** via `callPresenter(name, arg, timeoutMs)`
   (`web/components/presenter/bus.ts`): `compare-run` (`CompareView.tsx:232`),
   `compare-open-citation` (`CompareView.tsx:253`).
7. **Compare ignores unknown query parameters** (`web/components/compare/params.ts:33-49`), and
   rewrites the URL only when the spec differs (`CompareView.tsx:165`). The audit page reads only
   `verify`, `tamper`, `latest` (`AuditConsole.tsx:93-94,129`).
8. **Overlays mounted at the root.** `app/layout.tsx:36-37` mounts `<TourOverlay/>` and
   `<PresenterRoot/>` once; the slides (`app/present/layout.tsx`) have no app shell.
9. **The recording overlay belongs to the presenter**: `ClipOverlay` reads the presenter store
   (`ClipOverlay.tsx:16-18`) and is rendered only while armed (`PresenterRoot.tsx:103`).
10. **Model status** is read by `refreshHealth()` (`controller.ts:218`) from `/ib/status`
    (`models.active`, `internal_brain/databridge_ib/app.py:216-222`).
11. **Demo-only UI gating** uses `useDemoFeatures().server` (`web/components/sim/demo.ts:104`), true
    only when `/ib/status` says `demo_features` (`app.py:227`), i.e. `IB_DEMO_FEATURES=1` and not
    `IB_DEPLOYED` (`config.py:114-115`). The build flag `DEMO_COMPARE` (`web/components/shell/nav.ts:47`)
    hides demo sign-in UI. The preflight page uses a server-side `IB_DEPLOYED` 404
    (`app/present/preflight/page.tsx:57`). Demo sign-in is the only browser sign-in
    (`app.py:232`, the only `SessionStarted`, `app.py:245`).
12. **The kit opens a chromeless app window**: `Open-DemoBrowser` passes `--app=$Url`
    (`demo_day.ps1:271`) and `--start-fullscreen` unless `-Windowed` (`demo_day.ps1:278`). An
    `--app` window has **no address bar and no Back/Forward buttons**, so app mode cannot reuse it.
13. **An existing "guided tour"** (`web/components/tour/Tour.tsx`, header button `TourButton`,
    `Tour.tsx:767`) teaches the UI. App mode is a different thing (the pitch story); the names must
    not collide (CLAUDE.md working rule 6: never shadow).

---

## 3. User flows

```
Start Walnut Demo (app mode).cmd
  -> normal browser window at http://127.0.0.1:3100/demo
  -> signed out: /login?next=/demo (one click on any persona; the passcode if set)
  -> /demo  "Guided demo" overview: 11 chapter cards
       click "Open" on chapter n
  -> chapter n is set up (persona signed in, page opened, question asked)
       floating pill: "Step n of 11"   [< Back] [Next >]   [Overview]  [Watch recording]  [End demo]
       Next  -> chapter n+1 is set up        Back -> chapter n-1 re-opened
       browser Back/Forward -> the previous/next chapter's page, persona re-checked
       Watch recording -> modal with the clip, Close button, Esc
       End demo -> the pill goes away, the plain app (Ask page), nothing else changes
```

1. **Overview.** `/demo` in the app shell. Title "Guided demo", one line ("The Demo Day story, one
   chapter at a time. Synthetic data."), the model status line, and 11 cards in order.
2. **Card.** Number (`1` to `11`, with the HUD number `00` to `10` in small text), title (reuse the
   translated `step.<id>` keys, `presenter.ts` en and zh), one content-free line on what it proves
   (§8), the persona it signs in, and buttons: **Open** (primary), **Watch recording** (only when
   the step has a clip).
3. **Inside a chapter.** The real page, already set up. The pill shows the step, the title, and
   the buttons. While the setup runs the pill says "Setting up…" with a spinner and the buttons
   are disabled except **Overview** and **End demo**. If it fails, the pill says "This step did not
   finish" with **Try again**, **Skip** and **Watch recording** (the same choices as the keyboard
   presenter's failed state, `controller.ts:291`).
4. **The WorkBuddy chapter (HUD 07, off-app).** The pill expands to show the existing
   `WorkBuddyCard` content (question and Copy button; `WorkBuddyCard.tsx` imports only messages and
   `Q_WORKBUDDY`, so it is reusable unchanged) and a **Show newest record** button that opens
   `/dev/audit?latest=1` (the mouse version of the presenter's window-focus reload,
   `controller.ts:369`).
5. **End demo.** Clears the guided state, opens the mode's home (`MODE_HOME`), keeps the current
   session. It does not sign out (sign-out stays in the user menu).

---

## 4. Routes, URLs and history

**Design rule:** every chapter has a real URL, so the browser's history works like a website, and
the setup never adds an extra history entry.

| URL | What it is |
|---|---|
| `/demo` | Overview (in the app shell; signed-out goes to `/login?next=/demo`) |
| `/demo?step=n` (n = 1..11) | Deep link to a chapter. Runs the setup, then **replaces** itself with the chapter's page, so Back never lands on a page that re-runs a setup |
| `<step page>?…&gd=n` | The chapter's real page with a marker, e.g. `/compare?a=amy.am&b=mlro.tan&q=…&gd=2`, `/dev/audit?verify=1&gd=6`, `/present/proof?gd=9` |

**Why a `gd=n` marker on the real page.** Steps share pages: HUD 01 and 03 are the same Compare
URL; HUD 05, 06 and 07 are all `/dev/audit`. Only a marker tells the pill which chapter a history
entry is. Compare and Audit ignore unknown parameters (§2 item 7). The build checks every step page
(`/present`, `/compare`, `/dev/journey/<req>`, `/dev/audit`, `/present/proof|business|close`).

**History behaviour.**

| Action | History |
|---|---|
| Open from overview | push the chapter page |
| Next / Back in the pill | push the next / previous chapter page (like clicking a link) |
| `/demo?step=n` deep link | `replace` with the chapter page |
| Browser Back / Forward | Next.js restores the page. The pill reads `gd` from the URL, then only **re-checks the persona** (signs in the chapter's persona if the main session differs). It never re-runs the action and never navigates, so no history entry is added |
| Overview | push `/demo` |
| End demo | push the home page without `gd` |

**What browser Back shows.** Compare restores the earlier answers from the per-tab cache, without
asking again (`cache.ts`; the same mechanism the presenter's `←` uses). The audit pages re-open with
their `verify=1` / `tamper=1` parameters, as `←` does today (the tamper only ever touches a copy).
A full page reload of a Compare chapter asks again (the cache is in memory only).

**A page with no `gd`** (the user clicked elsewhere in the app): the pill collapses to a small
"Guided demo: back to step n" chip, so the user can always return. **End demo** removes it.

---

## 5. How each step is set up

App mode runs the **same `STEPS[i].action`** from `script.ts`, looked up by id. It does not copy
any step logic. The only new piece is a small `StepCtx` adapter owned by app mode
(`web/components/guided/runner.ts`, about 60 lines):

| `StepCtx` member | App-mode implementation | Why not the controller's |
|---|---|---|
| `go(url)` | `router.push(url + gd)`; `router.replace` for a deep link | The controller's `go` is always `push` with no marker (`controller.ts:63-68`) |
| `call(name, arg, t)` | `callPresenter` from `bus.ts` (imported, unchanged) | Same function |
| `signInMain(p)` | `api.whoami()`, `api.switchTo(p)` if different, then the shell's `refresh()` (the same three calls as `controller.ts:75-92`), serialised by one promise chain | `ctx()` is private to the controller |
| `req(p)` | presenter store `reqs[p]`, else the Compare cache (`cachedResult(SPEC_PEOPLE).right.res.request_id`) | `rememberRequest` is a no-op while unarmed (`store.ts:122`) |
| `reveal(sel, t)` | the same wait-and-scroll loop (12 lines), honouring reduced motion | private in the controller |

A generation counter, as in `controller.ts:31-45`, makes a superseded setup (the user clicked Next
twice) stop at its next checkpoint without navigating.

**Alternative (review question Q3):** export `ctx` from `controller.ts` with an options object
(`{ push | replace, marker }`). Fewer lines, but it edits a presenter file that another agent is
changing now and that the 5:00 depends on. The plan prefers the adapter.

**Per-step table.** "Signs in" is the main session app mode guarantees before the action runs;
the compare columns use their own slots (`b`, `c`) and do not depend on it.

| # (HUD) | Step id | Signs in (main) | Page | Question / action (from `script.ts`) | Clip |
|---|---|---|---|---|---|
| 1 (00) | `title` | Rachel `mlro.tan` | `/present` (title slide) | none | A |
| 2 (01) | `compare-people` | Rachel (kept) | `/compare`, Amy left, Rachel right | `Q_VELQUARRY` for both; `compare-run` | A |
| 3 (02) | `compare-person` | Rachel (kept) | `/compare?mode=person`, Ken | Ken: `Q_VELQUARRY` vs `Q_BAYFRONT`; `compare-run` | A |
| 4 (03) | `citation` | Rachel | Compare, Amy and Rachel | `compare-open-citation` slot `c`, n 2 (asks first if the answers are not on screen) | A |
| 5 (04) | `journey` | Maya `maya.compliance` | `/dev/journey/<Rachel's req>` | reveal the waterfall | B |
| 6 (05) | `verify` | Maya | `/dev/audit?verify=1` | Verify runs | B |
| 7 (06) | `tamper` | Maya | `/dev/audit?tamper=1` | sandbox tamper, then Verify on the copy | B |
| 8 (07) | `workbuddy` | Maya | `/dev/audit` | off-app card in the pill; Show newest record | C |
| 9 (08) | `proof` | (kept) | `/present/proof` | none | none |
| 10 (09) | `business` | (kept) | `/present/business` | none | none |
| 11 (10) | `close` | (kept) | `/present/close` | none | none |

**Opening a chapter on its own** (not in order). The presenter can rely on earlier steps; app mode
cannot. So each chapter declares a precondition:

- **Persona.** The table's "Signs in" column, as data in the guided module (`persona` per step id).
  The actions of steps 1, 4 and 5 sign in themselves; for steps 2, 3 and 6 to 8, app mode signs in
  the persona first. An e2e test opens every chapter cold and checks `whoami`, so this table cannot
  drift from the script silently.
- **Journey (5).** Needs Rachel's request id. If neither the store nor the Compare cache has it,
  app mode first runs step 2's action (one Compare run, about 3 s live), then step 5. The pill says
  "Setting up: asking the question first".
- **Citation (4).** Already self-sufficient (`CompareView.tsx:256-272` asks if needed).
- **The grow steps** are never part of app mode (they are a Q&A walk-up, RUN_SHEET §2).

**Model status line.** Call `refreshHealth()` (exported, `controller.ts:218`) and read
`usePresenter().health.model`. Text: `live` gives "Model: Tencent Hunyuan (live)"; anything else
gives "Model: not live. Answers are quoted from the sources, with the same checks." Default is the
honest one; nothing in app mode says "Hy3 wrote" (RUN_SHEET §1, tag [HY3]).

---

## 6. The floating step pill

**Placement.** `fixed`, bottom right, `z-40` (under dialogs and sheets at `z-50`, over the page).
Mounted once in `app/layout.tsx` next to `<TourOverlay/>`, so it shows on the slides (no shell)
as well as in the shell. Rendered only while the guided state is active (sessionStorage
`ib.guided`, this tab only, never sent to the server).

**Layout.**

| Width | Form |
|---|---|
| >= 768 px | One card, 22 rem wide, about 56 px tall: "Step 3 of 11", title (one line, truncated with a tooltip), `< Back`, `Next >`, a `More` menu (Overview, Watch recording, End demo), Collapse |
| < 768 px (390) | A full-width bar at the bottom, about 56 px: "3/11", `<`, `>`, `More`. Title on its own row only when expanded |
| Collapsed (any width) | A 40 px chip "Guided demo 3/11" with an Expand button |

**Never covering the content (1280 and 1920 px).**
1. While the pill is shown, `<html>` gets `scroll-padding-bottom` and `<main>` gets
   `padding-bottom` equal to the pill height (a CSS variable `--guided-h`, set from a
   ResizeObserver), so the end of every page scrolls above it, and focused elements are never hidden
   under it (WCAG 2.2 SC 2.4.11 Focus Not Obscured).
2. **Move** instead of free dragging: a "Move to the left" / "Move to the right" button swaps
   corners (remembered per tab). WCAG 2.2 SC 2.5.7 needs a single-pointer alternative to dragging;
   a corner swap is that alternative, and simpler. Free drag is review question Q6.
3. **Collapse** shrinks it to the chip.
4. A screenshot check at 1280x860, 1920x1080 and 390x844 on each chapter page confirms it covers
   no primary control (Compare's Ask button, the citation chips, Verify, the slide text).

**Accessibility (WCAG 2.2 AA).**

| Need | How |
|---|---|
| Landmark | `<nav aria-label="Guided demo">` (axe `region` rule) |
| Status | A polite live region: "Step 3 of 11: Source [2] on Rachel's answer", "Setting up…", "Done", "Did not finish" |
| Focus | Pill buttons keep the focus ring from `components/ui`. After Next/Back, focus moves to `<main>` (as the skip link does), not into the pill. The modal returns focus to its opener |
| Targets | All buttons at least 36x36 px (SC 2.5.8 needs 24) |
| Keys | No single-key shortcuts (SC 2.1.4 not triggered). Tab order: page first, then the pill (it comes after `<main>` in the DOM). The keyboard presenter's keys stay off unless armed (`controller.ts:264`) |
| Reduced motion | No slide-in or pulse when `prefers-reduced-motion` or `data-motion="reduce"`; `reveal` scrolls with `behavior: 'auto'` |
| Contrast | Design-system tokens only (`bg-surface`, `text-fg`, `border-line`), checked by axe in light and dark |
| 简体中文 | All new strings in `web/lib/i18n/guided.ts` (en and zh, `defineMessages` as in `presenter.ts:355`). Step titles reuse `step.<id>`. Developer pages stay English and say so (D-12) |
| Text size | Works at Text size 130 % and 200 % browser zoom (wraps to two rows; no horizontal scroll at 390) |

**Recording modal.** A normal centred dialog from `components/ui/dialog.tsx` (Radix): title
"Recording", the existing badge text "Recording · not live" (`pr.clip.badge`), a `<video controls
muted>` from `/clips/<file>`, the "Clip not recorded yet" fallback (`pr.clip.missing`), a visible
**Close** button, `Esc`, and a click outside. Not full screen. It reuses the clip file names from
`CLIPS` / `STEPS[i].clip` (`script.ts:21-25`). It does not reuse `ClipOverlay`, which is bound to
the presenter store.

---

## 7. Gating: where the link shows

**Rule.** App mode exists only where demo sign-in exists, and never on a deployment.

| Layer | Check |
|---|---|
| `/demo` page (server) | `notFound()` when `process.env.IB_DEPLOYED === '1'` (the preflight pattern, `preflight/page.tsx:57`) or `DEMO_COMPARE` is off (`nav.ts:47`) |
| `/demo` page (client) | Shows the chapters only when `useDemoFeatures().server === true` (`demo.ts:104`). Otherwise the same "not found" card as an unknown page |
| User-menu link | "Guided demo" item in `PersonaMenu`, between Settings and Sign out (`ShellControls.tsx:252-261`), shown only when `DEMO_COMPARE && demo.server === true` |
| Command palette | Same condition (optional, Q7) |
| Pill | Renders only when the guided state is active and the same condition holds |
| Signed in | Every browser session comes from demo sign-in (`app.py:232,245`), so a signed-in viewer on a non-deployed server with demo features on implies demo login on |

`demo.server` (not `demo.on`) is used on purpose: `demo.on` also needs the Settings toggle or an
armed presenter (`demo.ts:104`), and the app-mode browser starts with a clean profile, so the toggle
would always be off. The server-side half (`demo_features_on` = `IB_DEMO_FEATURES=1` and not
`IB_DEPLOYED`, `config.py:114-115`) is what keeps it off a deployment. Mock mode answers
`demo_features: true` (`web/lib/mock/extra/graph.ts:615`), so the e2e run sees the link.

---

## 8. Hard rule 4 (and 1, 2, 7): risks and how the plan keeps them

| Risk | Mitigation |
|---|---|
| The overview reveals restricted content to whoever is signed in (for example Amy reading "Rachel gets the suspicious-transaction report" on a card) | Card lines are **content-free** and describe the mechanism only: "Same question, two people: each answer cites only what that person can open." No company facts, no case words (STR, MLRO case), no answer text, no counts. A unit test scans `guided.ts` en and zh for a deny list (`STR`, `suspicious`, `可疑`, `报告`, case ids) |
| The hub fetches answers to preview them | It fetches nothing except `/ib/status` (model dot). No `/ib/ask` from `/demo` |
| "Open" signs in Rachel or Maya for anyone | Demo sign-in already lets any viewer pick any persona from the user menu (`ShellControls.tsx:214-250`); app mode adds no capability. Off where demo sign-in is off, and on any deployment (§7). Identity still comes only from the signed token (rule 2): app mode calls the same `/auth/demo-login` |
| Recordings show Rachel's answer to an Amy viewer | Clips are static files already served to anyone at `/clips/*` (the presenter's `B`). They are synthetic. Still, the button shows the label "Recording · not live" and the card names the personas shown. Q5 asks whether to hide clips while a non-privileged persona is signed in |
| The `gd` marker or guided state leaks identity | It holds a step number only; sessionStorage, this tab; never sent to the server |
| Step logic forked from the script | The actions are imported from `script.ts`; only the context adapter is new (§5) |

Rule 1 (filter before the model), rule 3 (write-ahead audit) and rule 5 (pointers) are untouched:
app mode calls the same pages and the same API.

---

## 9. Launcher: `-App` in `demo_day.ps1`

**One switch, everything else identical.** Prerequisites, ports, `demo_up.sh`, health waits,
preflight and warm-up (`Start-Live`, `demo_day.ps1:406-479`) do not change.

| Change | Where | What |
|---|---|---|
| New switch | `param` block (`demo_day.ps1:27-37`) | `[switch]$App` |
| Start path | next to `$PresentPath` (`demo_day.ps1:53`) | `$AppPath = '/demo'` |
| Browser URL | `Start-Live` (`:476`) and `Start-Offline` (`:552`) | `"$LiveUrl$(if ($App) { $AppPath } else { $PresentPath })"` |
| Normal window | `Open-DemoBrowser` (`:267-297`) | With `-App`: the URL as a plain argument instead of `--app=$Url` (so the window has an address bar and Back/Forward), `--start-maximized` instead of `--start-fullscreen`, same clean profile and other flags. Without `-App`: byte-for-byte unchanged |
| Messages | `Show-Verdict` (`:396`), the "press F11" hint (`:296`) | With `-App`: "Guided demo: http://127.0.0.1:3100/demo"; "Keyboard fallback: .../present?present=1"; no F11 hint; the key legend replaced by one line ("Click through; Back/Forward work; End demo returns to the app") |
| Help text | header (`:6-25`) | Document `-App` |

Rules for the edit: ASCII only, Windows PowerShell 5.1 (no `?:`, no `??`), parse check with
`[System.Management.Automation.Language.Parser]::ParseFile`. **Re-read the file right before the
edit**: another agent is changing `Test-SignIn` (`:114-127`, the passcode JSON). The `-App` edit
touches none of those lines.

**New root file `Start Walnut Demo (app mode).cmd`.** A copy of `Start Walnut Demo.cmd` with
`... demo_day.ps1" start -App %ARGS%` and its own title and comment. Also map `--app` to `-App` in
all five `.cmd` files' argument translation, so `Start Walnut Demo (offline).cmd --app` gives an
offline app mode (Q4).

**Reset in app mode.** `Reset Walnut Demo.cmd` reopens the presenter today (`Reset-Demo` calls
`Start-Live`, `:514`). Proposal: `start` writes `var\demo-day\last-mode.txt` (`app` or
`presenter`); `reset` reopens the same mode unless `-App` / `-Presenter` is given. Q4.

**Test without touching the shared stack.** Only `-DryRun`:

```powershell
powershell -NoProfile -File databridge\scripts\demo_day.ps1 start -App -DryRun -NoWarm
```

Expected: the `[DRY RUN] would ...` browser line shows `http://127.0.0.1:3100/demo` with no `--app=`
and no `--start-fullscreen`; the same command without `-App` prints exactly what it prints today
(diff the two transcripts). Never `start`, `stop` or `reset` for real while 3100/8100/8200/8300 are
in use.

---

## 10. Desktop shortcut

Add one row to `New-Shortcuts` (`demo_day.ps1:570-580`):
`@{ n = 'Start Walnut Demo (app mode)'; f = 'Start Walnut Demo (app mode).cmd'; i = <icon> }`, with
a `shell32.dll` icon distinct from the four in use (137, 131, 238, 12), picked by eye. Update the
"four buttons" text (`demo_day.ps1:23`, `Make Desktop Shortcuts.cmd:2`, RUNBOOK §1 "five buttons").
`-DryRun -Target <scratch folder>` shows the five `.lnk` names.

---

## 11. Tests

**E2E (mock mode), `web/tests/e2e/guided-demo.spec.ts`** with its own config
`guided-demo.config.ts` on port **3132**, `NEXT_DIST_DIR=.next-guided`, `NEXT_PUBLIC_IB_MOCK=1`,
so it never fights the main config (3101) or the a11y config (3123). `testMatch` limits it to the
one spec; the main config adds it to `testIgnore`.

| # | Test |
|---|---|
| 1 | The user menu shows "Guided demo" (mock: demo features on); clicking it opens `/demo` with 11 cards in `script.ts` order (titles from `step.<id>`) |
| 2 | Open chapter 2 (Amy vs Rachel): URL is `/compare?...&gd=2`, both answers done, pill says "Step 2 of 11", `whoami` is Rachel |
| 3 | Next: chapter 3, Ken; Back: chapter 2 again, answers restored from cache (no new ask: count `/api/ib/ask` requests) |
| 4 | Browser Back (`page.goBack()`): pill follows the URL; no extra history entry (check `history.length`) |
| 5 | Every chapter opened cold from the overview: the page and the main persona match §5's table |
| 6 | Deep link `/demo?step=6`: lands on `/dev/audit?verify=1&gd=6`, Back goes to the page before, not to the deep link |
| 7 | Watch recording: modal open, video present (or the "not recorded" fallback), Close button closes, Esc closes, focus returns to the opener |
| 8 | End demo: pill gone, URL is the home page, guided state cleared |
| 9 | The keyboard presenter is unchanged: `?present=1` still lands on `/present`, `P` starts at 00 (a smoke copy of two `presenter.spec.ts` checks, run under this config) |
| 10 | Gating: with `/ib/status` routed to `demo_features: false`, no menu link and `/demo` shows not found |
| 11 | Rule 4: signed in as Amy, `/demo` text contains none of the deny-list words; no `/ib/ask` request is made by `/demo` |
| 12 | axe (`expectAccessible` from `helpers.ts`) on `/demo` and on a chapter with the pill open and collapsed: light and dark, 1280x860 and 390x844, English and 简体中文 |
| 13 | Layout: at 1280 and 1920, the pill's box does not intersect the Compare Ask button, the citation chips, the Verify button or the slide headline; at 390 there is no horizontal scroll |

Also: `npx tsc --noEmit` (confirm `tsconfig.json` is not rewritten; `git diff --quiet tsconfig.json`,
restore with `git restore` on that one file only if it is), and the existing `presenter.spec.ts`
under the main config once, to prove no regression.

**Kit:** the parse check and the two dry runs in §9.

---

## 12. Docs

| File | Change |
|---|---|
| `docs/finals/demo_day/DEMO_DAY_RUNBOOK.md` | §1: a sixth button row. New short section "App mode (mouse)": which launcher, what the pill does, that the keyboard presenter (`Start Walnut Demo`) is the fallback, and that the 5:00 stays on the keyboard unless the founder decides otherwise |
| `docs/finals/rubric/demo_story/RUN_SHEET.md` | §0b: one paragraph "App mode (mouse)", same content, plus the chapter-number to HUD-number mapping |
| `docs/finals/FEATURE_CATALOGUE.md` | §3 Presenter: one line "P5 Guided demo (app mode, mouse)" with where, who, tests |
| `docs/finals/DECISIONS.md` | D-35 (D-34 is the next free number today if nothing else lands; check before writing) |
| `docs/finals/CHANGELOG.md` | One "In progress" line until committed |
| `docs/finals/PRESENTER_KEYS.md` (if present) | One line pointing to app mode |

Style: plain, numbered, sourced, as the DataBridge deck.

---

## 13. Risks and rollback

| Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|
| A change breaks the keyboard presenter | Low | High (the 5:00 path) | No edits to `components/presenter/*` (imports only); `presenter.spec.ts` re-run; the `-App` branch is the only kit change and is off by default |
| Two runners at once (guided setup and presenter keys) | Low | Medium | Presenter keys are off unless armed; the pill hides itself while the presenter is armed (`usePresenter().armed`) |
| `gd` marker breaks a page | Low | Medium | §4 check of every step page; e2e test 5 |
| Back/Forward shows a page signed in as the wrong person | Medium | Medium | Persona re-check on every `gd` change (§4); test 4 |
| Setup slower than a click (live Compare about 3 s) | Medium | Low | "Setting up…" state; buttons disabled; Next twice supersedes cleanly |
| Pill overlaps content | Medium | Low | Reserved padding, corner swap, collapse, test 13 |
| Clean browser profile signs everyone out | Certain | Low | `/login?next=/demo` brings the user straight back |
| Shared presenter store touched (`setPresenter` for `reqs` would arm nothing, but writes the tab's presenter state) | Low | Low | The adapter keeps its own request memory and reads the Compare cache; it never writes the presenter store |
| Naming confusion with the guided tour | Medium | Low | Name "Guided demo" vs "Tour"; different icons; Q2 |

**Rollback.** Delete the `/demo` route, the guided module, the pill mount line in `app/layout.tsx`,
the menu item, the new `.cmd` and the shortcut row; the `-App` switch is inert without its `.cmd`.
The keyboard presenter and `Start Walnut Demo.cmd` were never changed, so they are the fallback
throughout.

---

## 14. Effort and build order

About **6 to 8 hours** for one builder, plus review fixes.

| # | Work | Files | Est. |
|---|---|---|---|
| 1 | i18n strings (en, zh) and the content-free card lines; the deny-list unit check | `web/lib/i18n/guided.ts` | 0.5 h |
| 2 | Guided store (sessionStorage `ib.guided`) and the `StepCtx` adapter with generations, persona table, journey precondition | `web/components/guided/store.ts`, `runner.ts`, `chapters.ts` | 1.5 h |
| 3 | `/demo` overview page, server gate, cards, model line, deep link handling | `web/app/demo/page.tsx`, `web/components/guided/Overview.tsx` | 1 h |
| 4 | The pill (layout, collapse, corner swap, live region, reserved padding), WorkBuddy panel, recording modal; mount in `app/layout.tsx` | `web/components/guided/Pill.tsx`, `RecordingDialog.tsx` | 1.5 h |
| 5 | User-menu link (and palette, if Q7 says yes) | `web/components/shell/ShellControls.tsx` (and `CommandPalette.tsx`) | 0.25 h |
| 6 | E2E spec and config; `tsc`; presenter regression run | `web/tests/e2e/guided-demo.spec.ts`, `guided-demo.config.ts`, `playwright.config.ts` (testIgnore) | 1.5 h |
| 7 | Kit: `-App`, the new `.cmd`, the `--app` mapping, the shortcut row, last-mode for Reset; parse check and dry runs | `databridge/scripts/demo_day.ps1`, root `.cmd` files | 0.75 h |
| 8 | Docs and D-35 | §12 | 0.5 h |

Order: 1, 2, 3 (a usable hub with Open), then 4, 5, 6, then 7 and 8. Each step leaves the app
working. Builders on disjoint files can run 3/4 and 7 in parallel (working rule 1); `app/layout.tsx`
and `ShellControls.tsx` are shell-owned, so the coordinator lands those two lines.

---

## 15. Review questions (for the 5-seat review)

1. **Stage role.** Is app mode only a fallback and a booth tool, or should Dev drive the 5:00 with
   the mouse? (D-35 as drafted makes app mode the primary *mouse* path and leaves the keyboard path
   as it is.) The other agent is adding mouse buttons to the presenter bar today; do we need both?
2. **Name.** "Guided demo" next to the existing "Tour" button: clear enough, or call it "Demo story"?
3. **Adapter or export.** Keep the app-mode `StepCtx` adapter (§5, no presenter edits), or export a
   parameterised `ctx` from `controller.ts` after Demo Day?
4. **Kit.** Should `Reset` remember the last mode (§9)? Is an offline app mode (`--app` on the
   offline button) wanted, or is one app-mode button enough?
5. **Recordings and rule 4.** Hide "Watch recording" for the chapters whose clip shows Rachel's
   answer when a non-privileged persona is signed in, or is "static, synthetic, already public at
   `/clips/*`" enough?
6. **Dragging.** Corner swap and collapse, or true free dragging (needs a single-pointer alternative
   anyway, SC 2.5.7)?
7. **Entry points.** User menu only, or also the command palette and a card on Settings?
8. **Numbering.** "Step 1 of 11" in the pill with HUD `00` in small text, or show HUD numbers only
   (matching the run sheet)?
9. **Stage theme.** Offer a "Stage view" toggle in the pill (calls the exported `setStage`), or keep
   app mode in the normal theme?
10. **Gating.** Is `DEMO_COMPARE && /ib/status demo_features` the right proxy for "demo login on and
    not deployed", or should `/ib/status` expose `demo_login` explicitly (a backend change)?
11. **End demo.** Keep the session (as planned) or sign out too?
12. **Cold chapter 5.** Is running the Compare first (about 3 s live) acceptable, or should the
    journey chapter open the journey list instead when no request exists?
