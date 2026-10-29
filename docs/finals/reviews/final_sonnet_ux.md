# Final UX and accessibility review (Sonnet), Walnut DataBridge

Date: 30 Oct 2026. Build: http://127.0.0.1:3100 (production build). Method: built-in browser, axe-core 4.10.0 (injected from cdnjs; wcag2a/2aa/21aa/22aa + best-practice tags), real Tab/Enter/Esc/Ctrl+K key presses, DOM probes, resize to 390 and 320 px. Read-only; no Simulator, no reset.

Caveat: the browser pane was shared. The signed-in persona changed under me several times (Jordan, Rachel, Maya, Ken), so I re-checked "who am I" before every persona-specific claim. Pages marked "dev" were each audited with whichever persona was active; Rule 4 checks were repeated under Ken (see below). Not tested: Simulator, answers for personas other than Jordan/Ken, screen reader (NVDA/VoiceOver) itself, browser zoom 200%.

## Ranked findings

| # | Severity | Page | Issue | WCAG | Fix |
|---|---|---|---|---|---|
| 1 | High | All app pages, Settings 130% | At Text size 130% on a 1280 px window the top bar overflows: scrollWidth 1395 > 1280, the account button (and "Jordan Doe - Backend engineer") is clipped off the right edge. Page also gets a horizontal scrollbar. | 1.4.4, 1.4.10 | Let header actions wrap or collapse (icon-only account button, `min-w-0`, `flex-wrap`) when root font-size > 16 px; test 115% and 130% at 1024 and 1280. |
| 2 | High (i18n) | All pages, 简体中文 | "Skip to main content" (the skip link) stays English, as do the brand-adjacent role label "Jordan Doe - Backend engineer", and (zh) the suggestion chips and history questions. | 3.1.2, 2.4.1 | Translate the skip link and persona role strings; mark data questions `lang="en"` or translate the starter prompts. |
| 3 | High (i18n) | /dev, /dev/journey, /dev/trust in 简体中文 | Developer-mode pages are mostly English: Mission control intro, pipeline step names and tooltips, "Organisation numbers..." lock card, Journey table, Trust centre control text. Only nav chrome is translated. Mixed text is not tagged with `lang`. | 3.1.1, 3.1.2 | Either translate, or show a one-line "Developer pages are English only" note and wrap English blocks in `lang="en"`. State it in the accessibility statement. |
| 4 | Medium | Answer page, citation chips | Inline citation chips measure 24 x 22 px (below the 24 x 24 floor). They sit in running text, so the inline exception may apply, but the statement's own test is a bounding-box test and will fail. | 2.5.8 | Set `min-height: 24px` (padding or `::after` hit area) or document the inline exception. |
| 5 | Medium | /dev/journey, /dev/trust | axe `landmark-unique`: a `section[aria-labelledby]` has the same name as another landmark (journey "Your recent requests" appears twice as a landmark; trust "OWASP Top 10..." duplicates). | 1.3.1 (best practice) | Remove `aria-labelledby` from the inner section, or give each landmark a distinct name. |
| 6 | Medium | /dev/audit | 53 empty `aria-live="polite"` spans, one per row (copy/hash buttons), plus the "N requests found" status. Many live regions slow screen readers and can announce stray text. | 4.1.3 | Use one shared live region for "Copied". |
| 7 | Medium | Home, Settings | Page-level robustness: opening /settings or /dev/graph straight after a persona change sometimes lands on "/" or shows "Checking your session..." with no timeout message. (Seen twice, possibly caused by the shared browser; not reproduced under a clean session.) | 3.2.x, 4.1.3 | Add a visible timeout/retry message to the session check. |
| 8 | Low | /settings | axe reports `button-name` (critical) on the three `role=switch` buttons. False positive in practice: the browser accessibility tree gives them the names "Reduce motion", "High contrast", "Developer mode" via `<label for>`. Still, a judge running axe will see "critical". | 4.1.2 | Add `aria-labelledby` pointing at the label id (and `aria-describedby` for the hint) so axe passes. |
| 9 | Low | /value | Two source links ("APQC...", "Atlassian...") are 18 px tall. | 2.5.8 | `py-1` or `min-h-6` on source links. |
| 10 | Low | Journey receipt | Status filter reads "Refused" and Gate steps list "Gate 1 / Gate 2 ... done". Not a Rule 4 breach (no counts, same for every refusal), but user mode says "No answer from what you can open" while developer mode says "Refused". Pick one word in plain language. | content | Use "No answer" consistently. |
| 11 | Info | /compare | Any signed-in user can choose any two personas (including MLRO, Maya) and "Ask both". It is a demo-login feature (`IB_DEMO_LOGIN`), not tested by me (it would write ledger rows under other people's names). Judges may ask. | n/a | Make sure the stage script says it is demo-only and absent from a real SSO build. |

## Rule 4 check (critical product rule)

Checked as Ken (external contractor) and Jordan:
- Ken asks "Show me the security incident report from the Q3 breach." Answer is the constant "No answer from what you can open ... Asking in different words, or naming the project ..." plus the constant scope note. No counts, no "restricted", "hidden", "locked", "denied" in the page text or in the receipt (regex scan of the receipt returned zero hits; Sources cited: "No sources to show.").
- Ken on /dev/brain gets a role lock card ("This snapshot is not yours to explore ... Sign in as Jordan or as compliance"). Allowed (role gate, not content hint). Jordan sees "932 entities, 2,246 links, 4,710 pointers" there: it is labelled as Jordan's snapshot built 13 Oct. Worth a one-line confirmation that this snapshot is Jordan-visible content only (the label says so), because the count is of the whole engine store, not a live check.
- /dev/analytics for non-compliance shows a lock card ("Analytics needs the compliance role"), which is fine.
- Graph legend counts (Confluence 31, Slack 504, ...) are of items the asker can open ("for your own session").
- Result: no Rule 4 violation found in what I could reach.

## Verified good

- Skip link is the first Tab stop, visible, and moves focus to `main#main` (tabindex -1).
- Tab order on home: skip, logo, User/Developer toggle, palette, language, theme, tour, account, left nav, scope pill, textarea, suggestion chips, then wraps; no trap. Every focus stop had a 2 px solid outline (`:focus-visible` true).
- Ctrl+K palette opens as a dialog with a combobox and listbox (24 commands including text size, language, theme, sign out); Esc closes it and focus returns.
- Presenter keys do not hijack typing: typing "p b r l  hello" into the question box produced exactly that text, 0 `preventDefault` calls, no navigation.
- Answer live region: "Working on your answer." then "Answer ready, with 4 sources." (or "No answer was found in what you can open."), each announced once, polite; focus moves to the "Answer" heading; no progressbar spam (none present in the final state).
- Sources sheet: opens as a named dialog ("RCA: Payment authorisation outage ..."), focus goes to "Close source", 12 Tab presses stay inside, background is aria-hidden, Esc closes and focus returns to the exact citation chip that opened it.
- Citation chip names are rich: "Source 1: <title>, Confluence. Opens the passage."
- Tour dialog: named (`tour-title`), "Esc to close", Esc closes and focus returns to the "Take the tour" button.
- Landmarks: header, aside, nav "Primary", main on every page; one H1 per page; logical H1 > H2 > H3.
- axe (wcag 2a/2aa/21aa/22aa + best practice): 0 violations on /, /history, /compare, /value, /dev, /dev/graph, /dev/brain, /dev/analytics (as Maya), /dev/audit (as Maya), answer state; only the items in the table above elsewhere.
- Contrast: axe `color-contrast` (AA) is clean on /settings, /, /value, /dev, /dev/trust, /dev/graph in light and dark, and with High contrast + 130% on. Only AAA-enhanced (7:1) misses on small muted text in dark mode, which is out of scope for AA.
- High contrast switch sets `data-contrast=more`; text size 130% sets root 20.8 px and reflows (except finding 1).
- Reduced motion: switch sets `data-motion=reduce`, transitions drop to 1e-6 s; stylesheet also has 3 `prefers-reduced-motion` rules.
- Reflow: no page-level horizontal scroll at 390 px on /, /history, /compare, /settings, /value, /dev, /dev/journey, /dev/analytics, /dev/audit, /dev/trust, /dev/brain, /dev/graph; and none at 320 px on /, /settings, /dev/audit, /dev/analytics, /value, /compare.
- Target size: all buttons, links, radios, switches on every audited page were at least 24 x 24 except findings 4 and 9.
- Graph: has a Graph/Table toggle; the table has a caption ("Nodes on the canvas (589)"), column headers with scope, 590 rows; canvas wrapper has `role=img` with a full label and key hints (arrows pan, + and - zoom, 0 fits, / searches, Esc clears); on-screen pan and zoom buttons (a non-drag alternative).
- Analytics charts have "View as table" for every chart; tables have captions.
- `<html lang>` switches `en` / `zh-Hans`.
- Plain-language answer states: empty answer, "You could try" hints, and the role lock cards read clearly.
- Naming: brand is "Walnut DataBridge" in title, header and login; "Internal Brain" appears in developer copy ("How every question moves through the Internal Brain"). Acceptable but state the relation once in the About text.
- Note on first impression: a short question phrased differently from the starter chip ("What is the status of the database migration project?") returned the graceful "No answer" state, not an error; use the starter chips on stage.

## Accessibility statement: which rows can now be marked

File: docs/finals/rubric/hcd_ux/ACCESSIBILITY_STATEMENT.md. A row may say "pass" only with a real evidence path or test name, so "evidence now exists" below means my live results (this file) can be cited as manual/automated evidence; the coordinator should still attach a screenshot or the Playwright spec name.

| Row | Can mark? | Basis |
|---|---|---|
| 1.1.1 | Yes, with the graph table | axe clean; graph has Table view and labelled `role=img` |
| 1.3.1 | Yes | axe clean on main pages; heading/landmark structure verified; but note finding 5 (landmark-unique on journey and trust) |
| 1.4.3 | Yes (light and dark, plus high contrast) | axe color-contrast clean in both themes; "Stage" theme not tested |
| 1.4.4 | Partial | 130% tested, header overflows (finding 1); 200% not tested |
| 1.4.10 | Partly: 390 and 320 px pass on the pages listed above | list the pages; not all dev pages at 320 |
| 1.4.11 | Not evidenced | no non-text contrast measurement run |
| 1.4.12 | Not evidenced | text-spacing override not run |
| 1.4.13 | Not evidenced | hover/tooltips not tested |
| 2.1.1 | Yes | ask, open citation, close, switch via keyboard, palette |
| 2.1.2 | Yes | sheet, palette, tour, menus: no trap |
| 2.1.4 | Yes | typing test in the question field; the armed-presenter gating itself was not exercised |
| 2.3.3 / motion | Yes | `data-motion=reduce` and CSS `prefers-reduced-motion` rules found; OS-level emulation not run |
| 2.4.1 | Yes | skip link tested |
| 2.4.3 / 2.4.7 | Yes | order and 2 px outline on all stops |
| 2.4.11 | Not evidenced | sticky header/composer overlap not specifically checked |
| 2.5.7 | Yes (graph) | Table view and pan/zoom buttons exist; node drag not tested |
| 2.5.8 | Fail/partial | citation chips 24 x 22; two /value links 18 px |
| 3.2.6 | Not evidenced | manual not done |
| 3.3.7 | Not evidenced | |
| 3.3.8 | Yes (by design) | login is a persona list, no puzzle; passcode paste option exists |
| 4.1.2 | Yes with caveat | names verified; axe `button-name` flag on the three switches (finding 8) |
| 4.1.3 | Partly | live region announced once for answers (observed in DOM mutations); real screen reader pass not done |
| 3.1.1 / 3.1.2 | Partial | `lang` switches correctly, but English left in zh (findings 2, 3) is not marked up |

Passes in section 4: Automated: done manually via axe, not as Playwright e2e per page at three widths. Keyboard: done, not recorded. Screen reader (NVDA): not done. Reduced motion: done by switch. Contrast: done for dark and light. Zoom 200% and 320 px: 320 px partial, 200% not done.

## Top 10 (reply summary)

1 Header overflows at Text size 130% (1.4.4/1.4.10). 2 Skip link and persona role strings stay English in 简体中文. 3 Developer pages mostly English in 简体中文, no `lang` tags. 4 Citation chips 24 x 22 px (2.5.8). 5 `landmark-unique` on /dev/journey and /dev/trust. 6 53 empty live regions on /dev/audit. 7 Session check can hang or bounce to "/" with no message. 8 axe "critical" button-name on the three Settings switches (false positive; add aria-labelledby). 9 18 px source links on /value. 10 "Refused" vs "No answer" wording across modes.
