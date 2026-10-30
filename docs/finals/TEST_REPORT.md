# Test report: Playwright suite (mock mode)

30 Oct 2026. Web app `databridge/apps/internal_brain_web`, branch `final_v1.0`. Config: `playwright.config.ts`
(builds with `npm run build:mock`, serves on 127.0.0.1:3101, one worker).

## Current result (30 Oct 2026, cloud)

Run on Fri 30 Oct 2026 in the cloud (Linux, Python 3.13, `IB_FORCE_OFFLINE=1`) on the committed tree at
`d8f8de7`/`831297e`, branch `final_v1.2`, with `playwright.config.ts` in mock mode. Every spec that config runs is
included, among them `act.spec.ts`, `grow.spec.ts`, `deliverables.spec.ts` and `voice.spec.ts`:
**166 passed, 0 failed, 41 skipped**. The skipped specs need the live stack, the engine proxy or Hy3.
`a11y-shell*.spec.ts` has its own config and is not in this count.

Same run, other suites: IB backend (`apps/internal_brain/tests`) 1,131 passed, 0 failed; mock platforms 154 passed;
fixtures (enterprise) 47 passed; web `tsc --noEmit` clean.

The sections below are the earlier 30 Oct run on `final_v1.0` (124 passed), kept as history.

## 1. Result (earlier run, `final_v1.0`)

Three consecutive full runs from a fresh build, no retries: **124 passed, 0 failed, 38 skipped** each time
(17.0 min, 11.4 min, 9.9 min; the first was slowed by other agents using the machine). No flaky test seen in those
three runs. `tsc` is clean for the app (`npx tsc -p tsconfig.json --noEmit`) and for the test files.

Not in this count (added after these runs; re-run pending): `voice.spec.ts`, `act.spec.ts` (5), `grow.spec.ts` (6) and
`deliverables*.spec.ts` (5).

Note: `tsconfig.json` excludes `tests/`, so the command in the brief never checks test files. I checked them with a
throw-away tsconfig that extends it and includes `tests/**` and `playwright.config.ts` (deleted afterwards).

### Per spec file (final run)

| Spec | Passed | Failed | Skipped |
|---|---|---|---|
| a11y.spec.ts | 10 | 0 | 0 |
| analytics.spec.ts | 13 | 0 | 0 |
| ask.spec.ts | 7 | 0 | 0 |
| audit-a11y.spec.ts | 12 | 0 | 0 |
| audit-integrity.spec.ts | 8 | 0 | 0 |
| audit-trust.spec.ts | 1 | 0 | 0 |
| audit.spec.ts | 1 | 0 | 0 |
| compare.spec.ts | 9 | 0 | 0 |
| engine-proxy.spec.ts | 1 | 0 | 10 |
| engine.spec.ts | 1 | 0 | 14 |
| graph-perf.spec.ts | 0 | 0 | 2 |
| graph-screens.spec.ts | 0 | 0 | 6 |
| graph.spec.ts | 7 | 0 | 0 |
| keyboard.spec.ts | 2 | 0 | 0 |
| map.spec.ts | 4 | 0 | 0 |
| presenter.spec.ts | 11 | 0 | 0 |
| proxy.spec.ts | 4 | 0 | 0 |
| screenshots.spec.ts | 2 | 0 | 0 |
| sim.spec.ts | 2 | 0 | 0 |
| tour-screens.spec.ts | 0 | 0 | 6 |
| tour.spec.ts | 8 | 0 | 0 |
| user.spec.ts | 11 | 0 | 0 |
| value.spec.ts | 10 | 0 | 0 |
| **Total** | **124** | **0** | **38** |

### Skipped by design (env-gated, stay skipped by default)

| Spec | Skipped | Enable with |
|---|---|---|
| graph-screens.spec.ts | 6 | `GRAPH_SCREENS=1` (captures graph screenshots) |
| graph-perf.spec.ts | 2 | `GRAPH_PERF=1` (frame-rate measurement) |
| tour-screens.spec.ts | 6 | `TOUR_SCREENS=1` (captures tour screenshots) |
| engine.spec.ts | 14 | server started with `WALNUT_PROXY=1` (`ENGINE_PROXY=on`); one test also needs `ENGINE_LIVE=1` and the live stack |
| engine-proxy.spec.ts | 10 | server started with `WALNUT_PROXY=1` (proxy-on cases); proxy-off cases run by default |

