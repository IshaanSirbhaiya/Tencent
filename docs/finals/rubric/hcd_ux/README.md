# Human-centered design and UX: index

Owner: team Walnut (Ishaan, Dev, Mehul, Aaruni). Research: `rubric/research/B_hcd_ux_accessibility.md`. Decisions that bind this folder: D-07 (no hidden counts), D-22 (English and 简体中文 only), D-23 (real hallway test, real numbers only).

| File | What it is | State |
|---|---|---|
| `PERSONAS_AND_JOURNEYS.md` | Nine design personas and three journey maps (Amy, Maya, Ken) | Written; personas are assumption-based |
| `HALLWAY_TEST_PROTOCOL.md` | Five users, two hours, Saturday: tasks, consent line, SUS, metrics | Ready to run |
| `USABILITY_REPORT_TEMPLATE.md` | Report with blanks | EMPTY until the test is done |
| `FIX_LOG.md` | "Tester said X, we changed Y" | EMPTY until the test is done |
| `ACCESSIBILITY_STATEMENT.md` | WCAG 2.2 AA evidence table | Evidence "pending capture" |

## Order of work

1. Sat 31 Oct: run the hallway test (2 h). Fill the report and the fix log the same evening.
2. Before the cut line (Sat 22:00): fix the top three safe problems, screenshot before and after.
3. Coordinator: run the accessibility passes, paste evidence paths into the table.
4. Mon 2 Nov: strip every unfilled TO FILL from anything shown to judges, or say "not measured".

## Stage proof points (use only what is true on the day)

- "We designed for nine people, not one user. Ken, a contractor, asks about a customer outside his scope and gets the same answer as for a company that does not exist."
- "We tested with TO FILL people on TO FILL tasks; TO FILL completed without help. Mean SUS TO FILL, range TO FILL to TO FILL. That finds problems; it is not a benchmark." (Only after the report is filled.)
- "One tester TO FILL; we fixed it." (Only if a real FIX_LOG row exists.)
- "Two modes: User is one box and an answer; Developer is for people who must prove it."
- "We target WCAG 2.2 AA: automated plus keyboard and screen-reader passes. Gaps are listed."
- "The interface is in English and 简体中文." (Only if shipped.)

## Q&A

| Question | Answer (about 15 s) |
|---|---|
| Who exactly is the user? | Staff at a regulated fintech who hunt across four tools. The sharpest cases: the account manager who must not say too much, the contractor with narrow scope, the compliance officer who needs proof. |
| Did you test with real users? | "Yes: TO FILL people, TO FILL. Not Aspire staff. It finds problems; it is not a benchmark. Next: a pilot." Say this only if true; otherwise "not yet". |
| What did testing change? | Read one row from `FIX_LOG.md`. If empty, say so. |
| Isn't five too few? | For finding the main problems on one flow it is usual; we do not claim statistics. |
| Is it WCAG compliant? | "We target WCAG 2.2 AA. Automated plus keyboard and screen-reader passes; the table lists what is covered and what is not. We have not had an independent audit." |
| Did you test with disabled users? | Answer from the usability report. If none: "No, not yet; it is the next step." |
| How do you stop over-trust in the AI? | The cited passage opens beside the claim, checked live; unsupported output is replaced by a quote from the sources; every answer is logged. |
| Why do restricted and made-up look the same? | Any difference would tell the asker that something exists. One response shape. |
| Why only two languages? | A bad translation is worse than none (D-22). Native-speaker review: TO FILL. |
| Why two modes? | Simple by default; depth for people who must verify. |
