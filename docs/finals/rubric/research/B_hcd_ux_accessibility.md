# Research B: Human-Centered Design and UX and Accessibility

Scope: two judging criteria (10 pts each) for Demo Day, 3 Nov, 5 min pitch + live demo + 1 min Q&A.
Product: Walnut DataBridge / Internal Brain (permission-aware knowledge assistant for a fintech).

Source quality note: the web searches returned mostly secondary summaries. Where a primary source (W3C, NN/g, Glean docs, MeasuringU) was reachable it is linked; otherwise the claim is marked "secondary". Nothing below invents results. All user-test numbers in templates are PLACEHOLDERS to be filled with real data only.

---------------------------------------------------------------------
## 0. What the research says (shared foundation)

### HCD frameworks judges know
- Double Diamond: Discover, Define (diamond 1, "design the right thing"), Develop, Deliver (diamond 2, "design it right"). Secondary summary: https://dstudio.ubc.ca/research/toolkit/processes/ and https://dovetail.com/product-development/what-is-the-double-diamond-design-process
- IDEO-style lenses: desirability (people want it), feasibility, viability. Secondary: https://dstudio.ubc.ca/research/toolkit/processes/ (primary IDEO page not retrieved).
- Nielsen's 10 heuristics (visibility of system status; match to real world; user control; consistency; error prevention; recognition over recall; flexibility; minimalist design; error recovery; help). Primary: https://www.nngroup.com/articles/ten-usability-heuristics/ (not fetched; list confirmed via https://circa.cs.ualberta.ca/index.php/CIRCA:10_Usability_Heuristics_-_Nielson,_Jakob).
- Jobs-to-be-done: write each persona's need as "When I ___, I want to ___, so I can ___". Use it as the one-line framing per persona.

### 5-user testing
- The Nielsen/Landauer model: ~31% of problems per user, ~85% by five users, diminishing returns after. Secondary: https://monolytics.app/blog/how-to-test-usability-usability-testing-with-5-users/ . Caveats from the same results: it is for qualitative problem-finding on a focused flow, not for quantitative benchmarks or comparing multiple personas, and repeated rounds matter. We should say exactly that on stage.

### SUS
- Raw SUS 68 = 50th percentile ("average", C on the curved scale); ~75 = 73rd percentile; >80.3 = A (top 10%). SUS is not a percentage; report percentile. Source: https://measuringu.com/interpret-sus-score/ and https://www.measuringu.com/sus/ . Different grade scales exist (Bangor's is harsher), so name which one you use.
- With n=5 a SUS is a rough indicator only; present with the range, not as a benchmark.

### WCAG 2.2
- Adds 9 criteria; 4 at AA: 2.4.11 Focus Not Obscured (Minimum), 2.5.7 Dragging Movements, 2.5.8 Target Size (Minimum, 24x24 CSS px), 3.3.8 Accessible Authentication (Minimum). Level A: 3.2.6 Consistent Help, 3.3.7 Redundant Entry. 4.1.1 Parsing removed. Sources: https://dequeuniversity.com/resources/wcag-2.2 , https://accessibleweb.com/wcag/whats-new-in-wcag-2-2/ (secondary; primary is https://www.w3.org/TR/WCAG22/ , check wording before quoting).
- Reflow 1.4.10: no two-dimensional scroll at 320 CSS px wide. Resize text 1.4.4: usable at 200%. Sources: https://dequeuniversity.com/resources/wcag2.1/1.4.10-reflow , https://www.kent.edu/node/957535 . Our 390px no-sideways-scroll test is close but NOT the same as 320px: add a 320px case.
- Automation limits: Deque claims axe finds ~57% of issues by volume (vendor study, 2,000+ audits) https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues . So on stage never say "axe clean = accessible"; say "automated checks plus manual keyboard and screen-reader passes".

### Singapore relevance
- Home language 2020 census (residents 5+): English 48.3%, Mandarin 29.9%, Malay 9.2%, Tamil 2.5% (Tamil figure from jumbled extraction; verify) . Source: https://www.singstat.gov.sg/-/media/files/visualising_data/infographics/c2020/c2020-literacy-homelanguage.pdf . Four official languages: English, Mandarin, Malay, Tamil.
- Government Digital Service Standards are being refreshed with WCAG-based accessibility guidance and encouraged testing with persons with disabilities: https://www.mddi.gov.sg/newsroom/designing-for-digital-accessibility-and-inclusion/ and https://www.tech.gov.sg/media/designing-for-digital-accessibility-and-inclusion-factsheet/ . GovTech's Oobee scanner is named there as a checking tool. Secondary claims of a WCAG 2.1 AA by 2030 government target and "no private-sector accessibility law" were NOT verified in primary sources: do not say them on stage.

