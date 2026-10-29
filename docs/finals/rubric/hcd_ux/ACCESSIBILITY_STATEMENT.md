# Accessibility statement and WCAG 2.2 AA evidence table: Walnut DataBridge

Status: **TEMPLATE. Evidence pending capture.** The coordinator fills the evidence paths after the tests run. Nothing here is a claim of conformance.

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

Tests are named so the coordinator can match them. "Test id" is TO FILL where the Playwright spec name is not yet fixed. Paths are relative to `Tencent/submission/` (accessibility folder); fill when captured.

| WCAG 2.2 criterion | What it asks | How we check it | Test id | Evidence path | Status |
|---|---|---|---|---|---|
| 1.1.1 Non-text content | Text alternatives | axe e2e on each page; graph has a text/table alternative | TO FILL | pending capture | pending capture |
| 1.3.1 Info and relationships | Structure in markup | axe e2e; headings, landmarks; answer region | TO FILL | pending capture | pending capture |
| 1.4.3 Contrast (minimum) | 4.5:1 text, 3:1 large | automated contrast check on light, dark and Stage themes | TO FILL | pending capture | pending capture |
| 1.4.4 Resize text | Usable at 200% | browser zoom 200% screenshot per main page | TO FILL | pending capture | pending capture |
| 1.4.10 Reflow | No two-way scroll at 320 CSS px | Playwright at 320 px width on each main page (the existing 390 px test is not the same) | TO FILL | pending capture | pending capture |
| 1.4.11 Non-text contrast | 3:1 for UI parts and focus ring | contrast check on inputs, focus ring, chart marks | TO FILL | pending capture | pending capture |
| 1.4.12 Text spacing | No loss with spacing overrides | inject WCAG text-spacing CSS; check no clipping | TO FILL | pending capture | pending capture |
| 1.4.13 Content on hover or focus | Dismissible, hoverable, persistent | manual check on citation chips and tooltips | n/a (manual) | pending capture | pending capture |
| 2.1.1 Keyboard | All functions by keyboard | keyboard-only flow: ask, open citation, close, Verify | TO FILL | pending capture | pending capture |
| 2.1.2 No keyboard trap | Focus can leave | manual: sources sheet, dialogs, tour | n/a (manual) | pending capture | pending capture |
| 2.1.4 Character key shortcuts | Single-key shortcuts can be turned off or limited | Presenter keys (P, B, R, Space, L) active only when presenter mode is armed; not in text fields (see demo_story/PRESENTER_KEYS.md) | TO FILL | pending capture | pending capture |
| 2.3.3 / motion preference | Respect reduced motion | Playwright with `prefers-reduced-motion: reduce`; graph and tour animation off | TO FILL | pending capture | pending capture |
| 2.4.1 Bypass blocks | Skip link | screenshot and keyboard check | n/a (manual) | pending capture | pending capture |
| 2.4.3 Focus order and 2.4.7 Focus visible | Logical order; visible focus | keyboard sweep plus axe | TO FILL | pending capture | pending capture |
| 2.4.11 Focus not obscured (minimum) | Focused item not hidden by sticky parts | keyboard sweep on pages with a sticky header or composer; check scroll-padding | TO FILL | pending capture | pending capture |
| 2.5.7 Dragging movements | A non-drag alternative for dragging | Knowledge graph: buttons or keys for pan, zoom and move, plus a text/table view of the same nodes | TO FILL | pending capture | pending capture (likely gap until built) |
| 2.5.8 Target size (minimum) | At least 24 x 24 CSS px | Playwright bounding-box test on icon buttons and citation chips | TO FILL | pending capture | pending capture |
| 3.2.6 Consistent help | Help in the same place | manual | n/a (manual) | pending capture | pending capture |
| 3.3.7 Redundant entry | Do not ask twice | manual | n/a (manual) | pending capture | pending capture |
| 3.3.8 Accessible authentication (minimum) | No cognitive test to sign in | manual: demo login uses a persona list or passcode paste; no puzzle | n/a (manual) | pending capture | pending capture |
| 4.1.2 Name, role, value | Controls are named | axe; custom graph nodes labelled | TO FILL | pending capture | pending capture |
| 4.1.3 Status messages | Announced without focus | screen-reader pass: staged status line and the final answer announced politely, not per token | n/a (manual) | pending capture | pending capture |
| 3.1.1 / 3.1.2 Language | Page language set | `<html lang>` switches between `en` and `zh-Hans`; mixed-language spans marked | TO FILL | pending capture | pending capture |

## 4. Passes we will run and record

| Pass | Tool | Output | Evidence path |
|---|---|---|---|
| Automated | axe in Playwright e2e on every page, at desktop, 390 px and 320 px | report per page | pending capture |
| Keyboard only | No mouse: ask, read, open citation, Esc, switch persona, Verify | short screen recording | pending capture |
| Screen reader | NVDA with Chrome: ask, hear the answer and a citation | short recording with notes | pending capture |
| Reduced motion | Playwright media emulation | screenshot | pending capture |
| Contrast | automated, all three themes | table | pending capture |
| Zoom 200% and reflow 320 px | browser | screenshots | pending capture |

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