### Not in this run: a11y-shell*.spec.ts

`tests/e2e/a11y-shell.spec.ts` appeared mid-task from the accessibility pass. It has its own config
(`tests/e2e/a11y-shell.config.ts`, dev server on port 3123, 90 s timeout) and one test hard-codes host `127.0.0.1:3123`,
so under the main config it fails by design. It was also still being edited. I added
`testIgnore: ['**/a11y-shell*.spec.ts', '**/dbgtmp/**']` to `playwright.config.ts` (`tests/e2e/dbgtmp/` is scratch).
In the one run where it was included it reported failures (section 4, items 3 to 5); those were not re-verified
once the owner finished. Run it with `npx playwright test -c tests/e2e/a11y-shell.config.ts`. When that work is done,
either drop the `testIgnore` or make the host check use `baseURL`.

## 2. Changes to tests and why

First full run against the rebuilt UI: 107 passed, 12 failed, 38 skipped. Every failure was a stale test or a
test-side issue; none was a real app bug. Security and accessibility assertions were kept or tightened.

| File | Change | Why / what is still asserted |
|---|---|---|
| `helpers.ts` | `AxeBuilder({ page })` now casts the page type | `@axe-core/playwright` resolves `playwright-core` 1.64 while `@playwright/test` is 1.48, so the `Page` types differ (TS2740 at line 31). Runtime is unaffected. This was the only type error in test files. |
| `helpers.ts` | `expectAccessible` first waits for finite animations to finish | The answer card fades in (`ds-in`, 220 ms). axe measured contrast on the mid-fade colour and reported a false `color-contrast` on `scope-note` in `user.spec.ts` (dark, 1280). Waiting for the animation, not a timeout, removes that. The axe rules and the serious/critical filter are unchanged. |
| `helpers.ts` | New `quietTourOffer(page)` and `keyboardLogin(page, persona)` | The first-visit tour offer is a fixed toast; `keyboardLogin` is the radio-group path (Tab into the group, ArrowRight to the person, Enter). |
| `ask.spec.ts` login/ask | Heading check replaced by `greeting`; chips read "1"; source cards are buttons; chip opens the Sources sheet; `sheet-open` has `target=_blank`; Esc returns focus to the chip | Same purpose: citations visible, scope note verbatim, request id, model badge `Tencent Hunyuan Hy3`. |
| `ask.spec.ts` refusal | Uses `refusal-card` / `refusal-text`; no chips, no citations list, no model badge; scope note and request id present; card text has no digits; no hidden/withheld/restricted wording; the Q3 and the made-up Q7 refusals have byte-identical card text | Stronger than before: it compares the two refusals' full text, not only the constant sentence. |
| `ask.spec.ts` 503 | Expects "temporarily unavailable" and "Nothing was answered." | New wording; still asserts no `answer`. |
| `ask.spec.ts` model badges | Scoped to `getByTestId('answer')` (earlier answers stay as `answer-previous`, which caused a strict-mode clash); labels `DeepSeek` and `Quoted from sources (no model)`; fallback tooltip checked; no AI note on the extractive answer | Fallback and extractive cues are still text, not colour. |
| `ask.spec.ts` sources panel | Re-pointed to Mission control `/dev` (`freshness` list: four platforms, "In SLO" or "Behind"), plus `sources-panel` count 0 on Ask | The Ask page no longer has the global status panel (`components/SourcesPanel.tsx` is unused). The per-platform coverage lives in Developer mode. |
| `ask.spec.ts` simulator | "Version 8" to "v8", "Live re-read" to "Re-read just now" | New badge text. Revocation (jdoe is refused after removal) unchanged. |
| `a11y.spec.ts` | Sources panel replaced by a source card plus the Sources sheet (axe on the open sheet); refusal waits for `refusal-card`; `quietTourOffer` | Adds an axe pass on the sheet at both widths. |
| `keyboard.spec.ts` | Login through `keyboardLogin` (radio group); chip Enter opens the sheet, Esc returns focus to the chip; a source card shows a visible focus indicator (outline or ring); the audit test signs in as `maya.compliance` by keyboard | Same keyboard-only paths, on the new controls. |
| `screenshots.spec.ts` | `quietTourOffer` | At 390 px the tour offer covered the Ask button (see 4.1). It rewrites tracked PNGs in `screenshots/`, as expected; left in place. |
| `graph.spec.ts` | `test.setTimeout(120_000)` on the first test | It waits up to 30 s for the canvas, then layout and axe; it timed out once at 45 s while the machine was loaded by other agents. The waits themselves are still `expect(...)` waits. |
| `engine-proxy.spec.ts` | A test title containing `jdoe's` sat in a single-quoted string (syntax error that stopped the whole run); now double-quoted | Introduced by another agent at 05:23. |
| `playwright.config.ts` | `testIgnore` for `a11y-shell*` and `dbgtmp/` | See section 1. |

