# Impact & Relevance and Feasibility: deliverables

Walnut DataBridge · Internal Brain · **Answers you can prove.**
Research behind these files: [../research/A_impact_feasibility.md](../research/A_impact_feasibility.md).
Every number is linked to a source or a repo file; estimates are labelled.

## Index

| File | What it is | Used where |
|---|---|---|
| [IMPACT_SHEET.md](IMPACT_SHEET.md) | One page: the person, the problem in three sourced numbers, why now, Aspire relevance, proof, limits | Hook numbers; Q&A slide |
| [ROI_MODEL.md](ROI_MODEL.md) | Formula, inputs, 7-minute break-even, low/base/high, sensitivity | Business beat |
| [value_calculator.spec.json](value_calculator.spec.json) | Spec for the in-app `/value` calculator: inputs, defaults, formulas, presets, test vectors | UI agent builds it |
| [COST_PER_ANSWER.md](COST_PER_ANSWER.md) | Hy3 TokenHub list prices and cost per answer (estimate until the live run) | Business beat, one number |
| [SCALE_ARCHITECTURE.md](SCALE_ARCHITECTURE.md) | Slack's API terms first (a fit with Gate 2), three stages, mermaid diagram, connector path | Tencent stack beat; Q&A |
| [PILOT_PLAN.md](PILOT_PLAN.md) | Ten weeks with one Aspire team, shadow mode first, stop rules | One line on stage; Q&A |
| [RISK_REGISTER.md](RISK_REGISTER.md) | 14 risks, each with a mitigation and its test or file | Q&A |
| [PRICING_GTM.md](PRICING_GTM.md) | Price, unit economics, go-to-market (all estimates) | Q&A |
| [CERTIFICATION_PATH.md](CERTIFICATION_PATH.md) | From Tencent Cloud's MTCS L3/OSPAR attestations (to confirm) to Cyber Trust and SOC 2 | Q&A |
| [EVIDENCE_NOTES.md](EVIDENCE_NOTES.md) | How to read 0/7,374 (0.08% per path vs 0.04% pooled), all measured numbers, what is not measured | Before anyone quotes a number |

## Stage one-liners

**Impact & Relevance**

1. "Half of office workers say they can't find what they need. That's Gartner, 2023."
2. "In January, Microsoft's Copilot summarised emails marked Confidential. The label was there; the AI read them anyway."
3. "MAS issued its AI risk guidelines on 7 October. From October 2027 every FI needs an AI inventory and controls."
4. "Same question, two people, two different answers, and each one is correct for that person."
5. "It pays for itself if each person saves seven minutes a week. APQC measured nearly three hours a week spent searching."
6. "Aspire already promises customers its AI only sees what the user may see. We make that promise for Aspire's own staff, and we measured it: zero leaks in 7,374 attack requests."

**Feasibility**

1. "It runs today: 1,274 backend tests, and a leak harness that re-runs on any company's permissions."
2. "An answer costs under a tenth of a US cent on Hy3, at list price. Refusals cost nothing, because they never reach the model."
3. "Slack's 2025 terms bar indexing its data for AI. Its answer is live search as the user. That's already how our Gate 2 works."
4. "The design targets Tencent Cloud Singapore, which holds MTCS Level 3 and OSPAR. A one-command Lighthouse deploy kit is included; we have no live instance. Hy3's open weights would let a bank host the model itself."
5. "Ten-week pilot. Weeks two to four run our leak test on Aspire's real permissions before a single user."
6. "What breaks first at scale is API quota, not the model. We budget Gate 2 calls per source."

## Judge Q&A (short answers)

| Question | Answer |
|---|---|
| Isn't this Glean or Copilot? | They copy permissions into an index or trim inside one tenant. We re-check each passage with the source at answer time, deny on error, and prove it: 0 leaks in 7,374 requests. |
| Who is the user? | The account manager who needs a per-market answer now, the on-call engineer at 3 a.m., and the compliance officer who must prove who saw what. |
| Is US$1.3M real? | It's a projection: 600 × 1.5 h × 46 weeks × US$31. Break-even is 7 minutes a week. The pilot measures the real figure. |
| Why should Aspire care? | Faster answers for staff; readiness for MAS AI-risk rules by Oct 2027; and AspireOS bank clients will ask Aspire these same questions. |
| Have you connected a real Confluence or Slack? | Not yet. The mocks use the real API shapes and permission rules, and every connector fails closed. The pilot re-runs the leak test on real permissions first. |
| What does it cost to run? | About US$0.0007 an answer on Hy3 at list price; under US$100 a month in model spend for 600 people. Tokens are assumed until the live run. |
| Will it scale? | The API tier is stateless. The limit is each source's API quota, so Gate 2 checks only top candidates, bulk-checks Jira and goes federated for Slack. |
| Doesn't Slack forbid indexing? | Yes, since mid-2025. Production Slack uses the Real-time Search API as the user, with no index. Our demo indexes a mock. |
| What if TokenHub is down? | Hy3, then DeepSeek, then an extractive answer from permitted sentences only. The answer says which one wrote it. |
| Does data leave Singapore? | Only the permitted passages for one question go to the model. A bank can self-host Hy3's open weights. TokenHub's data terms: still to verify. |
| Who maintains it? | Narrow scope, safety tests on every change, and a design-partner pilot that funds maintenance. |
| Certifications? | None of our own yet. Tencent's MTCS L3 and OSPAR for hosting; pen test and AI Verify in the pilot; Cyber Trust within six months; then SOC 2. |
| How is it priced? | About US$10 a seat a month (estimate). At that price a person breaks even on five minutes a week. |
| How do you get 0.08% from 0 / 7,374? | It's 0 in 3,687 on each answer path; rule of three gives 3 ÷ 3,687 ≈ 0.08%. Pooling both paths (0.04%) would count each probe twice. |
