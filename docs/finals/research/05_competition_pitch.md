# 05 - Competition research and 5-minute pitch plan (Demo Day, 3 Nov 2026)

> **Dated note (30 Oct):** eval figures in this file (5,958 requests, 976 injection-relevant requests) are from the
> 14 Oct run. The current run is 0 leaks in 7,374 requests and 0 injection hard failures in 1,152
> (`databridge/evals/internal_brain/RESULTS.md`).

Research date: 30 Oct 2026. Items marked **[verify]** came from one source or from search summaries and should be checked before you rely on them. The contest page is mostly an empty shell, so most of what follows is inference from press coverage.

## 1. What the public pages actually say

| Item | What we found | Source |
|---|---|---|
| Contest page | Shows prize pool SGD 17,000, 400 registrations, 5 tracks (Banking, Real Estate, FinTech, Digital Native, Healthcare). No rules, rubric, judges, dates or Aspire brief are public. Status "Break Period". | [tch.tencentcloud.com/contest/44](https://tch.tencentcloud.com/contest/44) |
| Event page | Demo Day 3 Nov 2026, Marina Bay Sands, held alongside Tencent Cloud Day Singapore. Hosts Tencent Cloud and AI Singapore; AMD speaks at the opening. 150+ teams, top 10 present. | [luma.com/26fqf3hy](https://luma.com/26fqf3hy) |
| Run of show | 15:00 opening; 15:20 industry insights; **15:35-16:55 ten finalists demo live (about 8 min per slot including changeover)**; 16:55 judges deliberate; 17:10 awards. | same |
| Prizes | S$10,000 / S$5,000 / S$2,000, plus CodeBuddy and WorkBuddy credits for everyone. | same |
| Judges | Not named on the event page. At the May Singapore station the judges were Tencent Cloud's Chief Architect, an SMU computing dean and an AI Singapore representative, scoring human-centered design, AI interaction and feasibility as well as technical execution. | [PR Newswire via AAP](https://www.aap.com.au/aapreleases/cision20260513ae58232/) |
| Aspire brief | Not public. Use your own copy of the challenge text. | none |

Takeaway: there are no hidden rules. Your rubric (10 x 10 points) and the organisers' verbal advice (show Tencent products live, pitch like a business, skip background) are the best guide.

## 2. Aspire: what we can say about the sponsor

| Topic | Finding | Source |
|---|---|---|
| What it is | Singapore-headquartered SME finance platform (accounts, cards, payments, spend management, software), positioned as an SME neo-bank for Southeast Asia. | [Techsauce](https://techsauce.co/en/news/aspire-set-to-become-first-sme-neobank-in-southeast-asia-with-us32-5-million-raise) |
| Markets | Singapore, Indonesia, Thailand, Vietnam. | [Aspire HQ post](https://aspireapp.com/en-sg/blog/aspire-opens-new-hq-in-singapore-to-drive-fintech-innovation) |
| Scale | About 15,000 businesses in 2023 coverage; a careers page claims 50,000+. Different dates, so say "tens of thousands of SMEs" only. **[verify]** | [Aspire HQ post](https://aspireapp.com/en-sg/blog/aspire-opens-new-hq-in-singapore-to-drive-fintech-innovation), [careers listing](https://job-boards.greenhouse.io/aspire/jobs/5493932004) |
| Funding | Series B US$158M (equity plus debt), Series C US$100M led by Lightspeed and Sequoia SEA. | [DealStreetAsia](https://dealstreetasia.com/?p=262333) |
| AI products | "Aspire AI" suite (claims 75% less manual work), an OpenAI-powered chat-to-action assistant, and AspireOS (modular platform for banks serving SMEs, late 2025). | [CFOtech Asia](https://cfotech.asia/story/aspire-launches-ai-suite-promises-75-reduction-in-manual-tasks) |
| Licence | One low-quality source claims in-principle MAS approval for a neobank licence in Oct 2024. Do not state this on stage. **[verify]** | [wiki.private.law](https://wiki.private.law/aspire) |
| Head of AI | No public name or statement found. A reasonable inference for an AI lead at a regulated SME fintech: accuracy with citations, no data leakage between people, auditability for MAS and bank partners, cost per query, safe shipping. Do not claim to know their views. | none |

Pitch angle: Aspire sells AI to SMEs and sells into banks, so "an internal knowledge system a regulator would accept" is exactly its trust problem.

## 3. What previous winners looked like

Evidence is thin; no public write-up of the global finals result was found.

| Event | Winner | Pattern |
|---|---|---|
| Singapore station, May 2026 | 1st: "Auntie AI" (scam protection for seniors, local dialect warnings). 2nd: "EyeDentify" (wearable aid for visually impaired users). [AAP](https://www.aap.com.au/aapreleases/cision20260513ae58232/) | A named human user, an emotional real problem, a working demo, local relevance. |
| Hong Kong and Macau Demo Day, Aug 2026 | Anchor (HKUST), Walking in Poetry (HKU), LOONG SEASON. [AAP](https://www.aap.com.au/aapreleases/cision20260819ae29301/) | Culturally specific, human stories. |
| Global finals | No result found. **[verify]** | n/a |

What winners shared: one person you can picture, a live working product, local flavour, Tencent tools visibly used. Your risk is that "enterprise RBAC and audit" sounds dry next to Auntie AI. Fix it by opening with a person (a new analyst, a compliance officer facing an audit) and by showing something visible, a blocked answer and a broken chain, rather than tables of numbers. No AISG demo day winner lists worth citing were found.

## 4. What Tencent wants to see (products and facts)

| Product | Newest facts | Source |
|---|---|---|
| Hy3 (Hunyuan, now "Hy") | Released 6 Jul 2026. Mixture-of-experts, 295B total / 21B active parameters, 256K context, fast and slow thinking, Apache 2.0 open weights, API on TokenHub. List price about 1 yuan per million input tokens, 4 yuan per million output. | [Tencent](https://www.tencent.com/en-us/articles/2202386.html), [The Standard](https://www.thestandard.com.hk/innovation/article/336512/Tencents-Hunyuan-releases-Hy3-available-on-WorkBuddy-and-more) |
| WorkBuddy | Office agent running Hy3, concurrent multi-agent, 100+ built-in skills. Was free globally until 31 Aug 2026; check current credits. | [Tencent global release](https://www.tencent.com/tencent-hy3-now-available-globally-extending-practical-ai-across-products-workflows-and-cloud-services/) |
| CodeBuddy | Coding agent also on Hy3. No detailed feature list found. | same |
| TokenHub | Unified gateway for Hunyuan and third-party models. Page lists a "Hy4 preview"; no details found, do not claim it. **[verify]** | [TokenHub](https://www.tencentcloud.com/products/tokenhub) |
| Tencent RTC Conversational AI | Voice agent service: create an app, choose Conversational AI, use Agent Studio with three sample agents. Free trial of RTC Engine: 10,000 free minutes a month. Newer billing text says it can offset Conversational AI and speech services; an older version says speech-to-text is pay-as-you-go. LLM and TTS vendors are billed separately. | [Setup guide](https://trtc.io/document/69002), [Billing](https://trtc.io/document/67833), [Pricing](https://trtc.io/pricing/rtc-engine) |

Judges include RTC experts, so a short voice moment would help AI Interaction and UX and Accessibility.

### Can a voice-ask be added in 1-2 days?

Yes, as a small optional feature.

| Route | Effort | How | Risk |
|---|---|---|---|
| A. Browser mic to Tencent Cloud ASR, answer to TTS | 0.5-1 day | Record audio in the browser, send to your API, call Tencent Cloud ASR (one-sentence recognition), pass the text into the existing ask path under the user's RBAC session, return text plus TTS audio. Docs: [ASR](https://www.tencentcloud.com/document/product/1118), [TTS](https://www.tencentcloud.com/document/product/1073). Confirm international availability and free quota **[verify]**. | Low. Stays on your server; RBAC unchanged. |
| B. Tencent RTC Conversational AI voice agent | 1-2 days | Create the app in the RTC console, configure an agent in Agent Studio with a custom LLM endpoint pointing at your ask API (OpenAI-compatible shim), join from a web page with the RTC SDK. [Guide](https://trtc.io/document/69002) | Medium. Needs a shim that carries user identity, a separate TTS key, and live network on stage. |

Recommendation: do Route A only if the core demo is already locked. Keep the spoken question going through the same RBAC check and audit log (transcribe, then ask, so the audit record is text). Keep a recorded fallback; a failed voice demo costs more than the feature earns.

## 5. The 5-minute pitch plus live demo (run sheet)

Rubric criteria in brackets.

| Time | Beat | What you say and show | Rubric |
|---|---|---|---|
| 0:00-0:20 | Hook | "A new analyst asks the company AI about salaries. Watch what a normal chatbot does." A generic assistant blurts out a figure it should not know. | Impact, Storytelling |
| 0:20-0:35 | Problem, one line | "Company AI answers from everything it can read, not from what you are allowed to see, and nobody can prove afterwards what it said." | Impact |
| 0:35-1:25 | LIVE 1: same question, three people | Ask as analyst, manager and HR/finance. Different answers, citations shown, the restricted one refused in plain words. Hy3 on TokenHub is the live answer model; show the model name in the audit view. | AI Interaction, Technical, HCD |
| 1:25-2:10 | LIVE 2: attack and proof | One prompt-injection or cross-role attempt, blocked and logged. Open the hash-chained log, tamper with one line, show verification fail. | Responsible AI, Technical, Innovation |
| 2:10-2:50 | LIVE 3: second front door | Ask the same thing from WorkBuddy through the MCP connector. Same identity, same limits, same audit entry. Optional 15 s voice ask. | Tencent products, UX, Innovation |
| 2:50-3:25 | Tencent stack (one slide, 30 s) | CodeBuddy built it and its security review ran on Hy3; WorkBuddy as front door; Hy3 answers; Miora made the art. "Tencent is in the product, not just on the slide." | Overall, Feasibility |
| 3:25-4:05 | Evidence | 0 leaks in 5,958 adversarial requests; 0 of 400 in the differential test; 639 plus 38 tests. One sentence on how the differential test works. MAS TRM mapping. | Technical, Responsible AI |
| 4:05-4:40 | Business value | Base case about US$1.3M a year for a 600-person fintech; name the inputs (search time, incident cost avoided, audit prep) and say it is an estimate. Deploys on Tencent Cloud; open Hy3 weights allow in-house hosting. | Feasibility, Impact |
| 4:40-5:00 | Close | "Trust is what lets a regulated company switch AI on. Walnut Internal Brain: answers you can prove." Show URL or QR. | Storytelling |
| 5:00-6:00 | Q&A | Section 6. | all |

Stage rules: pre-warm accounts and the model before walking up; one recorded fallback clip per live moment; no background slide; numbers large on screen.

Weak spots to cover: Human-Centered Design and UX/Accessibility are hard for an RBAC product, so make the three-person view the centrepiece, use plain-language refusals, keyboard-usable UI, and voice if built.

## 6. Ten likely judge questions

| # | Question | Crisp answer |
|---|---|---|
| 1 | How do you know it does not leak? | 5,958 adversarial requests (injection, role-play, cross-role) gave 0 leaks; 400 paired questions asked as two roles gave 0 differences exposing restricted data. Reproducible from the repo. Admit no test proves zero forever. |
| 2 | Is the permission check in the model or outside it? | Outside. Retrieval filters documents by the caller's role before the model sees anything, so it cannot reveal what it was never given. |
| 3 | What does the audit trail give a regulator? | Every ask, retrieval, decision and answer with user, role and time, chained by hashes so edits or deletions are detectable; we just showed tamper detection. |
| 4 | How does this map to MAS TRM? | Controls are mapped to the relevant TRM areas (access control, logging, data protection, AI and third-party use) in a document in the repo. It supports a bank's own assessment, it does not replace it. |
| 5 | What is the US$1.3M based on? | Base case for 600 staff: search time saved, fewer permission incidents, faster audit preparation, with assumptions and a sensitivity range stated. An estimate, not a measured result. |
| 6 | Why Hy3? | Strong price and 256K context, open weights so it can be self-hosted if data cannot leave, live on TokenHub. The design is model-agnostic. |
| 7 | PDPA and data residency? | Data stays in the customer's tenant; only retrieved snippets reach the model; open weights allow fully private hosting. |
| 8 | How is it different from the other Internal Brain? | We lead with proof: measured leak tests, tamper-evident audit, a second front door through WorkBuddy. Be gracious; do not name them unless asked. |
| 9 | What if the model hallucinates? | Answers cite permitted documents; with no source it says it does not know; refusals are logged; shown live. |
| 10 | How would Aspire adopt it and what is next? | Connect to existing identity groups, pilot with one team (support or operations), then expand. Next: SSO/SCIM, more connectors, an admin review dashboard. |

Also be ready for "what did you build versus reuse" (answer honestly, say what the Tencent tools sped up) and "cost per query" (use Hy3 pricing, about 1 yuan per million input tokens).

## 7. Differentiating from EdgeLab's "InternalBrain"

I have not seen their product, so differentiate on evidence and presentation.

| Lever | Your move |
|---|---|
| Name | Say "Walnut DataBridge" first, "Internal Brain" second; keep the Walnut logo and colours on every screen. |
| Proof over claims | Put the measured numbers on screen and show a test run. |
| Tamper demo | A live hash-chain break is memorable and hard to fake. |
| Two front doors | Web app plus WorkBuddy MCP shows deeper Tencent integration. |
| Regulation | MAS TRM mapping speaks to the Aspire audience. |
| Business | A named ROI model with stated assumptions. |
| Order | You may not know who presents first. Prepare an opener that works either way ("you may have seen another Internal Brain today; here is what we measured"). Never criticise them. |

If their repo or video is public, check their RBAC and logging claims and make sure your first minute includes something they cannot show. **[verify: not researched here]**

## 8. Gaps and cautions

- No public rules, judges list or global finals results were found.
- The Aspire Head of AI is not identifiable from public sources; their preferences above are inference.
- The Hy4 preview appears only on a TokenHub page; do not claim it.
- RTC free-tier text is inconsistent across pages; confirm in the console.
- Tencent's "over 90% task success" figure is internal; do not repeat it as fact.