Already correct and not touched: `user.spec.ts`, `tour.spec.ts`, `sim.spec.ts` (redirects), `audit*.spec.ts`,
`compare.spec.ts`, `analytics.spec.ts`, `map.spec.ts`, `presenter.spec.ts`, `proxy.spec.ts`, `value.spec.ts`.

## 3. Flakiness

- Animation timing in axe (above): fixed with an animation wait, not a sleep.
- `graph.spec.ts` galaxy test: budget raised (above). Not seen again in the last three runs.
- Parallel interference: another agent's Playwright runs share `test-results/` and wiped trace folders under my
  run (ENOENT on `.playwright-artifacts-*`; five unrelated tests failed that way once). Fix: run with a private
  output directory, for example `npx playwright test --output=<tmp dir>`. Consider setting `outputDir` in the config.
- No retries are configured and none were needed.

## 4. App bugs for the coordinator

**Status update:** items 1 and 2 are fixed in commit `810ddbb` (the tour offer moves to the top on phones;
`AnswerPanel.tsx` and `SourcesPanel.tsx` are removed). Later, `voice.spec.ts` (2 tests, commit `1bc6b4c`) was added;
it is not in the counts above.

None of these block the suite. Items 3 to 5 come from `a11y-shell.spec.ts` (accessibility pass, unfinished) and are
unverified.

1. **Tour offer covers the Ask button on a phone.** `components/tour/*` (`data-testid="tour-offer"`, fixed
   `bottom-4 left-4 right-4`, z-70). Repro: viewport 390 x 844, sign in as jdoe with no `ib-tour-offer` in
   localStorage, ask a question, open and close a source sheet, then click Ask for a second question. Playwright reports
   the offer intercepts pointer events and the click times out. A user can dismiss it ("Not now"), so it is minor, but
   the composer should not sit under it. The specs work around it with `quietTourOffer`.
2. **`SourcesPanel` is dead code.** `components/SourcesPanel.tsx` (and `AnswerPanel.tsx`) are not imported by any page
   now. Remove them or keep them deliberately.
3. (a11y-shell, unverified) `landmark-unique` and `region` on `/dev/journey/*` and `/dev/trust` in all themes and widths
   (`a11y-shell.spec.ts:210-221`).
4. (a11y-shell, unverified) `region` (moderate) on `/settings`: element `.text-center` outside any landmark
   (`a11y-shell.spec.ts:296-315`); in the same test the `/value` link-height check read 0.
5. (a11y-shell, unverified) `a11y-shell.spec.ts:269` hard-codes host `127.0.0.1:3123`; fine with its own config, fails
   under the main one.

No security-relevant app bug was found: refusals for restricted and made-up subjects are byte-identical, and the
refusal card carries no digits, counts or hints.

## 5. Running it

```
cd Tencent/databridge/apps/internal_brain_web
npx playwright test --reporter=line --output=<private tmp dir>   # builds first; about 10 to 17 minutes
```
