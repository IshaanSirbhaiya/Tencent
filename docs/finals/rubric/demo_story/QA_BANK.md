# Q&A bank: 25 questions, about 15 seconds each

> Questions judges ask **the team** are below. For questions to **ask the product live** (verified answers per persona), see [JUDGE_QUESTION_BANK.md](JUDGE_QUESTION_BANK.md).


Rules:
- Pause one beat, repeat the question in about six words, answer in one sentence, stop. Bridge to the tagline only if it is natural.
- Every fact below comes from a file in this repo (shown in the last column). **[EST]** = an estimate, say "estimate". **[V]** = re-check on the morning of 3 Nov; if it cannot be traced, delete the claim.
- **[DELIVER]** = say "Dev, D." only if `/ib/status` shows `deliverables.enabled: true` on the stage IB and the `P` warm-up built Leo's deliverables; otherwise stop before "Dev, D."
- If you do not know: "I do not know; here is how we would find out."
- Never name or criticise the other FinTech team.
- Hand-offs: technical to Dev, compliance to Mehul, Tencent stack to Aaruni, business and everything else to Ishaan.

| # | Question | Answer (about 15 s) | Lead | Source in repo |
|---|---|---|---|---|
| 1 | How do you know it does not leak? | "We attacked it: 7,374 requests, zero leaks, and 400 paired questions asked two ways, zero differences. No test proves forever; the evals are in the repo and anyone can rerun them." [V] | Dev | `databridge/evals/internal_brain/RESULTS.md` |
| 2 | Is the permission check inside the model? | "No. It runs before the model. A document the asker cannot open never enters the prompt, so the model cannot reveal it." | Dev | `CLAUDE.md` rule 1; `INTERNAL_BRAIN.md` |
| 3 | What if the source system's permissions change? | "There is a second check at the source, live, just before release. Any error, timeout or rate-limit means deny." | Dev | `CLAUDE.md` rule 1 |
| 4 | What does the audit trail give a regulator? | "Each ask, retrieval decision and answer is written before it is released, hash-chained so an edit is detectable. You saw a change get caught." | Mehul | `CLAUDE.md` rule 3; `scripts/tamper_demo.py` |
| 5 | How does this map to MAS TRM? | "A mapping document in the repo links the controls to access control, logging and revocation. It supports a bank's own assessment; it does not replace it." | Mehul | `docs/tencent/MAS_TRM_MAPPING.md` |
| 6 | What is the 1.3 million based on? | "Base case for 600 staff: 90 minutes saved per person per week, 31 dollars an hour, 46 weeks. It is an estimate, not a measurement; a pilot would measure it." [EST][V] | Ishaan | `rubric/research/A_impact_feasibility.md` |
| 7 | Why Hy3? | "Open weights, so a bank can host it in its own walls, a low price, and it is on TokenHub. The design does not depend on it: with no model, answers quote the sources." Add today's honest status: [HY3] "Hy3 live on TokenHub: TO FILL (yes or not yet)." | Aaruni | `STATUS.md`; `INTERNAL_BRAIN.md` |
| 8 | What about PDPA and data residency? | "The data stays in the customer's tenant; only the retrieved passages the asker may see go to the model. Open weights allow fully private hosting. We have not done a PDPA review." | Mehul | `INTERNAL_BRAIN.md`; say the limit |
| 9 | How is it different from the other Internal Brain? | "We focused on proof: leak tests you can rerun, a log you saw break, and a second front door through WorkBuddy. It is a hard challenge and both teams are working on it." Do not compare further | Ishaan | `rubric/research/G_competitor_edgelab.md` (nothing is known about the other team) |
| 10 | What if the model makes something up? | "The answer must cite documents the asker may see. A grounding check compares values and names to the cited passages; if it fails, we serve a quote from the sources instead. The same check runs on anything we build from an answer: every number in a table or chart must sit on the source line that names it, or it is removed. It checks values and labels, not every relation between them. Dev, D." [DELIVER] Then the 15 s script in `docs/finals/deliverables/review_fable.md` §5: §5.1 if Hy3 is live, else §5.2 (the table or chart is built from the sources with no model; never say the model wrote it) | Dev | `CLAUDE.md` rule 5; `INTERNAL_BRAIN.md` limits; `docs/tencent/SECURITY.md` §15.14; `tests/test_deliverables_verify.py::test_adversarial_points_are_dropped` |
| 11 | What did you build and what did you reuse? | "Reused, unchanged: the Walnut evidence engine. Built for this hackathon: the Internal Brain service, the mock platforms, the web app, and the evals. The repo says what was generated." | Ishaan | `CLAUDE.md` code table |
| 12 | What does a query cost? | "About a hundredth of a cent for a typical Hy3 answer, from list prices: roughly 0.07 cents. Refusals and fallback answers cost zero. An estimate." [EST][V] | Aaruni | `rubric/research/A_impact_feasibility.md` (US$0.00069 per answer) |
| 13 | How would Aspire adopt it? | "Connect to their identity groups, pilot with one team, measure minutes saved, then expand. Today sign-in is a demo login standing in for SSO." | Ishaan | `INTERNAL_BRAIN.md` limits |
| 14 | Does it work on real Confluence, Jira, Slack and Drive? | "Today the platforms are mocks with the real API shapes and permission rules. The connectors are real code but have not run against a real tenant." | Dev | `INTERNAL_BRAIN.md` limits |
| 15 | What about prompt injection in documents? | "Retrieved text is treated as data. The adversarial set includes injected documents, with zero hard failures. And the permission gate sits outside the model." | Dev | `RESULTS.md` (injection hard failures 0) |
| 16 | Why does Ken get a refusal and not "access denied"? | "A different message for 'no access' and 'not found' would tell him something exists. One response shape, so restricted looks like non-existent." | Mehul | `CLAUDE.md` rule 4 |
| 17 | Can an admin edit the log? | "Not unseen. Any edit breaks the chain. A key holder could rewrite beyond the last head published off-host; that window is stated, and the tamper demo shows it." | Mehul | `INTERNAL_BRAIN.md` limits; `scripts/tamper_demo.py` |
| 18 | How did you use Tencent products? | "Hy3 on TokenHub for answers, WorkBuddy as a second front door, CodeBuddy to build, a Lighthouse deploy kit for Tencent Cloud (no live instance)." Be exact about what is live today [HY3][CB] | Aaruni | `INTERNAL_BRAIN.md` |
| 19 | What is the hardest unsolved problem? | "Permission drift at the source, and group membership delays. We fail closed. A real deployment needs SCIM and event-driven revocation." | Dev | `docs/finals/rubric/research/E_demo_story_overall.md` §8 |
| 20 | Isn't this RAG with a filter? | "The filter is the easy part. The live source check, one response shape, the grounding check and the tamper-evident log are each tested." | Dev | `CLAUDE.md` |
| 21 | What about other languages? | "The interface is English and 简体中文. Our leak tests are in English. A bad translation is worse than none, so we kept to two." | Ishaan | D-22 |
| 22 | What if the ledger is down? | "The system returns an error. No ledger, no answer: the record is written before the answer is released." | Mehul | `CLAUDE.md` rule 3 |
| 23 | What is your business model? | "TO FILL by the team before Demo Day. Do not improvise: per-seat licence plus a deployment fee for regulated customers is a draft in the research, not a decision." [EST] | Ishaan | `rubric/research/E_demo_story_overall.md` Q23 (unconfirmed) |
| 24 | Who is the first customer? | "We are asking for one pilot team at one regulated company, such as a fintech like Aspire's partners." | Ishaan | run sheet |
| 25 | What would you do with a year? | "Single sign-on and SCIM, more connectors, an admin review dashboard, entity resolution across sources, and an independent security assessment." | Dev | `ROADMAP_KNOWLEDGE_PLATFORM.md`; `INTERNAL_BRAIN.md` |

## Questions we should expect that are not in the 25

| Question | Honest answer |
|---|---|
| Did the 14 answer-quality findings leak anything? | "No. 7 questions expected a refusal but were answered from related pages the asker could open. None was a leak; all are listed in `RESULTS.md`." |
| How good is recall? | "Allowed documents reach the answer step 100% of the time in our tests; the answer cites the expected source 93.2% to 97.1% of the time, depending on how you count." (`INTERNAL_BRAIN.md`) |
| Is it accessible? | "We target WCAG 2.2 AA with automated, keyboard and screen-reader passes. We do not claim compliance." |
| How many people did you test with? | Use the filled usability report. If not filled: "Not yet reported." Never guess |
| Does the UI tell the user something is hidden? | "No. That would break our own rule: restricted and non-existent must look the same." |

## Q&A drill

A teammate asks 10 random questions from the bank at the rehearsal; stop at 1:00. Record it on a phone.
