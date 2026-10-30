# App mode plan: consistency and claims review (Haiku seat)

Reviewed 30 Oct 2026 against the working tree on branch `final_v1.2`. Read-only: nothing was run, built or
committed. Every row was checked by reading the cited file. `web/` means
`databridge/apps/internal_brain_web/`.

**Verdict.** The plan's facts are mostly right. The code it relies on works the way the plan says. There
are 14 corrections:

- 7 line numbers have drifted, because the presenter files changed after the plan was written
  (`controller.ts` gained the mouse controls). The plan cites the drifted `controller.ts` lines at
  `:57`, `:63-68`, `:129`, `:218`, `:264`, `:291` and `:369` and needs those cites updated; the
  `PresenterRoot`, preflight and `presenter.ts` drifts are separate rows below.
- 2 paths are wrong: `PRESENTER_KEYS.md`, and `$LiveUrl` in offline mode.
- 1 decision note is stale: D-34 is now taken.
- 4 are design gaps where the plan says something the code does not support. Rows 25, 27, 33 and 44
  below.

The step table matches `script.ts` exactly. It matches RUN_SHEET §2 except one row, and that mismatch is
an upstream run-sheet inconsistency, not a fault in the plan (row 59).

## 1. Facts about the code (PLAN §2)

| # | Claim in the plan | Verified | Correction |
|---|---|---|---|
| 1 | `script.ts` `STEPS` has 11 steps; ids at 92, 104, 115, 126, 140, 159, 167, 175, 185, 193, 201 | Y | Exact. The grow ids sit at 222, 234 and 244, outside `STEPS` |
| 2 | `StepCtx` is at `script.ts:27-45` with `go`, `call`, `signInMain`, `req`, `reveal` | Y | Exact |
| 3 | `controller.ts:57` builds the private `ctx()` | N (drift) | `function ctx(gen)` is at `controller.ts:60` |
| 4 | `runStep(i, dir)` is exported at `controller.ts:129` | N (drift) | It is exported at `controller.ts:137` |
| 5 | `ctx.go` is always `router.push`, at `controller.ts:63-68` | Y, but the lines drifted | It is at `controller.ts:66-71` (`host?.push(url)` at :70). `PresenterRoot.tsx:75` maps it to `router.push` |
| 6 | `PresenterRoot` installs the host on every page, even when unarmed (`PresenterRoot.tsx:74`) | Y | `setHost` at :74 runs in an effect, before the `!owner \|\| !s.armed` early return at :108 |
| 7 | The grow steps are opt-in through `?grow=1` (`script.ts:262-275`), and `setGrowSteps` splices them in "just below" | Y | `GROW_KEY` and `growOptIn` are at 262-274 and `setGrowSteps` at 279. They also need `demo.on` (`PresenterRoot.tsx:67`). The plan is right to address steps by id |
| 8 | `rememberRequest` returns early when `!state.armed` (`store.ts:120-123`) | Y | Exact (the check is at :122) |
| 9 | §2 item 4: "An unarmed run of step 4 would open the journey list" | Y | "Step 4" here is the HUD number (04, journey). Elsewhere the plan says "chapter 5" for the same step. Pick one numbering (see row 61) |
| 10 | The Compare cache is per tab and holds each side's `res.request_id` (`cache.ts`) | Y | The cache lives in module memory (`cache.ts:17`), and `DoneSide.res` is an `AskResponse`. `cachedResult(SPEC_PEOPLE).right` is Rachel (`mlro.tan`). A full reload clears it, as §4 says |
| 11 | `callPresenter(name, arg, timeoutMs)` is in `bus.ts`; `compare-run` is at `CompareView.tsx:232`; `compare-open-citation` at :253 | Y | The full signature is `callPresenter(name, arg, timeoutMs, live)` (`bus.ts:40`). The adapter should pass its own liveness function as the 4th argument, as `controller.ts:76` does. Otherwise a superseded call keeps polling until it times out |
| 12 | Compare ignores unknown query parameters (`params.ts:33-49`) | Y | `parseSpec` reads only mode, p/person, q1, q2, q, a/left and b/right |
| 13 | Compare rewrites the URL only when the spec differs (`CompareView.tsx:165`) | Y | When it does rewrite, it uses `specUrl(spec)`, which drops `gd`, so the pill falls back to the chip. That is acceptable, but worth a line in §4 |
| 14 | The audit page reads only `verify`, `tamper` and `latest` (`AuditConsole.tsx:93-94,129`) | Y | Exact |
| 15 | `app/layout.tsx:36-37` mounts `<TourOverlay/>` and `<PresenterRoot/>` **once**, and the slides have no app shell | Partly | Lines 36-37 are right. But `app/present/layout.tsx:14` mounts a second `<PresenterRoot/>`, and `useIsOwner` (`PresenterRoot.tsx:32-51`) keeps only one of them rendering. Drop the word "once", and say whether the pill needs the same one-renderer guard (it does not if it is only mounted at the root) |
| 16 | `ClipOverlay` reads the presenter store (`ClipOverlay.tsx:16-18`) and renders only while armed (`PresenterRoot.tsx:103`) | Y, but one line drifted | The store read is at `ClipOverlay.tsx:15-18`. The armed check is `PresenterRoot.tsx:108`, and ClipOverlay renders at :121 and :130 |
| 17 | `refreshHealth()` is exported (`controller.ts:218`) and reads `models.active` (`app.py:216-222`) | Y, but the line drifted | It is at `controller.ts:229`, and `app.py:219` is `"active": chain is not None`. **Note:** `refreshHealth` writes `health` into the presenter store (`setPresenter`). That contradicts §13 row 8 ("it never writes the presenter store"). The write is harmless, but the §13 sentence should say "except health" |
| 18 | `useDemoFeatures().server` (`demo.ts:104`); `demo_features` (`app.py:227`); `config.py:114-115` | Y | All exact |
| 19 | `DEMO_COMPARE` is at `nav.ts:47` | Y | Exact. It is a `NEXT_PUBLIC_` build-time value |
| 20 | The preflight page uses a server-side `IB_DEPLOYED` 404 (`preflight/page.tsx:57`) | N (drift) | The `IB_DEPLOYED` check is at `:93` (inside `allowed()`, which also requires a loopback Host and a valid session), and `notFound()` is at `:99`. Also copy `export const dynamic = 'force-dynamic'` (`:9`): without it, a server gate on `process.env` can be decided at build time |
| 21 | Demo sign-in is the only browser sign-in (`app.py:232`), and the only `SessionStarted` is at `app.py:245` | Y | Exact |
| 22 | `Open-DemoBrowser` passes `--app=$Url` (`demo_day.ps1:271`) and `--start-fullscreen` (`:278`) | Y | Exact. The `Ok "... app window ..."` message at `:295` also needs the `-App` wording |
| 23 | The tour: `Tour.tsx`, with `TourButton` at `Tour.tsx:767` | Y | Exact. **The naming collision is sharper than the plan says.** The tour's own UI label is already "Guided tour" (`lib/i18n/tour.ts:10,17`, `offer.label` and `card.label`), so "Guided demo" sits one word away from it. See the Fable review, Q2 |

