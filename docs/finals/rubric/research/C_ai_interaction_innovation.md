# C. AI Interaction, and Innovation & Creativity

Research date: 30 Oct 2026. Demo Day: 3 Nov 2026. Companion to `05_competition_pitch.md` (run sheet),
`E_demo_story_overall.md` (two live moments) and `docs/finals/PLAN.md` (what is being built).

Evidence labels: **[S]** = from a cited source; **[I]** = our inference or judgement; **[V]** = re-verify in
the repo or console before saying it on stage. Repo facts come from `docs/tencent/ARCHITECTURE.md`,
`docs/tencent/SECURITY.md`, `INTERNAL_BRAIN.md` and `docs/tencent/workbuddy/README.md`.

---

## 0. Read this first (six decisions)

1. **The single biggest AI Interaction risk is not a feature, it is a key.** `ARCHITECTURE.md` §6 still says
   "hy3 is configured but has not yet been called live; the TokenHub account is waiting on verification" [V].
   If Hy3 is not answering live on 3 Nov, the "AI Interaction" score drops to extractive-search level no matter
   what else we add. First job: confirm `GET https://tokenhub-intl.tencentcloudmaas.com/v1/models` returns
   `hy3` as `online` with our key and that one real answer carries `model: hy3` in the request journey.
2. **Depth, for judges, means orchestration that is visible.** The closest published rubric scores a plain
   API call lowest and "orchestration across several agents with robust error handling and real-world data
   pipelines" highest [S, AI Tinkerers]. We already have the pipeline; we lack a screen that *shows* the model
   and tools doing distinct jobs. Build the **AI usage map** (section 2.3 A) and the **request journey with
   model, reasoning mode and fallback** (already planned, PLAN.md `/dev/journey`).
3. **Add exactly two agentic things, no more:** (a) a **permission-checked action** (draft a reply or create a
   ticket, with the signed token, written to the ledger) and (b) a **voice ask in English and Chinese**
   through Tencent RTC Conversational AI if the shim is working by Sun 1 Nov 12:00, otherwise Route A
   (browser mic to Tencent ASR to the same ask path). Everything else in section 1.3 is optional.
4. **Innovation must be shown in 30 seconds with two things nobody else can fake on stage:** the refusal that is
   byte-identical to "does not exist" (two personas, same screen), and the tampered log line that breaks the
   chain. Then one slide: a 5-row comparison table against Glean, Microsoft 365 Copilot, Atlassian Rovo, Onyx
   and Elastic DLS, using "not found in public docs" rather than "they cannot".
5. **Say honestly what is not there yet.** No embeddings (by design, see TB8), no RAGAS-style faithfulness
   score yet, Hy4 preview is listed on TokenHub but untested by us. Judges from Tencent will know the products.
6. **Never claim more than a test proves.** 0 leaks in 5,958 adversarial requests and 0 of 400 differential
   mismatches are repo facts [V: re-run after the final build, `databridge/evals/internal_brain/RESULTS.md`].

---

## 1. Shared research

### 1.1 What "depth of AI usage" means to judges in 2026

