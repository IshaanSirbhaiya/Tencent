# D. Technical Execution and Responsible AI: research and deliverables

Research date: 30 Oct 2026, four days before Demo Day (3 Nov). Two criteria are covered:

- **Technical Execution:** "technical quality and completeness".
- **Responsible AI and Ethics:** "demonstrates responsible and trustworthy AI practices".

Repo facts come from `INTERNAL_BRAIN.md`, `docs/tencent/{SECURITY,RESPONSIBLE_AI,MAS_TRM_MAPPING,ARCHITECTURE}.md` and
`databridge/evals/internal_brain/RESULTS.md`. Web sources are linked inline and listed in section 9. Items marked
**[verify]** came from one secondary source, so check them before quoting them on stage.

---

## 0. Summary in ten lines

1. Technical judges score three things: does it run live, is the architecture sound and legible, and is there evidence beyond the demo (tests, evals, reviews).
2. We have unusually strong evidence: 0 leaks in 7,374 requests, 0 of 400 differential mismatches, 0 injection failures in 1,152 requests, 1,131 backend tests, 166 Playwright tests passing (30 Oct, commit 831297e; docs/finals/TEST_REPORT.md), and recorded security reviews (Codex tracks 1-7, CodeBuddy on Hy3, two Claude rounds) with written dispositions for all but Codex track 1.
3. Our biggest technical gap is that **Hy3 has never run live**. Every number comes from a capturing fake model or the extractive path. Close this before 3 Nov if TokenHub verification has cleared. If it has not, say so first, in one sentence.
4. Other gaps: a CI workflow exists (`.github/workflows/ci.yml`) (update 30 Oct: CI green, 17 successful runs, 0 failed), no live deployment (the Lighthouse kit exists but has never run), no real SSO, retrieval is lexical only, and there is no human review queue or feedback loop.
5. For Responsible AI, Singapore judges will think in **AI Verify (11 principles)**, **IMDA's GenAI framework (2024)**, the **agentic framework (Jan 2026)**, the **IMDA Starter Kit's four risks**, **MAS FEAT/Veritas** and the new **MAS AI Risk Management Guidelines (7 Oct 2026)**.
6. Our design already answers the hardest RAG risks: OWASP LLM08 (permission-aware retrieval), LLM02 (masking and a two-gate check), LLM01 (containment) and LLM06 (the model has no tools).
7. The missing pieces are mostly documents, not code: a system card that doubles as a MAS inventory entry, an AI Verify process-check self-assessment, an OWASP red-team report, a data card, a fairness note, an incident runbook and a privacy impact summary.
8. One new in-app page, **Trust center** (`/trust`), would turn all of this into something judges can see: live eval numbers from `results.json`, a "verify chain" button, an identical-bytes proof and the framework crosswalk.
9. Three live moments carry the Responsible AI score: break the audit chain and watch it fail; plant an injection and watch it be ignored; restricted and non-existent requests give byte-identical answers.
10. Expect the question "AWS Bedrock already re-checks ACLs live; what's new?" Answer: indistinguishable refusals (anti-tipping-off), identity bound to a signed token, ranking statistics computed only over permitted documents, and a sealed write-ahead audit log. Section 7 has the full answer.

---

## 1. Technical Execution

### 1.1 What judges look for (sourced)

