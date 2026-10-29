# A - Impact & Relevance and Feasibility: research and deliverables for Demo Day (3 Nov 2026)

Research date: 30 Oct 2026. Scope: the two rubric criteria **Impact & Relevance** and **Feasibility**
("realistic and scalable beyond the hackathon"), 10 points each.

How to read the tags:

- **[primary]** checked on the publisher's own page (regulator, vendor docs, company site).
- **[secondary]** from press or law-firm coverage of a primary source we could not open.
- **[verify]** single source, vendor-interested source, or a figure we could not trace to its origin. Do not put it on a slide without checking.
- **[estimate]** our own arithmetic or assumption. Say "estimate" or "projection" out loud.

This builds on `docs/finals/research/05_competition_pitch.md` (run sheet, Aspire facts, past winners) and
`docs/tencent/SUBMISSION.md` (ROI model) and `docs/tencent/MAS_TRM_MAPPING.md`. It does not repeat them.

---

## 0. Summary: what to build before Monday

| # | Artefact | File | Demo time | Criterion |
|---|---|---|---|---|
| 1 | One-page impact sheet with sourced numbers | `docs/finals/deliverables/IMPACT_SHEET.md` (and a 1-slide PNG for Q&A) | 0:00-0:35 hook numbers; Q&A backup | Impact |
| 2 | ROI calculator page in the app | `databridge/apps/internal_brain_web/app/value/page.tsx` (new "Value" route) | 4:05-4:40, about 15 s live | Impact, Feasibility |
| 3 | Cost-per-answer table (Hy3 on TokenHub, measured tokens) | `docs/finals/deliverables/COST_PER_ANSWER.md`, also a panel on the Value page | 4:05-4:40, 5 s | Feasibility |
| 4 | Production architecture and scale path diagram | `docs/finals/deliverables/SCALE_ARCHITECTURE.md` + `docs/tencent/diagrams/scale_path.mmd` | 2:50-3:25 stack slide; Q&A | Feasibility |
| 5 | 10-week pilot plan for Aspire | `docs/finals/deliverables/PILOT_PLAN.md` | 4:30-4:40, one line; Q&A | Feasibility, Impact |
| 6 | Risk register with mitigations | `docs/finals/deliverables/RISK_REGISTER.md` | Q&A only | Feasibility |
| 7 | Pricing, unit economics and go-to-market note | `docs/finals/deliverables/PRICING_GTM.md` | Q&A only | Feasibility |
| 8 | Certification path (one table) | section of `PILOT_PLAN.md` | Q&A only | Feasibility |

In the 5-minute pitch, Impact and Feasibility get about 70 seconds together (the hook plus the 4:05-4:40 value beat).
Everything else is a backup card for the 1-minute Q&A. Judges reward a narrow, finished path that has been proven
over a broad claim (section 1).

---

## 1. How judges score these two criteria (general evidence)

