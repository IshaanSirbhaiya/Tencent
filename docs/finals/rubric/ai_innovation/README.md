# AI Interaction and Innovation & Creativity: deliverables

Walnut DataBridge · Internal Brain · **Answers you can prove.**
Research behind these files: [../research/C_ai_interaction_innovation.md](../research/C_ai_interaction_innovation.md).
How to read every number: [../impact_feasibility/EVIDENCE_NOTES.md](../impact_feasibility/EVIDENCE_NOTES.md).

## Index

| File | What it is | Used where |
|---|---|---|
| [AI_USAGE_MAP.md](AI_USAGE_MAP.md) | 12 rows: each AI job, the Tencent model or tool, what it sees, its guardrail, its test, its honest status | Tencent stack beat; Q&A |
| [INNOVATION_ONE_PAGER.md](INNOVATION_ONE_PAGER.md) | The problem nobody states, the comparison with Glean, M365 Copilot, Rovo, Onyx and Elastic DLS ("not in public docs"), our five properties with proofs, and a 30-second script | Innovation beat; one slide |
| [AGENTIC_ROADMAP.md](AGENTIC_ROADMAP.md) | The "Act" button and voice ask, both **Planned**, with the rules and tests each needs | Q&A only, until built |

## Status at a glance (30 Oct 2026)

| Item | Status |
|---|---|
| Hy3 composing answers through TokenHub | **Wired, live pending** (account activation) |
| DeepSeek fallback | Wired, live pending |
| Extractive fallback | Running; serves every demo answer today |
| NL audit parser | Running, rules first; Hy3 fallback parser **wired, live run pending** |
| WorkBuddy MCP front door | Running |
| CodeBuddy build and Hy3 security review | Done (transcripts in `docs/tencent/evidence/`) |
| Miora art | Done |
| Act button, voice ask | **Planned** |

## Proof points to say on stage

**AI Interaction**

1. "The model only ever sees passages that passed two permission checks. A test asserts restricted text is never in any prompt." (`test_pipeline_core.py::test_p1_restricted_never_in_any_prompt`)
2. "If Hy3 fails, DeepSeek answers. If both fail, a deterministic quote answers, and the record says which." (`test_llm_chain.py`)
3. "The grounding check drops any number or name that isn't in the evidence." (`test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall`)
4. "The same brain answers inside WorkBuddy, through MCP, with the person's own token. The tool has no 'user' argument to abuse." (`test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument`)
5. "CodeBuddy built our WorkBuddy connectors and reviewed our security on Hy3. Every transcript is in the repo."
6. Only after the live run: "Hy3 on Tencent TokenHub wrote this answer; you can see the model name in the journey."

**Innovation & Creativity**

1. "Ask about a secret, ask about something that doesn't exist: identical bytes. 400 paired tests, zero differences."
2. "We ask the source again, for every passage, every time, and deny on any error."
3. "The log is written before the answer exists. Change one character and Verify shows where."
4. "An account manager asking about a customer under investigation gets the approved script, exactly as for a customer that doesn't exist. Anti-tipping-off, built in."
5. "Every value points back to the row and version it came from, and you can go back in time."

## Judge Q&A (short answers)

| Question | Answer |
|---|---|
| Where is the AI? It looks like a filter. | Hy3 is the answer writer (live run pending until confirmed), DeepSeek backs it up, the grounding wall checks it, and WorkBuddy's agent calls it through MCP. The filter is what makes the AI safe to switch on. |
| Is Hy3 actually running? | Say the truth on the day. Today: wired and tested with a capturing model; live run pending TokenHub activation. Hy3 did run our CodeBuddy security review. |
| Why does the answering model get no tools? | Deliberate. Tools touch systems. Actions will be a separate call whose proposals our code runs after a human confirms (planned). |
| Does the audit search use an LLM? | Rules run first. Only when they recognise nothing does a model parser (Hy3, then DeepSeek) get the question and the clock, never a ledger row; its output must pass a closed schema, or we fall back to the rules. It is wired and tested; the live Hy3 run is pending. |
| Why no embeddings? | Embedding at ingest would send every chunk to a provider before access is decided. We rank with BM25 over the asker's permitted chunks only, so statistics leak nothing. |
| What if TokenHub is down on stage? | The chain falls to DeepSeek, then to quotes; the answer is labelled. The demo continues. |
| How do you measure quality? | 0 leaks in 7,374 requests; 0 / 400 differential; 0 injection failures; 100% of allowed sources reach the answer step; 93.2-97.1% cite the expected source. No faithfulness score yet. |
| Copilot and Glean respect permissions. What's new? | They copy permissions into an index or trim within one tenant. We re-check each passage live and prove a refusal looks exactly like nothing. Neither claim is in their public docs. |
| Could Glean add this tomorrow? | Possibly. The differential test suite and the write-ahead ledger are the parts that are hard to retrofit. |
| Multi-agent? | Not today. We chose depth of proof over number of agents. Act and voice are next, under the same rules. |
| What did you reuse? | Walnut's ledger, grounding wall and provenance model, which are ours from before. New: the gates, compilers, differential test, compartment, MCP connectors and web app. |
| What is 0.08%? | Rule of three on 0 leaks in 3,687 requests per answer path. Pooled over both paths it would be 0.04%, but the paths share probes, so we quote 0.08%. |

## Evidence note: the rule-of-three bound

0 leaks in 7,374 requests = 3,687 probes, each run once on the model path and once on the extractive path.
- **Per answer path (our claim):** 3 ÷ 3,687 ≈ **0.08%** (exact one-sided 95%: 0.0812%). `RESULTS.md` reports 0.0814%.
- **Pooled (not our claim):** 3 ÷ 7,374 ≈ **0.04%**. Pooling counts each probe twice, so it overstates confidence.
- **Model actually saw text:** 0 in 1,047 → about 0.29%.

Full explanation: [EVIDENCE_NOTES.md](../impact_feasibility/EVIDENCE_NOTES.md) §1.
