# Hallway test protocol: five users, two hours, Saturday 31 Oct

Decision D-23: a hallway test with real people (the user and team recruit them) on Saturday. **We publish the real numbers only.** Nothing in this file is a result. Every blank is marked TO FILL.

What this test is: **problem-finding on one focused flow**. With about five users it can surface the main usability problems (the Nielsen and Landauer model: roughly 85% of problems by five users, with diminishing returns after; a secondary summary, not a primary citation). It is **not** a statistical benchmark, and it cannot compare personas. Say this out loud, in this form, whenever the numbers are shown.

## 1. Schedule (two hours total)

| Slot | Minutes | What |
|---|---|---|
| Setup | 10 | Laptop on the normal theme (not Stage), fresh login, screen recording ready, observer sheets printed |
| Sessions 1 to 5 | 5 x 20 = 100 | Brief, tasks, SUS, two questions |
| Buffer and notes | 10 | Gaps between sessions, tidy notes |

If a session overruns, shorten the next warm-up. Do not skip the SUS. If only four people turn up, run four and say "n = 4".

Roles: facilitator TO FILL (suggestion: Ishaan). Silent observer and note-taker TO FILL (suggestion: Aaruni). Clock and sheets TO FILL (suggestion: Mehul). Dev keeps the laptop working and does not facilitate, because he built it.

## 2. Who to recruit

Target five people. Aim for:
- at least one non-technical person;
- at least one using a phone;
- if someone offers, a keyboard-only or screen-reader user (invite; never pressure).

Record for each: an anonymous id (U1 to U5), a one-line background, and where they were recruited. State plainly in the report that they are **not Aspire staff** unless that is true.

Do not promise anything you cannot deliver. Do not use anyone who has already seen the product for more than a minute.

## 3. Consent line (read aloud, get a yes)

> "We are testing the product, not you. There are no wrong answers. We will take notes and, only if you say yes, record the screen. You can stop at any time. Nothing in our report will identify you; we will call you tester 1 to tester 5. Is that OK?"

Record on the sheet: consent to notes (Y / N), consent to screen recording (Y / N). If N, do not record. Do not record faces or voices unless the tester agreed to exactly that.

## 4. Session script (20 minutes)

1. **Consent** (1 min): read the line above.
2. **Warm-up** (1 min): "What do you do? How do you find company information today?" Write down the answer in their words. This is the only "before" evidence we collect.
3. **Context card** (30 s): "You work at a company. Your manager sent you a question. You will use this tool to answer it."
4. **Tasks** (about 12 min). Think aloud. The observer is silent. If the tester is stuck for 2 minutes, give one hint and mark the task **Assisted**.
5. **SUS** (2 min), then the trust item.
6. **Two open questions** (2 min): "What was most confusing?" and "What would make you use this on Monday?" Write the answers word for word.
7. **Thanks.** Do not argue with feedback.

## 5. Tasks

Success criteria are written before the session so we cannot move them afterwards. Persona labels are from `personas.ts`.

| # | Task (as read to the tester) | Persona | Success means | Route |
|---|---|---|---|---|
| T1 | "As Amy, ask: Why has Velquarry Trading's account been restricted, and what should I tell the customer? Then tell me what you would say to the customer." | Amy | Gets an answer and says a customer-safe sentence from it, unaided | `/` |
| T2 | "How do you know this answer is right? Show me." | Amy | Opens a citation and points at the passage that supports the claim | `/`, then the sources sheet |
| T3 | "Now you are Ken. Ask about Bayfront Spice Traders. Tell me in your own words why there was no answer, and what you would do next." | Ken | States that it is outside Ken's access and names a sensible next step. Does **not** say the screen is hiding something | `/` |
| T4 | "Change the interface to Chinese (简体中文) and back." | any | Completes in under 60 seconds, unaided | header language control |
| T5 (optional) | "As Maya, find this request and tell me what Verify shows." | Maya | Opens the request journey; says what the Verify result means | `/dev/journey/<req>`, `/dev/audit` |

Run T1 to T4 in order. Run T5 only if the session is under time. Do not drop T3; it tests the product's core promise.

Rules for the observer:
- Never explain the interface during a task.
- Never say "good" or "right".
- Note exact words.
- If the product shows an error, write down the text and the time.

## 6. SUS form (standard ten items, 1 to 5)

Standard wording after Brooke (1996), with "system" replaced by "product". Scale: 1 Strongly disagree to 5 Strongly agree.

| # | Statement | Score (1 to 5) |
|---|---|---|
| 1 | I think that I would like to use this product frequently. | TO FILL |
| 2 | I found the product unnecessarily complex. | TO FILL |
| 3 | I thought the product was easy to use. | TO FILL |
| 4 | I think that I would need the support of a technical person to be able to use this product. | TO FILL |
| 5 | I found the various functions in this product were well integrated. | TO FILL |
| 6 | I thought there was too much inconsistency in this product. | TO FILL |
| 7 | I would imagine that most people would learn to use this product very quickly. | TO FILL |
| 8 | I found the product very cumbersome to use. | TO FILL |
| 9 | I felt very confident using the product. | TO FILL |
| 10 | I needed to learn a lot of things before I could get going with this product. | TO FILL |

Scoring: odd items, score minus 1; even items, 5 minus score; add the ten, times 2.5. Result 0 to 100. SUS is not a percentage. Report the percentile on the MeasuringU curve (a raw 68 is about the 50th percentile) and name the curve you used. With n = 5, report the **range** as well as the mean.

Add two single items:
- Trust (1 to 5): "I trust the answer enough to act on it." TO FILL.
- Single Ease Question per task (1 to 7): "Overall, how easy or difficult was this task?" TO FILL.

## 7. Observer sheet (one per tester per task)

| Field | Value (TO FILL during the session) |
|---|---|
| Tester id | U_ |
| Task | T_ |
| Outcome | Unaided / Assisted / Failed |
| Seconds to finish | |
| Errors and hints (count; what) | |
| Verbatim quote | "" |
| Severity of any problem (0 to 3) | |
| SEQ (1 to 7) | |

Severity: 0 not a problem; 1 cosmetic; 2 slows the user; 3 blocks the task.

## 8. Metrics we will report

- Task success per task: unaided / assisted / failed. Counts only.
- Time on task: the **median**, not the mean.
- Errors and hints.
- SUS: each tester's score, the mean, the min to max, the percentile.
- Trust item and SEQ.
- Top five problems, each with severity and the fix (see `FIX_LOG.md`).
- What we did **not** test.

Rules:
- If a number was not measured, write "not measured".
- No "statistically significant". No "percent of users liked it" unless counted.
- Quote testers word for word, or not at all.
- Do not report a manual-search baseline unless it was actually timed.

## 9. After the sessions (Saturday evening)

1. Within 30 minutes, put all sheets in one private folder and type the numbers into `USABILITY_REPORT_TEMPLATE.md`.
2. Triage by severity. Fix the top three that are safe before the cut line (Sat 31 Oct 22:00).
3. Log each change in `FIX_LOG.md` with a before and after screenshot.
4. If time allows on Sunday, re-test the fixed tasks with two people and report both numbers.
5. Keep anonymised raw sheets under `Tencent/submission/` (organised, per the screenshot convention) so a judge can ask to see them.

## 10. Hard rules for this test

- Never show a tester anything that reveals the existence of hidden content, and never add UI that does (D-07).
- English and 简体中文 only (D-22). Do not ask testers to try other languages.
- If a tester finds a real leak, stop the session and tell the user at once.
