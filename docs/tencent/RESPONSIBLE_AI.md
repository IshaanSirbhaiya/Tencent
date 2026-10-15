# Responsible AI

> How the Walnut Internal Brain keeps a language model honest, accountable and inside the asker's
> permissions. `file:line` references are to commit `d69ab97`; paths are relative to
> `databridge/apps/internal_brain/databridge_ib/` unless they start with `tests/`
> (= `databridge/apps/internal_brain/tests/`) or `databridge/`. Eval figures are from
> `databridge/evals/internal_brain/RESULTS.md`.

## 0. Purpose and limits

**Purpose.** Staff ask questions across Confluence, Jira, Slack and Google Drive. The Brain answers only from
what that person may open in the source system, cites every claim, and logs every answer for compliance.

**What it is not.** It is not a decision-maker, not a source of advice to customers, and not a system of record.
It does not grant access, take actions or call tools. A refusal is a normal outcome.

**Scope today.** Synthetic data only ("Company A"). The model path is built and tested with a capturing fake
model. A live Hy3 run has **not** happened: Tencent account verification is pending, so no TokenHub key was
available. The platforms are mocks with real-API shapes, not live tenants. Timing side channels are not
measured.

**Five commitments.**

1. No restricted passage reaches the model (section 1, row 1).
2. Every answer is checked against its evidence, or replaced by a deterministic one (row 2).
3. Every answer is logged before it is returned; a ledger failure is a 503 (row 6).
4. People can inspect and challenge what happened, with a reason on record (section 2).
5. The numbers we publish are the numbers we measured, including the gaps (section 3).

## 1. Principles and where they live

