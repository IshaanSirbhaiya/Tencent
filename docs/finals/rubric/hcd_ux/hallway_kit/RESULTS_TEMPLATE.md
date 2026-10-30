# Sending the notes back, and how they become the FIX_LOG and the `[TESTER]` line

Only from real notes. A cell not measured stays "not measured". Nothing is invented, softened, or paraphrased into a quote.

## 1. What to send (Saturday evening, within 30 minutes of the last session)

Send to Claude, in one private place:
1. A photo or scan of every filled [NOTES_FORM.md](NOTES_FORM.md). Use U1 to U5 only; no real names in file names.
2. The screen recordings, only for testers who said yes.
3. This message, filled in:

```
HALLWAY TEST, Sat 31 Oct, notes attached
Testers run: n = __ (U1..U_)    Planned: 5
Recruited from: ______   Not Aspire staff: yes / no (be exact)
Mix: non-technical __ ; phone __ ; assistive tech __ (say "none" if none)
Deviations from the kit (changed questions, skipped tasks, dropouts): ______
Anything that looked like a real leak: none / (describe; tell the founder at once)
The things you saw most often (your view, labelled as opinion): ______
```

If a SUS or SEQ was not collected, say so; do not estimate.

## 2. How the notes map

| Notes form | Goes to |
|---|---|
| Success, outcome, time, SEQ per task | [USABILITY_REPORT_TEMPLATE.md](../USABILITY_REPORT_TEMPLATE.md): counts (unaided / assisted / failed), **median** time, SEQ |
| SUS items, trust | The same report: each score, mean, min to max; say n is small |
| Quote, issue, severity (2 and 3 first) | One row in [FIX_LOG.md](../FIX_LOG.md) per change |
| "Before" box, two open questions | The report, word for word or not at all |
| Hints, errors | The report (counts); a FIX_LOG row if they point at a change |

| FIX_LOG column | Filled from |
|---|---|
| Tester and task | Tester id and card number (for example "U3, Task 4") |
| Tester said / did | The **Quote** cell verbatim, or the **Issue** cell worded "observed: ..." |
| Severity | The observer's number |
| What we changed | Written after the fix, by whoever made it |
| Before / After | Screenshots we take (no screenshot, no row) |
| Commit or file | The real commit or file |
| Re-tested? | Only if two more people did the task |
| Status | fixed, or deferred with a reason |

FIX_LOG rules apply: no counter of hidden items and no "something is hidden" text (D-07). A severity 2 or 3 problem not fixed by the cut line (Sat 31 Oct 22:00) stays in the table as "deferred" with a reason.

## 3. The `[TESTER]` line and `fixlog.json`

1. Pick **one** real, fixed FIX_LOG row.
2. Write it in one sentence, 20 words at most, using the tester's own quote or observed behaviour plus the change. Shape only (not a result): `Tester 2 said "<exact words>"; we changed <what>.`
3. Put that sentence in the FIX_LOG "Stage line" field and in `databridge/apps/internal_brain_web/public/finals/fixlog.json` under `tester_lines`.
4. If there is no real, fixed row by the cut line, leave `tester_lines` as `[]` and **delete** the `[TESTER]` sentence from the run sheet. Do not soften it.

## 4. What we must not publish

No "statistically significant". No "percent of users liked it" unless counted. No persona comparison. No manual-search baseline unless it was timed. Always say: "n = __, problem-finding on one focused flow, not a benchmark".