| # | Signal | What it means in a 5-minute slot | Source |
|---|---|---|---|
| 1 | **It runs live** | "It WORKS. Live demo doesn't crash." Crashes, mockups and pre-loaded-only inputs score poorly | [Reskilll judging breakdown](https://blogs.reskilll.com/what-hackathon-judges-look-for-complete-judging-criteria-breakdown-2026/) |
| 2 | **End-to-end flow, not one screen** | The whole path works: auth, navigation, error states. It is deployed and reachable through a URL | same |
| 3 | **Error handling** | Judges ask "what happens with bad input?" Show one graceful failure on purpose | same |
| 4 | **Sound, simple architecture** | "A simple but sound architecture scores better than an unclear one." One diagram, read in 15 seconds | same |
| 5 | **Integration depth** | Frontend, backend, AI and external APIs integrated earns credit | same; [Devfolio judging guide](https://guide.devfolio.co/docs/guide/participating-in-hackathons/judging-1) |
| 6 | **Stability over bravado** | Mock risky external services and keep screenshot or video backups. "A working, stable demo matters more than a live API call that might fail" | [HackerEarth tips](https://www.hackerearth.com/blog/10-tips-win-hackathon) (via search summary) |
| 7 | **Evidence of quality** | For AI apps, testing the *application as deployed*, not the foundation model. Integration with data sources is where failures happen | [IMDA Global AI Assurance Pilot, May 2025](https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2025/sg-unveils-insights-from-worlds-first-technical-testing-of-real-world-applications-of-genai); [Main report](https://assurance.aiverifyfoundation.sg/wp-content/uploads/2025/05/Assurance-Pilot-Main-Report.pdf) |
| 8 | **Feasibility** | Tencent's May 2026 Singapore station judges (Tencent Cloud Chief Architect, an SMU dean, AISG) scored "feasibility as well as technical execution" | `docs/finals/research/05_competition_pitch.md` §1, citing [AAP/PR Newswire](https://www.aap.com.au/aapreleases/cision20260513ae58232/) **[verify]** (we could not re-read the page today) |

**Who is judging.** Tencent WorkBuddy/CodeBuddy and RTC engineers will look for real use of Tencent products and clean
engineering. AISG will look for evaluation rigour. DBS and Aspire will look for production readiness in a regulated
setting (audit, access control, MAS). Keppel, Ryde and NTU will look for clarity and feasibility.

### 1.2 What winners did

There is little public evidence. No 2025-26 hackathon write-up we found credits a winning RAG team for its eval harness
or red-teaming (search on 30 Oct). What we can say:

- The Tencent Singapore station winners (May 2026: "Auntie AI", "EyeDentify") had a **working demo, a named human user and local relevance** (`05_competition_pitch.md` §3).
- Enterprise products set the bar for "complete": permission-aware retrieval with crawled ACLs and identity crawling ([Amazon Q Business connectors](https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/connector-concepts.html)), **a real-time permission re-check at query time that fails closed** ([Bedrock managed KB ACLs](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)), and enforcement "upstream of the language model" ([Glean](https://www.glean.com/perspectives/top-ai-assistants-for-document-permissions-management)).
- **Implication:** a judge who knows Bedrock or Glean will see that our design follows the pattern industry ships. We then win on what goes beyond it (section 7, Q1).

### 1.3 Where we stand: the completeness checklist for enterprise RAG

Status key: **Done** = built and tested. **Partial** = built with a stated limit. **Missing** = not built.

| # | Capability | What "complete" means | Our status | Evidence | Gap / action |
|---|---|---|---|---|---|
| 1 | Connectors | Native APIs for each source | **Partial.** Four connectors (Slack, Confluence, Jira, Drive); real code, mock tenants | `connectors/*.py`; `ARCHITECTURE.md` §1 | Never run against a real tenant. Say "same connector code, injected base URL" |
| 2 | Sync and freshness | Initial crawl, change events, reconciliation, freshness SLO | **Done** | Signed webhooks (HMAC over ts.delivery.body, ±300 s, replay check); poller every 120 s with full reconciliation every 10th tick; `/ib/sources` `lag_s`/`within_slo` | Replay set is per process (`SECURITY.md` 3a-9) |
| 3 | ACL ingestion | Native permission model compiled per document; unknown means deny | **Done** | Compiled `AclExpr`; anything outside the supported subset is `nobody`; 30+ fail-closed shape tests (`SECURITY.md` §9) | Group namespace needs a tenant `group_resolver` in production (3a-7) |
| 4 | Query-time authorisation | Live re-check before generation | **Done** (Gate 2) | 2 s batch budget; error, timeout or 429 means deny; `test_s4_revocation_without_webhook` | Correctness depends on the platform API (JRACLOUD-84541) |
| 5 | Identity | SSO, short-lived tokens, revocation | **Partial** | HMAC token, ≤ 8 h, `jti` revocation that fails closed; no identity in any body | Demo login stands in for SSO; no IdP |
| 6 | Indexing and retrieval | Hybrid lexical plus semantic, reranker | **Partial** | BM25 over the permitted corpus only; offline hashing embedder; relevance gate | Lexical relevance answered 7 expected-refusal questions (RESULTS.md finding 1). No semantic embedder or reranker |
| 7 | Generation | Production model with fallback | **Partial** | Hy3 → deepseek-v4.1-flash → extractive; `test_llm_chain.py` | **Hy3 never called live** |
| 8 | Grounding and citations | Every claim cited; unsupported values refused | **Done** | Walnut grounding wall; versioned citations with `authz_checked_at`; 0 uncited answers in the final run | Relations between facts are not verified (P7); one valid citation is enough (finding 15) |
| 9 | Evaluation harness | Leak, injection, utility and canaries; reproducible | **Done** | `python -m evals.internal_brain.run`; exits 1 on any leak; canaries prove it can fail | No real-model run; no faithfulness or answer-quality score on a real model |
| 10 | Security review | Independent review with dispositions | **Done** (with one gap) | Codex tracks 1-7, CodeBuddy on hy3, Opus/Sonnet/Haiku rounds | Track 1 (35 findings) has no written disposition; findings 3, 9, 11, 12 and 15 are open |
| 11 | Audit and observability | Who saw what; tamper evidence; metrics and alerts | **Partial** | Write-ahead sealed ledger; external head; content hash check; `/audit/verify` | No metrics, tracing or alerting; no service-account monitoring (TRM 9.2.2); timing is not controlled |
| 12 | Deployment | Reproducible deploy, HTTPS, secrets | **Partial** | `deploy/lighthouse/` (Caddy, Docker Compose, `smoke.sh`); `assert_deployable` refuses dev keys | **No live instance** (Lighthouse capacity was sold out) |
| 13 | CI | Tests on every push | **Missing** | No `.github/workflows/` | Add a GitHub Action running pytest, the `--limit 60` eval and Playwright in mock mode. About 1 hour of work |
| 14 | Cost control | Token caps, no-call paths, budgets, rate limits | **Partial** | Refusals make **no model call** (2,640 of 3,687 fake-capture eval requests needed none); ≤ 14 passages, 2 per document; fallback chain | No per-user rate limit or budget; no token accounting in the ledger |
| 15 | Fallbacks and kill switch | Degrade safely | **Done** | Extractive fallback; `IB_FORCE_OFFLINE=1`; any ledger failure is a 503 | The kill switch needs a restart and is global only |
| 16 | Sensitive data | PAN, NRIC and IBAN masked before index and model | **Done** | `sensitive.py`; `test_sens_raw_values_appear_nowhere_in_the_index` | Other personal data is not redacted |
| 17 | Accessibility | WCAG checks | **Done** (automated) | axe on 5 screens at desktop and phone width; keyboard tests | No testing with assistive technology |
| 18 | Multilingual | Aspire's markets: SG, ID, TH, VN | **Missing** | English only; market-specific *policies* for SG and ID | Mention it as roadmap |
| 19 | Feedback loop | Thumbs-down feeding review | **Missing** | none | Roadmap |
| 20 | Retention and deletion | Defined retention; crypto-shredding | **Missing** | none (`MAS_TRM_MAPPING.md` 12.2.2) | Roadmap: COS Object Lock (WORM) |
| 21 | Load and scale | Large ledgers, concurrency | **Missing** | Mean 19-20 ms in the eval, excluding the model | Large-ledger load test not written (`SECURITY.md` §11 #9) |
| 22 | Second front door | API or agent integration | **Done** | WorkBuddy MCP servers (no identity argument); Playwright and pytest coverage | WorkBuddy's own model sits outside our ledger (TB7) |

**Score honestly:** 10 Done, 7 Partial, 5 Missing (22 rows). That is strong for a hackathon. The Partial rows are where judges
will probe.

### 1.4 Our concrete deliverables: Technical Execution

All paths below are new files under `docs/finals/rubric/` unless shown otherwise.

| # | Artefact | File | Outline | Where it shows in the demo |
|---|---|---|---|---|
| T1 | **Architecture one-pager** | `technical/ARCHITECTURE_ONE_PAGER.md` + `technical/architecture.png` | (a) The request path as 6 boxes: token → Gate 1 → Gate 2 → sealed ledger → Hy3 → grounding wall → one response shape. (b) Trust boundaries TB1-TB9 as small badges. (c) Tencent products marked: TokenHub/Hy3, WorkBuddy MCP, CodeBuddy (built and reviewed), Lighthouse kit. (d) "Fail closed" marks on every arrow that can fail. (e) A footer listing what is mocked | One slide, 15 seconds, right after the problem. Reuse `ARCHITECTURE.md`'s mermaid diagrams, simplified |
| T2 | **Evidence card** (test and eval dashboard) | `technical/EVIDENCE_CARD.md` plus the in-app Trust center (R9) | One table: test counts (1,131 backend; 166 browser, docs/finals/TEST_REPORT.md; *update 30 Oct: now 1,274 and 185*), the eval headline (RESULTS.md), the canaries, review rounds and finding counts, and **one command each** to reproduce | A 10-second slide; Trust center during Q&A |
| T3 | **Completeness checklist** | `technical/COMPLETENESS_CHECKLIST.md` | Table 1.3 above, with Done/Partial/Missing and the next step for each row | Appendix slide; hand it over when a judge asks "is this production-ready?" |
| T4 | **CI badge** | `.github/workflows/ib.yml` (code change, outside this research) | Jobs: `pytest apps/internal_brain/tests -q`; `python -m evals.internal_brain.run --limit 60` (fails on any leak); `npm run e2e` in mock mode | A green badge on the README and the evidence slide. It closes the "no CI" gap in about 1 hour |
| T5 | **Live Hy3 run** | Re-run the eval with `TOKENHUB_API_KEY`; append a "hy3" mode to `RESULTS.md` | Same checks; publish any non-zero count (the doc already promises this) | Ask one question live on Hy3 with the model name shown in the answer. If it is still blocked, show the extractive answer and say why in one sentence |
| T6 | **Demo reliability runbook** | `technical/DEMO_RUNBOOK.md` | Pre-warm the index (about 1 minute); `IB_FORCE_OFFLINE` fallback; recorded backup video; fixed persona order; "Reset demo"; port checks; what to do if Gate 2 times out on venue Wi-Fi (everything is localhost) | Not shown; it protects the live demo |
| T7 | **Failure on purpose** | Part of the demo script | Stop the ledger (or show `test_ledger_failure_is_503`): the answer becomes a 503, never an unlogged answer | 10 seconds; it answers "what happens with bad input" before anyone asks |
| T8 | **Cost and latency card** | `technical/COST_AND_LATENCY.md` | Measured: 19-20 ms mean pipeline latency excluding the model; 77% of eval requests needed no model call. **Estimate, to verify on the live run:** about 2-3k input tokens and about 200 output tokens per answered question at Hy3's list price (about 1 yuan per million input tokens and 4 per million output, `05_competition_pitch.md` §4), so well under one Singapore cent per answer | One line on stage; full card in the appendix |

### 1.5 Proof points to say on stage (Technical Execution)

1. "Everything you see is running on this laptop now: four platforms, the Brain and the web app. Nothing is pre-recorded."
2. "Two checks run before the model sees a word. The second asks Slack, Confluence, Jira or Drive live. If they don't answer in two seconds, the answer is no."
3. "We attacked it 7,374 times. Zero leaks. And we proved the test can fail: switch the checks off and it catches 18 of 20 leaks."
4. "1,131 backend tests, 166 browser tests, and ten security review passes, including one CodeBuddy ran on Hunyuan Hy3. Every finding is written down with its fix."
5. "If the audit log can't be written, you get no answer. Watch." (T7)
6. "Same connector code for mock and real: only the base URL changes."
7. If Hy3 has run: "This answer was written by Hunyuan Hy3 through TokenHub, and it went through exactly the same checks." If not: "Hy3 is wired in and tested. Tencent account verification is pending, so today's answers are quoted from the sources, and that is the safer path anyway."

### 1.6 Likely judge questions: Technical Execution

| # | Question | Answer (about 20 seconds) |
|---|---|---|
| 1 | "Is any of this real, or all mocked?" | "The four platforms are mocks that copy the real APIs' permission models, webhooks, errors and rate limits. The connectors are real code with an injected base URL, so pointing them at a tenant changes configuration, not code. We haven't run against a live tenant yet." |
| 2 | "Did Hy3 actually run?" | Truthful version of proof point 7. Never imply otherwise. |
| 3 | "What's the latency with two permission checks?" | "The pipeline averages about 20 ms in our eval, excluding the model. Gate 2 checks in parallel under one 2-second budget. Production latency is dominated by the model call." |
| 4 | "How does it scale to 100k documents?" | "Gate 1 is an indexed filter. Gate 2 only checks the shortlist (up to 12 hits plus 4 per named platform), so live calls scale with the answer, not the corpus. Large-ledger load tests aren't written yet; incremental verification is the fix (`SECURITY.md` track 2 #8)." |
| 5 | "Why lexical search, not embeddings?" | "Embeddings would send every chunk to a provider before any permission check (finding 11), so we kept a local embedder and BM25 computed over the asker's permitted documents only. A semantic reranker on released passages is the next step." |
| 6 | "What happens when Slack is down?" | "Gate 2 denies. You get fewer sources, never more. Ingest retries; Gate 2 never does." |
| 7 | "How do you know your eval isn't vacuous?" | "Two canaries: a deliberately leaky configuration must be caught (18 of 20) and a deny-all configuration must score 0% utility. If either canary fails, the run is invalid." |
| 8 | "What would you build next with Aspire?" | "Real SSO, one live tenant (Confluence first), a semantic reranker, WORM storage for the ledger, and a review queue for low-confidence answers." |
| 9 | "How was CodeBuddy used?" | "It built the WorkBuddy connector and the token tool, drew the trust boundaries, ran a security review on Hy3, then fixed its own code after a Codex review. Every transcript is kept (`DEV_PROCESS.md`)." |

---

## 2. Responsible AI: the frameworks Singapore judges will know, mapped to our system

Status key: **Meets** = mechanism plus test. **Partial** = mechanism with a stated limit. **Missing** = not built.
"Evidence" points to files in the repo.

### 2.1 AI Verify (IMDA / AI Verify Foundation)

**What it is.** A testing framework of **11 principles** assessed through **process checks** (documentary evidence,
marked Yes/No/N/A) plus technical tests. The process checks per principle are: transparency 15, explainability 1,
reproducibility 10, safety 17, security 14, robustness 10, fairness 6, data governance 4, accountability 14, human
agency and oversight 9, inclusive growth 6. It was updated on 29 May 2025 to cover generative AI. A **Process Checks
Tool** generates a summary report, and **Project Moonshot** is the technical testing tool for generative AI
([AI Verify Foundation](https://aiverifyfoundation.sg/what-is-ai-verify/);
[sample report](https://aiverifyfoundation.sg/downloads/AI_Verify_Sample_Report.pdf)).

| Principle | Our status | Evidence | Missing |
|---|---|---|---|
| Transparency | Partial | Answers name the model and fallback; citations show version and check times; `INTERNAL_BRAIN.md` "Honest limits" | A user-facing system card; an "AI-generated" label on the answer |
| Explainability | Meets | Per-answer citations; the "How this was checked" strip; ledger `retrieval.decided` lists considered and released documents with each Gate 2 verdict (compliance only) | A "why this answer" panel for the asker (R8) |
| Reproducibility | Meets | Deterministic gates, ranking, time parsing and extractive answer; fixed seed; world sha256 and seed 7 in RESULTS.md; `PROMPT_VERSION` logged | Model ids are aliases, not pinned builds |
| Safety | Meets | Constant refusal; no model call when nothing is relevant; grounding wall; extractive fallback | Relations between facts are not verified |
| Security | Meets | `SECURITY.md` threat model; Codex tracks 1-7, CodeBuddy and two Claude review rounds; 0 injection failures in 1,152 requests | Track 1 disposition; rate limits; timing side channel |
| Robustness | Partial | Fail closed on every error (PageGuard, Gate 2 timeouts, ledger 503) | No load tests; no real-model robustness run |
| Fairness | Partial | Same gates for every role; per-persona utility table in RESULTS.md | No written fairness note (R5); English only |
| Data governance | Meets | Synthetic data only; PAN, CVV, NRIC and IBAN masked at ingest; ids and hashes, not text, in the ledger | Retention policy; a data card (R3) |
| Accountability | Meets | Write-ahead sealed ledger; tamper detection (6 of 6 attacks; the 7th is the stated window) | Dual control on reveal |
| Human agency and oversight | Partial | Compliance console; reveal only with a reason, ledgered first; admin simulator; data owners stay the authority | Review queue, feedback routing, dual control |
| Inclusive growth | Partial | Reduces search time (Aspire: about 30% of the week); axe accessibility checks | Multilingual; assistive-technology testing |

**Action:** fill in the official AI Verify process-check list (Excel or Process Checks Tool) honestly and attach its
summary. "We ran ourselves through AI Verify's own checklist" is a sentence AISG judges will recognise. Marking the
GenAI checks takes about 3-4 hours; the founder must download the tool.

### 2.2 IMDA Model AI Governance Framework for Generative AI (30 May 2024)

Nine dimensions ([Baker McKenzie summary](https://insightplus.bakermckenzie.com/bm/technology-media-telecommunications_1/singapore-new-model-ai-governance-framework-for-generative-ai-launched-by-imda-and-ai-verify-foundation);
[OECD catalogue](https://oecd.ai/en/catalogue/tools/model-ai-governance-framework-for-generative-ai)). The wording of
dimensions 7-9 differs between summaries **[verify]**.

| Dimension | Status | Our mechanism / evidence | Missing |
|---|---|---|---|
| 1 Accountability | Meets | Ledger records who, what, which model, which prompt version | RACI between the deployer (Aspire) and the model provider (TokenHub) |
| 2 Data | Meets | Synthetic world; masking; permission-filtered context | Data card |
| 3 Trusted development and deployment | Partial | Disclosure of limits in `INTERNAL_BRAIN.md`; deploy kit refuses dev keys | System card ("food label") |
| 4 Incident reporting | **Missing** | Kill switch exists (`IB_FORCE_OFFLINE`) | Incident runbook (R6) |
| 5 Testing and assurance | Meets | Leak eval with canaries; external-model reviews | No third-party *human* assurance; Moonshot run optional (R4b) |
| 6 Security | Meets | Threat model; OWASP mapping (R4) | n/a |
| 7 Content provenance | Partial | Source provenance: every sentence cites a versioned source; the answer names the model that wrote it | No watermark or label on generated text (low value for an internal tool) |
| 8 Safety and alignment R&D | n/a | n/a | n/a |
| 9 AI for public good | Partial | Anti-tipping-off and DLP protect the public and customers | n/a |

### 2.3 IMDA Model AI Governance Framework for Agentic AI (22 Jan 2026)

Four dimensions: bound autonomy and tool/data access up front; meaningful human accountability; technical controls
across the lifecycle; end-user responsibility through transparency and training
([summary](https://singaporelegaladvice.com/singapore-new-agentic-ai-governance-framework/);
[launch](https://www.smartcitiesworld.net/news/singapore-launches-governance-framework-for-agentic-ai)). A later
v1.5 is reported by one vendor **[verify]**.

**This is our strongest framing for Tencent and AISG judges.** The Brain's model has **zero tools** and cannot choose an
identity (`llm.py:65-81`). When WorkBuddy, an agent, calls the Brain, each persona has its own connector with its own
token and no identity argument (`test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument`). So
**the agent's data access is bounded by the human's permissions, checked live**. That is the framework's first
dimension, implemented. The limit to state: WorkBuddy's own model and memory sit outside our ledger (TB7).

### 2.4 IMDA Starter Kit for testing LLM-based applications (consulted May 2025; v1.0 Jan 2026)

It covers four risks: **hallucination, undesirable content, data disclosure, vulnerability to adversarial prompts**.
Tests are progressively added to Project Moonshot
([FutureCIO](https://futurecio.tech/imda-unveils-worlds-first-guidelines-for-safety-testing-of-llm-based-applications);
[IMDA PDF](https://www.imda.gov.sg/-/media/imda/files/about/emerging-tech-and-research/artificial-intelligence/starter-kit-for-testing-llm-based-applications-for-safety-and-reliability.pdf);
[Moonshot CI/CD](https://github.com/aiverify-foundation/moonshot-cicd)).

| Starter Kit risk | Our test | Result |
|---|---|---|
| Data disclosure | Leak eval: P1 transport capture, fingerprints in payload, answer and response; P2 differential | 0 / 7,374; 0 / 400 |
| Adversarial prompts | Injection probes on all four platforms (`INJ-*`), forged citations, base64, tool text | 0 hard failures / 1,152 |
| Hallucination | Grounding wall; uncited-answer count; cited recall | 0 uncited; cited recall 87.8-96.4% |
| Undesirable content | Not tested (internal content, no generation beyond cited sentences) | Gap; state it as low-risk by design |

**Say it this way:** "Our eval covers three of IMDA's four Starter Kit risks with measured numbers. The fourth,
undesirable content, is bounded because the model can only restate cited company text."

### 2.5 MAS FEAT principles and the Veritas toolkit

FEAT means Fairness, Ethics, Accountability, Transparency. Veritas published assessment methodologies in 2022 and
**Veritas Toolkit 2.0** (June 2023) as open source ([MAS Veritas](https://www.mas.gov.sg/schemes-and-initiatives/veritas);
[2023 toolkit release](https://www.mas.gov.sg/news/media-releases/2023/toolkit-for-responsible-use-of-ai-in-the-financial-sector);
[FEAT assessment methodology](https://www.mas.gov.sg/-/media/MAS-Media-Library/news/media-releases/2022/Veritas-Document-3---FEAT-Principles-Assessment-Methodology.pdf)).
Veritas fairness metrics are built for **decisions about customers** (credit, insurance, marketing). Our system makes
no such decisions.

| FEAT | Applies how | Our status |
|---|---|---|
| Fairness | No decision about individuals, so Veritas group metrics do not apply directly. Fairness here means equal service quality across roles and markets | Partial: per-persona utility in RESULTS.md; R5 note |
| Ethics | Anti-tipping-off; DLP; the system never acts | Meets |
| Accountability | Write-ahead sealed ledger; reasoned reveal | Meets |
| Transparency | Citations; model named; limits disclosed | Partial: system card missing |

**Also know:** Project MindForge's seven GenAI risk dimensions (accountability and governance, monitoring and
stability, transparency and explainability, fairness and bias, legal and regulatory, ethics and impact, cyber and data
security). DBS was in the consortium ([MAS MindForge](https://www.mas.gov.sg/schemes-and-initiatives/project-mindforge);
[2023 release](https://www.mas.gov.sg/news/media-releases/2023/mas-partners-industry-to-develop-generative-ai-risk-framework-for-the-financial-sector)).
A DBS judge may use these words.

### 2.6 MAS Guidelines on AI Risk Management (issued 7 Oct 2026)

Expectations: board and senior-management oversight; **identify AI use and keep an inventory**; **assess risk
materiality** per use case; proportionate lifecycle controls (data governance, testing, human oversight,
cybersecurity, monitoring, change management); FIs remain accountable for third-party AI. Sections 3-4 apply from
**7 Oct 2027**, Sections 5-6 by **7 Oct 2028**. MAS intends to consult in 2027 on agentic AI
([MAS media release](https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions);
[Allen & Gledhill](https://www.allenandgledhill.com/sg/publication/articles/34686/mas-publishes-ai-risk-management-guidelines-setting-out-supervisory-expectations-on-responsible-ai-adoption);
[Baker McKenzie](https://www.bakermckenzie.com/en/insight/publications/2026/10/singapore-mas-issues-guidelines-on-artificial-intelligence-risk-management)).

Our existing paragraph-level mapping is `MAS_TRM_MAPPING.md` §2 (§4.7, §5.4(d)-(e), §5.9(c), §5.23(b), §5.23(d)).

| Expectation | Status | Gap → deliverable |
|---|---|---|
| Inventory entry | Provides inputs (model, prompt version per answer) | **System card formatted as an inventory record** (R2) |
| Risk materiality | Missing | One-page materiality assessment inside the system card: impact (internal staff, no customer decisions, but tipping-off and data-protection exposure), complexity (RAG, third-party model), reliance (advisory; humans decide) |
| Testing | Meets | Eval, canaries, reviews |
| Human oversight / escalation (§5.9(c)) | Partial | A deterministic fallback, not a human review queue → describe it in the oversight design (R6) |
| Kill switch (§5.23(b)) | Partial | Global and needs a restart → runbook step, roadmap for per-source switches |
| Logging prompts and responses with model versions (§5.23(d)) | Aligned | Prompt not stored verbatim; configuration changes not ledgered |
| Third-party AI (TokenHub) | Partial | Provider retention not verified → state it in the privacy summary (R7) |

**Stage line:** "MAS issued its AI risk guidelines on 7 October. They expect an inventory, a materiality assessment
and lifecycle controls. Our system card is the inventory entry, and our ledger is the logging they ask for."

### 2.7 PDPA and PDPC guidance on AI

- **PDPA s.24 (protection) and s.26 (transfer):** mapped in `MAS_TRM_MAPPING.md` §3. The TokenHub endpoint and provider retention are **not verified**.
- **PDPC Advisory Guidelines on personal data in AI recommendation and decision systems (1 Mar 2024):** cover development, deployment (business to consumer) and procurement (business to business). They discuss the business-improvement and legitimate-interests exceptions, and the PDPA does not override sectoral (financial) rules ([PDPC PDF](https://www.pdpc.gov.sg/-/media/files/pdpc/pdf-files/advisory-guidelines/advisory-guidelines-on-the-use-of-personal-data-in-ai-recommendation-and-decision-systems.pdf); [Baker McKenzie](https://insightplus.bakermckenzie.com/bm/data-technology/singapore-pdpc-releases-advisory-guidelines-on-the-use-of-personal-data-in-ai-recommendation-and-decision-systems)). Our relevance: we train nothing; we use employee-authored content **for its existing purpose**, to the same people who can already open it. That is a minimal-new-purpose argument; take a legal view before relying on it.
- **PDPC Proposed Guide on Synthetic Data Generation (15 Jul 2024):** synthetic data is generally not personal data but carries re-identification risk ([PDPC](https://pdpc.gov.sg/help-and-resources/2024/07/proposed-guide-on-synthetic-data-generation)). Our Company A is generated from a seed, not derived from real records, so re-identification risk is nil by construction. Put this in the data card.

| PDPA obligation | Status | Gap |
|---|---|---|
| Protection (s.24) | Supports | — |
| Purpose limitation / notification (ss.18, 20) | Partial: content is used for its existing purpose | Notice to staff that questions are logged → put it in the login screen copy |
| Access and correction (ss.21, 22) | Missing | Staff cannot see their own question history (only compliance can) → roadmap |
| Retention limitation (s.25) | **Missing** | No retention period |
| Transfer (s.26) | Partial | Provider terms not verified |

### 2.8 ISO/IEC 42001, NIST AI RMF and the EU AI Act

- **ISO/IEC 42001:2023** is a management-system standard. Relevant clauses: **6.1.4 AI system impact assessment** (effects on individuals, groups and society), **8.4** (repeat it after major changes), and an Annex A Statement of Applicability ([Advisera](https://advisera.com/articles/iso-42001-clauses-requirements/)). We have no AI management system, so do not claim alignment. Say: "Our system card plus impact note is the 6.1.4 artefact an AIMS would need."
- **NIST AI RMF 1.0 and the GenAI Profile (NIST AI 600-1, July 2024):** Govern, Map, Measure, Manage, with 12 GenAI risks ([NIST AI 600-1](https://airc.nist.gov/docs/NIST.AI.600-1.GenAI-Profile.ipd.pdf); [Hunton summary](https://www.hunton.com/privacy-and-cybersecurity-law-blog/nist-generative-ai-profile-highlights-actions-for-addressing-data-protection-risks-associated-with-generative-ai)). The best fit is **Measure**: the leak eval measures data privacy, information security and information integrity. **Confabulation** is handled by the grounding wall and extractive fallback.
- **EU AI Act:** the Annex III high-risk uses in finance are **creditworthiness and credit scoring of natural persons** (5(b)) and **life and health insurance risk and pricing** (5(c)); employment uses are also high-risk ([Recital 58](https://artificialintelligenceact.eu/recital/58/)). An internal knowledge assistant that makes no decision about people is **not Annex III high-risk**. Article 50 transparency (telling people they are dealing with AI) is the relevant duty. The Digital Omnibus provisional deal moves stand-alone Annex III obligations to **2 Dec 2027** ([Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/); [Sidley](https://datamatters.sidley.com/2026/06/22/eu-lawmakers-reach-provisional-agreement-to-delay-key-eu-ai-act-obligations/)). The Article 50 date under the Omnibus is **[verify]**. Caveat for Q&A: if the Brain were wired into HR or credit decisions about individuals, that use could become high-risk. Our scope statement ("not a decision-maker", `RESPONSIBLE_AI.md` §0) is the control.

### 2.9 OWASP Top 10 for LLM Applications 2025 (v2.0, Nov 2024)

([OWASP release](https://genai.owasp.org/2024/11/17/owasp-reveals-updated-2025-top-10-risks-for-llms-announces-new-llm-project-sponsorship-program-and-inaugural-sponsors/);
[LLM08](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/);
[LLM02](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)). This table is the skeleton of the
red-team report (R4).

| ID | Risk | Our control | Evidence | Residual |
|---|---|---|---|---|
| LLM01 | Prompt injection | Containment: access before content; no tools; delimited "untrusted data" passages; citation-tag neutralisation; addressed-to-AI sentences dropped; no arbitrary fetch | `SECURITY.md` §4; 0 / 1,152 injection failures | Delimiting is not datamarking; real-model behaviour not yet measured |
| LLM02 | Sensitive information disclosure | Two gates before the model; PAN, CVV, NRIC and IBAN masked at ingest; one response shape | 0 leaks / 7,374; `test_sensitive.py` | Other personal data in permitted passages; timing not controlled |
| LLM03 | Supply chain | `requirements-lock.txt`; vendor SDKs only through the harness; model aliases | `ARCHITECTURE.md` §6 | No SBOM; no pinned model builds; gitleaks not run |
| LLM04 | Data and model poisoning | No training; poisoned docs can only reach someone who can already read them; grounding wall | Injection fixtures | A malicious author can still mislead readers of their own page (by design; the citation shows who wrote it) |
| LLM05 | Improper output handling | Markdown stripped; links rendered only for http(s); answer text never executed | `codex_track6` #4 | — |
| LLM06 | Excessive agency | **The model has no tools**; identity never from the model; WorkBuddy tools take no identity | `llm.py:65-81`; `test_mcp_server.py` | WorkBuddy's own agent is outside our boundary |
| LLM07 | System prompt leakage | The prompt holds no secrets or permissions; authorisation lives in code, not in the prompt | `narrate.py:21-35` | Prompt is not confidential and does not need to be |
| LLM08 | Vector and embedding weaknesses | **Permission-aware index**: per-user readable set; BM25 statistics over permitted docs only; local embedder (no inversion exposure to a provider); P2 differential | `test_p2_differential...`; 0 / 400 | Remote embedder would break this (TB8, disabled) |
| LLM09 | Misinformation | Grounding wall; citations with version and check time; extractive fallback; approved-policy-only rule (draft fee schedule never cited) | `test_fee_draft_is_never_indexed...` | Relations between facts not verified; lexical relevance can answer from related pages (7 cases) |
| LLM10 | Unbounded consumption | No model call on refusal; 14-passage cap; 2,000-char question; 64 KiB body; 2 s Gate 2 budget | `models.py`; proxy caps | **No rate limiter**; audit verify is O(records) (track 2 #8) |

**OWASP LLM08's own mitigation list** is "permission-aware vector and embedding stores", "partition by access level"
and "detailed, immutable logs of retrieval activity". That reads like a description of our design. Quote it on the
red-team slide.

### 2.10 Real incidents to anchor the "why"

- **EchoLeak (CVE-2025-32711), Microsoft 365 Copilot:** zero-click indirect prompt injection, CVSS 9.3, patched June 2025 ([The Hacker News](https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html)).
- **Slack AI (Aug 2024):** an instruction planted in a public channel made Slack AI surface private-channel data ([PromptArmor](https://promptarmor.substack.com/p/data-exfiltration-from-slack-ai-via)).
- Both are already cited in `INTERNAL_BRAIN.md`. Our `INJ-slack-base64` and `INJ-drive-audit-tools` fixtures re-enact these attack shapes.

---

## 3. Responsible AI: what judges look for and what winners did

### 3.1 What judges look for (sourced)

1. **Testing the deployed application, not just the model.** This is the main lesson of IMDA's Global AI Assurance Pilot (17 deployers, 16 testers, 10 industries). Risk tolerance depends on the use case, and integrating models with data sources creates failure points ([IMDA](https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2025/sg-unveils-insights-from-worlds-first-technical-testing-of-real-world-applications-of-genai)). A RAG participant's testing centred on grounding and retrieval correctness (Unique / QuantPi, a vendor account **[verify]**).
2. **Documented, checkable claims:** AI Verify's process checks need documentary evidence for each item ([AI Verify](https://aiverifyfoundation.sg/what-is-ai-verify/)).
3. **Human oversight and accountability:** a constant theme across MGF-GenAI, the agentic MGF, FEAT and the MAS guidelines.
4. **Honesty about limits:** judges discount teams that over-claim. Our "Honest limits" section is an asset; keep it visible.
5. **Proportionality:** MAS asks for controls scaled to materiality ([MAS](https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions)). Show that you thought about *which* risks matter for *this* use case, not a generic ethics slide.

### 3.2 What winners did

There is no public record of how Responsible AI was scored at previous Tencent Cloud stations. The pattern from
regulated-industry pitches (and the reason Aspire set this brief) is that **one visible trust moment beats a slide of
principles**. Our plan is three live trust moments (section 4.3) and one Trust center page.

---

## 4. Responsible AI: our concrete deliverables

All paths are under `docs/finals/rubric/responsible_ai/` unless shown otherwise. "Effort" assumes the existing docs
are reused.

| # | Artefact | File | Outline | Effort | Where it shows |
|---|---|---|---|---|---|
| R1 | **AI Verify-style self-assessment** | `AI_VERIFY_SELF_ASSESSMENT.md` (+ the official tool's summary PDF if produced) | For each of the 11 principles: outcome; our process (Yes/No/N/A); evidence path; gap. Header: "self-assessed, not certified; synthetic data". Counts: X of 106 process checks evidenced | 3-4 h | Q&A; Trust center tab |
| R2 | **System card** (doubles as MAS AIRG inventory entry) | `SYSTEM_CARD.md` | 1 Intended use and out-of-scope uses ("not a decision-maker"). 2 Users and roles. 3 Model(s): Hy3 → deepseek → extractive; prompt version; kill switch. 4 Data: sources, masking, synthetic world. 5 Controls: two gates, grounding, ledger. 6 Evaluation summary (RESULTS.md headline incl. 95% bounds). 7 Known limits (copy "Honest limits"). 8 **Risk materiality** (impact, complexity, reliance). 9 Inventory fields per MAS AIRG §4.7 (owner, scope, jurisdiction SG/ID, model type, third parties). 10 Change log. Format follows [Model Cards (Mitchell et al.)](https://arxiv.org/abs/1810.03993) adapted to a system | 2 h | One appendix slide; Trust center |
| R3 | **Data card for Company A** | `DATA_CARD_COMPANY_A.md` | Motivation; composition (32 people, 209 pages, 251 issues, 602 threads, 81 files; personas; planted facts: FinCrime, market policies, draft fee schedule, INJ-*, SENS-*); generation (seed 7, generator v1, world sha256); what is *not* in it (no real people or companies; only published test card numbers); known biases (English only; SG/ID markets only; one org shape); intended use (eval and demo only); PDPC synthetic-data note (not derived from real records) | 1.5 h | Q&A ("where did your data come from?") |
| R4 | **OWASP LLM Top 10 red-team report** | `OWASP_LLM_REDTEAM.md` | Section 2.9 table expanded. For each risk: attack tried; test or probe id; result; residual; fix. Include the first-run failures (6 forged citations, 102 uncited answers) and their fixes. **Showing what broke is the credibility.** Appendix: the probe taxonomy (16 types × 11 personas) from RESULTS.md | 2-3 h | A slide titled "We attacked it 7,374 ways" |
| R4b | (Optional) **Moonshot run** | `MOONSHOT_RUN.md` | Point AI Verify's Moonshot at `/ib/ask` through a custom connector with a jdoe token; run its prompt-injection and data-disclosure cookbooks; report as is | 0.5-1 day, risky | Only if it finishes by 2 Nov; otherwise list it as next step |
| R5 | **Fairness note** | `FAIRNESS_NOTE.md` | (a) Why classic fairness metrics (Veritas, demographic parity) don't apply: no decision about individuals. (b) What does apply: **equal quality of service** across roles. Cite our per-persona cited recall (e.g. fake-capture 95.5-100% across personas; extractive 80-100%; mlro.tan world must-cite 71.4% on the model path) and say where it is uneven. (c) **Exposure fairness across sources**: results are interleaved across platforms so one platform can't crowd out the others (`pipeline.py` re-rank). RAG exposure bias is a known issue ([Dehghan & McDonald 2026](https://arxiv.org/abs/2603.24218); [Does RAG introduce unfairness?](https://arxiv.org/pdf/2409.19804)). (d) **Denial is not unfairness**: a contractor seeing less is the source system's policy, mirrored, not created. (e) Inclusion gaps: English only; Aspire's ID, TH and VN markets | 1.5 h | Q&A only |
| R6 | **Human oversight design + incident runbook** | `HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md` | Oversight: who can do what (`RESPONSIBLE_AI.md` §2); automatic escalation triggers (grounding violation, model error, uncited answer → extractive); what is *not* built (review queue, dual control). Runbook for 5 incidents: (1) suspected leak → find recipients with `/audit/doc/{id}/accessors`, revoke at the source (honoured on the next question), preserve the ledger, verify the chain; (2) model misbehaviour → `IB_FORCE_OFFLINE=1`; (3) ledger tamper alert → compare with the external head, freeze, escalate; (4) prompt-injection doc found → restrict at source, search the ledger for answers citing it; (5) key compromise → rotate `IB_TOKEN_KEY` (all sessions die), re-publish the head. Each step names the command or route, owner and the MAS/PDPC notification consideration | 2 h | One slide "If something goes wrong" + live revocation demo |
| R7 | **Privacy impact summary** | `PRIVACY_IMPACT_SUMMARY.md` | Data inventory (questions, answer text, ids, hashes: where each lives, from `DATA_FLOWS.md`); purposes; who can see what; transfers (TokenHub: passages only; retention unverified); retention (none defined → proposal); minimisation (≤ 14 passages; no text in ledger; local embeddings); masking; PDPA ss.18/20/24/25/26 table; staff notice text for the login screen | 1.5 h | Q&A |
| R8 | **"Why this answer" explainability view** | Web app (`components/AnswerPanel.tsx`); doc `EXPLAINABILITY.md` | Expand the existing "How this was checked" strip: for *this* answer show passages released, each source's version, `authz_checked_at`, "checked live with Slack at 14:02:07", model and prompt version, fallback used. **Never show denied or considered-but-denied counts to the asker** (that would leak); the full `retrieval.decided` view stays in the compliance console. Explaining the asymmetry is itself a Responsible AI point | 2-4 h (UI) | Ask screen, step 3 of the tour |
| R9 | **Trust center page** | `databridge/apps/internal_brain_web/app/dev/trust/page.tsx` (built) + read-only `/ib/trust` or a static `results.json` import | Tiles: Leaks 0 / 7,374 (95% UB 0.08% per path); P2 0 / 400; injection 0 / 1,152; canaries valid; tests 639 + 38; last eval date; "Verify audit chain" button (compliance only, or a public read of `ok` + `seq`); framework crosswalk table (AI Verify, MGF-GenAI, agentic MGF, Starter Kit, FEAT, MAS AIRG, PDPA, OWASP) with links to R1-R7; "Honest limits" box | 0.5-1 day | 20 seconds at the end of the demo; open during Q&A |
| R10 | **Framework crosswalk** | `FRAMEWORK_CROSSWALK.md` | One matrix: rows = our controls (two gates, grounding, ledger, masking, no-tools, constant refusal, kill switch, eval); columns = AI Verify principle, MGF-GenAI dimension, agentic MGF dimension, Starter Kit risk, FEAT, MAS AIRG para, PDPA section, ISO 42001 clause, NIST function, OWASP id | 1.5 h | The Trust center table; one slide |

### 4.1 Priority with four days left

1. **Must:** R2 system card, R4 red-team report, R10 crosswalk. Together about 6 hours, all reusing existing docs.
2. **Must, if feasible:** T5 live Hy3 run. It closes the biggest technical gap.
3. **Should:** R9 Trust center (it makes everything visible), T4 CI badge (1 hour), R6 runbook.
4. **Could:** R1 AI Verify self-assessment, R3, R5, R7, R8 expansion.
5. **Skip unless time is left:** R4b Moonshot.

### 4.2 Proof points to say on stage (Responsible AI)

1. "The model never sees what you can't open. We don't filter the answer; we filter what goes in."
2. "A contractor asking about a report that exists gets exactly the same bytes as one asking about a report that doesn't. In a bank, confirming a suspicious-transaction file exists can itself be an offence."
3. "Every question is written to a sealed log *before* it's answered. If the log can't be written, there is no answer."
4. "Change one byte of the log and verification fails. Watch." (live tamper)
5. "We planted attacks in all four platforms: a fake approval, a hidden base64 command, a request to call audit tools. Zero worked in 1,152 tries. When our first run found six failures, we published them and fixed them."
6. "The model has no tools. It can't act, can't fetch, can't pick who you are. That bounds what any agent built on it, including WorkBuddy, can reach."
7. "We map to AI Verify's eleven principles, IMDA's Starter Kit and MAS's new AI guidelines from October, and we list what we *don't* do yet."

### 4.3 The three live trust moments (script)

| # | Moment | Steps | Time | Fallback |
|---|---|---|---|---|
| L1 | **Restricted vs non-existent** | Compare: `ken.ext` asks "Show me the security incident report from the Q3 breach." and the same with Q7. Trust center or a small "response hash" line shows both hashes identical | 25 s | `test_s3_existing_vs_nonexistent_identical` screenshot |
| L2 | **Live prompt injection** | As `jdoe`, ask an `INJ-q*` question (e.g. about the Jira ticket carrying the forged `[C9]` security approval). Show the planted text in the source (Map → open item), then the answer: the forged approval is not cited and the instruction is not followed | 30 s | RESULTS.md injection section |
| L3 | **Tamper** | `maya.compliance` → Audit → **Verify audit chain** = "Chain intact". In a terminal, run `scripts/tamper_demo.py` (or edit one ledger byte) → Verify again = broken, with the seq | 30 s | Recorded clip of `tamper_demo.py` (6 of 6 attacks detected) |

Optional fourth (already in the tour): **revocation on the next question** (Simulator → remove jdoe from #db-migration
→ ask again → refusal). It shows that human authority at the source wins.

### 4.4 Likely judge questions: Responsible AI

| # | Question | Answer |
|---|---|---|
| 1 | "Bedrock / Glean already enforce source permissions and re-check live. What's new?" | "Good, and that validates the design. Bedrock re-checks some connectors live and fails closed, but its own docs say ACL awareness 'is not authorization' and that error responses let you distinguish an ACL failure from no match ([AWS](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html)). In a bank that difference is the tipping-off problem. We make restricted and non-existent byte-identical, tested on 400 pairs; we bind identity to a signed token, never a passed-in email; we compute ranking statistics only over what you can open; and every decision is in a sealed, write-ahead log a regulator can verify. It is also open, on Tencent's stack, and runs on-prem." |
| 2 | "What about bias?" | "We make no decisions about people, so credit-style fairness metrics don't apply. What does apply is equal quality of service: we publish per-role recall, and it is uneven for some roles; that is in our fairness note. And a contractor seeing less isn't bias; it's the company's own access policy, mirrored, not invented by us." |
| 3 | "Can the user understand why they got an answer?" | "Every sentence cites a source with its version and the second the permission was checked. Why they *didn't* get something, we deliberately don't explain, because explaining a denial leaks that it exists. Compliance sees the full decision trail instead." |
| 4 | "Who is accountable when it's wrong?" | "The answer always cites its source, so the owner of the source is accountable for content, and the log shows which model and prompt version wrote it. If the model's answer fails grounding, we fall back to quoting. There's a kill switch to stop all model calls." |
| 5 | "Is there a human in the loop?" | "Humans stay the authority on access: the source system decides, and revocations take effect on the next question. Compliance can reconstruct and reveal any answer, with a logged reason. We haven't built a review queue for low-confidence answers; that's next, and MAS §5.9(c) is why." |
| 6 | "What data goes to Tencent's model?" | "Per question, only the passages that passed both checks, at most 14, with card numbers and IDs already masked. Nothing goes out at indexing; embeddings are local. We have not verified TokenHub's retention terms; that's in our privacy summary." |
| 7 | "Does this comply with the MAS AI guidelines?" | "They were issued on 7 October and apply from 2027. We don't claim compliance; we map to them. Our system card is the inventory entry they ask for, the ledger is the logging, and the eval is the testing. Our mapping lists the gaps: dual control, a review queue, retention." |
| 8 | "What if the compliance officer abuses the reveal?" | "Every reveal needs a reason and is logged *before* the text is read, and the log is tamper-evident. Two-person approval isn't built yet; it's on the roadmap." |
| 9 | "Can an admin rewrite the log?" | "Someone holding the sealing key could rewrite records after the last published head. That's our stated window. Production keeps the key in a KMS and publishes the head off-host every 15 minutes, or more often." |
| 10 | "Is it high-risk under the EU AI Act?" | "As scoped, no. It doesn't score credit, price insurance or make employment decisions. If someone wired it into those decisions, that use would need to be reassessed; our scope says it is not a decision-maker." |
| 11 | "How do you handle hallucination?" | "Numbers, dates and names must appear in the cited evidence, or the answer is replaced by direct quotes. In the final run, no answer was uncited. We don't yet verify relations between facts; we say so." |
| 12 | "What about agents?" | "The Brain's model has zero tools. When WorkBuddy calls us, each person has their own token and the tools take no identity argument, so an agent can never reach more than the person it acts for. That's the first dimension of IMDA's agentic framework." |

---

## 5. Gaps worth closing before 3 Nov (ordered by judge impact per hour)

| # | Gap | Fix | Effort | Criterion |
|---|---|---|---|---|
| 1 | Hy3 never run live | Run if TokenHub is verified; add a hy3 mode to RESULTS.md | 1-3 h once the key exists | Both |
| 2 | No system card / inventory entry | R2 | 2 h | RAI |
| 3 | No OWASP red-team report | R4 | 2-3 h | RAI |
| 4 | No CI | T4 GitHub Action + badge | 1 h | Tech |
| 5 | Evidence not visible in the app | R9 Trust center | 0.5-1 day | Both |
| 6 | Track 1 security review has no disposition | Write a disposition table for the 35 findings (open: 3, 9, 11, 12, 15) | 2-3 h | Tech |
| 7 | No incident runbook | R6 | 2 h | RAI |
| 8 | Staff are not told questions are logged | One line on the login screen | 15 min | RAI (PDPA notice) |
| 9 | No live URL | Only if Lighthouse capacity returns; otherwise say "deploy kit, smoke-tested" | — | Tech |

---

## 6. Do-not-say list

- "Compliant with MAS / PDPA / AI Verify." Say "mapped to" or "aligned with", plus the gap list.
- "Certified" or "audited." All our reviews are by AI models and the team; no human third party.
- "Zero risk" or "unhackable." Say "0 in 7,374, with a 95% upper bound of 0.08% per request on each answer path."
- "Runs on Hy3" without the live caveat.
- Any count of Aspire customers or a licence status (see `05_competition_pitch.md` §2 **[verify]**).

---

## 7. Competitor framing (for Q1 in 4.4)

| Property | Typical enterprise RAG (replicate and filter) | Bedrock managed KB (2026 docs) | Walnut DataBridge: Internal Brain |
|---|---|---|---|
| ACLs crawled and filtered | Yes | Yes | Yes (Gate 1) |
| Live re-check at query time | No; stale until the next sync | Yes for 8 connector types; fails closed | Yes for all 4 platforms; fails closed (Gate 2) |
| Identity | App-supplied | App-supplied email; "not authorization" | Signed token only; no identity in any body or tool |
| Restricted vs non-existent | Often distinguishable | Errors distinguishable by design | Byte-identical (P2, 0 / 400) |
| Ranking statistics leak | Global corpus statistics | Not documented | Statistics over the asker's permitted corpus only |
| Audit | App logs | Not in scope | Write-ahead, sealed, externally anchored ledger; reasoned reveal |

Sources: [AWS Bedrock managed ACL docs](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html);
[Amazon Q Business connector concepts](https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/connector-concepts.html);
[AWS blog on replicate-and-filter](https://aws.amazon.com/blogs/machine-learning/rethinking-access-control-for-rag-with-amazon-quick-and-amazon-bedrock/)
(via search summary). Do not claim anything about Glean internals beyond "enforces native permissions upstream of the
model".

---

## 8. Where each item shows in the 5-minute run

| Time | Beat | Technical evidence | RAI evidence |
|---|---|---|---|
| 0:00-0:40 | Person and problem | — | EchoLeak and Slack AI as "why" |
| 0:40-0:55 | Architecture one-pager (T1) | Two gates, ledger, Tencent stack | "Filter what goes in" |
| 0:55-2:40 | Live demo: Ask (R8 strip), Compare (L1), revocation, injection (L2) | Live, end to end | Explainability, anti-tipping-off, injection |
| 2:40-3:20 | Audit + tamper (L3) | Write-ahead, 503 on failure (T7) | Accountability |
| 3:20-4:10 | Evidence slide (T2) + Trust center glimpse (R9) | 7,374 / 0, canaries, 1,131 backend tests, CI badge | Crosswalk: AI Verify, Starter Kit, MAS AIRG |
| 4:10-5:00 | Honest limits + rollout with Aspire | Mocked tenants, Hy3 status | "What we don't do yet" |

---

## 9. Sources

**Judging and demos**
- Reskilll, "What hackathon judges look for" (2026): https://blogs.reskilll.com/what-hackathon-judges-look-for-complete-judging-criteria-breakdown-2026/
- Devfolio judging guide: https://guide.devfolio.co/docs/guide/participating-in-hackathons/judging-1
- HackerEarth, 10 tips: https://www.hackerearth.com/blog/10-tips-win-hackathon
- Tencent Cloud Singapore station, May 2026 (AAP / PR Newswire): https://www.aap.com.au/aapreleases/cision20260513ae58232/ (via `05_competition_pitch.md`)

**Singapore frameworks**
- AI Verify testing framework: https://aiverifyfoundation.sg/what-is-ai-verify/ ; sample report: https://aiverifyfoundation.sg/downloads/AI_Verify_Sample_Report.pdf
- MGF for Generative AI (2024): https://insightplus.bakermckenzie.com/bm/technology-media-telecommunications_1/singapore-new-model-ai-governance-framework-for-generative-ai-launched-by-imda-and-ai-verify-foundation ; https://oecd.ai/en/catalogue/tools/model-ai-governance-framework-for-generative-ai
- MGF for Agentic AI (2026): https://singaporelegaladvice.com/singapore-new-agentic-ai-governance-framework/ ; https://www.smartcitiesworld.net/news/singapore-launches-governance-framework-for-agentic-ai
- IMDA Starter Kit: https://www.imda.gov.sg/-/media/imda/files/about/emerging-tech-and-research/artificial-intelligence/starter-kit-for-testing-llm-based-applications-for-safety-and-reliability.pdf ; https://futurecio.tech/imda-unveils-worlds-first-guidelines-for-safety-testing-of-llm-based-applications
- Project Moonshot: https://github.com/aiverify-foundation/moonshot-cicd ; https://www.allenandgledhill.com/sg/publication/articles/28286/launches-generative-ai-testing-toolkit-project-moonshot-to-address-llm-safety-and-security-challenges
- Global AI Assurance Pilot: https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/press-releases/2025/sg-unveils-insights-from-worlds-first-technical-testing-of-real-world-applications-of-genai ; https://assurance.aiverifyfoundation.sg/wp-content/uploads/2025/05/Assurance-Pilot-Main-Report.pdf
- MAS Veritas / FEAT: https://www.mas.gov.sg/schemes-and-initiatives/veritas ; https://www.mas.gov.sg/news/media-releases/2023/toolkit-for-responsible-use-of-ai-in-the-financial-sector ; https://www.mas.gov.sg/-/media/MAS-Media-Library/news/media-releases/2022/Veritas-Document-3---FEAT-Principles-Assessment-Methodology.pdf
- MAS Project MindForge: https://www.mas.gov.sg/schemes-and-initiatives/project-mindforge ; https://www.mas.gov.sg/news/media-releases/2023/mas-partners-industry-to-develop-generative-ai-risk-framework-for-the-financial-sector
- MAS AI Risk Management Guidelines (7 Oct 2026): https://www.mas.gov.sg/news/media-releases/2026/mas-sets-out-supervisory-expectations-on-responsible-ai-adoption-by-financial-institutions ; https://www.allenandgledhill.com/sg/publication/articles/34686/mas-publishes-ai-risk-management-guidelines-setting-out-supervisory-expectations-on-responsible-ai-adoption ; https://www.bakermckenzie.com/en/insight/publications/2026/10/singapore-mas-issues-guidelines-on-artificial-intelligence-risk-management
- PDPC AI advisory guidelines (2024): https://www.pdpc.gov.sg/-/media/files/pdpc/pdf-files/advisory-guidelines/advisory-guidelines-on-the-use-of-personal-data-in-ai-recommendation-and-decision-systems.pdf ; https://insightplus.bakermckenzie.com/bm/data-technology/singapore-pdpc-releases-advisory-guidelines-on-the-use-of-personal-data-in-ai-recommendation-and-decision-systems
- PDPC Proposed Guide on Synthetic Data Generation (2024): https://pdpc.gov.sg/help-and-resources/2024/07/proposed-guide-on-synthetic-data-generation

**International**
- ISO/IEC 42001 clauses (Advisera): https://advisera.com/articles/iso-42001-clauses-requirements/
- NIST AI 600-1 GenAI Profile: https://airc.nist.gov/docs/NIST.AI.600-1.GenAI-Profile.ipd.pdf ; https://www.hunton.com/privacy-and-cybersecurity-law-blog/nist-generative-ai-profile-highlights-actions-for-addressing-data-protection-risks-associated-with-generative-ai
- EU AI Act Recital 58: https://artificialintelligenceact.eu/recital/58/ ; Omnibus: https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/ ; https://datamatters.sidley.com/2026/06/22/eu-lawmakers-reach-provisional-agreement-to-delay-key-eu-ai-act-obligations/
- OWASP Top 10 for LLM 2025: https://genai.owasp.org/2024/11/17/owasp-reveals-updated-2025-top-10-risks-for-llms-announces-new-llm-project-sponsorship-program-and-inaugural-sponsors/ ; LLM08: https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/ ; LLM02: https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/

**Industry and incidents**
- AWS Bedrock managed KB ACLs: https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-acl.html
- Amazon Q Business connector concepts: https://docs.aws.amazon.com/amazonq/latest/qbusiness-ug/connector-concepts.html
- AWS blog, access control for RAG: https://aws.amazon.com/blogs/machine-learning/rethinking-access-control-for-rag-with-amazon-quick-and-amazon-bedrock/
- Glean on permissions: https://www.glean.com/perspectives/top-ai-assistants-for-document-permissions-management
- EchoLeak: https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html
- Slack AI exfiltration: https://promptarmor.substack.com/p/data-exfiltration-from-slack-ai-via

**Documentation and fairness research**
- Model Cards (Mitchell et al., 2019): https://arxiv.org/abs/1810.03993
- Dehghan & McDonald, "Who Benefits from RAG?" (2026): https://arxiv.org/abs/2603.24218
- "Does RAG Introduce Unfairness in LLMs?": https://arxiv.org/pdf/2409.19804