## 2. User flows, setup and the pill (PLAN §3, §5, §6)

| # | Claim in the plan | Verified | Correction |
|---|---|---|---|
| 24 | The failed state matches the presenter's (`controller.ts:291`) | N | `controller.ts:291` is `goBack`. The failed UI is in `PresenterBar.tsx:136-140,177-178,288-290`, with the text `pr.failed` at `presenter.ts:80`. That text names keys ("Space tries again, → skips, B…"), so app mode needs its own strings in `guided.ts` |
| 25 | `WorkBuddyCard.tsx` imports only messages and `Q_WORKBUDDY`, so it can be reused unchanged | Partly | The imports are right (`WorkBuddyCard.tsx:4-9`). But the component positions itself as a fixed, bottom-centred `z-40` overlay (`:15`), not as content that can go inside a pill. "Expanding the pill to show it" therefore means either a wrapper that overrides the position, or rendering the card where it already sits. It cannot be used unchanged inside the pill |
| 26 | The window-focus reload is at `controller.ts:369` | N (drift) | It is at `controller.ts:426-430` (`host?.push('/dev/audit?latest=1')`) |
| 27 | Adapter `signInMain`: "the same three calls as `controller.ts:75-92`, serialised by one promise chain" | N (design gap) | The controller's `signInMain` (`:73-97`) goes through `serialSession`, `sessionOwner` and `markSessionOwned` from `components/presenter/session.ts:17-29`. That queue is shared with the shell's Sign out and persona switch (round-4 Opus #1). If the adapter uses a private promise chain instead, it can interleave with a persona switch from the user menu. The adapter should import `serialSession` (an import, not an edit). It also needs to keep the `SessionTaken` rule (if the shell changed the persona, the shell wins), and the `void refreshHealth()` after sign-in |
| 28 | Adapter `req(p)`: presenter store `reqs[p]`, else the Compare cache | Y | It works. Note that `forgetRequests` runs at the start of every Compare run (`CompareView.tsx:162`), so a stale stored id cannot outlive a new run |
| 29 | `reveal`: "the same wait-and-scroll loop (12 lines)" | Y | `controller.ts:99-113` |
| 30 | A generation counter, as in `controller.ts:31-45` | Y | The comment and `let generation` (`:33-46`) are within a line or two |
| 31 | Persona column: the actions of steps 1, 4 and 5 sign in themselves | Y | `title`, `citation` and `journey` call `signInMain` (`script.ts:98,133,145`). `verify`, `tamper` and `workbuddy` do not, so app mode is right to sign in Maya first (D-05: `/dev/audit` needs `compliance_officer`) |
| 32 | Citation (4) is self-sufficient (`CompareView.tsx:256-272`) | Y | `:253-275`: it asks when the answers are not on screen |
| 33 | Cold journey (5): run step 2's action first, then step 5 | Y, but it conflicts with §4 | Step 2's action calls `ctx.go(/compare…)`, and the journey then calls `ctx.go(/dev/journey…)`. That is **two history entries**, which breaks the §4 rule that "the setup never adds an extra history entry". Specify that the precondition run uses `replace` (or a no-navigation ask), and say what Back shows afterwards |
| 34 | Browser Back/Forward "never re-runs the action" | Partly | The step action is not re-run. But going Back to `/dev/audit?tamper=1&gd=7` remounts `AuditConsole`, which runs the sandbox tamper and Verify again from the URL (`:93-94`). That is safe (a copy only) and matches the presenter's `←`. Reword it as "never re-runs the script action; the page's own URL parameters still apply" |
| 35 | The pill uses `z-40`, "under dialogs and sheets at `z-50`" | Partly | The dialog and sheet **content** is `z-50` (`dialog.tsx:58,93`), but the scrim is `z-40` (`dialog.tsx:19`). That is the same layer as the pill, and the order is decided by DOM position. `WorkBuddyCard` is also `z-40`. State the intended stacking, or put the pill at `z-30` |
| 36 | `defineMessages` "as in `presenter.ts:355`" | N (drift) | `presenter.ts:382` (`presenterMessages = defineMessages(...)`). The `step.<id>` keys (en 141-154, zh 326-339) and `pr.clip.badge` and `pr.clip.missing` (126-127) exist |
| 37 | The live-region example: "Step 3 of 11: Source [2] on Rachel's answer" | N | "Source [2] on Rachel's answer" is `step.citation`, which is step **4** of 11 (HUD 03). Step 3 is Ken (`step.compare-person`) |
| 38 | Presenter keys stay off unless armed (`controller.ts:264`) | N (drift) | The guard is at `controller.ts:330`. Mouse side buttons are only taken over while armed (`:444`), so under app mode they stay the browser's Back and Forward, as the plan needs |
| 39 | `PersonaMenu` has Settings and Sign out at `ShellControls.tsx:252-261`, and the persona switch at `:214-250` | Y | Settings is at 251-256 and Sign out at 258-261 |
| 40 | `MODE_HOME`, `components/ui/dialog.tsx` (Radix), mock `demo_features: true` (`graph.ts:615`) | Y | `MODE_HOME` is at `lib/prefs.ts:113` |
| 41 | `/login?next=/demo` brings the user back | Y | `AppShell.tsx:96-99` together with `loginHref` / `safeNextPath` (`nextPath.ts`) |
| 42 | End demo goes to `MODE_HOME` | Y, with a gap | On a clean profile, `/` in user mode shows the first-visit **tour offer toast** (`Tour.tsx:464`, shown only on `/`). That is harmless, but End demo will usually be followed by a "Take the tour" prompt. Either dismiss the offer for guided tabs, or accept it |

## 3. Kit, shortcuts, tests and docs (PLAN §9 to §12)

| # | Claim in the plan | Verified | Correction |
|---|---|---|---|
| 43 | `param` block at `demo_day.ps1:27-37`; `$PresentPath` at :53; `Start-Live` at 406-479 (`:476`); `Open-DemoBrowser` at 267-297; `Show-Verdict` at `:396`; F11 hint at `:296`; header at 6-25; `Test-SignIn` at 114-127; `Reset-Demo` calls `Start-Live` at :514; `New-Shortcuts` at 570-580; icons 137, 131, 238 and 12 | Y | All exact today. The passcode edit in `Test-SignIn` has already landed (`:118-121`). There is no existing `$App` variable to collide with |
| 44 | Browser URL in `Start-Offline` (`:552`) is `"$LiveUrl$(if ($App) …)"` | N | `Start-Offline` uses **`$MockUrl`** (`:552`, port 3101). The offline branch must be `"$MockUrl$(if ($App) { $AppPath } else { $PresentPath })"`, otherwise offline app mode would open the live port. `Show-Verdict` prints `$Base$PresentPath` (`:396`), so its `-App` line has to use `$Base` too |
| 45 | "The key legend replaced" | Y | The legend is `Show-Keys` (`:85-95`), which `Show-Verdict` calls |
| 46 | All five `.cmd` files translate arguments | Y | The five files are Start, Stop, Reset, Start (offline) and Make Desktop Shortcuts, each with the `set "ARGS=%ARGS:--x=-X%"` pattern. `--app` does not overlap any existing flag |
| 47 | The "four buttons" text is at `demo_day.ps1:23` and `Make Desktop Shortcuts.cmd:2`, plus RUNBOOK §1 "five buttons" | Partly | `demo_day.ps1:23` is right. Also `:4` ("The four .cmd files", which is already five today). `Make Desktop Shortcuts.cmd:2` lists the names, not "four". The RUNBOOK also says "four buttons" at lines 4 and 15, not only in the §1 heading |
| 48 | E2E port 3132 and `.next-guided` do not clash | Y | The ports in use are 3101 (main, which is also the offline mock server), 3123 (a11y), 3124 (act), 3127 (grow) and 3128 (deliverables). Only `a11y-shell` is in the main `testIgnore` today (`playwright.config.ts:11`); grow, act and deliverables are not. Adding `guided-demo` to `testIgnore` is fine |
| 49 | `expectAccessible` from `helpers.ts`, `presenter.spec.ts`, `CommandPalette.tsx` | Y | `helpers.ts:30`. The other two files exist |
| 50 | `docs/finals/PRESENTER_KEYS.md` "(if present)" | N | The file is `docs/finals/rubric/demo_story/PRESENTER_KEYS.md`. It exists, so drop "if present" |
| 51 | `FEATURE_CATALOGUE.md` §3: add "P5 Guided demo" | Y | §3 has P1 to P4 (lines 210-247), so P5 is the next number |
| 52 | `CHANGELOG.md` "In progress" line | Y | The section is at `CHANGELOG.md:14` |
| 53 | RUNBOOK §1 and RUN_SHEET §0b exist | Y | RUNBOOK §1 is "The five buttons"; RUN_SHEET §0b is "Stage setup notes" |
| 54 | **Decision number: "D-35 (D-34 is the next free number today if nothing else lands; check before writing)"** | N (stale) | **D-34 is taken**: `DECISIONS.md:41`, "Upload folder picker by name, writable folders only" (30 Oct), also cited in `contracts/uploads.md:168`, `backend_v1.md:768` and `FEATURE_CATALOGUE.md:570`. The last row is D-34, so **D-35 is the next free number**. Keep D-35 in the status line and in §14 row 8, and delete the parenthetical in §12 |
| 55 | §12 lists every doc to change | N (gap) | If app mode is ever used for the 5:00 (Q1), three claims become false and are not in the §12 list: (a) `RUN_SHEET.md:63`, Aaruni's spoken line "Dev has not touched the mouse all demo"; (b) `rubric/README.md` row 6 ("Presenter mode, no mouse") and row 8 ("Keyboard-only driving"); (c) `rubric/demo_story/README.md:29`. Hard rule 8 means they must change in the same commit |

## 4. The step table compared with the run sheet and `script.ts`

| # | Check | Verified | Correction |
|---|---|---|---|
| 56 | 11 chapters equal HUD 00 to 10 (RUN_SHEET §2 line 42, "the 11 steps in `script.ts` (HUD 00 to 10)") | Y | |
| 57 | The id, page and question per row match `script.ts` | Y | All 11 rows match: `title` goes to `/present` and signs in Rachel; `compare-people` uses `SPEC_PEOPLE` and `Q_VELQUARRY` ×2; `compare-person` uses `SPEC_KEN` with Velquarry vs Bayfront; `citation` uses slot `c`, n 2; `journey` signs in Maya and reveals `journey-waterfall`; `verify` and `tamper` open `/dev/audit?verify=1` and `?tamper=1`; `workbuddy` opens `/dev/audit` (`offApp`); and the three slides |
| 58 | The clip per row | Y | A for 00-03, B for 04-06, C for 07, none for 08-10 (`script.ts:21-25` and each step's `clip`) |
| 59 | The run sheet agrees row by row | Y except one | RUN_SHEET 0:00 says "`/compare` already open, Stage theme", but `script.ts` step 00 is the `/present` title slide, and `PRESENTER_KEYS.md` and `FEATURE_CATALOGUE.md` P1 say "title card" too. The plan follows `script.ts`, which is correct. This is an **upstream run-sheet inconsistency**: flag it to whoever owns RUN_SHEET, not to this plan |
| 60 | Grow is not part of the guided demo ("a Q&A walk-up, RUN_SHEET §2") | Y | RUN_SHEET line 42; D-26 |
| 61 | Numbering inside the plan | N | The plan mixes three numberings: chapter 1-11 (§3, §5 table, §11), HUD 00-10 (§2 item 4, "step 4" = journey) and "step 3" in §6 (which is wrong, row 37). Use "chapter n (HUD hh)" everywhere |

## 5. Corrections to apply, in order

1. §12: D-34 is taken (`DECISIONS.md:41`), so the decision is **D-35**. Delete the parenthetical.
2. §9: the `Start-Offline` URL uses `$MockUrl`, not `$LiveUrl`.
3. §5: the adapter's `signInMain` must use `serialSession` / `sessionOwner` from `session.ts`, not a
   private promise chain, and its `call` should pass a liveness function to `callPresenter`.
4. §5 / §4: the cold-journey precondition pushes two history entries. Specify `replace`.
5. §3: `WorkBuddyCard` cannot go inside the pill unchanged, because it is a fixed overlay.
6. §12: fix the `PRESENTER_KEYS.md` path. Add RUN_SHEET:63, rubric README rows 6 and 8, and
   `demo_story/README.md:29` as conditional on Q1.
7. Line drifts: `controller.ts` 60, 66-71, 137, 229, 330, 426-430; `PresenterRoot.tsx:108`;
   `preflight/page.tsx:93,99` (copy `dynamic = 'force-dynamic'`); `presenter.ts:382`; failed UI in
   `PresenterBar.tsx:136-178`.
8. §6: fix the live-region example (step 4, not step 3). Restate the z-index (the scrim is at `z-40`).
9. §2 item 8: two `PresenterRoot` mounts, not one.
10. §13 row 8: `refreshHealth` writes `health` to the presenter store.
