# Update for the organisers (send to Cindy before Fri 30 Oct 23:59)

**Status: READY TO SEND**, as of 30 Oct 2026 (cloud session). Every bullet below is shipped and tested on
`final_v1.3`. Before sending, check one line: if Hy3 has been activated and run live by then, change the
Hy3 line; otherwise send it as written.

---

**To:** Cindy (Tencent Cloud Hackathon team)
**Subject:** Team Walnut (FinTech finalist): updated project link and description

Hi Cindy,

Thank you again for selecting Team Walnut as a FinTech finalist. Here are our updated materials for the
judges:

- **Project link:** https://github.com/IshaanSirbhaiya/Tencent/tree/final_v1.3
  - Start at `INTERNAL_BRAIN.md` (a 10-minute guided walk-through).
  - The evidence for each of the 10 judging criteria is in `docs/finals/rubric/README.md`.
- **Demo video:** https://youtu.be/fr9wqHWsijY (unchanged)
- **Updated description:** below.

**Walnut DataBridge: answers you can prove.**
A company brain for regulated fintechs. Staff ask one question across Confluence, Jira, Slack and
Google Drive, and get a cited answer built only from what they are allowed to open, checked live at
the source. Every step goes into a tamper-evident audit log. "Internal Brain" is the ask product inside
it.

Since submission we have added:
- A **User mode** for staff (ask, compare, history, settings) and a **Developer mode** for engineers and
  compliance, in one app.
- The **request journey**: every step of one answer, its permission checks and its sealed audit records.
- A **permission-aware knowledge graph**: each person sees only what they can open, with two people side
  by side.
- The **Walnut engine view**: the evidence engine's entity graph, time slider and provenance.
- **Compliance analytics** computed from the verified audit log.
- A **sandbox tamper demo**: attacks run on a copy of the audit log and are detected; the real log is
  never touched.
- A **Trust centre** mapping each control to Singapore and international frameworks (MAS, PDPA, AI Verify, OWASP) and to the test that proves it.
- A **presenter mode** and **interactive tours** for both modes.
- An **evidence pack** for every rubric criterion.
- **Act on an answer:** draft a reply built only from the answer's cited sentences (never sent), or
  propose a follow-up ticket that is created only after the person confirms; both are sealed in the audit log.
- **Deliverables from an answer:** a table or chart where every number is checked against its source line,
  with .csv and .pptx download.

Test and eval status (30 Oct, on `final_v1.3`, a clean run in the cloud): 1,274 backend tests pass with
0 failures, and 185 browser end-to-end tests pass with 0 failures; the leak evaluation,
re-run this evening on the final build, found 0 leaks in 7,374 adversarial requests, 0 differences in 400 paired questions and 0 failures in 1,152 injection-relevant requests. Hy3 via TokenHub
is wired in; the live run is pending activation, so today's answers are quoted from the sources with the
same checks. The platforms are mock servers that use the real APIs' shapes, and all data is synthetic.

Best regards,
Ishaan Sirbhaiya, for Team Walnut (Ishaan, Dev, Mehul, Aaruni)