| Principle | What the system does | Evidence | Limits |
|---|---|---|---|
| **Permission before generation** | A model only ever sees passages the asker may open now: Gate 1 on source ACLs, then a live Gate 2 check per document | `pipeline.py:113-161`; `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt`; leak eval §3 | The eval's model path is a capturing fake; a real hy3 run is pending |
| **Grounding** | After markdown is stripped, the answer passes Walnut's grounding wall: sentences are bound to `[C<n>]` passages, and numbers, dates and named terms must appear in cited evidence. On any violation the next model is tried, then the extractive answer. A model answer that cites no released passage is also replaced. | `narrate.py:99-120`, `llm.py:65-81`, `pipeline.py:162-171`, `grounding.py`; Walnut's own wall tests: `databridge/apps/gateway/tests/test_llm_codex_f2.py`, `test_llm_codex_f1_r2.py`, `test_fin08_values_compared_by_value.py` | **Relations between evidenced facts are not verified** (P7). One valid citation is enough for a model answer to be accepted (Codex security review r1 finding 15). IB-specific relation, negation and date negatives (`test_grounding_negatives`) are not written yet. hy3 grounding acceptance rate: **TBD (measured in RESULTS.md)**. |
| **Abstention** | If nothing permitted is relevant, the Brain gives one constant refusal and makes **no model call**. The model is told to give the same refusal when the passages do not answer. | `index.py:37-41, 374-377`; `pipeline.py:115-116, 157-158`; `narrate.py:25-26`; `models.py:21` | Refusals are deliberately uninformative ("restricted" looks like "nothing found"), which can frustrate users |
| **Citations** | Cited sentences carry a `[C<n>]` tag. Each citation has a deep link, the content version, `content_checked_at`, `authz_checked_at`, and `live_read` when content was re-fetched on drift. Only tags that match a released passage become citations; tag-shaped text inside sources is neutralised. | `models.py:158-171`; `pipeline.py:172-175, 316-326`; `narrate.py:50-54`; `tests/test_pipeline_core.py::test_drift_read_through` | The first eval run counted 102 model-path answers served without citations (RESULTS.md finding 2); after the trailing-tag fix in `8b5eb57` the final run (2026-10-14) counts **0** uncited answers; its 14 quality findings are all relevance expectations |
| **No identity from the model** | Identity is the signed token's `sub` only; request bodies are closed schemas; the model has no tools and cannot choose a principal | `auth.py`; `models.py:154-155`; `tests/test_contracts.py::test_no_identity_in_request_shapes`; `tests/test_app_routes.py::test_identity_from_token_only` | WorkBuddy front door: `tests/test_mcp_server.py` (no tool takes an identity argument) |
| **Accountability per answer** | `answer.released` records the model provider and id, `fallback_used`, `PROMPT_VERSION`, the answer hash, citations with versions and check times, and latency. It is written **before** the response. | `pipeline.py:176-185`; `narrate.py:19`; `tests/test_pipeline_core.py::test_ledger_events_order` | Model ids are aliases, not pinned builds. The exact prompt payload can be reconstructed, but is not stored verbatim (MAS_TRM_MAPPING.md §2). |
| **Data minimisation** | Per query, only released passages (at most 14, two per document) go to the model. The ledger holds ids and hashes, not answer text; the audit projection never holds answer text; embeddings are computed locally. | `pipeline.py:33-35, 144`; `audit/ledger.py:1-16`; `audit/query.py:1-12`; `index.py:158-167`; `tests/test_audit_ledger.py::test_answer_text_never_in_ledger` | The question text is stored in the ledger (`pipeline.py:106`) so compliance can see what was asked. Card numbers, security codes, NRIC/FIN and IBANs are masked at ingest (`ingest.py:19, 63-64`, `sensitive.py`; `tests/test_sensitive.py`). Other personal data inside permitted passages is not redacted. Synthetic data only. |
| **Injection defence** | Containment: access before content, no model tools, delimited and labelled passages under an "untrusted data" system rule, neutralised citation tokens, the grounding wall, and no arbitrary link fetching. The extractive answer skips sentences addressed to an AI. | SECURITY.md §4; injection fixtures on all four platforms (`databridge/fixtures/enterprise/generate.py`, facts `INJ-*`); `tests/test_scenarios_e2e.py::test_canonical` (`INJ-q1`…`INJ-q4`) | First run: 6 injection hard failures, all forged citations (fixed in `8b5eb57`). The final run (2979 probes) found model-path answers repeating a sentence addressed to an AI; model answers now drop such sentences too (`narrate.drop_addressed_to_ai`). Final run: **0** injection hard failures |
| **Determinism where it matters** | Time-window parsing, platform hints, ranking, both gates, the audit filter and the extractive answer are deterministic; the model is called with a fixed seed | `timewin.py`; `pipeline.py:36-50`; `audit/nl_filter.py:1-20`; `llm.py:73` | TokenHub sampling determinism under a seed is not verified |

## 2. Human oversight

| Who | What they can do | Evidence |
|---|---|---|
| Compliance officer (`maya.compliance`) | Query every event's metadata (who, when, question, documents considered and released, decisions, model, citations); ask in natural language through a closed filter; verify the chain and the external head | `audit/routes.py:92-133, 163-174`; `tests/test_audit_routes.py::test_ask_and_accessors`, `::test_verify_route` |
| Compliance officer | **Reveal** an answer's text only with a non-empty reason. The reveal is ledgered **before** the text is read, and the response says whether the text still matches the sealed hash. | `audit/routes.py:135-161`; `tests/test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered` |
| IT admin (`admin.it`) | Trigger a closed list of platform changes from the Admin simulator (runbook update, channel revocation, page restriction, reset) to demonstrate S2 and S4. Changes reach the Brain only through signed webhooks or the poller. | `sim.py:1-8, 50-66`; UI `databridge/apps/internal_brain_web/app/sim/page.tsx` |
| Data owners | Remain the authority: the Brain mirrors source permissions and never grants access | `connectors/base.py:1-16` |

**Not built:** a human review queue for low-confidence answers, user feedback ("thumbs down") routing, and a
dual-control ("never alone") reveal.

## 3. Model governance