### Trust UI patterns from enterprise AI
- Glean docs: inline citation markers plus a "View sources" list; hover preview; click opens the original in its native app, subject to the user's own access; citations never grant new access; deep-linked citations jump to and highlight the supporting passage. Primary: https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations and https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/deep-linked-citations
- Perplexity: direct answer first, numbered inline citations, expandable source list, suggested follow-ups; weaknesses reported (uneven source quality, dense mobile). Secondary: https://www.925studios.co/blog/perplexity-design-breakdown
- Microsoft 365 Copilot: inline citation chips that open the source; "deep citations" to the specific part of a file on roadmap; users report citations that do not support the sentence. Secondary: https://mc.merill.net/message/RM523223 , https://learn.microsoft.com/en-us/answers/a/12602302
- A May 2026 benchmark (secondary, via https://watts.it.com/modules/grounded-answers-need-a-source-check) reports that a resolving link does not prove support: only 39-77% of cited sources supported the attached sentence. Opportunity: our "cited passage highlighted beside the claim" and "what I could not show you" lines beat link-only citations.

---------------------------------------------------------------------
# CRITERION 1: HUMAN-CENTERED DESIGN ("how well designed for real users")

## 1.1 What judges look for (sourced)
No hackathon-specific rubric text was found for this event; the following is inferred from the frameworks above and standard judging practice (treat as informed expectation, not quotation).
1. A named, specific user with a real job and pain (discover/define), not "employees". (Double Diamond diamond 1; JTBD.)
2. Evidence that real people shaped the product: quotes, observed behaviour, a test with numbers (Develop/Deliver).
3. Fit to the user's context: non-technical staff, time pressure (on-call), restricted access (contractor), regulation (MLRO). (IDEO desirability.)
4. Trade-offs made visibly for the user (e.g. USER mode simple vs DEVELOPER mode deep). (Nielsen: flexibility and efficiency; minimalist design.)
5. Trust and error handling for real use: what happens when access is denied or the answer is unsure. (Nielsen: error recovery, visibility of status.)
6. Honesty about limits and next iteration. (Repeated testing is the NN/g norm.)

## 1.2 What winners did (pattern, not claims about specific teams)
We found no verifiable named winner write-ups in this search, so this is the pattern we recommend, not a citation of winners:
- Open with one person and one moment ("Priya, 2 a.m., an incident"), not a feature list.
- Show before/after of ONE task with a stopwatch number from real testers.
- Put 1-2 verbatim tester quotes on a slide.
- State n, method and limits in one line.
- Make the demo path the same as the persona's job.

## 1.3 Our concrete deliverables

| # | Artefact (file) | Outline | Where in 5-min demo |
|---|---|---|---|
| H1 | `Tencent/docs/finals/ux/personas_and_journeys.md` + in-app page `/about/people` (DEVELOPER mode) | One card per persona: Jordan, Priya, Ken, Sara, Maya, Leo, Amy, Rachel, IT admin. Each: role, context, JTBD sentence, top pain, "what Walnut does", access level, a 5-step journey (trigger, ask, answer, verify, act) with an emotion line (worried, relieved). Mark clearly which personas are DESIGN PERSONAS (assumption-based) vs TESTED (people who actually tried it). | 0:00-0:30 hook (Priya/Ken), then flash the card grid in 5 s |
| H2 | `Tencent/docs/finals/ux/usability_test_report.md` | 1 page: goal, method (5 hallway users, 4 tasks, think-aloud), tasks, per-task success/time/errors table, SUS per user + mean + percentile, top 5 findings, what we changed (fix log with before/after screenshot), what we did NOT test. | Slide 3 (20 s): "5 people, 4 tasks, N of 20 completed unaided" |
| H3 | `Tencent/docs/finals/ux/test_script_and_consent.md` | Script, tasks, consent line, observer sheet, SUS form (see section 3). | Appendix / Q&A |
| H4 | `Tencent/docs/finals/ux/heuristic_review.md` | Our own Nielsen heuristic audit: each of 10 heuristics, one screenshot, one finding, status. Label as self-review (lower evidential weight than user tests). | Appendix |
| H5 | Fix log: `Tencent/docs/finals/ux/changes_from_testing.md` | "Tester said X, we changed Y, commit/screenshot". 3 to 5 rows. This is the strongest HCD proof. | One sentence on stage + one before/after screenshot |
| H6 | In-app: persona switcher ("View as Ken") | Demo-friendly toggle already implied by personas; shows the SAME question giving different cited answers per permission. | 1:30-3:00 core demo |
| H7 | In-app: trust answer card | Answer first in plain language; numbered inline chips [1][2]; sources panel with the exact passage highlighted (Glean-style deep link); "Hidden from you: 2 sources you cannot open. Ask Sara/IT for access" button; confidence/"I'm not sure" state; audit-receipt line ("Logged as #id"). | 1:00-2:30 |
| H8 | 60-second interactive tour (DEVELOPER mode) | 6 steps max, skippable, keyboard operable, replayable from Help. Not auto-playing while presenting. | Mention only; run it in Q&A if asked |

Journey map format (use for H1): columns = Trigger / Ask / Read answer / Verify source / Act; rows = user does / thinks-feels / pain today / Walnut helps / metric.

Before/after task time table (H2, fill only with measured values):

| Task | Today (manual: search Confluence+Slack+Drive) | With Walnut | n |
|---|---|---|---|
| T1 Find the on-call runbook for payments timeout | ___ s (measured baseline, or "estimated, not measured") | ___ s | ___ |
| T2 Ask a policy question and cite where it comes from | ___ | ___ | ___ |
| T3 (as Ken) Ask for something you may not access | n/a | success = understands why and what to do | ___ |
| T4 (Maya) find who accessed X yesterday in audit | ___ | ___ | ___ |

If we cannot measure a manual baseline in 2 days, drop the "before" column and report only Walnut times plus success rate. Do not fabricate a baseline.

## 1.4 Proof points to say on stage
- "We designed for nine people, not one 'user'. Ken, a contractor, sees a different answer to the same question than Sara, and he is told why."
- "We tested with N people, on 4 tasks. N of 4N tasks were completed without help. Mean SUS was __ (about the __th percentile on the MeasuringU curve). Five people finds most big problems, not all; we are showing this as problem-finding, not a benchmark."
- "Here is one thing a tester broke, and the fix." (show before/after)
- "Two modes: USER is one box and an answer; DEVELOPER shows the graph, request journey and audit, because Maya and Sara need proof, not simplicity."
- "Every answer shows its sources, and what we could not show you."

## 1.5 Likely judge questions and answers
1. Who exactly is the user? Answer: Employees at a fintech who currently hunt across 4 tools; the sharpest pains are on-call (time), contractor (access boundaries) and compliance (proof). Each has a card with a JTBD line.
2. Did you test with real users? Answer: Honest numbers: n=5 hallway testers (state who they were: e.g. SUTD students / engineers, NOT Aspire staff unless true). Say "not representative of Aspire staff yet; next step is a pilot with 5 Aspire employees".
3. How do you know answers are trustworthy for non-technical staff? Answer: Plain-language answer, inline numbered citations, the cited passage highlighted, and a visible "could not access" notice. Mention that link-only citations are known to be unreliable (benchmark cited above) so we show the passage.
4. Why two modes? Answer: Progressive disclosure: simple default for most staff; depth for people who must verify (Nielsen: flexibility/efficiency, minimalist design).
5. What did testing change? Answer: Read from the fix log (H5). Must be real.
6. Isn't n=5 too small? Answer: For finding usability problems on a focused flow, yes enough to surface the main ones (NN/g model); it is not a statistical benchmark, and we do not claim one.
7. What happens when the AI is wrong? Answer: Source passage beside claim lets the user check; "not sure" state; every step audit-logged; feedback thumbs per answer.

---------------------------------------------------------------------
# CRITERION 2: UX AND ACCESSIBILITY ("overall usability and accessibility")

## 2.1 What judges look for (sourced)
1. Task flow works without help, fast, no dead ends. (Nielsen heuristics: visibility of status, user control, error prevention.) https://www.nngroup.com/articles/ten-usability-heuristics/
2. Conformance evidence against a recognised standard, ideally WCAG 2.2 AA, with honest limits. https://www.w3.org/TR/WCAG22/ ; Singapore public sector is moving to WCAG-based DSS guidance (https://www.mddi.gov.sg/newsroom/designing-for-digital-accessibility-and-inclusion/).
3. Beyond automation: keyboard and screen-reader behaviour shown live (automation catches roughly half by volume; https://www.deque.com/blog/automated-testing-study-identifies-57-percent-of-digital-accessibility-issues).
4. Inclusive in context: Singapore's four languages and readable plain language (https://www.singstat.gov.sg/-/media/files/visualising_data/infographics/c2020/c2020-literacy-homelanguage.pdf).
5. Mobile and zoom resilience (reflow 320px, 200% text). https://dequeuniversity.com/resources/wcag2.1/1.4.10-reflow
6. Polish: performance, empty/error/loading states, consistent design.

## 2.2 What winners did (recommended pattern; no verified named winners found)
- Show one live accessibility moment (keyboard-only the whole ask->answer->source path, 20 s) instead of a badge slide.
- Put a small evidence table on one slide: standard, how checked, result, known gaps.
- Admit gaps ("Tamil is machine-translated UI only, not reviewed by a native speaker") which raises credibility.

## 2.3 Our concrete deliverables

### A. Accessibility statement + evidence table
File: `Tencent/docs/finals/ux/accessibility_statement.md`, mirrored at in-app `/accessibility`.
Outline: commitment; standard targeted (WCAG 2.2 AA); how tested (automated + manual); scope; known limitations; how to report a problem (contact); date.

Evidence table (fill Status honestly; "Evidence" = file/screenshot):

| WCAG 2.2 criterion | What we did | Evidence | Status |
|---|---|---|---|
| 1.1.1 Non-text content | alt text, decorative icons aria-hidden; graph has text alternative table | axe + screenshot | verify |
| 1.3.1 Info and relationships | semantic landmarks, headings, lists; chat as a log region | axe, SR recording | verify |
| 1.4.3 Contrast (min) | AA contrast in light theme (tested). Dark theme/high contrast: check | Playwright test id ___ | light: done; dark: TO CHECK |
| 1.4.4 Resize text 200% | test at 200% browser zoom and text-only zoom | add Playwright test | TO ADD |
| 1.4.10 Reflow 320px | existing 390px test; add 320 | add test | PARTIAL |
| 1.4.11 Non-text contrast | focus ring, input borders, chart colours | manual | verify |
| 1.4.12 Text spacing | inject WCAG text-spacing CSS, check no clipping | add test | TO ADD |
| 1.4.13 Content on hover | citation hover preview dismissible, hoverable, persistent | manual | verify (important: citation chips) |
| 2.1.1 Keyboard | keyboard-only flow tests (done) | test names | done |
| 2.1.2 No keyboard trap | modals/tour/drawers escape | manual | verify |
| 2.4.1 Bypass blocks | skip link | screenshot | verify |
| 2.4.3 Focus order, 2.4.7 Focus visible | verify | axe + manual | verify |
| 2.4.11 Focus Not Obscured (2.2) | sticky header/composer must not cover focused item; use scroll-padding | manual keyboard sweep | verify |
| 2.5.7 Dragging (2.2) | knowledge-graph pan/drag needs a non-drag alternative (buttons/keys) | manual | LIKELY GAP, fix |
| 2.5.8 Target size 24px (2.2) | icon buttons, citation chips >= 24x24 | Playwright bounding-box test | TO ADD |
| 3.2.6 Consistent help (2.2) | help link in same place on every page | manual | verify |
| 3.3.7 Redundant entry (2.2) | do not ask for same info twice | manual | verify |
| 3.3.8 Accessible authentication (2.2) | no cognitive tests in login; allow paste/password manager; demo persona switch is not auth | manual | verify |
| 4.1.2 Name, role, value | Radix primitives provide; custom graph nodes need labels | axe | verify |
| 4.1.3 Status messages | streaming answer announced politely (aria-live polite, not per token) | SR recording | verify |
| 2.3.3 / motion | `prefers-reduced-motion` respected, tour/graph animations off | Playwright emulateMedia | TO ADD |

Rule: only tick "done" for items with a test or recording. Anything unticked goes in "Known limitations".

### B. Evidence screenshots and scores
- `Tencent/submission/accessibility/` (follow the existing screenshot-organisation convention): axe report per page (desktop + 390 + 320), Lighthouse accessibility score for USER home, answer page, DEVELOPER analytics (Chrome DevTools Lighthouse; report the number you actually get, with the Lighthouse version and date). Note: Lighthouse 100 does not mean fully accessible; say so.
- `Tencent/submission/accessibility/keyboard_demo.mp4` (30 s), `screenreader_demo.mp4` (45 s, NVDA on Windows with Chrome; narrate what is announced), `reduced_motion.png`, `forced_colors.png`, `zoom200.png`, `reflow320.png`.
- Optional second scanner: GovTech Oobee (named in the Singapore factsheet above) for a "Singapore-aligned" tick.

### C. In-app accessibility settings panel
Route/component: `/settings/accessibility` (also a header menu button). Controls, all persisted per user, none requiring a reload:
1. Text size (100/125/150/200%) via root font-size, layout tested at each.
2. Theme: light / dark / high contrast (maps to `forced-colors` friendly tokens).
3. Reduce motion (default follows OS `prefers-reduced-motion`; override toggle).
4. Plain-language answers toggle ("Simple" vs "Detailed").
5. Read answer aloud (browser speech synthesis, with `lang` chosen from answer language).
6. Dyslexia-friendly spacing (letter/line spacing preset, not a gimmick font).
7. Show keyboard shortcuts (`?` opens list).
Evidence: a Playwright test that toggles each and asserts a DOM/CSS effect.

### D. Language toggle (English / 简体中文 / Bahasa Melayu / தமிழ்)
- Scope for 2 days (be realistic): UI chrome and tour strings in 4 languages; answers: the model replies in the language of the question. Do NOT claim retrieval across languages is tested unless tested.
- Implementation notes: set `<html lang>` per locale (screen readers pick voice from it) and `lang` on mixed-language spans; load Noto Sans SC and Noto Sans Tamil for glyph coverage (check no tofu squares); avoid fixed-width buttons that clip Tamil's longer text. Source for lang/dir pattern is a secondary guide: https://ecosire.com/blog/internationalization-nextjs-guide ; confirm in next-intl docs https://next-intl.dev .
- File: `Tencent/apps/web/messages/{en,zh-Hans,ms,ta}.json` (adapt to repo layout).
- Honesty line: "Machine-translated, native-speaker review pending" unless a speaker actually reviewed it. Singapore context: the four official languages; Mandarin 29.9% and Malay 9.2% home use, Tamil ~2.5% (verify), per the 2020 census link above. Product is workplace software, so English is expected default; the toggle shows inclusion, not a core requirement. Do not over-claim.

### E. Plain-language cognitive accessibility
- Answer template: 1-sentence answer, then 3 bullets max, then "Where this comes from" with citations. Target reading level about Grade 8 (check with a readability tool, report if used). Avoid jargon without an explanation; expand acronyms on first use (SRE, MLRO).
- "Explain simply" button re-writes the last answer; "Show full detail" expands.
- Errors: say what happened, why, and what to do next ("You do not have access to the Compliance Drive. Ask IT admin or Sara to grant it" with a one-click request).
- Consistent layout, no timed interactions, tour is optional (WCAG 2.2 3.2.6 consistent help and cognitive-load principles).

### F. 60-second interactive tour
- DEVELOPER mode only; 6 steps: ask, answer+sources, hidden-source notice, knowledge graph, request journey, audit/analytics. Next/Back/Skip/Esc; focus is moved into the step card and returned afterwards; not auto-started on stage.

### G. Where each appears in the 5-minute demo
- 0:00-0:30 Hook: persona (Priya on-call) and the pain. (HCD)
- 0:30-2:00 Live demo, USER mode: ask in the single box; answer streams; point to inline [1] chip, highlighted passage, "could not show you 2 sources". (Trust UX)
- 2:00-2:30 Switch to Ken: same question, different answer, plain-language denial with next step. (HCD + trust)
- 2:30-3:15 Accessibility moment: hands off the mouse, keyboard-only the same flow; NVDA reads the answer and the citation; flip language toggle to 简体中文 and the answer and UI change; toggle large text/reduced motion. (20-45 s maximum total; pre-rehearsed; have recording as fallback.)
- 3:15-4:00 DEVELOPER mode: graph, request journey, audit, analytics (Maya/Sara). Tour is one click, mention only.
- 4:00-4:40 Evidence slide: left = test result (n, tasks, SUS, one quote, one fix); right = accessibility table (WCAG 2.2 AA, axe, 38 browser tests, honest gaps).
- 4:40-5:00 Close: "simple for Priya, provable for Maya".
Q&A 1 min: answers below.

## 2.4 Proof points to say on stage
- "38 automated browser tests, including axe checks at desktop and phone widths, AA contrast in light theme, keyboard-only flows and no sideways scroll at 390 pixels." (true as given)
- "Automated tools find about half of issues, so we also did keyboard and screen-reader passes; here is the NVDA recording." (cite Deque 57% as vendor figure only if asked)
- "We target WCAG 2.2 AA and list known gaps: [dark-theme contrast, Tamil review, graph drag alternative]" (only state the ones that remain true)
- "Answers are written for non-experts: one sentence first, sources underneath."
- "Four official-language UI, because Singapore workplaces are multilingual." (only if shipped)
- "Respects reduced motion and large text."

## 2.5 Likely judge questions and answers
1. Is it WCAG compliant? Answer: "We target WCAG 2.2 AA. We tested X criteria with automated and manual checks; the evidence table lists what is verified and what is not. Full conformance requires an independent audit with disabled users, which is our next step." Never claim full compliance.
2. Did you test with a screen reader / disabled users? Answer: NVDA + Chrome self-test, recorded. If no disabled users were involved say so plainly and name it as next step (Singapore guidance also encourages testing with persons with disabilities: https://www.tech.gov.sg/media/designing-for-digital-accessibility-and-inclusion-factsheet/).
3. How do you handle the knowledge graph for blind users? Answer: Table/outline alternative with the same nodes and relationships, keyboard navigable; drag not required (WCAG 2.5.7). Only claim if built; otherwise "graph is a developer-mode visual aid; the same facts are in the request-journey list".
4. Why those languages? Answer: Singapore's four official languages; Mandarin and Malay are significant home languages (census link). UI is translated; model answers in the asker's language; native review pending.
5. How do you stop users over-trusting the AI? Answer: Passage-level citations, hidden-source notice, "not sure" state, audit trail, feedback. Link-only citations are often not supportive (cited benchmark), so we show the passage.
6. What would you do with another month? Answer: Pilot with 5 Aspire employees including a screen-reader user, a native-speaker review, independent audit.
7. Performance on slow networks/phones? Answer: Only claim what is measured (Lighthouse performance number, streaming first-token time).

---------------------------------------------------------------------
# 3. Rapid user validation in 2 days (protocol)

## 3.1 Plan (about 6 hours total)
- Day 1 morning: write tasks, SUS form, consent line (1 h). Recruit 5-6 people from hallway/Discord/classmates; at least 1 non-technical, 1 on a phone, and if available 1 screen-reader or keyboard-only user (invite, do not pressure). Free to fix ahead: run 1 pilot on a teammate to debug the script.
- Day 1 afternoon: 5 sessions x 20 min = ~2 h (run each: brief, tasks, SUS, 2 questions).
- Day 1 evening: triage findings (severity 0-3), fix the top 3 (2 h).
- Day 2 morning: re-test top fixes with 2 of the same/new people (1 h). Day 2 afternoon: write the report (1 h).

## 3.2 Session script (20 min)
1. Consent (read aloud): "We are testing the product, not you. We will take notes and, with your OK, a screen recording. You can stop any time. Nothing identifies you in our report." Get a yes.
2. Warm-up (1 min): "What do you do? How do you find company information today?"
3. Context card (30 s): "You are [persona]. Your manager sent you a question."
4. Tasks, think-aloud, observer silent (don't help; at 2 min of being stuck, give a hint and log it as 'assisted'):
   - T1 Ask where the payments-timeout runbook is. Success: opens the cited source.
   - T2 Decide whether you can trust the answer: "How do you know this is right?" Success: points to citation/passage.
   - T3 (as Ken) Ask for a restricted document. Success: can explain why you cannot see it and what to do.
   - T4 Change text size or language through Settings. Success: completes in under 60 s.
   - Optional T5 (DEVELOPER mode, for Maya persona): find who looked at a document.
5. SUS (2 min), then 2 open questions: "What was most confusing? What would make you use this on Monday?"
6. Observer sheet per task: success (Y / Assisted / N), seconds, errors, quote.

## 3.3 SUS questionnaire (standard 10 items; 1-5 Strongly disagree to Strongly agree)
Use the standard wording (Brooke 1996), "system" can be replaced with "product":
1. I think that I would like to use this system frequently.
2. I found the system unnecessarily complex.
3. I thought the system was easy to use.
4. I think that I would need the support of a technical person to be able to use this system.
5. I found the various functions in this system were well integrated.
6. I thought there was too much inconsistency in this system.
7. I would imagine that most people would learn to use this system very quickly.
8. I found the system very cumbersome to use.
9. I felt very confident using the system.
10. I needed to learn a lot of things before I could get going with this system.
Scoring: odd items = score - 1; even items = 5 - score; sum x 2.5 = 0-100. Interpretation and percentile: https://measuringu.com/interpret-sus-score/ . Add 1 single-item trust question (1-5): "I trust the answer enough to act on it", plus Single Ease Question per task (1-7).

## 3.4 Metrics to report
- Task success: unaided / assisted / failed, per task.
- Time on task (median, not mean, with n=5).
- Errors and hints count.
- SUS: each user score, mean, min-max, percentile on MeasuringU curve.
- Trust item and SEQ.
- Top 5 problems with severity and the fix.

## 3.5 Presenting results honestly
- State n, who they were, where recruited, and that they were not Aspire staff (if so).
- Say "problem-finding, not a benchmark"; show range with SUS (n=5 gives wide uncertainty).
- Show failures and the fixes made; one slide line "What did not work".
- Do not use words like "statistically significant". Do not report a percentage of users liking it unless counted.
- Keep the raw sheet (anonymised) in `Tencent/docs/finals/ux/raw/` so a judge can ask to see it.
- If a number was not measured, write "not measured". Never invent quotes; use verbatim or omit.
- If a re-test happened, report before and after SUS or success counts for those tasks.

---------------------------------------------------------------------
# 4. Concrete UI patterns for trustworthy answers (apply to H7)

| Pattern | Seen in | Our version |
|---|---|---|
| Answer first, short | Perplexity | One-sentence answer, 3 bullets max |
| Numbered inline citations | Perplexity, Glean, Copilot chips | [1] chips >= 24px, focusable, Enter opens source panel, hover preview is dismissible and hoverable (WCAG 1.4.13) |
| Deep-link / highlighted supporting passage | Glean deep-linked citations | Side panel shows exact passage highlighted; "Open in Confluence" button |
| Click-through respects user access | Glean ("citations never grant new access") | Same, and we show hidden sources count and the reason |
| Source list ("View sources") | Glean, Perplexity | Collapsible, keyboard operable, lists app icon + title + last updated |
| Follow-up suggestions | Perplexity | 2-3 chips, role-aware |
| Visible limits | gap in all three per sources | "I could not use 2 sources you cannot open" and "I am not sure" state |
| Audit receipt | ours | "Logged as #id" in user mode, full trail in DEVELOPER mode |
| Feedback | common | Thumbs + "this does not match the source" button |
| Freshness | common | "Source updated 3 days ago" badge, stale warning |

---------------------------------------------------------------------
# 5. Priority build list for the next 2 days (by value per hour)
1. Real 5-user test + SUS + fix log (H2, H3, H5) - biggest HCD proof.
2. Trust answer card details (H7): highlighted passage + hidden-sources notice.
3. Add Playwright tests: 320px reflow, 200% zoom, text-spacing, target size 24px, reduced-motion, forced-colors, hover-content dismissible. Update evidence table.
4. Accessibility settings panel (C) with 4 controls minimum: text size, contrast/theme, reduce motion, simple answers.
5. Keyboard and NVDA recordings (30-45 s each).
6. Language toggle with 4 UI languages; mark review status honestly.
7. Accessibility statement page + evidence table.
8. Persona/journey page.
Cut first if short of time: dyslexia preset, read-aloud, heuristic review doc.

---------------------------------------------------------------------
# 6. Risks and honesty guardrails
- Do not say "fully accessible" or "WCAG compliant"; say "targets WCAG 2.2 AA, evidence table lists verified items".
- Do not publish placeholders: every ___ above must be real or removed before the deck.
- The knowledge-graph drag (WCAG 2.5.7) and focus-obscured by sticky composer (2.4.11) are the two likeliest WCAG 2.2 failures: check first.
- Translation: unreviewed machine translations of financial/permission wording can mislead; label them.
- Unverified claims found in research: Tamil 2.5% figure, Singapore 2030 WCAG 2.1 target, Microsoft 365 Copilot deep-citation GA date. Do not state on stage without checking primary sources.
- Glean docs are vendor documentation; claims about their behaviour are what they state, not independently tested.
