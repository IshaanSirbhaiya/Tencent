# Accessibility statement and WCAG 2.2 AA evidence table: Walnut DataBridge

Status: **PARTLY EVIDENCED (30 Oct 2026).** Rows that cite a spec or a screenshot were checked on a mock-mode dev build (`NEXT_PUBLIC_IB_MOCK=1`, port 3123) with axe-core 4.10 through `@axe-core/playwright` (tags wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa, best-practice), in dark and light, at 1280 and 390 px. Everything else is still pending capture. Nothing here is a claim of conformance.

## 1. Wording rules (read before editing)

- We **target** WCAG 2.2 level AA.
- We never write "compliant", "conformant", "fully accessible" or "accessibility score 100".
- The sentence we use: **"automated plus keyboard and screen-reader passes"**, followed by the table and the known gaps.
- Automated tools find only part of the problems (Deque's vendor study reports about 57% by volume; cite only if asked, as a vendor figure). So an axe pass alone proves nothing.
- A row may say "pass" only if its evidence cell holds a real path or test name. Otherwise it says "pending capture".
- Full conformance would need an independent audit with disabled users. That is a next step, not something we have done.

## 2. Statement (draft text for the in-app `/accessibility` page, if built)

> Walnut DataBridge targets WCAG 2.2 level AA. We have checked it with automated tests (axe in end-to-end browser tests), keyboard-only passes and a screen-reader pass. The table below lists what each check covers and where the evidence is. It also lists known gaps. We have not had an independent audit and we have not yet tested with disabled users. The interface is available in English and 简体中文. To report a problem: TO FILL (contact). Date of last review: TO FILL.

## 3. Evidence table

Tests are named so the coordinator can match them. "Test id" is TO FILL where the Playwright spec name is not yet fixed. Evidence paths are relative to `Tencent/`. The spec `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts` runs only with its own config (`npx playwright test -c tests/e2e/a11y-shell.config.ts`, against a dev server you start; it never builds the app).

| WCAG 2.2 criterion | What it asks | How we check it | Test id | Evidence path | Status |
|---|---|---|---|---|---|
| 1.1.1 Non-text content | Text alternatives | axe e2e on each page; graph has a text/table alternative | TO FILL | pending capture | pending capture |
| 1.3.1 Info and relationships | Structure in markup | axe e2e (best-practice tags on); headings, landmarks; answer region | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "journey and trust: no landmark-unique, no serious axe" (both themes, 1280 and 390, as jdoe and maya) | docs/finals/screens/a11y/journey-1280-dark.png, docs/finals/screens/a11y/journey-1280-light.png | pass on /dev/journey and /dev/trust (0 axe violations incl. landmark-unique); other pages: manual review `docs/finals/reviews/final_sonnet_ux.md` |
| 1.4.3 Contrast (minimum) | 4.5:1 text, 3:1 large | automated contrast check on light, dark and Stage themes | TO FILL | pending capture | pending capture |
| 1.4.4 Resize text | Usable at 200% | Text size 115% and 130% (root 18.4 / 20.8 px) at 1024, 1280 and 390; browser zoom 200% on a 1280 window simulated as a 640 px CSS viewport; top bar folds its controls instead of overflowing | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "reflow: top bar fits at text …", "reflow: 简体中文 + text 130%…", "reflow: the overflow menu…" | docs/finals/screens/a11y/reflow-text130-1280-dark.png, docs/finals/screens/a11y/reflow-text130-390-dark.png, docs/finals/screens/a11y/reflow-text130-zoom200-1280-dark.png, docs/finals/screens/a11y/reflow-text130-1280-light-zh.png, docs/finals/screens/a11y/reflow-text130-390-light-zh.png | partial: pass on /, /settings, /dev, /dev/audit; other pages not measured; real browser zoom (not viewport emulation) not run |
| 1.4.10 Reflow | No two-way scroll at 320 CSS px | Playwright scrollWidth check at 390 px and 640 px (zoom 200%) at text 115/130%; 320 px only in the manual review | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "reflow: top bar fits at text …" | docs/finals/screens/a11y/reflow-text130-390-dark.png; 320 px: `docs/finals/reviews/final_sonnet_ux.md` (manual) | partial: no horizontal scroll at 390 and 640 px on /, /settings, /dev, /dev/audit; 320 px not automated |
| 1.4.11 Non-text contrast | 3:1 for UI parts and focus ring | contrast check on inputs, focus ring, chart marks | TO FILL | pending capture | pending capture |
| 1.4.12 Text spacing | No loss with spacing overrides | inject WCAG text-spacing CSS; check no clipping | TO FILL | pending capture | pending capture |
| 1.4.13 Content on hover or focus | Dismissible, hoverable, persistent | manual check on citation chips and tooltips | n/a (manual) | pending capture | pending capture |
| 2.1.1 Keyboard | All functions by keyboard | keyboard-only flow: ask, open citation, close, Verify | TO FILL | pending capture | pending capture |
| 2.1.2 No keyboard trap | Focus can leave | manual: sources sheet, dialogs, tour | n/a (manual) | pending capture | pending capture |
| 2.1.4 Character key shortcuts | Single-key shortcuts can be turned off or limited | Presenter keys (P, B, R, Space, L) active only when presenter mode is armed; not in text fields (see demo_story/PRESENTER_KEYS.md) | TO FILL | pending capture | pending capture |
| 2.3.3 / motion preference | Respect reduced motion | Playwright with `prefers-reduced-motion: reduce`; graph and tour animation off | TO FILL | pending capture | pending capture |
| 2.4.1 Bypass blocks | Skip link | first Tab stop is the skip link, translated in 简体中文 ("跳到主要内容") | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "zh: skip link, persona role and <html lang>…" | docs/finals/screens/a11y/zh-skip-link-1280-dark.png | pass |
| 2.4.3 Focus order and 2.4.7 Focus visible | Logical order; visible focus | keyboard sweep plus axe | TO FILL | pending capture | pending capture |
| 2.4.11 Focus not obscured (minimum) | Focused item not hidden by sticky parts | keyboard sweep on pages with a sticky header or composer; check scroll-padding | TO FILL | pending capture | pending capture |
| 2.5.7 Dragging movements | A non-drag alternative for dragging | Knowledge graph: buttons or keys for pan, zoom and move, plus a text/table view of the same nodes | TO FILL | pending capture | pending capture (likely gap until built) |
| 2.5.8 Target size (minimum) | At least 24 x 24 CSS px | Playwright bounding-box test: citation chips (now 24 x 24, line pitch unchanged) and /value source links (24 px tall); axe target-size | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "citation chips are at least 24 x 24…", "settings switches are named; value links are 24 px" | docs/finals/screens/a11y/citation-chips-1280-dark.png, docs/finals/screens/a11y/citation-chips-1280-light.png | pass for chips and /value links; other controls: manual review `docs/finals/reviews/final_sonnet_ux.md` |
| Input alternative (supports 2.5.x, 3.3.x) | Voice dictation in the Ask composer as an alternative to typing: browser speech service, hidden when unavailable, aria-pressed, live status, Esc stops, 24 px target, no pulse under reduced motion, never auto-asks | Playwright with a fake SpeechRecognition: text lands in the box, Ask not pressed, hidden when absent, axe | `databridge/apps/internal_brain_web/tests/e2e/voice.spec.ts` | n/a (no screenshot) | automated with a fake API only; a real browser microphone and screen-reader pass not done |
| 3.2.6 Consistent help | Help in the same place | manual | n/a (manual) | pending capture | pending capture |
| 3.3.7 Redundant entry | Do not ask twice | manual | n/a (manual) | pending capture | pending capture |
| 3.3.8 Accessible authentication (minimum) | No cognitive test to sign in | manual: demo login uses a persona list or passcode paste; no puzzle | n/a (manual) | pending capture | pending capture |
| 4.1.2 Name, role, value | Controls are named | axe; the three Settings switches now carry aria-labelledby (axe button-name clean); custom graph nodes labelled | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "settings switches are named; value links are 24 px" | docs/finals/screens/a11y/settings-1280-dark.png, docs/finals/screens/a11y/settings-1280-light.png | pass on /settings and /value (both themes, 1280 and 390) |
| 4.1.3 Status messages | Announced without focus | one shared polite live region (`components/ui/announce.tsx`) for "Copied" instead of one per row; slow session check shows an alert with "Check again" after 8 s; screen-reader pass still to do | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "audit: one shared live region for 'Copied'", "a slow session check shows a message…" | docs/finals/screens/a11y/session-slow-1280-dark.png | partial: DOM and axe evidence only; NVDA pass not done |
| 3.1.1 / 3.1.2 Language | Page language set | `<html lang>` switches between `en` and `zh-Hans`; skip link and persona roles translated; Developer mode (English by design, D-12) shows "开发者模式仅提供英文" and its English-only pages (/dev, /dev/journey, /dev/analytics, /dev/styleguide) mark `<main lang="en">` | `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts`: "zh: skip link, persona role and <html lang>; Developer mode note and lang='en'" | docs/finals/screens/a11y/zh-persona-menu-1280-dark.png, docs/finals/screens/a11y/zh-dev-english-note-1280-dark.png, docs/finals/screens/a11y/zh-dev-english-note-390-dark.png | partial: mixed Developer pages (audit, graph, engine, simulator, trust, value) keep `zh-Hans` and still contain untagged English data; User-mode starter questions are English and untagged |

## 4. Passes we will run and record

| Pass | Tool | Output | Evidence path |
|---|---|---|---|
| Automated | axe in Playwright e2e on every page, at desktop, 390 px and 320 px | report per page | partial: `databridge/apps/internal_brain_web/tests/e2e/a11y-shell.spec.ts` covers /dev/journey, /dev/trust, /dev/audit, /settings, /value and the answer state at 1280 and 390 in both themes; other pages pending |
| Keyboard only | No mouse: ask, read, open citation, Esc, switch persona, Verify | short screen recording | pending capture |
| Screen reader | NVDA with Chrome: ask, hear the answer and a citation | short recording with notes | pending capture |
| Reduced motion | Playwright media emulation | screenshot | pending capture |
| Contrast | automated, all three themes | table | pending capture |
| Zoom 200% and reflow 320 px | Playwright viewport emulation (640 px = 200% of 1280) plus text size 130% | screenshots | docs/finals/screens/a11y/reflow-text130-zoom200-1280-dark.png (partial: 320 px manual only) |

Do not report a Lighthouse number as proof. If one is run, give the number, version and date, and say it is not proof of full accessibility.

## 5. Known limitations (fill from real results; start with these likely ones)

- No independent audit. No testing with disabled users unless the usability report says so.
- Knowledge-graph drag alternative: status TO FILL.
- Language: English and 简体中文 only (D-22). The 简体中文 text is machine-assisted; native-speaker review: TO FILL.
- The answer language follows the question; retrieval and answer tests are in English.
- Anything marked "pending capture" at submission time is listed here as "not evidenced".

## 6. Honesty lines for the stage and Q&A

- "We target WCAG 2.2 AA. Automated plus keyboard and screen-reader passes, with the table and the gaps in the repo."
- If asked "are you compliant?": "We do not claim that. Here is what we tested and what we did not. An independent audit with disabled users is the next step."
- Do not mention a test count unless it is read from the latest run on the day.