| Signal judges look for | Evidence | Where we stand |
|---|---|---|
| Agentic orchestration with error handling, not one API call | AI Tinkerers "Technical Complexity & Integration" 1-5 scale: lowest for "a simple API call without real-time interaction", highest for "advanced orchestration across several agents with robust error handling and real-world data pipelines"; "sophisticated multi-modal integration (e.g., browser + voice)" scores high [S, [rubric](https://abu-dhabi.aitinkerers.org/hackathons/h_1de1QREJyuU/entries)] | Have: model chain hy3 → deepseek-v4.1-flash → extractive, `fallback_used` flagged; two gates; grounding wall. Missing: a visible map of it, and any tool use by the model |
| Working end-to-end prototype | Same rubric weights "must run end-to-end and demonstrate the agent's core functionality" [S] | Have, provided Hy3 is live |
| Retrieval quality judged per stage | 2026 RAG-evaluation practice separates retrieval (context precision/recall) from generation (faithfulness, groundedness) and answer relevance ("RAG triad", TruLens; Ragas claim-level faithfulness) [S, [Openlayer](https://www.openlayer.com/blog/rag-pipeline-evaluation-groundedness-faithfulness.md), [klu](https://klu.ai/glossary/rag-triad-evaluation)] | Have: grounding wall drops unsupported values and names; per-sentence `[C1]` tags. Missing: a reported faithfulness number. Section 1.3 item 6 |
| Citations that can be inspected | Slack AI and Notion AI both ship citations; Guru's edge is human verification [S, [eesel](https://eesel.ai/blog/slack-ai), [fast.io](https://www.fast.io/resources/notion-ai-review-2026.md)] | Have: passage-level citations plus "checked live at" and version |
| Tool use / MCP | Theme of this hackathon is "Agentic AI with CodeBuddy, WorkBuddy and Miora" [S, [TechNode](https://technode.global/2026/09/18/tencent-cloud-ai-singapore-industry-ai-hackathon-dbs-keppel/)]; WorkBuddy launched 9 Mar 2026 with MCP support and 20+ built-in skill packs [S, [abit.ee](https://abit.ee/en/artificial-intelligence/tencent-workbuddy-wechat-ai-agent-openclaw-tencent-cloud-2026-ai-office-automation-en), secondary] | Have: WorkBuddy MCP connector with per-user signed token (`docs/tencent/workbuddy/README.md`). Missing: an MCP *action* tool |
| Multimodal (voice, image) | Singapore station winner "Auntie AI" read phone screens and warned in dialects [S, [AAP](https://www.aap.com.au/aapreleases/cision20260513ae58232/)]; judges on 3 Nov include Tencent RTC experts (organiser email) | Missing: voice. Section 1.2 rows 5-6 |
| Human in the loop | Guru's verification model; Glean actions run under the user's own OAuth and need admin enablement [S, [Glean docs](https://docs.glean.com/administration/actions/setup-actions/jira-actions-setup)] | Have: compliance "reveal with reason". Proposed: every action is a *draft* the person confirms |
| Memory | WorkBuddy has a Memory feature in its docs navigation [S, [workbuddy.ai docs](https://www.workbuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)] | We do not keep conversational memory; say so as a privacy choice [I] |
| Novel interaction, not the old product mould | At Alibaba Cloud x Atlas Singapore 2026 almost every team pulled off the agent tasks, "almost none broke out of the old product mold" [S, WebinTravel via search summary, page not read in full] | Our "View as" second persona and byte-identical refusal are the non-standard interactions |

### 1.2 Tencent capabilities we could show live (links, effort for a 2-3 day team)

| # | Capability | What is verified | Doc links | Effort | Fit for 3 Nov |
|---|---|---|---|---|---|
| 1 | **TokenHub** (international) | Base URL Singapore `https://tokenhub-intl.tencentcloudmaas.com/v1`; also Guangzhou and Silicon Valley; keys not interchangeable across sites. Three protocols on one key: OpenAI Chat Completions `/v1/chat/completions`, OpenAI Responses `/v1/responses`, Anthropic Messages `/v1/messages`. Models must be activated in console first, else `402`. `GET /v1/models` lists what is `online`. Reasoning comes back in `reasoning_content` (OpenAI path) or a `thinking` block (Anthropic path). Model IDs include `hy3`, `hy4-preview`, `deepseek-v4-pro`, `deepseek-v4-flash`, `glm-5.3`, `kimi-k3.1-pro`, `minimax-m3`, `hy-mt2-plus` (translation) [S, [official overview](https://www-sg.tencentcloud.com/document/product/1300/80632)] | [Overview](https://www-sg.tencentcloud.com/document/product/1300/80632), [Hy API guide](https://intl.cloud.tencent.com/document/product/1300/80695) | 0.5 day to verify key, add `/v1/models` health call, show model name and `reasoning_effort` in the journey | Must have |
| 2 | **Hy3** | 295B MoE, 21B active, 256K context, Apache 2.0. `reasoning_effort`: `no_think` (default), `low`, `high`. Card stresses "tool-call error recovery" and stable results across CodeBuddy, Cline, KiloCode scaffolds. Recommended temperature 0.9, top_p 1.0 [S, [HF model card](https://huggingface.co/tencent/Hy3)]. OpenAI-style `tools` / `tool_choice` works on third-party hosts [S, [Novita](https://blogs.novita.ai/hy3-api-quick-start/)]; Tencent's own page only says tool calling "depends on the model" [V: test `tools` on TokenHub] | [HF](https://huggingface.co/tencent/Hy3), [TokenHub overview](https://www-sg.tencentcloud.com/document/product/1300/80632) | 0.5 day: show `reasoning_effort` toggle (no_think for answers, high for the audit-filter builder); 1 day for tool calling in the action agent | Must have (answers), nice (tools) |
| 3 | **Hy4 preview** | Listed on the official TokenHub international model table as `hy4-preview` with all three protocols [S, same page]. Third parties: 770B/49B MoE, 1M context, released 28 Aug 2026, JSON-schema structured outputs, tools [S, [OpenRouter](https://openrouter.ai/tencent/hy4-preview), [vLLM recipe](https://recipes.vllm.ai/tencent/Hy4-preview), secondary] | as above | 0.25 day to add as a selectable model if activated; otherwise one sentence | Optional; only if activated and tested |
| 4 | **WorkBuddy** | Desktop app; project-level `.workbuddy/mcp.json` (our repo already generates one per persona, `databridge/scripts/workbuddy_setup.py`); Skill Marketplace with an automatic security scan of skills before install; docs list Connectors, Automation, "Automated Daily Briefing", Memory, Permission Modes, and platform integrations for Slack, Telegram, Discord, WeChat Work, Feishu, DingTalk [S, [docs nav](https://www.workbuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)]; third-party guide confirms `~/.workbuddy/mcp.json` with URL plus auth header for remote servers [S, [PartnerBoost](https://docs.partnerboost.com/ai-center/mcp/workbuddy-setup/)]. Custom-skill page body not retrievable [V] | [Docs](https://www.workbuddy.ai/docs/workbuddy/), [Overseas install guide](https://www.tencentcloud.com/techpedia/144100?lang=en) | Have. 0.5 day to add one MCP action tool and a WorkBuddy skill file "Ask the Brain" | Must have (live ask), nice (action) |
| 5 | **Tencent RTC Conversational AI** (voice agent) | Server API `StartAIConversation` with `LLMConfig` (`LLMType: "openai"`, `Model`, `APIKey`, `APIUrl`, `Streaming`, `SystemPrompt`, `Timeout`, `History`) and `TTSConfig` (`TTSType: "tencent"`, `VoiceType`, `PrimaryLanguage` 1 Chinese / 2 English / 3 Japanese); any OpenAI-protocol endpoint, including "your own `/v1/chat/completions` in your backend to manage context or RAG"; web client `trtc.startLocalAudio()`, subtitles via `TRTC.EVENT.CUSTOM_MESSAGE`; VAD and interrupt settings [S, [AI Interview guide](https://trtc.io/document/75309)]. Free trial 10,000 minutes a month, offsettable against Conversational AI; a marketing blog says about 1,000 AI minutes from that pool, trust the billing doc [S, [billing](https://trtc.io/document/67833), [pricing](https://trtc.io/pricing/rtc-engine)] | [Solution](https://trtc.io/solutions/conversational-ai), [Integration](https://trtc.io/document/75309), [Setup](https://trtc.io/document/69002) | 1.5-2 days: console app, OpenAI-compatible shim in front of `/ib/ask` that treats `APIKey` as the person's signed token, web page with RTC SDK, recorded fallback | High wow for RTC judges, medium risk |
| 6 | **Tencent Cloud ASR + TTS** (no RTC) | Real-time ASR over WebSocket; TRTC AI Transcription 2.0 has a 16k Chinese-English engine `Lang=16k_zh_en` [S, [ASR 2.0 guide](https://intl.cloud.tencent.com/document/product/647/79873)]; Mandarin engine handles mixed Chinese-English at word level [S, [FAQ](https://cloud.tencent.com/document/faq/1093/35802)]. TTS: streaming WebSocket (`stream_wsv2`) for word-by-word LLM output, Chinese and English voices; `TextToSpeechSSE` HTTP API, ap-singapore only, `zh`/`en`/`yue`/`ja`/`ko`, 255 chars [S, [TTS API](https://intl.cloud.tencent.com/document/api/1154/82360), [SSE API](https://www.tencentcloud.com/document/api/647/80009)] | as linked | 0.5-1 day: mic in browser, ASR to text, text into the normal ask path (so the ledger record is text), TTS the first two sentences | Low risk fallback for voice |
| 7 | **CodeBuddy** | Modes Ask / Craft (agent) / Plan; MCP including Roots and Sampling (4.3.0); Hooks via `.codebuddy/settings.json` (4.3.0, 15 Jan 2026); Plan mode integrates MCP and Skills [S, [release notes](https://www.tencentcloud.com/document/product/1256/77277), [overview](https://www.tencentcloud.com/document/product/1256/77264)]. Our transcripts: `docs/tencent/evidence/codebuddy_cb1..cb6.jsonl` | as linked | 0.25 day to render 2 transcript excerpts as one slide | Must have (one beat) |
| 8 | **Miora** | Tencent's agent-based creative studio for images, video, 3D and UI from one brief; international open beta 28 May, full launch 22 Jul 2026 [S, [KuCoin news](https://www.kucoin.com/news/flash/tencent-launches-agent-miora-for-full-stack-visual-content-generation), secondary] | [tooldirectory](https://tooldirectory.ai/tools/miora) | Done (cover art) | One line |
| 9 | **Tencent ADP** (Agent Development Platform) | Standard / Single Workflow / Multi-Agent modes; knowledge bases; workflow nodes (LLM, knowledge search, condition, API call, code) [S, [ADP docs](https://www-sg.tencentcloud.com/document/product/1254/78764)] | as linked | 1+ day; duplicates our pipeline | Skip; mention as a deployment path only if asked |
| 10 | **Tencent RTC MCP server** (`@tencent-rtc/mcp`) | Gives coding agents TRTC docs and test credentials [S, [TRTC docs](https://www.tencentcloud.com/document/product/647/77713)] | as linked | 0.1 day: use it inside CodeBuddy when building item 5, screenshot it | Nice: "CodeBuddy used Tencent's own MCP to build the voice path" |

### 1.3 Quick agentic features, ranked by wow against risk

Scores are our judgement [I]. Wow 1-5 (what judges remember), Risk 1-5 (chance it fails live or costs a day
we do not have). Rule from PLAN.md §2: the model never sees restricted text, and every action runs under the
signed token, after Gate 2, write-ahead to the ledger.

| Rank | Feature | How, in one paragraph | Wow | Risk | Effort | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Permission-checked action: "Draft the customer reply" / "Open a ticket"** | After an answer, an **Act** button. Hy3 is called a second time with `tools` (`draft_reply`, `create_ticket`) over the *released* passages only; the tool call is executed by our server, not the model; it writes `action.proposed` to the ledger before anything else, shows the draft for the person to confirm, then `action.committed`. The ticket is a local JSON "Jira-like" tracker or a real Jira Cloud sandbox if a key exists [V]. Same tool exposed over MCP so WorkBuddy can trigger it. Mirrors Glean's model where write actions run as the connected user [S] | 4 | 2 | 1 day | **Build** |
| 2 | **Voice ask, English and Chinese** | Route B (RTC Conversational AI) if the shim works by Sun noon, else Route A (ASR + TTS). Spoken question becomes text, text goes through the identical gates, the ledger holds text, the answer is spoken with `PrimaryLanguage` matched to the question language. Chinese UI already planned (`/settings`) | 5 with RTC judges | 3 (network, mic, latency) | 1.5-2 days (B), 0.5-1 (A) | **Build A for certain, B if time** |
| 3 | **"Explain this answer"** | Already half-built as "How this was answered" → `/dev/journey`. Add a one-paragraph plain-English narration generated by Hy3 (`reasoning_effort: low`) from the journey JSON only: which gates ran, how many passages were released, which model answered, which values the grounding wall dropped. Narration is itself grounded (every number comes from the JSON) | 3 | 1 | 0.5 day | **Build** |
| 4 | **Answer-quality self-check (faithfulness score)** | Offline eval, not live: for the 40 persona starter questions, split each answer into claims and ask Hy3 (judge) whether each claim is supported by its cited passage; report "claims supported / total" on `/dev/analytics` next to 0/5,958. Ragas-style claim-level faithfulness [S]. Also run the same prompt with grounding wall off to show the delta it buys | 3 | 1 | 0.5 day | **Build if 1-3 are done** |
| 5 | **Proactive freshness alert** | When the simulator edits a runbook or revokes a channel, the brain emits a `freshness.changed` event; a WorkBuddy "Daily briefing" style automation or a Slack message tells the people who cited that document in the last 7 days (people are read from the ledger, so only those who were allowed to see it are told). WorkBuddy Automation exists in docs but we have not confirmed it can call an MCP tool on a schedule [V] | 3 | 3 | 1 day | Optional; do as a UI toast on `/dev` if cheap |
| 6 | **Multi-agent audit investigator** | Compliance types "who saw the Q3 pricing memo after the leak on Friday"; a planner (Hy3 `high`) splits into filter → journey fetch → summary; each step is a ledger-visible tool call. Risky because our NL audit query already does the core; multi-agent adds latency and a chance to look staged | 3 | 4 | 1.5 days | Skip; say "the NL audit filter is one agent step; chaining is roadmap" |
| 7 | **Hy4 preview side-by-side** | Same question answered by `hy3` and `hy4-preview`, both grounded; shows the chain is model-agnostic | 2 | 2 | 0.25 day if activated | Only if the model is online in our console |
| 8 | **Translation via `hy-mt2-plus`** | Chinese answer for a Chinese question when sources are English, through TokenHub's translation model, after the grounding wall | 2 | 2 | 0.5 day | Skip unless voice B lands |

### 1.4 State of the art, and what is genuinely ours

| Product | How it handles permissions and proof (public docs) | Gap we fill |
|---|---|---|
| **Glean** | Connectors crawl source ACLs periodically and map users into Glean's identity schema; "a document is only returned to a user who is allowed to see it"; blog notes permissions "can be changed at any time, which is why freshness matters". Agent actions (Jira create/comment) run under the user's own OAuth, admin-enabled [S, [Glean indexing permissions](https://developers.glean.com/libraries/indexing-sdk/permissions), [identity blog](https://www.glean.com/blog/using-our-identity-schema-to-deliver-personalized-permissions-aware-results), [Jira actions](https://docs.glean.com/administration/actions/setup-actions/jira-actions-setup)] | Index-time ACL copy with a sync window. We re-check each candidate document against the live source at answer time and fail closed |
| **Microsoft 365 Copilot** | Grounding reads only what the signed-in user can access because Graph and the Semantic Index "honor that user identity-based access boundary"; Purview-encrypted content needs View right [S, [Microsoft Learn module](https://learn.microsoft.com/en-us/training/modules/examine-data-security-microsoft-365-copilot/3-examine-copilot-protects-sensitive-business-data)]. Oversharing is the known failure: trimming enforces whatever permissions exist, so broadly shared files surface [S, several secondary]. Audit: Datadog found Copilot Studio did not log certain admin actions (Aug-Sep 2025) and a later regression; a separate report found blank `AccessedResources` fields [S, [Datadog](https://securitylabs.datadoghq.com/articles/copilot-studio-logging-gaps/), [GovInfoSecurity](https://www.govinfosecurity.com/copilot-kept-access-logs-unless-you-told-to-a-29274)] | Query-time trimming is similar in spirit to our Gate 1, within one tenant. Ours spans platforms and adds a write-ahead, hash-chained ledger that detects edits, which no Copilot doc claims |
| **Atlassian Rovo** | "Always honors the permissions of the content it surfaces"; agents in automations run under a connected identity and can leak to less-privileged viewers (Atlassian community thread, staff reply) [S, [Atlassian blog](https://www.atlassian.com/blog/rovo/team25-rovo-for-all), [community](https://community.atlassian.com/forums/Atlassian-AI-Rovo-discussions/Discussing-governance-for-Rovo-agents-within-Automation-flows/m-p/3239561)] | Our actions carry the asker's token only; no privileged service identity exists in the path |
| **Onyx (ex-Danswer)** | Permission syncing is Enterprise Edition; connector access Public / Private / Sync; synced ACLs matched by email; supported for Confluence, Jira, Google Drive, Gmail, Slack, Salesforce, GitHub, SharePoint [S, [Onyx docs](https://docs.onyx.app/enterprise_edition/permissioning), [connectors](https://docs.onyx.app/admins/connectors/overview.md)] | Sync-based; no answer-time re-check, no non-existence guarantee in docs |
| **Elastic DLS** | Role-based document-level security as query filters inside the engine; a role without DLS sees the whole index [S, [Satori](https://satoricyber.com/elasticsearch/elasticsearch-document-level-security/), [Search Guard](https://docs.search-guard.com/latest/search-guard-fine-grained-access)] | Engine-level filter only; ranking statistics still computed over the whole index [I]. Our BM25 statistics come from the asker's permitted chunks only (`index.py`), so restricted documents cannot shift a score |
| **Pinecone namespaces** | Partitioning; the application chooses the namespace per query; no per-user enforcement in docs [S, [Pinecone compare page](https://www.pinecone.io/compare/elasticsearch/), vendor] | Not a permission model |
| **Slack AI / Notion AI / Guru** | Follow the requester's permissions, show citations; Guru adds human verification [S, secondary] | Single-platform; no live re-check, no tamper-evident log, no non-existence proof |
| **Tamper-evident agent logs** | Field is early: Arcaeon Ledger (hash-chained JSONL), inference-receipts (SHA-256 chain, RFC 8785, Ed25519), an IETF individual draft "Agent Audit Trail" (Mar 2026), AuditWeave (arXiv Jun 2026). No enterprise search vendor documents tamper evidence [S, [IETF draft](https://www.ietf.org/ietf-ftp/internet-drafts/draft-sharif-agent-audit-trail-00.txt), [arXiv](https://arxiv.org/abs/2607.09682)] | We ship it inside the product, write-ahead, with a live verify, reusing Walnut's `DecisionLedger` |

**What is genuinely ours (five claims we can defend):**

1. **Gate 2: live per-document re-check at answer time, fail closed.** Every vendor above copies ACLs at index
   time (Glean, Onyx) or trims within one tenant's graph (Copilot, Rovo). None documents a per-document call
   back to the source system at answer time with deny-on-error.
2. **Differential non-existence proof.** "Restricted" is byte-identical to "does not exist", tested on 400
   paired worlds with 0 mismatches, including ranking statistics and the FinCrime case
   (`test_fc_real_vs_nonexistent_customer_is_byte_identical`). No competitor documents a test for existence
   leakage.
3. **Write-ahead, hash-chained ledger, verified live.** Intent is sealed before the answer exists; a reveal is
   refused if the text's SHA-256 differs from the ledger digest (`SECURITY.md` fix 7). Copilot's logging gaps
   are the public counter-example.
4. **Tipping-off compartment.** Suspicious-transaction case notes are readable only by the MLRO; an account
   manager asking about that customer gets the approved script, identical to a non-existent customer.
   Grounded in Singapore's CDSA tipping-off offence (s.57 in the 2020 Revised Edition, formerly s.48 [V:
   confirm on Singapore Statutes Online; search sources only cite s.48 from older guidance]) and MAS notices.
5. **Provenance-pointer knowledge graph with time travel.** Every value points to the exact source row and
   version; the Walnut brain has a time slider and bi-temporality (`docs/vault/24 Bi-temporality.md`),
   so "what did we know on 12 March" is a query, not a guess.

**How to make the originality obvious in 30 seconds [I]:** show, do not tell. (1) Two personas on one
screen ask "What is the Q3 pricing memo?"; the engineer's answer is the same text as a question about a memo
that does not exist. Say "that is the point: nobody can learn that a secret exists". (2) Open the ledger,
edit one character of one line, press Verify, the chain breaks at that line. Say "the regulator can run this
too". (3) One slide, five rows, our column green, the others marked "index-time sync" or "not in public
docs". Tagline: "Answers you can prove."

### 1.5 Past winners: what AI usage and novelty they showed

| Event | Winner and AI usage | Lesson |
|---|---|---|
| Tencent "AI Coding Challenge", Singapore station, 9 May 2026 | 1st **Auntie AI** (Elena Xiao): multimodal, reads the phone screen, spots phishing SMS and links, explains in plain language, warns in dialects (voice). 2nd **EyeDentify** (University of Malaya): low-cost wearable plus vision model for visually impaired users. Judges: Michael Xu (Tencent Cloud Chief Architect), Feida Zhu (SMU), an AISG engineer; criteria included human-centered design, AI interaction, feasibility, technical execution [S, [AAP](https://www.aap.com.au/aapreleases/cision20260513ae58232/)] | Multimodal plus voice plus plain-language explanation won. A named user. |
| AI CAN DO IT, Hong Kong and Macau Demo Day, Aug 2026 | 1st HKUST **Anchor**, 2nd HKU **Walking in Poetry**, 3rd **LOONG SEASON**; pillars social good, cultural expression, narrative innovation; projects on high-functioning autism support and silver-tech. Project details not public [S, [Webull/PR](https://www.webull.com/news/15427742073897984), [TechCoffeeHouse](https://techcoffeehouse.com/2026/08/20/tencent-cloud-hackathon-crowns-hong-kong-macau-ai-finalists/)] | Creative track; not comparable. Cultural specificity rewarded. |
| Tencent Cloud x UTM AI Hackathon, Kuala Lumpur, 13 Aug 2026 | AI Agent Track champion **AI Scam Shield** (Team Sleep_Well), scam-prevention platform for a Silverlake (fintech) challenge [S, [TechNode](https://technode.global/prnasia/tencent-cloud-and-utm-connect-malaysias-emerging-ai-talent-with-real-world-industry-challenges/)] | Closest analogue: fintech, agent track, sponsor challenge. Protection-of-money narrative. |
| Alibaba Cloud x Atlas Agentic AI Hackathon, Singapore, Aug-Oct 2026 | Agents did search, ticketing, rebooking; observer: nearly all teams achieved the tasks, "almost none broke out of the old product mold" [S, search summary of WebinTravel; page not read] | Competence is table stakes; a new interaction wins. |
| Generic 2026 hackathon scorecards | Four axes about 25% each: innovation, technical depth, impact, presentation; "retrieval, agents, fine-tuning, multimodal ... read as real engineering" [S, [BYTEFORGE guide](https://byte-forge-2026.lovable.app/guide/how-to-win), third-party] | Our depth is real; make it visible. |
| Tencent global finals results | Not found [V] | n/a |

---

## 2. Criterion: AI Interaction ("quality and depth of AI usage")

### 2.1 What judges look for

- Several distinct AI jobs orchestrated with error handling, visible live, not a single chat call
  [S, AI Tinkerers rubric; I].
- Tencent products used for real work, live, not on a slide (organiser instruction).
- Grounded answers with inspectable citations; a defensible answer when the model fails or is unsure.
- Tool use through MCP; the agent acting, not only answering (hackathon theme).
- Multimodal reach (voice) with the same safety rules [S, Auntie AI pattern; RTC judges].
- Evaluation: a number for retrieval and generation quality, and how it was measured.
- Honesty about limits; Tencent judges know Hy3, TokenHub and WorkBuddy well.

### 2.2 What winners did

Auntie AI chained vision (screen reading), reasoning (risk explanation) and voice (dialect warning) around
one human moment. AI Scam Shield won a fintech agent track by protecting money. Both made the AI's several
jobs legible in the demo. Neither, from public text, showed evaluation numbers; we can.

### 2.3 Our concrete deliverables

**A. AI usage map** (new, the artefact for this criterion)

- File: `docs/finals/rubric/deliverables/C1_ai_usage_map.md` plus the same content as a panel
  `AiUsageMap` on `/dev` (mission control) and one slide.
- Outline, one row per AI job, columns *Job · Tencent model or tool · Input it sees · Output · Guard · Test*:
  1. Build and review the code · CodeBuddy (Craft and Plan, MCP, Hooks) on Hy3 · repo · code and the security
     review · human review · `docs/tencent/evidence/codebuddy_cb1..cb6.jsonl`
  2. Compose the answer · Hy3 on TokenHub (`reasoning_effort: no_think`) · released passages only · `[Cn]`
     tagged sentences · grounding wall · `test_p1_restricted_never_in_any_prompt`
  3. Fallback · DeepSeek V4.1 Flash on TokenHub, then deterministic extractive · same input ·
     `fallback_used=true` · `test_hy3_raises_falls_back_to_deepseek_flash`
  4. Explain the answer · Hy3 (`low`) · journey JSON · one paragraph · numbers must match JSON (new)
  5. Natural-language audit filter · Hy3 (`high`) · compliance question · structured filter · schema check ·
     existing tests [V name]
  6. Act · Hy3 with `tools` · released passages · `draft_reply` / `create_ticket` proposal · write-ahead
     ledger, human confirm (new)
  7. Second front door · WorkBuddy MCP · per-user signed token · same answers, same ledger ·
     `docs/tencent/workbuddy/README.md`
  8. Voice · Tencent RTC Conversational AI or Tencent ASR/TTS · speech · text question → same path (new)
  9. Faithfulness eval · Hy3 as judge, offline · claims vs cited passages · score on `/dev/analytics` (new)
  10. Cover art · Miora
- Demo placement: 2:50-3:15 "Tencent stack" beat (replaces the logo slide), 25 seconds.

**B. Request journey shows the AI** (planned in PLAN.md `/dev/journey/[req]`, add three fields)

- Model name and `reasoning_effort`, `fallback_used`, grounding-wall drops (count and the dropped tokens
  masked), tool calls if any. Demo placement: LIVE 1, when the first answer lands, click "How this was
  answered" (0:35-1:25).

**C. Act button and MCP action tool** (new)

- Backend: `databridge_ib` `actions.py` (proposed) with `propose_action(req_id, kind)`; ledger events
  `action.proposed`, `action.confirmed`, `action.rejected`; tool schemas in `tools.py`; Hy3 called with
  `tools` and `tool_choice: "auto"`; execution by our code only; result file `var/tickets/*.json` or Jira Cloud
  sandbox [V].
- MCP: tool `ib_propose_action` beside `ib_ask` in `databridge_ib.mcp_server`.
- UI: Act menu under an answer → draft sheet → Confirm; journey shows the second model call.
- Demo placement: end of LIVE 1 (1:10-1:25): "Draft the reply to the customer" as the account manager; the
  draft cites the approved script; confirm; ledger gains two sealed records.

**D. Voice ask** (new)

- Route A files: `apps/internal_brain_web` `VoiceButton.tsx`, API route `/api/voice/asr` and `/api/voice/tts`
  calling Tencent ASR (16k_zh_en) and TTS (`en`/`zh`); the recognised text is shown before sending so the
  person stays in control.
- Route B files: `databridge_ib/openai_shim.py` exposing `/v1/chat/completions`, reading `Authorization:
  Bearer <signed token>` (RTC passes `LLMConfig.APIKey` as the bearer) and calling `/ib/ask`; `voice.html`
  with the TRTC web SDK; `StartAIConversation` from a tiny admin script. The identity rule holds: the voice
  agent's "API key" is the person's token.
- Demo placement: LIVE 2 (2:10-2:50), 15 seconds, Chinese question from the compliance persona, English
  answer spoken back, same ledger.

**E. Explain this answer** (new, 0.5 day): see 1.3 item 3; appears as the first line of the journey.

**F. Faithfulness panel** (new, 0.5 day): `databridge/evals/internal_brain/faithfulness.py` producing
`eval_summary.json` fields `claims_total`, `claims_supported`, `wall_on`, `wall_off`; shown on
`/dev/analytics`. Demo placement: Evidence beat (3:25-4:05) one number.

### 2.4 Proof points to say on stage

- "Hy3 on Tencent TokenHub writes every answer; you are watching the model name in the journey." [V live]
- "The model only ever sees passages that passed two gates; restricted text is never in any prompt, and a
  test asserts it." (`test_p1_restricted_never_in_any_prompt`)
- "If Hy3 fails, DeepSeek answers; if both fail, a deterministic extract answers and the record says so."
- "The grounding check removed N unsupported values across the eval set; faithfulness X of Y claims
  supported." [V numbers after F is built]
- "The same brain answers inside WorkBuddy with the same token, and now acts: it drafted this reply and
  sealed the intent before it did."
- "The spoken question went through the identical gates; the audit record is text." [if D is built]
- "CodeBuddy wrote parts of this and reviewed it for security on Hy3; the transcripts are in the repo."

### 2.5 Likely judge questions

| Question | Answer |
|---|---|
| Where is the AI? This looks like a permission filter. | Four model jobs: compose with Hy3, explain, build the audit filter from plain English, and act with tools. The filter is what makes the AI safe to switch on; the model is what makes it useful. |
| Why does the answering model get no tools? | Deliberate. Tools touch systems; the model only sees released passages. Actions are a second, separate call whose proposals our code executes under the asker's token, after a human confirm, write-ahead to the ledger. |
| Why BM25 and no embeddings? | Embedding at ingest would send every chunk of every document to the provider regardless of permissions, and would embed the query before access is decided (Codex review finding 11). Permitted-only embeddings are on the roadmap; BM25 over the asker's corpus leaks nothing through statistics. |
| Do you use Hy3's thinking mode? | `no_think` for answers (speed, grounded by construction), `high` for the audit-filter builder and planning. The journey shows which. |
| What happens if TokenHub is down on stage? | Chain falls to DeepSeek, then extractive; `fallback_used` is shown; demo continues. |
| How do you measure answer quality? | Grounding wall at runtime; offline faithfulness over N questions (claims supported / total) and leak evals (0/5,958; 0/400); 639 + 38 tests [V]. We do not yet report retrieval recall per stage; that is next. |
| Does the voice path weaken the controls? | No. Speech becomes text before any gate; the token is the identity; the ledger stores the text. |
| Is WorkBuddy just a chat window on your API? | It is a second identity-bound front door through MCP, and it can trigger the action tool; a judge can connect their own WorkBuddy in 2 minutes with the README. |
| Did you try Hy4 preview? | It is listed on TokenHub; [either] we ran it side by side [or] we did not activate it in time; the chain is model-agnostic. Say which is true. |
| Multi-agent? | One planner step today (NL audit filter). Chaining investigators is roadmap; we chose depth of proof over number of agents. |

---

## 3. Criterion: Innovation & Creativity ("originality and uniqueness")

### 3.1 What judges look for

- A mechanism they have not seen, demonstrated, not described [I; consistent with the Alibaba x Atlas
  observation that equal competence made novelty the tiebreaker].
- A clear contrast with incumbents they know (Copilot, Glean, Rovo) without disparagement.
- Creative use of the sponsor's tools (two front doors, voice, agent actions).
- A problem framing that is new: "prove the answer" rather than "find the answer".

### 3.2 What winners did

Auntie AI reframed phishing defence as a dialect-speaking auntie reading your screen: familiar tech, new
frame. Winners at Tencent stations were remembered for one vivid moment, not a feature list. Our vivid
moments are the identical refusal and the broken chain.

### 3.3 Our concrete deliverables

**A. Novelty comparison table** (new)

- File: `docs/finals/rubric/deliverables/C2_novelty_comparison.md`, one slide, and a section on `/dev/trust`.
- Rows: live per-document re-check at answer time · restricted ≡ non-existent (tested) · write-ahead
  hash-chained ledger with live verify · tipping-off compartment · provenance pointers with time travel ·
  actions under the asker's token only. Columns: Walnut · Glean · M365 Copilot · Rovo · Onyx · Elastic DLS.
  Cell values: "Yes, shown live" / "Index-time sync" / "Tenant trimming" / "Not in public docs" with the
  source link in the markdown version (section 1.4 above). Never write "cannot".
- Demo placement: Tencent stack beat, second half (3:15-3:25), 10 seconds, or Evidence beat.

**B. Innovation one-pager** (new)

- File: `docs/finals/rubric/deliverables/C3_innovation_one_pager.md` (also exported to the deck folder).
- Outline: (1) The problem nobody else states: a company AI must not reveal *that* something exists.
  (2) Three inventions in one sentence each, with the test that proves each. (3) Why now: regulators ask
  for logs that cannot be silently edited (EU AI Act Art. 12 in force Aug 2026 [S, secondary, V]), MAS TRM.
  (4) What is reused (Walnut's DecisionLedger, grounding wall, provenance model) and what is new (gates,
  differential test, compartment, two front doors). (5) Roadmap.

**C. The two live proofs** (already planned, make them the Innovation beats)

- Restricted ≡ non-existent on `/compare`: two personas, one question, plus a third column "a memo that does
  not exist" so the sameness is visible. 0:35-1:25.
- Tamper demo on `/dev/audit`: edit, Verify, red line. 1:25-2:10.

**D. "View as" in the knowledge graph** (planned, `/dev/graph`): the second persona's locked nodes greyed
out is a visual that no incumbent search tool offers [I]. 15 seconds if time allows, else in the fallback
video.

**E. Time slider on the Walnut brain** (`/dev/brain`): "what did we know on 12 March" in one drag.
Mention only; show in Q&A if asked about provenance.

### 3.4 Proof points to say on stage

- "Every other product we studied copies permissions into its index and hopes the copy is fresh. We ask the
  source again, for each document, every time, and deny on any error."
- "Ask about a secret and ask about a thing that does not exist: identical bytes. 400 paired tests, 0
  differences." [V]
- "The ledger is written before the answer is composed; change one character and the chain tells you where."
- "A compliance officer can read suspicious-transaction notes; the account manager asking about the same
  customer gets the approved script, exactly as for any other customer. That is anti-tipping-off, built in."
- "Over 90% of this is new Walnut code; the ledger and provenance model are our own engine from before the
  hackathon." [per team record]

### 3.5 Likely judge questions

| Question | Answer |
|---|---|
| Copilot and Glean already respect permissions. What is new? | They enforce a copied or tenant-scoped permission at query time. We re-check each document against the live source at answer time and fail closed, and we prove that a refusal is indistinguishable from non-existence. Neither claim appears in their public docs. |
| Is the live re-check not slow? | Per-document checks run in parallel; p50 for Gate 2 is shown live in mission control [V number]. We trade a few hundred milliseconds for a property no cache can give. |
| Why a hash chain and not a blockchain? | Tamper evidence needs a chain and an anchor, not consensus. Anchor digests can be published externally later; today the anchor ledger lives in a separate directory. |
| What is tipping-off and why does an AI need it? | In Singapore it is an offence to disclose that a suspicious-transaction report or investigation exists. A normal AI that says "you are not allowed to see this" has just tipped off. Ours says the same thing it says about nothing. |
| Is the knowledge graph only a visualisation? | Every node is a provenance pointer to a row and a version; the time slider replays state; "View as" renders another person's permitted subgraph. It is the data model, drawn. |
| Could Glean add this tomorrow? | Architecturally yes, but their products are index-first by design; ours is deny-by-default at answer time with a sealed ledger. The differential test suite is the part that is hard to retrofit. |
| What did you reuse? | Walnut's DecisionLedger, grounding wall and provenance model are ours from before. New for this hackathon: the gates, the differential test, the compartment, WorkBuddy MCP, actions, voice. |
| Who else does the differential non-existence test? | We found no vendor documenting one; academic work on membership inference is the nearest relative. Say "none that we found", not "nobody". |

---

## 4. Build order (fits the PLAN.md dates)

| When | Item | Owner note |
|---|---|---|
| Fri 30 Oct | Verify TokenHub key, `/v1/models`, one live Hy3 answer, journey shows model | Blocks everything |
| Sat 31 Oct am | Act button + ledger events + MCP action tool (2.3 C) | 1 day |
| Sat 31 Oct pm | Explain this answer (2.3 E); AI usage map panel and markdown (2.3 A) | 0.5 + 0.5 day |
| Sun 1 Nov am | Voice Route A (2.3 D); attempt Route B shim in parallel only if two people are free | Stop Route B at 12:00 if not working |
| Sun 1 Nov pm | Faithfulness eval (2.3 F); novelty table and one-pager (3.3 A, B); re-run leak evals; record fallback clips | Freeze 18:00 |
| Mon 2 Nov | Rehearsal, fix numbers in scripts, nothing new | |

## 5. Gaps and cautions

- Hy3 live call not yet confirmed in the repo docs [V]. Hy4 preview is on the official TokenHub model list
  but untested by us; do not claim results.
- Tencent's own TokenHub page does not spell out the `tools` schema for Hy3; third-party hosts confirm
  OpenAI-style tools work. Test on TokenHub before building the action agent around it; fallback is a
  JSON-schema prompt without `tools`.
- WorkBuddy custom-skill and connector doc pages returned only a title to our fetch; the `.workbuddy/mcp.json`
  path is confirmed by our own working setup, the rest from third parties.
- RTC free minutes: billing doc says 10,000 a month offsettable against Conversational AI; a blog says about
  1,000 AI minutes. Check the console.
- CDSA section number for tipping-off: repo says s.57 (2020 Rev. Ed.), older sources say s.48. Confirm on
  Singapore Statutes Online before printing it.
- No public write-up of what "Anchor" (HK winner) or the Tencent global finals winners built.
- The Alibaba x Atlas "old product mold" quote came through a search summary; the page was not read.
- Competitor claims are from public docs only; say "not in their public documentation".

## Sources

Tencent: [TokenHub language model overview](https://www-sg.tencentcloud.com/document/product/1300/80632) ·
[Hy API guide](https://intl.cloud.tencent.com/document/product/1300/80695) · [Hy3 model card](https://huggingface.co/tencent/Hy3) ·
[Hy3 tools example (Novita)](https://blogs.novita.ai/hy3-api-quick-start/) · [Hy4 preview (OpenRouter)](https://openrouter.ai/tencent/hy4-preview) ·
[Hy4 vLLM recipe](https://recipes.vllm.ai/tencent/Hy4-preview) · [WorkBuddy docs](https://www.workbuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market) ·
[WorkBuddy overseas guide](https://www.tencentcloud.com/techpedia/144100?lang=en) · [awesome-workbuddy](https://github.com/semlinker/awesome-workbuddy) ·
[WorkBuddy MCP third-party setup](https://docs.partnerboost.com/ai-center/mcp/workbuddy-setup/) · [CodeBuddy release notes](https://www.tencentcloud.com/document/product/1256/77277) ·
[CodeBuddy overview](https://www.tencentcloud.com/document/product/1256/77264) · [RTC Conversational AI integration](https://trtc.io/document/75309) ·
[RTC Conversational AI solution](https://trtc.io/solutions/conversational-ai) · [RTC billing](https://trtc.io/document/67833) · [RTC pricing](https://trtc.io/pricing/rtc-engine) ·
[Tencent RTC MCP server](https://www.tencentcloud.com/document/product/647/77713) · [ASR 2.0 guide](https://intl.cloud.tencent.com/document/product/647/79873) ·
[ASR FAQ](https://cloud.tencent.com/document/faq/1093/35802) · [TTS streaming API](https://intl.cloud.tencent.com/document/api/1154/82360) ·
[TextToSpeechSSE](https://www.tencentcloud.com/document/api/647/80009) · [ADP docs](https://www-sg.tencentcloud.com/document/product/1254/78764) ·
[Miora launch (KuCoin)](https://www.kucoin.com/news/flash/tencent-launches-agent-miora-for-full-stack-visual-content-generation) · [Miora listing](https://tooldirectory.ai/tools/miora).

Hackathons: [Singapore 2026 hackathon (TechNode)](https://technode.global/2026/09/18/tencent-cloud-ai-singapore-industry-ai-hackathon-dbs-keppel/) ·
[Singapore station May 2026 (AAP)](https://www.aap.com.au/aapreleases/cision20260513ae58232/) · [HK & Macau Demo Day](https://www.webull.com/news/15427742073897984) ·
[HK finalists (TechCoffeeHouse)](https://techcoffeehouse.com/2026/08/20/tencent-cloud-hackathon-crowns-hong-kong-macau-ai-finalists/) ·
[UTM Malaysia Demo Day](https://technode.global/prnasia/tencent-cloud-and-utm-connect-malaysias-emerging-ai-talent-with-real-world-industry-challenges/) ·
[AI Tinkerers rubric](https://abu-dhabi.aitinkerers.org/hackathons/h_1de1QREJyuU/entries) · [BYTEFORGE guide](https://byte-forge-2026.lovable.app/guide/how-to-win) ·
[Alibaba x Atlas hackathon](https://www.createwith.com/event/singapore-alibaba-cloud-x-atlas-agentic-ai-hackathon-aug-2026).

Competitors: [Glean indexing permissions](https://developers.glean.com/libraries/indexing-sdk/permissions) · [Glean identity blog](https://www.glean.com/blog/using-our-identity-schema-to-deliver-personalized-permissions-aware-results) ·
[Glean Jira actions](https://docs.glean.com/administration/actions/setup-actions/jira-actions-setup) · [Microsoft Learn: Copilot data security](https://learn.microsoft.com/en-us/training/modules/examine-data-security-microsoft-365-copilot/3-examine-copilot-protects-sensitive-business-data) ·
[Datadog: Copilot Studio logging gaps](https://securitylabs.datadoghq.com/articles/copilot-studio-logging-gaps/) · [GovInfoSecurity on Copilot logs](https://www.govinfosecurity.com/copilot-kept-access-logs-unless-you-told-to-a-29274) ·
[Atlassian Rovo blog](https://www.atlassian.com/blog/rovo/team25-rovo-for-all) · [Atlassian community on agent identity](https://community.atlassian.com/forums/Atlassian-AI-Rovo-discussions/Discussing-governance-for-Rovo-agents-within-Automation-flows/m-p/3239561) ·
[Onyx permissioning](https://docs.onyx.app/enterprise_edition/permissioning) · [Onyx connectors](https://docs.onyx.app/admins/connectors/overview.md) ·
[Elastic DLS (Satori)](https://satoricyber.com/elasticsearch/elasticsearch-document-level-security/) · [Search Guard DLS](https://docs.search-guard.com/latest/search-guard-fine-grained-access) ·
[Pinecone vs Elasticsearch](https://www.pinecone.io/compare/elasticsearch/) · [Slack AI (eesel)](https://eesel.ai/blog/slack-ai) · [Notion AI review](https://www.fast.io/resources/notion-ai-review-2026.md).

Evaluation and audit: [Openlayer RAG evaluation](https://www.openlayer.com/blog/rag-pipeline-evaluation-groundedness-faithfulness.md) · [RAG triad](https://klu.ai/glossary/rag-triad-evaluation) ·
[IETF draft Agent Audit Trail](https://www.ietf.org/ietf-ftp/internet-drafts/draft-sharif-agent-audit-trail-00.txt) · [AuditWeave (arXiv)](https://arxiv.org/abs/2607.09682) ·
[CDSA tipping-off (Thomson Reuters whitepaper)](https://www.thomsonreuters.com.sg/content/dam/ewp-m/documents/singapore/en/pdf/white-papers/singapore-aml-cft-whitepaper-final.pdf).