| Item | What is recorded or fixed | Evidence |
|---|---|---|
| Model and fallback | Provider, model id and `fallback_used` on every `answer.released` event | `pipeline.py:176-185`; `models.py:235` |
| Prompt version | `PROMPT_VERSION` (`ib-prompt-2026-10-13.1`) logged per answer and shown in the audit view | `narrate.py:19`; `pipeline.py:183`; `audit/query.py:503` |
| Evidence | Released document ids, content versions and check times per citation | `models.py:158-171`; `tests/test_pipeline_core.py::test_ledger_events_order` |
| Kill switch | `IB_FORCE_OFFLINE=1` stops every model call; the Brain answers extractively | `llm.py:56` |
| Known gaps | Model ids are aliases, not pinned builds. The prompt payload is not stored verbatim. Configuration changes are not ledgered. | MAS_TRM_MAPPING.md section 2 |

## 4. Accessibility

axe-core runs on the five screens in their populated states, at desktop and phone widths
(`databridge/apps/internal_brain_web/tests/e2e/a11y.spec.ts`). Keyboard use is tested in
`databridge/apps/internal_brain_web/tests/e2e/keyboard.spec.ts`. Automated checks do not replace testing with
assistive technology, which has not been done.

## 5. Evaluation

The leak eval lives in `databridge/evals/internal_brain/` (run `python -m evals.internal_brain.run` from
`databridge/`; smoke test `tests/test_eval_smoke.py`).

**What each request is checked for** (RESULTS.md, "What each request is checked for"):

1. **P1 transport capture:** every model payload is exactly the system prompt + the context of released
   passages + the question; every passage's document is in the ledger's released set, is Gate-2-allowed, and is
   readable under an independent ground-truth ACL (the generator's own evaluator, not the Brain's index).
2. **No forbidden fingerprints** (denied-fact sentinel sentences, canaries of unreadable docs, word shingles
   unique to unreadable docs) in any payload, embedder input, answer or response body.
3. **Ledger invariants:** all three write-ahead events exist; ledger citations are released.
4. **World expectations:** expected refusals are the constant refusal; `must_cite_facts` measure utility.
5. **Eval canaries:** the detectors must fire on a deliberately leaky configuration (gates off), and a
   deny-all configuration must score zero utility, so "no leaks" cannot pass vacuously
   (`tests/test_eval_smoke.py::test_eval_canary_detects_a_leaky_pipeline`).

**Final run** (RESULTS.md, 2026-10-14; two model paths: a capturing fake model and the deterministic
extractive answerer; offline embedder; after the fixes in `8b5eb57` and `d69ab97`. The first run, 2026-10-13,
found 6 forged-citation injection failures and 102 uncited answers; both are fixed and counted 0 here):

| Measure | Value |
|---|---|
| Probes | 2,979, each run in both paths (5,958 requests), over 11 personas and 16 probe types (direct, identifier, paraphrase, completion, three metadata types, multi-hop, injection, positive, and world questions) |
| Requests with a leak | **0** of 5,958; 95% upper bound 0.101% per request per path (rule of three) |
| Requests where the model actually received passages | 683 (95% upper bound on payload leak rate 0.44%) |
| P1 / ledger contract violations | 0 |
| P2 differential (world with vs without restricted docs) | 400 compared, **0** mismatches (control rebuild: 0) |
| Injection hard failures | **0** (the earlier run's forged citations and quoted injection sentences are fixed; regression tests in `test_pipeline_core.py`) |
| Answer-quality findings (not disclosures) | 14: seven world questions the manifest expects refused were answered from permitted, related pages (relevance, not access) |
| Utility: released recall on positive probes | 100.0% (fake model path), 100.0% (extractive) |
| Eval canaries valid | True |
| Real-model run (hy3 via TokenHub) | **TBD**: no TokenHub key in the eval environment |

RESULTS.md lists every finding with a root-cause hypothesis. The hy3 run will be published the same way, including any non-zero count.
