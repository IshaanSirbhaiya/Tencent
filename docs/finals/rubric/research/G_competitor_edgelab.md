# G. Competitor recon: EdgeLab / "InternalBrain" (public web only)

Date of search: 30 Oct 2026. Internal research. Not for judges or the submission.

## 5-line summary

1. No public trace of team "EdgeLab" or a project called "InternalBrain" was found: no GitHub repo, YouTube video, Devpost, LinkedIn post, university news, or team member list.
2. The hackathon is confirmed: Tencent Cloud and AI Singapore, AI CAN DO IT Hackathon Singapore 2026, with Aspire's FinTech challenge. Finalists were due 23 Oct, but no finalist list was found in public.
3. Strengths and weaknesses of EdgeLab cannot be assessed. Nothing is known about their stack, features or claims, so any comparison would be a guess and is not made here.
4. Our 13 Oct field notes (KnowBuddy, SoLah, ContextLedger, TenTen) are a different set of public repos and are not evidence about EdgeLab.
5. Five points to emphasise are listed below. None names or disparages any team.

## 1. What was searched

Seven web searches and one page fetch, run 30 Oct 2026:

- "EdgeLab InternalBrain Tencent Cloud AI CAN DO IT hackathon"
- "InternalBrain Aspire hackathon Tencent FinTech github"
- "EdgeLab Tencent Cloud hackathon Singapore 2026 InternalBrain YouTube demo"
- "Tencent Cloud AI CAN DO IT Hackathon Singapore 2026 FinTech finalists"
- "InternalBrain RBAC audit trail Tencent github repository"
- "InternalBrain Tencent Cloud youtube demo enterprise knowledge RBAC Confluence Jira Slack Drive"
- "EdgeLab team hackathon Tencent CodeBuddy WorkBuddy Hunyuan project github 2026"
- WebFetch of the TechNode article on the Singapore industry AI hackathon (DBS, Keppel).

Result: no match for "EdgeLab" or "InternalBrain" in any search. Every hit about the team name was either generic or about another event.

## 2. What was found (public, about the event, not about EdgeLab)

- Kick-off event listing: https://luma.com/26fqf3hy
- TechNode, Singapore industry AI hackathon with DBS and Keppel: https://technode.global/2026/09/18/tencent-cloud-ai-singapore-industry-ai-hackathon-dbs-keppel/ (fetched; it names no teams, finalists, or project names)
- Event facts, as reported in the search summaries (not re-checked against the official page):
  - Runs 16 Sep to 3 Nov 2026, with five tracks: Banking, Real Estate, Healthcare, FinTech, Digital Native.
  - Aspire's FinTech challenge is the enterprise knowledge system with RBAC, security logging and audit trail.
  - Submissions closed 16 Oct; finalists announced 23 Oct; Demo Day and awards 3 Nov.
  - Prize pool S$17,000 (S$10,000 / S$5,000 / S$2,000), per the summaries.
  - Tools named for participants: CodeBuddy, WorkBuddy, Miora. Hy3 is reported to have been integrated into WorkBuddy (36Kr, cited in search summary; not opened).
- Not found: any finalist list, team roster, GitHub organisation, Devpost page, YouTube demo, or university or LinkedIn post for EdgeLab.

## 3. EdgeLab strengths and weaknesses versus our product

Not assessable. No public artefact exists in what we could search, so there is nothing to test or read. Any statement about their RBAC, citations, audit or Tencent use would be invented.

Items to check if the team appears later (only if public):

- Does their repo or demo show a permission check before retrieval, or only after?
- Do they show a live source re-check per document, or only an index-time ACL?
- Is there a published leak test with a restricted-vs-nonexistent comparison?
- Is the audit log tamper-evident, and is there a demo of tampering?
- Do they use WorkBuddy over MCP, and do they use Hy3 live?

## 4. Context from our own file (not about EdgeLab)

COMPETITIVE_NOTES.md (13 Oct, internal) lists four other Aspire-track public repos: KnowBuddy, SoLah, ContextLedger, TenTen. It is self-reported and not run by us. Re-check before quoting. EdgeLab is not in that file.

## 5. Five things to emphasise (no names, no disparagement)

1. Restricted equals nonexistent, tested as a pair: identical responses with and without restricted documents, 0 of 400 differing pairs (`databridge/evals/internal_brain/RESULTS.md`).
2. Tamper-evident audit with an external head and a tamper demo, not only a hash chain. The attacker model is stated (`databridge/scripts/tamper_demo.py`).
3. Filter before the model, twice: an index pre-filter, then a live source check, with deny on any error, timeout or 429.
4. Cited answers bound to permitted evidence, with the grounding wall serving the extractive answer on violation.
5. Measured leak evaluation with canaries that must fire when gates are off, so the 0-leak result cannot pass vacuously. Be explicit that the real-model run is still pending (STATUS.md, Hy3 not yet run live).

Caution from COMPETITIVE_NOTES: several rivals are ahead on live URL, PII masking breadth, WorkBuddy MCP and real embeddings. Do not claim those until built.

## 6. Walnut roadmap is not finished (per repo CLAUDE.md)

Open items read from ROADMAP_KNOWLEDGE_PLATFORM.md and STATUS.md:

- Entity resolution across sources is the top priority and is not yet designed (ROADMAP §4).
- Proof gaps and backend gaps are listed in ROADMAP §3.
- Founder decisions are waiting in ROADMAP §5.
- STATUS: Hy3 via TokenHub awaits Tencent account verification; Lighthouse deploy has no live instance.