There is no published rubric text for this contest beyond the criterion names (see `05_competition_pitch.md` §1).
At the May 2026 Singapore station, the judges were a Tencent Cloud chief architect, an SMU computing dean and an AISG
representative. They scored human-centred design, AI interaction and feasibility as well as technical execution
([AAP/PR Newswire](https://www.aap.com.au/aapreleases/cision20260513ae58232/), via 05 doc) **[secondary]**.

What published rubrics and judges say:

1. **Impact means value to named, real users.** Okta's Devpost-style rules define "Potential Impact" as the potential value and usefulness to real-world users ([Okta dev forum](https://devforum.okta.com/t/what-are-the-judging-criteria-for-the-hackathon/15842)) **[secondary]**. Imagine Cup asks whether there is a clear target audience and a clear need ([Imagine Cup FAQ](https://imaginecup.microsoft.com/support/faq)) **[secondary]**.
2. **Feasibility means a real team could build, deploy and maintain it.** Recent Imagine Cup criteria list "viability of the solution" and "founder-led validation" (same FAQ). Older Imagine Cup finals weighted business viability at 15% ([Microsoft archive](https://learn.microsoft.com/pt-br/archive/blogs/academicbelux/imagine-cup-2011-details-of-the-belgian-software-design-final)) **[secondary]**.
3. **A working demo beats ambition.** Interledger's hackathon gives implementation the largest share of points ([Interledger judging](https://interledger.org/summit/hackathon/resources/judging)). MLH tells judges to expect "a demo of their hack, not a presentation" ([MLH judging plan](https://guide.mlh.com/general-information/judging-and-submissions/judging-plan.md)) **[primary]**.
4. **Naming your limits raises trust.** A judge's year of notes: teams that named a specific user, separated what they built from what they reused, and named what breaks first at scale scored higher. Broad "platforms" and claims that nothing would break scored lower ([DEV, "What judges actually score"](https://dev.to/kurbaitaev/what-judges-actually-score-notes-from-a-year-of-hackathon-judging-3p4l)) **[secondary, one judge's view]**.
5. **YC: clear beats impressive. Size the market bottom-up. Show traction relative to time spent.** Seibel's advice: say what you do in plain words, explain the insight others lack, size the market as customers × price rather than top-down, and avoid "platform" language ([YC, How to Pitch Your Company](https://www.ycombinator.com/blog/how-to-pitch-your-company/)) **[primary]**.
6. **Accelerator Q&A goes straight to adoption and market.** At Techstars NYC demo day, experts questioned founders on customer acquisition, market size and competition ([Techstars](https://www.techstars.com/content/accelerators/focus-founders-techstars-nyc-demo-day)) **[primary]**.
7. **AISG's own programmes reward deployable MVPs with a sponsor.** 100E projects must address a problem with no off-the-shelf AI solution and aim for "a minimum viable product" deployed in 9-18 months, with the sponsor matching funds in cash and in kind ([AISG 100E](https://www.aisingapore.org/100E/)) **[primary]**. LADP applicants need a workplace problem statement and an employer endorsement, and the course covers production readiness ([AISG LADP](https://aisingapore.org/llm-application-developer-programme-ladp/), [LADP Essentials](https://lumino.aisingapore.org/ladp-essentials)) **[primary]**. Read: AISG judges will ask "who is the sponsor, and what ships in the first quarter?"
8. **Regulators' fintech competitions say "market-ready".** MAS's 2025 Global FinTech Hackcelerator asked for market-ready solutions to "AI for Financial Services". Its three winners were all narrow and workflow-specific: insurance document structuring, SME debt collection, and behavioural personalisation for banks ([MAS media release](https://www.mas.gov.sg/news/media-releases/2025/mas-and-sfa-announce-fintech-award-winners-at-singapore-fintech-festival-2025)) **[primary]**.
9. **Why sponsors care about feasibility: pilots die.** Gartner predicted that at least 30% of GenAI projects would be abandoned after proof of concept by end-2025, citing poor data quality, inadequate risk controls, rising costs and unclear business value ([Gartner via Intelligent CIO](https://www.intelligentcio.com/apac/2024/08/05/gartner-predicts-30-of-generative-ai-projects-will-be-abandoned-after-proof-of-concept-by-end-of-2025/)) **[secondary]**. Two of those four causes (risk controls, unclear value) are what our Feasibility story must answer.

---

## 2. Criterion: Impact & Relevance

### 2.1 What judges look for

- **A named person with a felt problem**, not "enterprises" (point 4 above; also the pattern of past Tencent winners: Auntie AI for scam-targeted seniors, EyeDentify for visually impaired users; see 05 doc §3).
- **Relevance to the sponsor's own words and business.** Aspire's judge will check whether we solved *their* brief (the five scenarios) and whether it fits Aspire's direction (AI suite, MCP, AspireOS for banks).
- **A sized problem with a source**, said in one breath. YC: specific and plain (point 5).
- **Why now.** For us, the answer is regulation: the MAS AI Risk Management Guidelines were issued on 7 Oct 2026 (section 2.3).
- **Honest value claims.** Mark a projection as a projection. Judges discount unlabelled big numbers (point 4).

### 2.2 What winners did (impact framing examples)

| Winner | Framing that worked | What we copy |
|---|---|---|
| Auntie AI, 1st at Tencent Singapore station, May 2026 (05 doc §3) | One person you can picture (an elderly auntie), a real local harm (scams), local language | Open on one person: Maya, the compliance officer facing a MAS inspection, or Priya, the new account manager |
| EyeDentify, 2nd, same event | A disability made concrete, with a device in hand | Show the harm concretely: a chatbot leaking a salary or an STR, then ours refusing |
| MAS Hackcelerator 2025 winners (ActuaViz, Claimsio, Oxford Risk) | One workflow and one buyer each, e.g. "debt collection for SMEs" ([MAS](https://www.mas.gov.sg/news/media-releases/2025/mas-and-sfa-announce-fintech-award-winners-at-singapore-fintech-festival-2025)) | Name the buyer: "the AI lead at a MAS-regulated fintech who can't switch on Copilot" |
| Glean's pitch pattern (industry, not a hackathon) | "Search that respects permissions" became a category. We beat it on proof, not on features | Lead with *measured* proof (0 / 5,958) instead of a feature list |

No public write-ups of the AISG or Tencent global-final judging rationale were found **[verify]**.

### 2.3 Evidence bank: the problem is large, current and local

Use at most **three** of these on stage. Keep the rest for Q&A.

**A. Time lost to finding information**

| Stat | Source | Strength | How to say it |
|---|---|---|---|
| 47% of digital workers struggle to find the information they need to do their jobs; workers use 11 apps on average, up from 6 in 2019 (survey of 4,861 workers, Sep-Nov 2022) | [Gartner press release, 10 May 2023](https://www.gartner.com/en/newsroom/press-releases/2023-05-10-gartner-survey-reveals-47-percent-of-digital-workers-struggle-to-find-the-information-needed-to-effectively-perform-their-jobs) | **[secondary]**: Gartner's page returned 403 to our fetcher; figures consistent across outlets | "Half of office workers say they can't find what they need. Gartner, 2023." |
| Knowledge workers spend 2.8 hours a week looking for or requesting information (982 workers, 2021). A broader APQC figure is 8.2 h/week including recreating information | [APQC via BusinessWire](https://www.businesswire.com/news/home/20211109005449/en), [APQC blog](https://www.apqc.org/blog/km-makes-knowledge-workers-more-productive-and-less-stressed-out) | **[secondary]**, conservative | **Best anchor for the ROI**: "Our break-even needs 7 minutes a week. That is 4% of the 2.8 hours APQC measured." |
| Leaders and teams spend 25% of their time searching for answers (12,000 knowledge workers, 200 executives) | [Atlassian State of Teams 2025](https://www.atlassian.com/blog/state-of-teams-2025) **[primary]** | Atlassian makes Confluence and Jira, two of our four sources | "Atlassian, which makes Confluence and Jira, says a quarter of the week goes on searching." |
| "1.8 hours a day" (McKinsey) | widely repeated | **Do not use.** No primary McKinsey document gives 1.8 h. The MGI 2012 social-economy report says about 19-20% of the week ([search summary](https://cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information/)) **[verify]** | If asked: "McKinsey's 2012 figure is about a fifth of the week." |
| IDC "2.5 hours a day, 30%" | 2001-era IDC (Feldman), quoted second-hand | **[verify]**, old. Later Feldman work: 9-10 h/week searching, 3.5 h/week wasted ([KMWorld](https://www.kmworld.com/Articles/Editorial/Feature/The-high-cost-of-not-finding-information-9534.aspx), [Network World](https://www.networkworld.com/article/2303260/software/you-are-wasting-time--find-out-why.html)) | Avoid. The brief's "30%" probably comes from this |

**B. Why companies don't just switch on an AI assistant: leaks are real**

| Incident | Source | Use |
|---|---|---|
| **Microsoft 365 Copilot Chat summarised emails labelled Confidential despite DLP policies** (advisory CW1226324; detected 21 Jan 2026, fix rolling out February 2026) | [BleepingComputer](https://www.bleepingcomputer.com/news/microsoft/microsoft-says-bug-causes-copilot-to-summarize-confidential-emails/), [Computing](https://www.computing.co.uk/news/2026/security/microsoft-copilot-bug-led-to-confidential-emails-being-summarised/) **[secondary]** | **Best hook.** Recent and from the market leader. The label existed, but the AI read the email anyway. Ours checks the source before the model sees anything |
| EchoLeak, CVE-2025-32711 (CVSS 9.3): zero-click prompt injection in M365 Copilot let a crafted email pull internal data out; patched June 2025 | [The Hacker News](https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html) **[secondary]** | Prompt injection is not theoretical |
| Slack AI (Aug 2024): indirect prompt injection in a public channel could pull data from private channels the attacker was not in | [PromptArmor](https://promptarmor.com/resources/data-exfiltration-from-slack-ai-via-indirect-prompt-injection), [The Register](https://www.theregister.com/2024/08/21/slack_ai_prompt_injection/) | Slack is one of our four sources; our 0-injection-failures result speaks to this |
| ForcedLeak (Sep 2025, CVSS 9.4): Salesforce Agentforce leaked CRM data via a web-form prompt injection and a $5 expired allow-listed domain | [The Hacker News](https://thehackernews.com/2025/09/salesforce-patches-critical-forcedleak.html) **[secondary]** | Agents with tools widen the attack surface; our model has no tools |
| Samsung banned staff GenAI use after engineers pasted source code into ChatGPT (Apr-May 2023) | [Forbes AU](https://www.forbes.com.au/news/innovation/samsung-bans-chatgpt-among-employees-after-sensitive-code-leak/) | Shows the "ban it" reflex we replace with "allow it safely" |
| Gartner: oversharing is the top M365 Copilot risk. One secondary report says 40% of 132 IT leaders delayed Copilot rollout by 3+ months over oversharing (June 2025) | [Gartner abstract](https://www.gartner.com/en/documents/5326163), [Compare the Cloud](https://www.comparethecloud.net/articles/stop-microsoft-copilot-surfacing-confidential-data-wrong-people-365-tenant) | **[verify]**: the 40% figure is secondhand. Say "Gartner calls oversharing the top Copilot risk" only |
| IBM Cost of a Data Breach 2025: financial-sector average breach about US$5.56M; shadow-AI breaches cost about US$670k more where shadow AI is high; 20% of breaches involved shadow AI | [Baker Donelson summary](https://www.bakerdonelson.com/ten-key-insights-from-ibms-cost-of-a-data-breach-report-2025) **[secondary]** | One line in Q&A: "One avoided incident pays for years of this" |

**C. Regulation: the "why now" for Singapore fintech**

| Rule | Fact | Source |
|---|---|---|
| **MAS Guidelines on AI Risk Management** | Issued **7 Oct 2026**. Apply to all FIs and all AI, including third-party AI (FIs stay accountable) and agentic AI. Sections 3-4 (oversight, inventory, materiality) from **7 Oct 2027**; Sections 5-6 (life-cycle controls, capabilities) by **7 Oct 2028**. MAS plans an agentic-AI consultation in 2027 | [MAS media release](https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions) **[primary]**, [Guidelines PDF](https://www.mas.gov.sg/-/media/mas-media-library/regulation/guidelines/bd/guidelines-on-artificial-intelligence-risk-management-for-financial-institutions/guidelines-on-artificial-intelligence-risk-management.pdf) |
| MAS TRM Guidelines (2021) | Least privilege (9.1.1), logged access (9.1.3), prompt revocation (9.1.7), DLP for data in use (11.1.1), protected logs (12.2.2): all mapped in `docs/tencent/MAS_TRM_MAPPING.md` | [TRM PDF](https://www.mas.gov.sg/-/media/MAS/Regulations-and-Financial-Stability/Regulatory-and-Supervisory-Framework/Risk-Management/TRM-Guidelines-18-January-2021.pdf) **[primary]** |
| PDPA penalties | Since 1 Oct 2022: up to **10% of annual Singapore turnover** (for turnover above S$10M) or S$1M, whichever is higher | [PDPC](https://www.pdpc.gov.sg/News-and-Events/Announcements/2022/09/Amendments-to-Enforcement-under-the-Personal-Data-Protection-Act-in-updated-Advisory-Guidelines-and-Guide) **[primary]** |
| APAC bank leaders' top AI barrier | Data privacy and security was the top barrier at 42%, then regulatory risk and explainability at 21% each; cost only 16%. 77% want AI purpose-built for financial services. Small sample: 51 executives, FICO forum, Aug 2026 | [Tech Coffee House](https://techcoffeehouse.com/2026/10/16/77-of-apac-banks-want-purpose-built-ai-fico/) **[secondary, n=51]** |
| Size of the Singapore market | More than 1,800 fintech firms, employing close to 10,000 people; fintech investment S$2.9B in 2025 | [Mothership on MAS, Sep 2026](https://mothership.sg/2026/09/mas-fintech/) **[secondary]** |

**D. Aspire: why this matters to the sponsor (public facts only)**

| Fact | Source |
|---|---|
| "Trusted by 50,000+ modern businesses"; products: business account, FX and payments, cards, yield, expense, AP/AR, AI Accounting, payroll; **"MCP" listed as NEW** | [aspireapp.com](https://aspireapp.com/) **[primary]** |
| AspireOS (18 Nov 2025): modular, AI-powered business-banking platform **for banks**, aimed at 6,000+ commercial banks; Aspire sizes this market at about US$9B a year (its own unsourced figure); clients in 30+ markets | [Aspire blog](https://aspireapp.com/en-sg/blog/aspire-launches-aspireos-its-ai-powered-platform-for-banks) **[primary]** |
| Aspire's MCP promise: the assistant "can only access information that the authenticated user is already permitted to access"; OAuth tokens are tied to one business and revocable; full card numbers are excluded | [Aspire Help Centre, Security & privacy](https://help.aspireapp.com/sg/en/articles/15550809-security-privacy) **[primary]** |
| Earlier company post: 15,000+ businesses, US$15B annualised TPV, profitable from Q2 2023, **400+ employees in four countries**; Singapore PSA exemptions stated | [Aspire blog](https://aspireapp.com/en-sg/blog/aspire-grows-revenue-3x-yoy-hits-profitability-3-months-after-closing-us-100m-series-c) **[primary, dated]** |
| Headcount about 600 | Third-party trackers only ([bitscale](https://bitscale.ai/directory/aspire), private.law wiki) **[verify]**. Do not say "Aspire has 600 staff" |

**The relevance line:** Aspire already promises customers, in writing, that its MCP assistant sees only what the user
is permitted to see. Walnut makes that same promise for Aspire's *internal* knowledge, and proves it with numbers.
AspireOS sells to banks, which will ask Aspire the same TRM and AIRM questions.

### 2.4 Our concrete deliverables for Impact

**D1. One-page impact sheet**: `docs/finals/deliverables/IMPACT_SHEET.md` (+ `IMPACT_SHEET.png` exported for a Q&A slide)

Outline (one page, numbered, every number with its source):
1. The person: "Maya, compliance officer at a 600-person Singapore fintech, two weeks before a MAS inspection" (synthetic Company A persona).
2. The problem in three numbers: Gartner 47%; APQC 2.8 h/week; Copilot CW1226324 (Jan 2026).
3. Why now: MAS AIRM issued 7 Oct 2026, Sections 3-4 apply from 7 Oct 2027; PDPA 10% of turnover.
4. What we change: "answers only from what you're allowed to open, and proof afterwards". Two gates, sealed audit.
5. Measured proof: 0 / 5,958 adversarial; 0 / 400 differential; 6 / 6 tamper attacks caught; 639 + 38 tests.
6. Value (projection): break-even 7 min/person/week; base US$1.3M/yr; range US$0.44M-4.1M; assumptions in one line.
7. Relevance to Aspire: covers all five scenarios; matches Aspire's own MCP permission promise; extends to AspireOS bank clients.
8. Limits: synthetic data, mock platforms with real API shapes, no live tenant yet.

Where in the demo: the 0:00-0:35 hook uses items 1-2 (one number on screen: "47%" or the Copilot headline). Items 5-6 come back at 3:25-4:40. The sheet itself is shown only in Q&A.

**D2. ROI calculator page**: `databridge/apps/internal_brain_web/app/value/page.tsx` (route `/value`, add to `AppShell` nav)

Outline:
- Inputs (sliders, defaults in brackets): staff [600]; minutes saved per person per week [90]; loaded cost per hour US$ [31]; working weeks [46]; annual price US$ [72,000 = 600 × $10 × 12]; answers per person per day [10].
- Outputs: annual value; **break-even minutes per week** (= price ÷ (staff × weeks × hourly cost) × 60); model cost per year from the cost table (D3); a payback bar.
- Anchors drawn as reference lines: APQC 2.8 h/week; Atlassian 25% of the week. Each links to its source.
- Footer: "Projection, not a measurement. Hours saved are to be measured in the pilot."
- Accessibility: inputs as labelled number fields as well as sliders; keyboard operable; values announced (`aria-live`).
- Formula check: at the defaults, 600 × 1.5 h × 46 × 31 = US$1,283,400 ≈ US$1.3M; break-even at US$72k = 72,000 ÷ (600 × 46 × 31) = 0.084 h ≈ **5.0 min/week**; at the US$100k in SUBMISSION.md it is 0.117 h ≈ **7.0 min/week**.

Where in the demo: 4:05-4:40. Drag "minutes saved" down to 7 and show "still breaks even". About 15 s. That move is more convincing than stating US$1.3M.

**D3. "Why us, why Aspire" relevance card**: one slide in the deck, generated by `docs/tencent/deck/make_deck.py`

Content: Aspire's MCP quote next to ours ("same promise, internal knowledge, with proof"); the five Aspire scenarios ticked; AspireOS → bank customers → TRM/AIRM. Use it in Q&A, or 5 s at the close.

### 2.5 Proof points to say on stage (Impact)

1. "Half of office workers can't find what they need. That's Gartner. Company AI was supposed to fix that, and it keeps leaking."
2. "In January, Microsoft's Copilot summarised emails marked Confidential. The label was there. The AI read them anyway."
3. "MAS issued its AI risk guidelines three weeks ago. From October 2027, every FI needs an AI inventory and controls. We built the controls."
4. "Same question, three people, three different answers, and each of them correct for that person."
5. "It pays for itself if each person saves seven minutes a week. APQC measured nearly three hours a week spent searching."
6. "Aspire already promises customers its AI only sees what the user may see. We make that promise for Aspire's own staff, and we measured it: zero leaks in 5,958 attacks."

### 2.6 Likely judge questions (Impact), with crisp answers

| Question | Answer (about 20 s) |
|---|---|
| Isn't this just Glean or Copilot? | "They inherit permissions and trust the index. Copilot still summarised Confidential emails this year. We re-check the source live on every answer, prove it with 5,958 attacks, and seal the audit trail for a regulator. Glean is also around US$45-50 a seat by third-party estimates; we target US$10." (Glean price **[verify]**: [GoSearch, a competitor](https://www.gosearch.ai/blog/glean-pricing-explained).) |
| Who exactly is the user? | "Three people: the account manager who needs a market-specific answer now, the on-call engineer at 3 a.m., and the compliance officer who has to prove afterwards who saw what." |
| Is US$1.3M real? | "It's a projection. 600 people × 1.5 h/week × 46 weeks × US$31/h. US$31 is close to Singapore's median pay including employer CPF (S$5,775 a month, about US$29/h), so it's conservative for fintech. Break-even is 7 minutes a week, and the pilot measures the real figure." (MOM figure via secondary sources **[verify]** at [stats.mom.gov.sg](https://stats.mom.gov.sg/Pages/IncomeTimeSeries.aspx); FX 1.30 SGD/USD **[estimate]**.) |
| Why should Aspire care beyond the hackathon? | "Three reasons. Internal speed. MAS AIRM readiness by 2027. And AspireOS sells to banks, which will ask Aspire exactly these questions." |
| Why fintech, not any company? | "Regulated firms need two things others don't: tipping-off compartments for STRs and an audit a regulator accepts. We built both: the FinCrime compartment, PAN/NRIC masking and the TRM mapping." |
| What's the impact on SMEs (Aspire's customers)? | "Faster, correct answers from Aspire's support and account teams, per market, from approved policy only. No customer data leaks between staff who shouldn't see it." |
| How will you measure impact? | "In the pilot: time-to-answer against baseline tickets, answer acceptance, citation clicks, zero leaks on Aspire's own permissions, and audit-prep hours for compliance." |

---

## 3. Criterion: Feasibility ("realistic and scalable beyond the hackathon")

### 3.1 What judges look for

- **It runs today, and the path to production is concrete:** architecture, named cloud services, connector APIs and their limits (points 2-3, section 1).
- **Unit economics:** cost per query, price, margin. FICO's poll shows APAC bank leaders rank cost low (16%) and security high (42%), so show cost as small and stable, then move on.
- **A pilot with a sponsor, dates and exit criteria** (AISG 100E/LADP pattern).
- **Named risks with mitigations,** especially "what breaks first at scale" (point 4).
- **Compliance path** that a regulated buyer's procurement would accept: TRM, AIRM, PDPA, outsourcing due diligence, certifications.
- **Team credibility:** what was built against what was reused; who maintains it.

### 3.2 What winners and real products did

| Example | Feasibility signal | Lesson |
|---|---|---|
| MAS Hackcelerator winners 2025 | "Market-ready" solutions; each had an existing company and product ([MAS](https://www.mas.gov.sg/news/media-releases/2025/mas-and-sfa-announce-fintech-award-winners-at-singapore-fintech-festival-2025)) | Show a buyer and a deployment, not a concept |
| AISG 100E | MVP deployed in 9-18 months with a co-funding sponsor ([AISG](https://www.aisingapore.org/100E/)) | Pilot plan with Aspire as sponsor, measured outcomes |
| Glean's Slack connector after Slack's 2025 API changes | Does not crawl Slack messages; fetches them live at query time through Slack's Real-time Search API, with Slack enforcing permissions ([Glean docs](https://docs.glean.com/connectors/native/slack/setup/slack-rts-connector)) **[primary]** | Market leaders moved to the **live, as-the-user** check we already do in Gate 2. This is our best feasibility argument |
| Tencent station winners | Working demo on stage with Tencent tools (05 doc §3) | Run Hy3 live; keep a recorded fallback |

### 3.3 Evidence bank for Feasibility

#### (a) Cost per answer: Hy3 on TokenHub

Official price, Tencent Cloud model pricing page (updated 26 Oct 2026), **US$ per million tokens**: Hy3 input **0.132**, output
**0.528**, cached input **0.033**. Hy4 preview: 0.834 / 2.501 / 0.042
([Tencent Cloud pricing](https://www.tencentcloud.com/document/product/1300/78937)) **[primary]**. Both regional tables show
the same Hy3 price. Free-trial terms are not stated on that page.

Token sizes are from SUBMISSION.md (about 4,000 tokens in, 300 out per answer). **Re-measure from the audit log once Hy3 runs live.**

| Case | Input tokens | Output tokens | Cost per answer (US$) | Per 1,000 answers |
|---|---|---|---|---|
| Typical answer, Hy3 | 4,000 | 300 | 4,000×0.132e-6 + 300×0.528e-6 = **0.00069** | **US$0.69** |
| Typical, 1,500-token system prompt cached | 1,500 cached + 2,500 | 300 | 0.0000495 + 0.00033 + 0.000158 = **0.00054** | US$0.54 |
| Long answer, Hy3 | 12,000 | 800 | 0.00158 + 0.00042 = **0.0020** | US$2.01 |
| Typical answer on Hy4 preview (for comparison) | 4,000 | 300 | 0.00334 + 0.00075 = **0.0041** | US$4.09 |
| Refusal (Gate 1/2 denies everything) | 0 | 0 | **0** (no model call by design) | 0 |
| Extractive fallback (model down or grounding fails) | 0 | 0 | **0** | 0 |
| DeepSeek fallback | n/a | n/a | not priced; the pricing page did not render for us **[verify]** | n/a |

Per seat **[estimate]**: 10 answers/day × 21 days = 210 answers/month × US$0.00069 = **US$0.14 per seat per month**.
At 50 answers a day, US$0.72. For 600 seats at 10/day: about **US$87/month**, or about US$1,040 a year in model spend.
**The model is not the cost driver.** Connector API quotas and Gate 2 latency are (section 3.3c).

#### (b) Deployment architecture (today → pilot → scale)

| Stage | Hosting | What's added | Monthly infra **[estimate]** |
|---|---|---|---|
| Today (demo) | Laptop; deploy kit for Tencent Cloud Lighthouse exists, not yet live | Mock platforms with real API shapes; Hy3 via TokenHub (international endpoint) | Lighthouse 2 vCPU / 4 GB is about US$6-8.50/month list ([Tencent Lighthouse pricing](https://www.tencentcloud.com/document/product/1103/47794)) **[primary; region labelling unclear]** |
| Pilot (≤100 users, 2-4 sources) | Tencent Cloud **Singapore region**, single tenant: 1 app VM (API + web), managed PostgreSQL (index metadata, directory), object storage for the sealed ledger and external anchor, KMS for HMAC keys and connector secrets | SSO (OIDC) + SCIM; real connectors; per-tenant leak harness; monitoring | about US$150-400 |
| Scale (1,000-10,000 users) | Containers (TKE) behind a load balancer; stateless API pods; a Gate 2 worker pool per source with per-source rate-limit budgets; vector index service; read replicas; ledger on write-once storage | Webhook/event ingestion (Drive changes.watch, Slack events, Atlassian webhooks); per-source circuit breakers; per-compartment kill switch; SIEM export of the ledger | about US$1-3k, i.e. US$0.30-1.00 per seat |
| Option: no data leaves the tenant | Self-host **Hy3 open weights** (Apache 2.0, 295B total / 21B active MoE; see 05 doc §4) on GPU instances | Removes the PDPA s.26 cross-border transfer question | High fixed GPU cost (FP8 weights alone are about 300 GB, so a multi-GPU node) **[estimate]**. Worth it only for large banks |

Infrastructure trust we inherit: Tencent Cloud states it holds **MTCS Level 3** and an **OSPAR attestation (since June 2020)**
for multiple products in its Singapore region ([Tencent Cloud compliance](https://www.tencentcloud.com/document/product/363/34543)) **[primary; confirm which products are in scope]**.
OSPAR is the Association of Banks in Singapore's audit standard for outsourced service providers to banks.

#### (c) Scaling path to real connectors: APIs, permission models, limits

| Source | Today (mock, real API shapes) | Production API and permission model | Limits and policy that matter | What we change |
|---|---|---|---|---|
| **Slack** | Index threads; Gate 2 calls `users.info`, `conversations.list`, `conversations.members`, `conversations.replies` per answer | **Real-time Search API** (`assistant.search.context`) called **as the user** with their xoxp token, so Slack enforces permissions; available to directory-published and internal apps ([Slack RTS docs](https://docs.slack.dev/apis/web-api/real-time-search-api)) **[primary]** | (1) **Slack API Terms, May/June 2025: no bulk export, no persistent copies, archives or indexes, and no use of API data in LLMs** ([Slack changelog](https://docs.slack.dev/changelog/2025/05/29/tos-updates), [Hunton analysis](https://www.natlawreview.com/article/salesforce-locks-down-slack-data-time-review-your-slack-api-terms)) **[primary + secondary; legal reading needed]**. (2) Non-Marketplace distributed apps: `conversations.history/replies` cut to 1 request/min and 15 objects. **Internal customer-built apps keep 50+/min and 1,000 objects** ([Slack changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)) **[primary]**. Limits apply per method, per workspace, per app ([rate limits](https://docs.slack.dev/apis/web-api/rate-limits)) | **Biggest change.** Production Slack should be **federated**: no message index; Gate 1 and 2 collapse into one RTS call as the user. Ship to Aspire as an **internal app** in their workspace. Our "next-query revocation" property holds by construction. Per-method tiers (e.g. `conversations.list`) to be re-checked **[verify]** |
| **Confluence Cloud** | Compiles space permissions + page restrictions (narrowing through ancestors); Gate 2 = `POST /wiki/rest/api/content/{id}/permission/check` | Same check API. **It requires a Confluence admin account** (super-reader trade-off, TRM 9.2.2, already documented). Alternative: per-user OAuth 2.0 (3LO) and CQL search as the user | **Points-based rate limits for Forge/Connect/OAuth apps, enforced from 2 Mar 2026**: hourly point quotas, HTTP 429 when exceeded; API-token traffic stays on burst limits ([Atlassian docs](https://developer.atlassian.com/cloud/confluence/rate-limiting), [Atlassian blog](https://www.atlassian.com/blog/platform/evolving-api-rate-limits)) **[primary]**. One developer reports a single permissions call costing nearly 1,000 points **[verify]** | Budget Gate 2 points per answer; check only the top-k candidates after ranking; batch; apply for Tier 2; webhook-driven re-compile of the ACL index |
| **Jira Cloud** | Compiles "Browse projects" holders plus issue security levels | Bulk permission check `POST /rest/api/3/permissions/check` with `accountId` + `projectPermissions`; checking other users needs **Administer Jira** ([community thread](https://community.developer.atlassian.com/t/im-cannot-check-a-user-permission-from-specific-jira-project-via-rest-api/52682)) **[secondary; confirm in Atlassian REST reference]** | Same Atlassian points model | One bulk call per answer covers all Jira candidates |
| **Google Drive** | Compiles `files/{id}/permissions` incl. inherited; fails closed on "anyone with link" and expiring grants | Service account with **domain-wide delegation**: impersonate the asking user and `files.get` (a 404 means no access) as the Gate 2 check; `changes.watch` push channels for freshness ([Drive push docs](https://developers.google.com/workspace/drive/api/guides/push)) **[primary]** | Push notifications are empty-body signals, so you still fetch changes. Channels expire (third-party: 1 h default, 7 days max **[verify]**) | Renew channels on a schedule; impersonation keeps Gate 2 "as the user" |
| **Identity** | Synthetic directory, per-platform IDs | SSO (Okta / Google / Entra) + SCIM; map platform IDs to one person | Unmapped identity = deny (already fail-closed) | Admin console showing unmapped accounts |

**Feasibility headline from this table:** the 2025-26 platform changes (Slack's terms, Atlassian's point quotas) push the
whole market *away* from "crawl everything into one index" and *towards* "ask the source, as the user, at query time".
Gate 2 already works that way. **The scale risk is API quota, not model cost.** Say it before a judge does.

#### (d) Pilot plan (for `PILOT_PLAN.md`)

Ten weeks, one Aspire team, read-only:

| Weeks | Step | Exit criterion |
|---|---|---|
| 0-2 | Security review (TRM / outsourcing questionnaire), DPA, choose 2 sources (suggest Confluence + Slack-as-internal-app), SSO + SCIM, Singapore-region tenant | Aspire security sign-off |
| 2-4 | **Shadow mode**: ingest, compile ACLs, run the leak harness and the 400-pair differential on **Aspire's real permissions**, with no users | 0 leaks; Gate 2 p95 latency measured; unmapped identities listed |
| 4-8 | Live with 30-50 people in one team (suggest Customer Success or Operations, Singapore market first), in the web app and WorkBuddy | Weekly: questions, answer acceptance, citation clicks, refusals, Gate 2 p95, cost per answer |
| 8-10 | Read-out to Aspire's AI lead and compliance: time saved against baseline, audit pull ("who saw doc X"), go/no-go for a second team and market | Break-even shown at ≥7 min/person/week, or stop |

Success metrics, set up front: median time-to-answer (baseline from 20 sampled tickets); ≥X% of answers accepted (Aspire
picks X); **0 leaks**; tamper verification passes daily; p95 answer latency under a target agreed with users. Model cost is
reported, not targeted.

#### (e) Security and certification path

| When | Item | Why | Source |
|---|---|---|---|
| Now | Inherit Tencent Cloud Singapore MTCS L3 + OSPAR for infrastructure | Covers the hosting layer of an FI's due diligence | [Tencent Cloud](https://www.tencentcloud.com/document/product/363/34543) **[confirm scope]** |
| Pilot | Third-party pen test; TRM and AIRM mapping (exists); **AI Verify / Project Moonshot** baseline tests (hallucination, data disclosure, adversarial prompts) | Singapore's own GenAI testing norms; easy credibility with AISG/IMDA-minded judges | [IMDA on the Global AI Assurance Pilot and Starter Kit](https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2025/sg-unveils-insights-from-worlds-first-technical-testing-of-real-world-applications-of-genai) **[primary]** |
| 0-6 months | **CSA Cyber Trust mark** (tiered; certification bodies offer a cloud and AI-security pillar; valid 3 years) | Singapore-recognised, cheap. SGS lists fees from about S$1,375 plus S$225-450 per pillar; CSA subsidies up to S$3,600 **[verify fees]** | [CSA](https://www.csa.gov.sg/cyber-essentials/certification-for-the-cyber-essentials-mark), [SGS](https://www.sgs.com/en-sg/services/cyber-trust-mark) |
| 3-12 months | SOC 2 Type I, then Type II; ISO/IEC 27001 | What bank procurement asks for | general practice **[estimate of timing]** |
| On first bank customer | Support the FI's MAS outsourcing / third-party assessment; consider an OSPAR report if we become a material outsourced service provider to banks | AspireOS route to banks | ABS OSPAR (via Tencent compliance page) |
| By Oct 2027 / Oct 2028 | Supply AIRM inventory inputs (model, version, prompt version, scope per answer, already logged) | Customers must meet AIRM Sections 3-4 by Oct 2027 and 5-6 by Oct 2028 | [MAS](https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions) |

#### (f) Pricing, unit economics, go-to-market (for `PRICING_GTM.md`)

All **[estimate]** unless marked.

| Item | Value | Note |
|---|---|---|
| Price | **US$10 per seat per month**, minimum 100 seats; plus a fixed pilot fee | Below Glean's reported US$45-50 per seat (third-party, competitor-authored **[verify]**) |
| Customer break-even at that price | 72,000 ÷ (600 × 46 × 31) × 60 ≈ **5 min/person/week** | Shown live on `/value` |
| Model cost per seat | about US$0.14-0.72/month | Section 3.3a |
| Infra per seat at scale | about US$0.30-1.00/month | Section 3.3b |
| **Gross margin** before support | about 80-95% | Support and implementation are the real cost |
| Self-hosted tier | Annual licence + customer's GPUs | For banks with data-residency demands |

Go-to-market, in order:
1. **Design partner: Aspire internal** (the challenge sponsor). One team, ten weeks, Singapore first, then Aspire's other markets with market-scoped answers.
2. **Distribution through Tencent:** WorkBuddy as the front door (MCP connector already built) and a Tencent Cloud Marketplace listing, which keeps hosting in the Tencent Singapore region **[to explore with Tencent]**.
3. **Segment:** MAS-regulated, mid-size FIs (roughly 200-2,000 staff) that cannot switch on Copilot-style assistants because of AIRM and TRM. Singapore has 1,800+ fintech firms ([Mothership/MAS](https://mothership.sg/2026/09/mas-fintech/)); qualifying FIs are a subset **[verify count]**.
4. **Later, via AspireOS banks** (6,000+ target banks per Aspire): an internal-knowledge module for bank staff. A speculative idea to raise *with* Aspire, not to claim.

Bottom-up sizing (YC style), **illustrative only**: 200 regulated SEA firms × 500 seats × US$10 × 12 = **US$12M ARR**.
The 200-firm figure is an assumption to be replaced with a MAS FID count **[verify]**.

#### (g) Risk register (for `RISK_REGISTER.md`)

| # | Risk | Likelihood / impact | Mitigation | Status |
|---|---|---|---|---|
| R1 | **Slack API terms forbid indexing or LLM use of API data** | High / High | Federated Slack via the RTS API as the user; deploy as Aspire's internal app; legal review of terms | Design ready; not built |
| R2 | **Connector quotas cap Gate 2 throughput** (Atlassian points, Slack per-method tiers) | Medium / High | Gate 2 only on the top-k after ranking; bulk Jira check; per-source budgets and circuit breakers; webhook-driven ACL recompiles; Atlassian Tier 2 application | Partly designed |
| R3 | Gate 2 adds latency | Medium / Medium | Parallel per-source checks; 2 s timeout already fails closed; measure p95 in shadow mode. Any cache must keep a configurable bound, default 0, so "next-query revocation" stays true | Measure in pilot |
| R4 | Privileged "super-reader" service accounts (Confluence/Jira admin) | Medium / High | KMS-held secrets; scoped tokens; per-user OAuth where possible; monitoring and alerting on service accounts (currently a gap against TRM 9.2.2) | Gap documented |
| R5 | Identity mismatch across platforms | Medium / High | SCIM; unmapped identity = deny (built); admin report | Built (deny path) |
| R6 | Hallucination / ungrounded answers | Medium / Medium | Grounding check, extractive fallback, citations, refusal without a model call (built) | Built |
| R7 | Prompt injection through documents | Medium / High | Model has no tools; passages are data; 0 injection failures in evals | Built; re-test live |
| R8 | Model outage, price change or deprecation | Low / Medium | Model chain (Hy3 → DeepSeek → extractive); kill switch (global; per-source is a gap) | Built (global) |
| R9 | Cross-border transfer (PDPA s.26) / residency | Medium / Medium | Only Gate-2-released passages go to the model; Singapore endpoint; self-host Hy3 open weights option | TokenHub terms not yet verified by us |
| R10 | Never tested on a live tenant | Certain / High | Shadow-mode weeks 2-4 re-run the leak harness on real permissions before any user | Pilot step |
| R11 | Unsupported content (PDF, slides, images skipped) | Certain / Medium | Clearly scoped; roadmap: text extraction with the same ACL rules | Documented |
| R12 | Adoption: people don't change habits | Medium / High | Front doors where people already work (WorkBuddy; Slack later); pilot metrics weekly | Built (WorkBuddy) |
| R13 | Small team maintaining a security product | High / High | Narrow scope; 639 + 38 tests and the leak harness in CI; seek a design-partner contract; certification path above | Honest answer in Q&A |
| R14 | Ledger growth and retention | Low / Low | Retention policy per MAS record-keeping needs; external anchor; answer text outside the ledger (built) | Partly built |

### 3.4 Our concrete deliverables for Feasibility

**D4. `docs/finals/deliverables/COST_PER_ANSWER.md`** (also a panel on `/value`)
Outline: price table with link and date; token sizes **measured from the audit log of N live Hy3 answers** (replace the
4,000/300 assumption); per-answer, per-1,000, per-seat-month and 600-seat-year costs; refusals and fallback cost zero; a
comparison row for Hy4 preview. **Demo: 4:05-4:40, one sentence and one number** ("under a tenth of a US cent an answer").

**D5. `docs/finals/deliverables/SCALE_ARCHITECTURE.md` + `docs/tencent/diagrams/scale_path.mmd`**
Outline: the three-stage table (3.3b); a diagram with user → SSO → API (stateless) → Gate 1 index → Gate 2 per-source
workers (rate budgets) → Hy3/TokenHub (or self-hosted) → grounding → sealed ledger + external anchor; the connector table
(3.3c) with the "federated Slack" change; the Tencent services named. **Demo: 2:50-3:25 on the Tencent stack slide
(one diagram, 10 s); full version in Q&A.**

**D6. `docs/finals/deliverables/PILOT_PLAN.md`**
Outline: the 10-week table (3.3d), success metrics, the roles needed from Aspire (sponsor, IT admin for SSO and
connectors, a compliance reviewer), data handling (read-only, Singapore region, synthetic-to-real switch), the
certification path (3.3e), and the exit and stop criteria. **Demo: 4:30-4:40, one line: "Ten-week pilot: two weeks
shadow mode re-running our leak test on Aspire's real permissions before anyone uses it."**

**D7. `docs/finals/deliverables/RISK_REGISTER.md`**
Outline: table 3.3g, each risk linked to its test or file where built. **Q&A only.** Bring it up yourself if asked
"what breaks first": *"Connector quotas, not the model."*

**D8. `docs/finals/deliverables/PRICING_GTM.md`**
Outline: table 3.3f, three-step GTM, illustrative bottom-up with its flags. **Q&A only.**

**D9 (small, high value): measure two numbers before Monday**
1. Run Hy3 live for 50 questions; record real tokens in/out and p50/p95 latency (`RESULTS.md` still says Hy3 is pending a key).
2. Time Gate 2 against the mock platforms with a realistic injected delay (for example 150 ms per call) to quote p95 with honest wording.
Without these, cost and latency remain estimates. Say so.

### 3.5 Proof points to say on stage (Feasibility)

1. "It runs today: 639 backend and 38 browser tests, and the leak harness re-runs on any company's permissions."
2. "An answer costs under a tenth of a US cent on Hy3. Refusals cost nothing, because they never reach the model."
3. "Slack and Atlassian tightened their APIs in 2025 and 2026. The market is moving to checking the source live, as the user. That's already how our Gate 2 works."
4. "Deploys to Tencent Cloud Singapore, which already holds MTCS Level 3 and OSPAR. Hy3's open weights mean a bank can host the model itself."
5. "Ten-week pilot. The first two weeks run in shadow mode: our leak test on Aspire's real permissions, before a single user."
6. "What breaks first at scale is API quota, not the model. We budget Gate 2 calls per source."

### 3.6 Likely judge questions (Feasibility), with crisp answers

| Question | Answer |
|---|---|
| Have you connected to a real Confluence/Slack? | "Not yet. The mocks use the real API shapes and permission rules, and every connector fails closed. The pilot's first two weeks re-run the leak test on real permissions before any user touches it." |
| What does it cost to run? | "Hy3 is US$0.132 per million input tokens and US$0.528 output. A typical answer costs about US$0.0007; 600 people asking ten questions a day costs under US$100 a month in model spend. Hosting is a few hundred dollars a month at pilot size." |
| Will it scale to 10,000 users / millions of docs? | "The API tier is stateless and scales out. The limit is each source's API quota, so Gate 2 checks only the top candidates, uses bulk checks for Jira, and goes federated for Slack. We'll measure p95 in shadow mode." |
| Doesn't Slack forbid indexing its data now? | "Yes. Since mid-2025 Slack's terms bar persistent indexes and LLM use for API data. Production Slack therefore uses the Real-time Search API as the user, with no index. Glean made the same move. Our demo indexes a mock." |
| Latency of the live check? | "Each source check has a 2-second timeout and fails closed. We'll publish p95 from shadow mode. If a source is slow, the answer leaves that source out rather than guessing." |
| What if TokenHub is down? | "Hy3, then DeepSeek, then an extractive answer built only from permitted sentences. A kill switch turns the model off entirely." |
| Does data leave Singapore? | "Only the permitted passages for one question go to the model, through the Singapore endpoint. A bank that wants nothing to leave can self-host Hy3's open weights." (Endpoint location and retention terms **[verify]** in TokenHub terms.) |
| Who maintains this? You're students. | "Fair question. The scope is narrow, the safety properties are tested on every commit, and the pilot is the contract that funds maintenance. We'd want Aspire as a design partner, as AISG's 100E model does." |
| Certifications? | "Infrastructure inherits Tencent's MTCS L3 and OSPAR. For us: pen test and AI Verify tests in the pilot, CSA Cyber Trust within six months, then SOC 2." |
| How is it priced? | "Around US$10 a seat a month. At that price a person breaks even on five minutes a week." |
| What did you build versus reuse? | "Built: the two gates, the four permission compilers, the sealed ledger, the eval harness, the UI and the MCP server. Reused: the Walnut evidence engine by import, Hy3 via TokenHub, and WorkBuddy as a front door." (Keep this consistent with SUBMISSION.md.) |

---

## 4. Where it lands in the 5-minute run (changes to 05 run sheet)

| Time | Beat (05 doc) | Add from this research |
|---|---|---|
| 0:00-0:20 | Hook | Use the **Copilot CW1226324** headline (Jan 2026) as the "normal AI" foil; one number on screen: **47%** (Gartner) |
| 0:20-0:35 | Problem line | "...and MAS's AI guidelines, issued three weeks ago, will require proof." |
| 2:50-3:25 | Tencent stack | Add the scale-path diagram (D5) as a small inset; say "Tencent Cloud Singapore, MTCS L3 and OSPAR" |
| 4:05-4:40 | Business value | **Live `/value` page** (D2): drag to 7 min, still breaks even; cost per answer (D4); one line of pilot plan (D6) |
| Q&A | | Backup cards: impact sheet (D1), cost table (D4), connector table (3.3c), pilot (D6), risk register (D7), pricing (D8) |

---

## 5. Do not say (or say only with the caveat)

- "McKinsey says 1.8 hours a day": not traceable to McKinsey.
- "Aspire has 600 staff": third-party estimates only; Aspire's own post said 400+.
- "Gartner says 40% delayed Copilot": secondhand.
- "Aspire is a licensed neobank": Aspire states PSA *exemptions* (05 doc flags this too).
- Glean's price as fact: competitor-sourced estimates.
- Any Hy3 latency figure until it has run live (D9).
- "95% upper bound 0.10%" for 0 / 5,958: check the method. The standard one-sided 95% bound for 0 events (rule of three) is about 3 / 5,958 ≈ **0.05%**; two-sided Clopper-Pearson is about 0.06%. 0.10% is conservative, but be ready to say how it was computed.

---

## 6. Sources (all accessed 30 Oct 2026)

Judging and pitching
- YC, How to Pitch Your Company: https://www.ycombinator.com/blog/how-to-pitch-your-company/
- Techstars NYC demo day: https://www.techstars.com/content/accelerators/focus-founders-techstars-nyc-demo-day
- MLH judging plan: https://guide.mlh.com/general-information/judging-and-submissions/judging-plan.md
- Okta hackathon criteria (Devpost-style): https://devforum.okta.com/t/what-are-the-judging-criteria-for-the-hackathon/15842
- Interledger hackathon judging: https://interledger.org/summit/hackathon/resources/judging
- Imagine Cup FAQ: https://imaginecup.microsoft.com/support/faq
- "What judges actually score" (DEV): https://dev.to/kurbaitaev/what-judges-actually-score-notes-from-a-year-of-hackathon-judging-3p4l
- AISG 100E: https://www.aisingapore.org/100E/ ; LADP: https://aisingapore.org/llm-application-developer-programme-ladp/
- MAS Hackcelerator 2025 winners: https://www.mas.gov.sg/news/media-releases/2025/mas-and-sfa-announce-fintech-award-winners-at-singapore-fintech-festival-2025
- Tencent Singapore station (May 2026): https://www.aap.com.au/aapreleases/cision20260513ae58232/ ; HK/Macau Demo Day: https://www.aap.com.au/aapreleases/cision20260819ae29301/
- Gartner, 30% of GenAI projects abandoned after PoC: https://www.intelligentcio.com/apac/2024/08/05/gartner-predicts-30-of-generative-ai-projects-will-be-abandoned-after-proof-of-concept-by-end-of-2025/

Problem size
- Gartner 47% (May 2023): https://www.gartner.com/en/newsroom/press-releases/2023-05-10-gartner-survey-reveals-47-percent-of-digital-workers-struggle-to-find-the-information-needed-to-effectively-perform-their-jobs
- APQC: https://www.businesswire.com/news/home/20211109005449/en ; https://www.apqc.org/blog/km-makes-knowledge-workers-more-productive-and-less-stressed-out
- Atlassian State of Teams 2025: https://www.atlassian.com/blog/state-of-teams-2025
- Feldman/IDC: https://www.kmworld.com/Articles/Editorial/Feature/The-high-cost-of-not-finding-information-9534.aspx ; https://www.networkworld.com/article/2303260/software/you-are-wasting-time--find-out-why.html
- McKinsey 1.8 h caveat: https://cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information/
- Copilot CW1226324: https://www.bleepingcomputer.com/news/microsoft/microsoft-says-bug-causes-copilot-to-summarize-confidential-emails/ ; https://www.computing.co.uk/news/2026/security/microsoft-copilot-bug-led-to-confidential-emails-being-summarised/
- EchoLeak: https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html
- Slack AI exfiltration: https://promptarmor.com/resources/data-exfiltration-from-slack-ai-via-indirect-prompt-injection ; https://www.theregister.com/2024/08/21/slack_ai_prompt_injection/
- ForcedLeak: https://thehackernews.com/2025/09/salesforce-patches-critical-forcedleak.html
- Samsung: https://www.forbes.com.au/news/innovation/samsung-bans-chatgpt-among-employees-after-sensitive-code-leak/
- Gartner Copilot risk abstract: https://www.gartner.com/en/documents/5326163 ; secondary: https://www.comparethecloud.net/articles/stop-microsoft-copilot-surfacing-confidential-data-wrong-people-365-tenant
- IBM breach 2025 (summary): https://www.bakerdonelson.com/ten-key-insights-from-ibms-cost-of-a-data-breach-report-2025
- FICO APAC banks poll: https://techcoffeehouse.com/2026/10/16/77-of-apac-banks-want-purpose-built-ai-fico/
- Singapore fintech count: https://mothership.sg/2026/09/mas-fintech/
- MOM wages: https://stats.mom.gov.sg/Pages/IncomeTimeSeries.aspx

Regulation
- MAS AIRM release (7 Oct 2026): https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions
- MAS AIRM guidelines page: https://www.mas.gov.sg/regulation/guidelines/guidelines-on-artificial-intelligence-risk-management-for-financial-institutions
- MAS TRM PDF: https://www.mas.gov.sg/-/media/MAS/Regulations-and-Financial-Stability/Regulatory-and-Supervisory-Framework/Risk-Management/TRM-Guidelines-18-January-2021.pdf
- PDPC penalties: https://www.pdpc.gov.sg/News-and-Events/Announcements/2022/09/Amendments-to-Enforcement-under-the-Personal-Data-Protection-Act-in-updated-Advisory-Guidelines-and-Guide
- IMDA / AI Verify assurance pilot: https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2025/sg-unveils-insights-from-worlds-first-technical-testing-of-real-world-applications-of-genai
- CSA Cyber Essentials / Cyber Trust: https://www.csa.gov.sg/cyber-essentials/certification-for-the-cyber-essentials-mark ; https://www.sgs.com/en-sg/services/cyber-trust-mark

Aspire
- Homepage: https://aspireapp.com/
- AspireOS: https://aspireapp.com/en-sg/blog/aspire-launches-aspireos-its-ai-powered-platform-for-banks
- Growth/profitability post: https://aspireapp.com/en-sg/blog/aspire-grows-revenue-3x-yoy-hits-profitability-3-months-after-closing-us-100m-series-c
- MCP security & privacy: https://help.aspireapp.com/sg/en/articles/15550809-security-privacy

Feasibility (pricing, platforms, connectors)
- Tencent Cloud model pricing (Hy3): https://www.tencentcloud.com/document/product/1300/78937
- Tencent Lighthouse pricing: https://www.tencentcloud.com/document/product/1103/47794
- Tencent Cloud compliance (MTCS, OSPAR): https://www.tencentcloud.com/document/product/363/34543
- Slack rate-limit change (May 2025): https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps
- Slack ToS update: https://docs.slack.dev/changelog/2025/05/29/tos-updates ; analysis: https://www.natlawreview.com/article/salesforce-locks-down-slack-data-time-review-your-slack-api-terms
- Slack rate limits: https://docs.slack.dev/apis/web-api/rate-limits
- Slack Real-time Search API: https://docs.slack.dev/apis/web-api/real-time-search-api
- Glean Slack RTS connector: https://docs.glean.com/connectors/native/slack/setup/slack-rts-connector
- Atlassian rate limiting: https://developer.atlassian.com/cloud/confluence/rate-limiting ; https://www.atlassian.com/blog/platform/evolving-api-rate-limits
- Jira bulk permissions (community): https://community.developer.atlassian.com/t/im-cannot-check-a-user-permission-from-specific-jira-project-via-rest-api/52682
- Google Drive push notifications: https://developers.google.com/workspace/drive/api/guides/push
- Glean pricing (competitor-authored estimate): https://www.gosearch.ai/blog/glean-pricing-explained
