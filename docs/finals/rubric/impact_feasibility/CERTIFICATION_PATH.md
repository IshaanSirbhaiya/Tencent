# Certification path

We hold **no certifications today**. This is the path a regulated buyer's procurement would expect.
Timings are estimates.

| When | Item | Why it matters | Source |
|---|---|---|---|
| Now | Inherit Tencent Cloud Singapore's **MTCS Level 3** and **OSPAR** attestation for the hosting layer | Covers infrastructure in an FI's due diligence. OSPAR is the Association of Banks in Singapore's audit standard for outsourced providers | [Tencent Cloud compliance](https://www.tencentcloud.com/document/product/363/34543) [primary; confirm which products are in scope] |
| Now | MAS TRM and AI-risk mapping, with limits | Already written | `docs/tencent/MAS_TRM_MAPPING.md` |
| Pilot (weeks 0-10) | Third-party pen test | Standard first ask | [estimate of timing] |
| Pilot | **AI Verify / Project Moonshot** baseline tests (hallucination, data disclosure, adversarial prompts) | Singapore's own GenAI testing norms | [IMDA](https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2025/sg-unveils-insights-from-worlds-first-technical-testing-of-real-world-applications-of-genai) [primary] |
| 0-6 months | **CSA Cyber Trust mark** (tiered; valid 3 years) | Singapore-recognised and affordable. SGS lists fees from about S$1,375 plus S$225-450 per pillar; CSA subsidies up to S$3,600 | [CSA](https://www.csa.gov.sg/cyber-essentials/certification-for-the-cyber-essentials-mark), [SGS](https://www.sgs.com/en-sg/services/cyber-trust-mark) [verify fees] |
| 3-12 months | SOC 2 Type I, then Type II; ISO/IEC 27001 | What bank procurement asks for | general practice [estimate] |
| First bank customer | Support the bank's MAS outsourcing and third-party assessment; consider an OSPAR report if we become a material outsourced provider | The AspireOS route to banks | ABS OSPAR (via the Tencent compliance page) |
| By 7 Oct 2027 and 7 Oct 2028 | Supply AI-inventory inputs to customers: model, prompt version and scope per answer (already logged on every `answer.released`) | Customers must meet MAS AI-risk Sections 3-4 by Oct 2027 and 5-6 by Oct 2028 | [MAS](https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions) [primary]; `databridge_ib/pipeline.py` (model and `prompt_version` in the release record) |

**One line for Q&A:** "Infrastructure inherits Tencent's MTCS Level 3 and OSPAR. For us: pen test and AI Verify
tests in the pilot, CSA Cyber Trust within six months, then SOC 2."
