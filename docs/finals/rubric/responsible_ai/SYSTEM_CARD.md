# System card: Walnut DataBridge, Internal Brain

> **Status.** Written 30 Oct 2026 for Demo Day (3 Nov). Self-assessed by the team. Not certified, not
> audited by a third party, not legal advice. Synthetic data only.
>
> **How to read references.** `databridge_ib/…` means `databridge/apps/internal_brain/databridge_ib/…`.
> `tests/…` means `databridge/apps/internal_brain/tests/…`. Line numbers refer to
> commit `e5406b9` (30 Oct). The function or test name is the stable part.
>
> **Eval numbers** come from [RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md) (run of
> 14 Oct). They predate the 30 Oct pipeline edits, so each one is marked **(re-run pending)**.

This card follows the shape of a model card ([Mitchell et al., 2019](https://arxiv.org/abs/1810.03993)),
adapted to a whole system. Section 9 is laid out as an AI inventory record for the MAS Guidelines on AI
Risk Management (issued 7 Oct 2026). Section 8 is the risk materiality assessment those guidelines ask for.

---

## 1. Purpose and scope

| Item | Statement | Evidence |
|---|---|---|
| What it does | Answers a staff member's plain-English question from Confluence, Jira, Slack and Google Drive, using only what that person can open in the source system at that moment. Every sentence cites a source. Every question and answer is logged first. | `INTERNAL_BRAIN.md:10-16`; `databridge_ib/pipeline.py:1-10` |
| Intended users | Employees of a fintech (engineers, SREs, ops, account managers, security, compliance) and contractors. | `INTERNAL_BRAIN.md:129-131` |
| Out of scope | Not a decision-maker. Not advice to customers. Not a system of record. It grants no access, takes no actions and calls no tools. | `docs/tencent/RESPONSIBLE_AI.md:14-15` |
| A refusal is normal | One constant refusal text for "restricted", "does not exist" and "not relevant". | `databridge_ib/models.py:20-21` |
| Deployment today | A laptop demo on synthetic data. Four mock platforms with real API shapes. No live tenant. No public URL. | `INTERNAL_BRAIN.md:29-33`; `docs/finals/DECISIONS.md` D-03 |

**Misuse we warn against.** Wiring the answers into decisions about people (hiring, credit, conduct)
would change the risk class (see [FRAMEWORK_CROSSWALK.md](FRAMEWORK_CROSSWALK.md), EU AI Act row). The
scope statement above is the control.

## 2. Users and roles

| Role (persona) | Can do | Cannot do | Evidence |
|---|---|---|---|
| Employee (`jdoe`, `priya.sre`, `leo.ops`, `sara.sec`, `amy.am`, `mlro.tan`) | Ask; see their own map, history and own-request trace | See audit, other people's requests, counts or denial reasons | `databridge_ib/app.py:138-203` |
| Contractor / guest (`ken.ext`) | Same as an employee, limited by the source's own guest rules | Same as above | `tests/test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical` (line 53) |
| Compliance officer (`maya.compliance`) | Audit search, accessors, reasoned reveal, verify chain, `/audit/stats`, `/audit/journey` | Read more *content* than their own source permissions allow | `databridge_ib/audit/routes.py:46, 84`; `tests/test_audit_routes.py::test_non_compliance_roles_forbidden` (line 68) |
| IT admin (`admin.it`) | Run a fixed list of platform changes in the simulator | Reach audit | `databridge_ib/sim.py:29`; same test |
| Data owners | Stay the authority: the Brain mirrors source permissions and never grants | — | `docs/tencent/RESPONSIBLE_AI.md:51` |

## 3. Model chain

| Order | Model | Provider | When it is used | Evidence |
|---|---|---|---|---|
| 1 | `hy3` (Tencent Hunyuan Hy3) | Tencent TokenHub, `https://tokenhub-intl.tencentcloudmaas.com/v1` | When a TokenHub key is set and `IB_FORCE_OFFLINE` is not `1` | `databridge_ib/llm.py:29-30, 74-86` |
| 2 | `deepseek-v4.1-flash` | Same TokenHub endpoint | When `hy3` errors, times out, returns 429, returns empty text or fails grounding | `databridge_ib/llm.py:88-104`; `tests/test_llm_chain.py::test_hy3_raises_falls_back_to_deepseek_flash` (line 84) |
| 3 | `extractive-v1` (deterministic) | None: quotes up to 4 verbatim sentences from released passages | When no model is configured, the kill switch is on, every model fails, or a model answer cites no released passage | `databridge_ib/narrate.py:145-197`; `databridge_ib/pipeline.py:171-179`; `tests/test_llm_chain.py::test_both_fail_pipeline_serves_extractive` (line 112) |

- **Live status.** Hy3 has **not** been run live. Tencent account verification is pending; TokenHub
  answered 402 / 401006 for every model on 30 Oct (`docs/finals/PLAN.md` §3.4). All numbers on this card
  come from a capturing fake model and the extractive path. Demo answers are extractive.
- **Bring your own model.** `IB_MODELS` plus any OpenAI-compatible endpoint (`databridge_ib/llm.py:11-14,
  38-46`; `tests/test_model_config.py`).
- **The model has no tools.** It gets a system message and a user message only (`databridge_ib/llm.py:91-92`).
- **Prompt version.** `ib-prompt-2026-10-13.1` (`databridge_ib/narrate.py:19`), written to every
  `answer.released` event (`databridge_ib/pipeline.py:190-191`).
- **Seed.** Fixed at 7 (`databridge_ib/llm.py:96`). Whether TokenHub honours it is not verified.
- **Kill switch.** `IB_FORCE_OFFLINE=1` stops every model call (`databridge_ib/llm.py:78-79`;
  `tests/test_model_config.py::test_force_offline_wins_over_any_key`). It is global and read at start-up.

## 4. Data

| Item | Statement | Evidence |
|---|---|---|
| Source data | Synthetic "Company A": 32 people, 1,143 documents, 49 planted facts | [DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md) |
| Training | None. No model is trained or fine-tuned on any data. | No training code in `databridge/apps/internal_brain/` |
| Sent to the model | Per question only: the system prompt, at most 14 released passages (at most 2 per document) and the question | `databridge_ib/pipeline.py:34-35, 147`; `docs/tencent/DATA_FLOWS.md:18` |
| Masked before indexing | Card numbers (first 6 + last 4), card security codes, NRIC/FIN, IBANs | `databridge_ib/ingest.py:64, 102`; `databridge_ib/pipeline.py:232`; `tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` (line 206) |
| Embeddings | Local offline hashing embedder; nothing leaves at ingest | `databridge_ib/index.py:159-168`; `databridge_ib/bootstrap.py:63` |
| Kept | Question text and ids/hashes in the ledger; answer text in a separate store | `docs/tencent/DATA_FLOWS.md:33-41` |

## 5. Controls

| Control | What it does | Evidence |
|---|---|---|
| Identity from a signed token only | HMAC token, 60 s to 8 h, revocable; no identity in any request body | `databridge_ib/auth.py:19, 36, 41, 62-63`; `databridge_ib/models.py:154-155`; `tests/test_app_routes.py::test_identity_from_token_only` (line 60) |
| Gate 1 | Ranks only documents the stored source permissions allow; BM25 statistics come from those documents only | `databridge_ib/index.py:1-12, 289-316`; `databridge_ib/pipeline.py:114-118` |
| Relevance gate | Nothing relevant means no Gate 2 call, no model call and the constant refusal | `databridge_ib/index.py:39-42, 377`; `tests/test_pipeline_core.py::test_s3_contractor_refusal_is_the_constant` (line 161) |
| Gate 2 | Asks each source live, one 2 s budget per batch; error, timeout or 429 means deny | `databridge_ib/gates.py:28-54`; `tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook` (line 83) |
| Write-ahead ledger | `request.accepted` → `retrieval.decided` → `answer.released`; a ledger failure means 503 | `databridge_ib/pipeline.py:106-110, 151-158, 184-197`; `tests/test_pipeline_core.py::test_write_ahead_fail_closed` (line 181); `tests/test_app_routes.py::test_ledger_failure_is_503` (line 93) |
| Grounding wall | Numbers, dates and names must appear in cited evidence; else next model, then extractive | `databridge_ib/narrate.py:113-134`; `tests/test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall` (line 198) |
| Injection containment | Passages are labelled untrusted; citation-shaped text is neutralised; sentences addressed to an AI are dropped | `databridge_ib/narrate.py:31-32, 43-67`; `databridge_ib/pipeline.py:169-170` |
| One response shape | Constant `scope_note`, no counts, no denial reasons | `databridge_ib/models.py:18-21, 179-185`; `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted` (line 153) |
| Tamper evidence | Sealed hash chain, separate anchor ledger, external head | `docs/tencent/SECURITY.md:69-90`; `tests/test_audit_tamper.py` |
| Reasoned reveal | Answer text only with a reason, ledgered before it is read, re-hashed against the seal | `databridge_ib/audit/routes.py:183-222`; `tests/test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered` (line 119) |

## 6. Evaluation summary

| Measure | Result | Source |
|---|---|---|
| Leaks | 0 of 5,958 requests; 95% upper bound 0.1007% per request per path **(re-run pending)** | `RESULTS.md:9-11` |
| Requests where the fake model received passages | 683; 95% upper bound on payload leak rate 0.4392% **(re-run pending)** | `RESULTS.md:12` |
| Restricted vs non-existent (P2) | 400 compared, 0 mismatches; control rebuild 0 **(re-run pending)** | `RESULTS.md:16, 101-108` |
| Injection hard failures | 0 in 976 injection-relevant requests **(re-run pending)** | `RESULTS.md:17, 110-116` |
| Released recall on positive probes | 100% on both paths **(re-run pending)** | `RESULTS.md:18` |
| Cited recall | 96.4% (fake model), 87.8% (extractive) **(re-run pending)** | `RESULTS.md:131, 141` |
| Eval canaries | Valid: leaky config caught on 18 of 20; deny-all scores 0% utility **(re-run pending)** | `RESULTS.md:143-146` |
| Backend tests | 645 passed, 30 Oct, 75 s | `python -m pytest apps/internal_brain/tests -q` (see [EVIDENCE_CARD.md](../technical/EVIDENCE_CARD.md)) |
| Real-model run (Hy3) | **Not run** | `RESULTS.md:30` |

## 7. Known limits and gaps

Copied in substance from `INTERNAL_BRAIN.md:243-261` and `docs/tencent/SECURITY.md:208-224`, plus the
30 Oct status.

1. **Hy3 has not run live.** Every number comes from a capturing fake model or the extractive path.
2. **Platforms are mocked.** The connector code is the same; the base URL is injected (`databridge_ib/config.py:36-38`).
3. **Sign-in is a demo login**, not SSO (`databridge_ib/app.py:92-113`). A deployed build refuses to start without a passcode (`databridge_ib/config.py:62-66`).
4. **Grounding checks values and names, not relations between them.** One valid citation is enough for a model answer to be accepted (`databridge_ib/pipeline.py:171-173`; Codex track 1 finding 15).
5. **Relevance is lexical.** 7 expected-refusal questions per path were answered from related permitted pages; none was a leak (`RESULTS.md:24`).
6. **Ledger key-holder window.** A key holder could rewrite records after the last off-host head (`docs/tencent/SECURITY.md:81, 129`).
7. **No retention period** for the ledger or answer store (`docs/tencent/DATA_FLOWS.md:36-38`).
8. **No human review queue, feedback routing or dual-control reveal** (`docs/tencent/RESPONSIBLE_AI.md:53-54`).
9. **No rate limiter; timing side channel not controlled** (`docs/tencent/SECURITY.md:194, 220-224`).
10. **Codex track 1 (35 findings) has no written disposition;** findings 3, 9, 11, 12 and 15 are open by our reading (`docs/tencent/SECURITY.md:208-218`).
11. **English only.** Market-specific policy content for SG and ID only.
12. **In progress (30 Oct):** per-stage timings, `/audit/stats`, `/audit/journey` and `/ib/trace` are built and tested (`tests/test_dev_views.py`). The permission-aware graph (`/ib/graph`) and document view (`/ib/doc`) are **in progress**, not built (`docs/finals/PLAN.md` §5, B5-B6).

## 8. Risk materiality assessment (self-assessed)

The MAS guidelines ask for risk materiality per use case, along impact, complexity and reliance. This is our
reading for the intended use in section 1.

| Dimension | Rating | Why | What keeps it there |
|---|---|---|---|
| **Impact** if it goes wrong | **High** for disclosure; **Low** for wrong answers | A leak of restricted content, or confirming that a suspicious-transaction file exists, is a data-protection and tipping-off exposure (CDSA s.57; `docs/tencent/MAS_TRM_MAPPING.md:53`). A wrong answer misleads one staff member about internal content, and the citation lets them check it. | Disclosure is controlled by deterministic code before the model (Gates 1 and 2, P2), not by model behaviour. Wrong answers are bounded by grounding and citations. |
| **Complexity** | **Medium-High** | Retrieval over four platforms with different permission models; a third-party LLM; webhooks and polling. | Each platform's permission model compiles to `nobody` when unsupported (`docs/tencent/SECURITY.md:228-235`); fail-closed everywhere. |
| **Reliance** | **Low-Medium** | Advisory only. People read cited sources and decide. No automated action, no customer-facing output. | Scope statement; no tools; no decision outputs. |
| **Autonomy** | **None** | The model cannot act, fetch or choose an identity. | `databridge_ib/llm.py:91-92`; `tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument` (line 149) |

**Overall: Medium materiality, with one high-impact failure mode (disclosure) that is controlled outside
the model.** A deploying institution should re-assess when moving from synthetic to real data, when a live
tenant is connected, and when Hy3 (or any model) goes live.

## 9. AI inventory record (MAS AIRG format, draft)

| Field | Value |
|---|---|
| System name | Walnut DataBridge: Internal Brain |
| Version | Branch `final_v1.0`, commit `e5406b9` (30 Oct 2026); prompt `ib-prompt-2026-10-13.1`; policy `ib-policy-1` (`databridge_ib/config.py:21`) |
| Business owner | To be named by the deploying institution (prototype today) |
| Technical owner | Team Walnut |
| Use case | Internal knowledge retrieval with citations for staff; no decisions about people or customers |
| Approved scope of use | Internal staff and contractors; content they can already open; synthetic data in this build |
| Jurisdiction | Singapore (deployment). Content covers SG and ID market policies. |
| AI type | Retrieval-augmented generation over a third-party LLM, with a deterministic extractive fallback |
| Models | `hy3`, `deepseek-v4.1-flash` (aliases, not pinned builds), `extractive-v1` |
| Third parties | Tencent Cloud TokenHub (generation); Tencent WorkBuddy (second front door, outside our ledger: TB7, `docs/tencent/ARCHITECTURE.md:204`) |
| Data used | Source documents filtered per person; question text; see section 4 |
| Personal data | Employee names and authored text in source documents; masked card, NRIC/FIN and IBAN values |
| Materiality | Medium (section 8) |
| Key controls | Section 5 |
| Validation | Leak and injection eval, canaries, 645 backend tests, recorded security reviews ([EVIDENCE_CARD.md](../technical/EVIDENCE_CARD.md)) |
| Monitoring | Ledger events, `/audit/verify`, `/audit/stats` (counts and timings only, `databridge_ib/audit/stats.py:1-5`). No alerting yet. |
| Kill switch | `IB_FORCE_OFFLINE=1` (global, needs a restart) |
| Incident process | [HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md](HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md) |
| Status | Prototype. Not in production. |

## 10. Change log

| Date | Change | Evidence |
|---|---|---|
| 13 Oct 2026 | First eval run: 0 leaks; 6 forged-citation injection failures; 102 uncited model answers | `docs/tencent/SECURITY.md:178-183`; `docs/tencent/RESPONSIBLE_AI.md:37` |
| 14 Oct 2026 | Fixes in `8b5eb57` and `d69ab97`; final run: 0 leaks, 0 injection failures, 0 uncited answers | `RESULTS.md:3` |
| 15 Oct 2026 | Codex tracks 5-7 and two Claude review rounds; fixes recorded | `docs/tencent/SECURITY.md:316-393` |
| 30 Oct 2026 | Per-stage timings, `/audit/stats`, `/audit/journey`, `/ib/trace` (`e5406b9`). Eval re-run pending. | `git show e5406b9`; `tests/test_dev_views.py` |
