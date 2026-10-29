# AI usage map

Which Tencent model or tool does which job in Walnut DataBridge, what it is allowed to see, the guardrail
around it, and the test that covers it. Paths are under `databridge/apps/internal_brain/` unless shown
otherwise.

**Status words:** **Running** = used in the product today · **Wired, live pending** = code and tests exist,
the real service has not answered yet · **Planned** = not built ([AGENTIC_ROADMAP.md](AGENTIC_ROADMAP.md)).

> **Hy3 live status (30 Oct 2026): pending.** Hy3 has not answered inside the Brain yet. TokenHub account
> activation is the blocker (`STATUS.md`; `RESULTS.md` configuration line "hy3: PENDING"). Every eval number
> comes from a capturing test model on the same path, or from the extractive path. Hy3 *has* run inside
> CodeBuddy sessions CB1 and CB5, through CodeBuddy's own model access (`docs/tencent/DEV_PROCESS.md`).
> Update row 1 the moment a live Hy3 answer carries `model: hy3` in its release record.

## The map

| # | Job | Tencent model or tool | What it sees | Guardrail | Test or evidence | Status |
|---|---|---|---|---|---|---|
| 1 | **Compose the answer** | **Hy3 on TokenHub** (Singapore international endpoint), `DEFAULT_CHAIN = ("hy3", "deepseek-v4.1-flash")` in `databridge_ib/llm.py` | Only passages released by Gate 1 and Gate 2, plus the question. System prompt `ib-prompt-2026-10-13.1` (`narrate.py`): cite every sentence `[Cn]`, copy numbers exactly, treat passages as untrusted data | Filter before the model; grounding wall drops unsupported values and names; `retrieval.decided` sealed before the call | `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt`; `tests/test_llm_chain.py::test_hy3_success_returns_hy3_answer_no_fallback`; 0 payload leaks in 683 model calls (`RESULTS.md`) | **Wired, live pending** |
| 2 | **Fallback model** | **DeepSeek V4.1 Flash on TokenHub** | Same released passages | Same gates and grounding wall; the answer names the model that wrote it | `tests/test_llm_chain.py::test_hy3_raises_falls_back_to_deepseek_flash` | **Wired, live pending** |
| 3 | **Last-resort answer** | Deterministic extractive answer (no model), `narrate.extractive_answer` | Same released passages | Quotes only permitted sentences; used when both models fail, the grounding check fails, or no key is set | `tests/test_llm_chain.py::test_both_fail_pipeline_serves_extractive`; `tests/test_pipeline_core.py::test_model_answer_that_is_only_injection_falls_back_to_extractive`; extractive path in `RESULTS.md` (0 / 2,979) | **Running** (all demo answers today) |
| 4 | **Model switch and kill switch** | Config: `TOKENHUB_API_KEY`, `IB_MODELS`, `IB_FORCE_OFFLINE` | Environment only, never a key in any response | No key = deterministic path; `IB_FORCE_OFFLINE=1` wins over any key | `tests/test_model_config.py::test_no_key_means_the_deterministic_path`, `::test_force_offline_wins_over_any_key`, `::test_tokenhub_key_gives_the_tencent_chain` | **Running** |
| 5 | **Plain-English audit question → filter** | **Rule-based parser.** `databridge_ib/audit/nl_filter.py` has an optional model hook (`llm=`), but the running app does not pass one (`bootstrap.py` builds the audit router without it). **No model is used here today** | The compliance officer's question and the clock. A model, if ever wired, gets the same and never a ledger row | Closed `AuditFilter` schema (`extra="forbid"`), 365-day cap, fail closed on any unparsed constraint | `tests/test_audit_nl_filter.py::test_llm_only_when_rules_recognise_nothing`, `::test_llm_output_is_validated_and_fails_closed`, `::test_model_receives_only_question_and_clock`, `::test_injection_text_cannot_escape_the_schema`; `tests/test_audit_query.py::test_s5_reconstruction` | **Running** (rules); model hook tested, not wired |
| 6 | **Second front door** | **WorkBuddy** via MCP: `ask_internal_brain`, `my_recent_questions`; a separate audit server for compliance (`mcp_server.py`, `audit_mcp_server.py`) | Our answer and citations for one persona. WorkBuddy's own model then sees that answer, **outside our ledger** (`docs/tencent/DATA_FLOWS.md`) | One signed token per persona; tools take no identity argument; employees refused on audit tools | `tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument`, `::test_audit_tools_have_no_identity_argument`, `::test_employee_forbidden_on_audit_verify`; screenshots in `submission/02_workbuddy/` | **Running** |
| 7 | **Build the code** | **CodeBuddy** (sessions CB2, CB3, CB4, CB6) | The repo, task prompts in `docs/tencent/evidence/codebuddy_prompts/` | Human review; tests must pass; Codex review; CB6 fixed its own code after that review | Transcripts `docs/tencent/evidence/codebuddy_cb2..cb6.jsonl`; `tests/test_mcp_server.py`, `tests/test_mint_token_cli.py`, `tests/test_llm_chain.py` | **Done** |
| 8 | **Security review and design** | **CodeBuddy on Hy3** (CB5 security review, CB1 trust-boundary diagram) | Read-only repo | Every finding checked against the code; dispositions recorded | `docs/tencent/reviews/codebuddy_security_review.md`; `docs/tencent/SECURITY.md` §10; `docs/tencent/diagrams/trust_boundaries.mmd`; transcripts CB1, CB5 | **Done** |
| 9 | **Retrieval ranking** | No hosted model. BM25 over the asker's permitted chunks, plus a local hashing vector leg (`databridge_ib/index.py`) | The asker's permitted chunks only | Ranking statistics come from permitted chunks only, so hidden documents cannot shift a score; no chunk is sent to an embedding provider | `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted`; 0 / 400 differential (`RESULTS.md`) | **Running** (by design: no provider embeddings) |
| 10 | **Cover and thumbnail art** | **Miora** | Our written brief | Not in the product path; title and tagline added afterwards so text stays sharp | `submission/06_cover/miora_session1.png`, `miora_session2.png` | **Done** |
| 11 | **Act on an answer** (draft reply, open ticket) | Hy3 with `tools`, proposals executed by our code | Released passages only | Asker's token; write-ahead `action.proposed`; human confirm | None yet | **Planned** |
| 12 | **Voice ask** (English and Chinese) | Tencent ASR / TTS (Route A) or Tencent RTC Conversational AI (Route B) | Speech becomes text before any gate | Same gates; the ledger stores text; the person sees the recognised text before sending | None yet | **Planned** |

## What this shows

- **Several distinct AI jobs, each fenced.** The answering model has no tools and sees only released
  passages. The audit parser is deterministic. Every fallback is labelled.
- **Honest gaps.** Hy3 live is pending. The audit parser uses no model today. Act and voice are planned.
  Say these before a judge asks.

## One line for stage

"Hy3 on Tencent TokenHub writes the answer, but only from passages that passed two permission checks. If
Hy3 fails, DeepSeek answers; if both fail, a deterministic quote answers, and the record says which."
(Say "Hy3 writes" only after the live run is confirmed. Until then: "Hy3 is wired in; today's answers are
quoted straight from the sources, with the same checks.")
