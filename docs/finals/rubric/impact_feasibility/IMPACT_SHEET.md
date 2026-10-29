# Impact sheet: Walnut DataBridge

**Answers you can prove.** Internal Brain is the ask product inside Walnut DataBridge. It answers questions
across Confluence, Jira, Slack and Google Drive, using only what the person asking is allowed to open.

Labels: **[primary]** checked on the publisher's own page · **[secondary]** press or analyst coverage ·
**[verify]** single or vendor source, check before saying it on stage · **[estimate]** our own arithmetic.
Research date 30 Oct 2026; sources from `../research/A_impact_feasibility.md` §2.3.

---

## 1. The person

Maya is a compliance officer at a mid-size Singapore fintech, two weeks before a MAS inspection. She needs
answers from four tools, and afterwards she must prove who saw what. Next to her are Amy, an account
manager who needs "what can we offer this customer?" answered for her market, and Ken, a contractor who must
never learn that a restricted report exists. (All three are synthetic personas from the "Company A" world.)

## 2. The problem, in three numbers

| # | Number | Source | Label |
|---|---|---|---|
| 1 | **47%** of digital workers struggle to find the information they need; they use 11 apps on average | [Gartner, 10 May 2023](https://www.gartner.com/en/newsroom/press-releases/2023-05-10-gartner-survey-reveals-47-percent-of-digital-workers-struggle-to-find-the-information-needed-to-effectively-perform-their-jobs) | [secondary]: Gartner's page blocked our fetcher; figures match across outlets |
| 2 | **2.8 hours a week** spent looking for or requesting information | [APQC via BusinessWire, 2021](https://www.businesswire.com/news/home/20211109005449/en) | [secondary], conservative |
| 3 | **Jan 2026:** Microsoft 365 Copilot Chat summarised emails labelled Confidential despite DLP policies (advisory CW1226324) | [BleepingComputer](https://www.bleepingcomputer.com/news/microsoft/microsoft-says-bug-causes-copilot-to-summarize-confidential-emails/) | [secondary] |

Aspire's own brief puts search at about 30% of the week (challenge brief, quoted in
`docs/tencent/SUBMISSION.md` §2). The label existed, and the AI read the email anyway. That is why
regulated firms hesitate to switch AI on.

## 3. Why now

| Rule | What it says | Source |
|---|---|---|
| **MAS Guidelines on AI Risk Management** | Issued **7 Oct 2026**. Apply to all FIs and all AI, including third-party and agentic AI. Sections 3-4 (oversight, inventory, materiality) apply from **7 Oct 2027**; Sections 5-6 (life-cycle controls) by **7 Oct 2028** | [MAS media release](https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions) [primary] |
| MAS TRM Guidelines (2021) | Least privilege, logged access, prompt revocation, protected logs | [TRM PDF](https://www.mas.gov.sg/-/media/MAS/Regulations-and-Financial-Stability/Regulatory-and-Supervisory-Framework/Risk-Management/TRM-Guidelines-18-January-2021.pdf) [primary]; our mapping: `docs/tencent/MAS_TRM_MAPPING.md` |
| PDPA penalties | Up to 10% of annual Singapore turnover (above S$10M) or S$1M, whichever is higher | [PDPC](https://www.pdpc.gov.sg/News-and-Events/Announcements/2022/09/Amendments-to-Enforcement-under-the-Personal-Data-Protection-Act-in-updated-Advisory-Guidelines-and-Guide) [primary] |

## 4. What we change

- **Filter before the model.** Gate 1 searches only what you may open. Gate 2 asks the source system again,
  live, for every passage. Any error means deny.
- **Restricted looks exactly like "does not exist".** Nobody learns that a secret exists.
- **Sealed audit.** Every question and decision is written before the answer, in a hash-chained log that
  shows any edit.

## 5. Measured proof (synthetic estate, mock platforms)

| Measure | Result | Evidence |
|---|---|---|
| Leaks in adversarial requests | **0 / 5,958** (2,979 probes × 2 answer paths) | `databridge/evals/internal_brain/RESULTS.md` |
| "Restricted" vs "does not exist" differences | **0 / 400** | same file |
| Prompt-injection failures | **0** in 976 injection-relevant requests | same file |
| Tamper attacks caught | **6 / 6** | `submission/04_evidence/tamper_demo_output.txt` |
| Automated tests | **639** passing at submission (16 Oct); **645** collected on 30 Oct; **38** browser tests | `databridge/apps/internal_brain/tests/`, `databridge/apps/internal_brain_web/tests/e2e/` |

How to read "0 / 5,958": see [EVIDENCE_NOTES.md](EVIDENCE_NOTES.md) §1.

## 6. Value (projection, not a measurement)

- **Break-even: 7 minutes per person per week** (US$100k deployment, 600 people, US$31/h, 46 weeks).
  That is about 4% of the 2.8 hours APQC measured. [estimate]
- **Base case US$1.3M a year**; range **US$0.44M to US$4.1M**. Formula and inputs: [ROI_MODEL.md](ROI_MODEL.md).
- **Model cost under a tenth of a US cent per answer** on Hy3 (list price; tokens assumed until the live run):
  [COST_PER_ANSWER.md](COST_PER_ANSWER.md).

## 7. Why it matters to Aspire (Aspire's own pages)

| Aspire says | Source | Our link |
|---|---|---|
| Its MCP assistant "can only access information that the authenticated user is already permitted to access" | [Aspire Help Centre, Security & privacy](https://help.aspireapp.com/sg/en/articles/15550809-security-privacy) [primary] | We make the same promise for Aspire's *internal* knowledge, and measure it |
| "Trusted by 50,000+ modern businesses"; MCP listed as new | [aspireapp.com](https://aspireapp.com/) [primary] | Staff answer questions for many SMEs, per market |
| AspireOS (18 Nov 2025): an AI-powered business-banking platform **for banks** | [Aspire blog](https://aspireapp.com/en-sg/blog/aspire-launches-aspireos-its-ai-powered-platform-for-banks) [primary] | Bank clients will ask Aspire the same TRM and AI-risk questions |
| 400+ employees in four countries (dated post) | [Aspire blog](https://aspireapp.com/en-sg/blog/aspire-grows-revenue-3x-yoy-hits-profitability-3-months-after-closing-us-100m-series-c) [primary, dated] | Our 600-person model is a fintech of that size class, not a claim about Aspire's headcount |

All five Aspire scenarios are covered by end-to-end tests (`INTERNAL_BRAIN.md`, "Aspire's five scenarios").

## 8. Limits we state up front

- Synthetic data only ("Company A" is fictional).
- Mock platforms with the real APIs' shapes; not yet run against a live tenant.
- Hy3 through TokenHub is wired in and tested with a capturing test model; the live run is pending
  account activation. All eval numbers above come from the test model and the deterministic path.
