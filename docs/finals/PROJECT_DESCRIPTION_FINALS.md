# Project description for the finals submission (30 Oct 2026)

Every claim below is true for the `final_v1.3` link. Copy everything below the line.

---

**Walnut DataBridge: answers you can prove**

*One connected company brain, cited and access-checked.*

Walnut DataBridge is a company brain for regulated fintechs. Staff ask one question across Confluence,
Jira, Slack and Google Drive. They get a cited answer built only from what they are allowed to open, and
those permissions are checked again, live, at the source before anything is used. Every step goes into a
tamper-evident audit log.

**Who it's for**
- **Account managers:** asked why a customer's account is restricted, they get the approved customer
  script, never the financial-crime case. Hinting at an investigation (tipping-off) is a criminal
  offence in Singapore.
- **Compliance officers:** they see the complete record of who saw what. Verify proves the log was never
  edited, and a sandbox tamper test shows edits are caught.
- **Engineers on call:** one cited answer stitched from Jira, Slack and Confluence.
- **Contractors and new joiners:** useful answers within their scope. When something is restricted, the
  reply is word-for-word identical to the reply for something that doesn't exist.

**What's new since our first submission**
- A User mode for asking and comparing, and a Developer mode with the request journey, a per-person
  knowledge graph, the Walnut evidence engine, analytics, the audit log with Verify and tamper detection,
  a trust centre and a value calculator.
- Act on an answer: draft a reply from the cited sentences only (never sent), or propose a follow-up
  ticket that is created only after the person confirms.
- Deliverables: turn an answer into a table, chart or slide deck where every number is checked against
  its source.
- Live brain growth and file upload, guided tours, English and 简体中文, voice dictation and a presenter
  mode.

**The platform underneath:** connectors to every source, AI agents that build new adapters, a federated
engine, an evidence graph, a grounding wall that ties every value to its source, and a sealed,
hash-chained audit ledger.

**Built with Tencent**
- **CodeBuddy:** nine recorded build sessions, including security reviews that found real bugs.
- **WorkBuddy:** people ask from inside WorkBuddy through our MCP connectors, with the same checks, and
  every question is logged.
- **Miora:** our cover.
- **Hunyuan (Hy3) through TokenHub:** wired in, live once activated.
- **Tencent Cloud:** packaged to deploy on Lighthouse.

**Proof**
- 1,274 backend tests and 185 browser tests pass; CI is green.
- A leak evaluation of 7,374 adversarial requests found 0 leaks; 400 "secret vs made-up" pairs showed 0
  differences; 0 of 1,152 injection attempts succeeded.
- The platforms are mock servers with the real APIs' shapes, and all data is synthetic.

**Code:** https://github.com/IshaanSirbhaiya/Tencent/tree/final_v1.3

---

## Only if the link becomes `final_v1.4` (once verified)

Add to "What's new": **pages beside the chat (just-in-time UI)**: a broader question opens a page with a
table, timeline or next steps, every value checked and cited. Model line: "an AI model writes answers from
the cited sources" (never name a provider; never say Hy3 unless TokenHub is live). Update the test counts
to the final_v1.4 verified run.
