# Leave-behind: one A4 page (content)

Light theme, large type, Walnut logo and colours. Print 50 and a QR stand. Every number must map to a file or test, or be marked "estimate". Fill TO FILL items only from real results; delete a line rather than guess.

## Page content, in order

**1. Title**
Walnut DataBridge. Internal Brain. *Answers you can prove.*

**2. One sentence**
A permission-aware answer engine for a regulated company's Confluence, Jira, Slack and Drive: it answers from what the asker can open, cites the passage, and keeps a log that shows if it was changed.

**3. Three screenshots** (captioned)
- Amy and Rachel: the same question, two different cited answers.
- Ken: a refusal for a customer in scope of nobody and the same refusal for a company that does not exist.
- The audit chain: intact, then broken at an exact entry.
(Paths: `Tencent/submission/` once captured: TO FILL.)

**4. Three proof numbers** (each with its source)
- 7,374 adversarial requests checked, 0 leaks. `databridge/evals/internal_brain/RESULTS.md`
- 400 paired questions (restricted vs non-existent), 0 differences. Same file.
- Usability: TO FILL (only from the filled report: n, tasks, mean SUS and range, or "not yet reported").
Note on the page: the real-model (Hy3) run was pending when the evals were generated; the numbers come from a capturing test model and the quoting path. Keep this note unless the evals are re-run with Hy3.

**5. The Tencent stack line**
Hy3 through TokenHub, WorkBuddy (MCP) as a second front door, CodeBuddy to build and review, a one-command Lighthouse deploy kit for Tencent Cloud (no live instance). State only what is live on 3 Nov: TO FILL.

**6. Value (estimate)**
About US$1.3M a year for a 600-person fintech. Inputs: 600 staff, 90 minutes saved per person per week, US$31 an hour, 46 weeks. Break-even about 5 to 7 minutes per person per week, depending on price. Estimate, not a measurement; a pilot would measure it. Confirm all numbers against `rubric/research/A_impact_feasibility.md` and the submission.

**7. Honest limits** (three lines)
- Platforms are mocks with real API shapes; not yet run against a real tenant.
- Sign-in is a demo login standing in for SSO.
- The log detects tampering by anyone without its key; beyond the last off-host published head a key holder could rewrite it.

**8. Try it**
QR to `https://github.com/IshaanSirbhaiya/Tencent` with the short URL printed beneath. Pointer: `INTERNAL_BRAIN.md`.

**9. Team and ask**
Ishaan, Dev, Mehul, Aaruni. "We want one pilot team at one regulated company."

## Rules

- Nothing on the page that a test or file does not prove (`CLAUDE.md` rule 8).
- Never show hidden counts or "something is hidden" in any screenshot (D-07).
- English only on the printed page (the 简体中文 interface may appear in a screenshot).
- Accessibility: plain language, large type, the URL in text as well as the QR.
