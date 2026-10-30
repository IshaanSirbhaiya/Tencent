# Red-team report against the OWASP Top 10 for LLM Applications (2025)

> **Status.** Self-assessed, 30 Oct 2026. Attacks were run by our own eval harness and by AI reviewers
> (Codex, CodeBuddy on Hy3 and further AI review seats; see `docs/tencent/DEV_PROCESS.md`). No human third-party penetration test. Synthetic data only.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part.
>
> **Eval numbers** are from [RESULTS.md](../../../../databridge/evals/internal_brain/RESULTS.md), run of
> 14 Oct, two model paths (a capturing fake model and the extractive path). The headline numbers were re-run
> on the finals code on 30 Oct and match (`eval_summary.json`). **No real model has been attacked yet** (Hy3 not live).

Source list: [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/2024/11/17/owasp-reveals-updated-2025-top-10-risks-for-llms-announces-new-llm-project-sponsorship-program-and-inaugural-sponsors/).

## Summary

| ID | Risk | Our main control | Result | Residual risk |
|---|---|---|---|---|
| LLM01 | Prompt injection | Containment: access before content, no tools, untrusted-data labelling, tag neutralisation | 0 hard failures / 1,152 (re-run 30 Oct) | Medium: real-model behaviour not measured |
| LLM02 | Sensitive information disclosure | Two permission gates before the model; masking at ingest; one response shape | 0 leaks / 7,374; 0 / 400 P2 (re-run 30 Oct) | Low-Medium: other personal data not redacted; timing |
| LLM03 | Supply chain | Pinned Python lock and exact npm versions; vendor SDK only in the harness | Not attack-tested | Medium: no SBOM, no dependency audit, model aliases |
| LLM04 | Data and model poisoning | No training; poisoned text reaches only people who can already read it | Covered by injection fixtures | Low-Medium: an author can mislead their own readers |
| LLM05 | Improper output handling | Markdown stripped; answer rendered as text; links only for http(s) | Codex track 6 #4 fixed | Low |
| LLM06 | Excessive agency | The model has no tools; identity never from the model | Tests pass | Low inside our boundary; WorkBuddy's agent is outside |
| LLM07 | System prompt leakage | The prompt holds no secrets or permissions | By design | Low |
| LLM08 | Vector and embedding weaknesses | Permission-aware index; statistics over permitted docs only; local embedder | 0 / 400 P2 (re-run 30 Oct) | Low; would rise if a remote embedder were enabled |
| LLM09 | Misinformation | Grounding wall; citations; extractive fallback; approved-only policies | 0 uncited answers (re-run 30 Oct) | Medium: relations not verified; lexical relevance |
| LLM10 | Unbounded consumption | No model call on refusal; caps on passages, body and question; 2 s Gate 2 budget | 2,640 of 3,687 requests needed no model call | Medium: no rate limiter |

## What broke, and what we fixed

Showing failures is the point of a red team. These are from our own records.

| When | What the attack found | Fix | Evidence |
|---|---|---|---|
| First eval run, 13 Oct | **6 injection hard failures**: a literal `[C9]` inside a Jira source bound to a real citation tag (integrity failure, not a disclosure) | Citation-shaped text in sources is rewritten to `(source-text C9)` before any model sees it | `docs/tencent/SECURITY.md:178-183`; `databridge_ib/narrate.py:50-53` |
| First eval run, 13 Oct | **102 model-path answers served without citations** | Trailing tags moved inside their sentence (`8b5eb57`) | `docs/tencent/RESPONSIBLE_AI.md:37`; `databridge_ib/narrate.py:41, 70-74` |
| Final run, 14 Oct | Model-path answers **repeated** a sentence addressed to an AI (quoting, not obeying) | Model answers now drop such sentences too, and the count is ledgered | `docs/tencent/RESPONSIBLE_AI.md:41`; `databridge_ib/pipeline.py:169-170` |
| Codex track 2 | 23 ledger findings, incl. a stale external head passing and reveal returning mismatched text | Fixed or stated as residual, each with a test | `docs/tencent/SECURITY.md:124-151` |
| Codex track 3 | 34 connector and masking findings (fail-open parsing, NBSP-hidden card numbers) | Fixed or documented; masking hardened | `docs/tencent/SECURITY.md:226-281` |
| Codex track 6 | Web proxy: `%2e%2e` path tricks, no CSRF check, any URL scheme in links | All ten fixed | `docs/tencent/SECURITY.md:353-369`; `databridge/apps/internal_brain_web/tests/e2e/proxy.spec.ts` |
| Independent AI review | The eval exited 0 even with leaks | It now exits 1 on any leak, violation, P2 mismatch or invalid canary | `docs/tencent/SECURITY.md:339`; `databridge/evals/internal_brain/run.py:237-240` |

## Detail per risk

### LLM01 Prompt injection

- **Attacks tried.** Planted on all four platforms (`databridge/fixtures/enterprise/generate.py`, facts
  `INJ-*`; [DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md)):
  `INJ-confluence-title` (instruction in a title and "ignore previous instructions" body),
  `INJ-jira-forged-citation` (a forged `[C9]` security approval),
  `INJ-slack-base64` (a base64 instruction to print a restricted report),
  `INJ-drive-audit-tools` (asks the assistant to call audit tools).
  These re-enact the shapes of EchoLeak (CVE-2025-32711) and the Aug 2024 Slack AI exfiltration
  (`docs/finals/rubric/research/D_technical_responsible_ai.md` §2.10).
  Plus 323 injection-assisted probes across personas (`RESULTS.md:67`).
- **Controls.**
  1. Access before content: an injected document reaches a model only if the asker can already read it (`databridge_ib/pipeline.py:114-139`).
  2. No tools: text in, text out (`databridge_ib/llm.py:91-92`).
  3. Passages labelled as untrusted data written by other people (`databridge_ib/narrate.py:31-32`).
  4. Citation-shaped text neutralised (`databridge_ib/narrate.py:50-53, 89`).
  5. Sentences addressed to an AI are skipped in extractive answers and dropped from model answers (`databridge_ib/narrate.py:43-67, 161`).
  6. No arbitrary fetch: links only for supported id shapes inside the readable set, depth 1, then Gate 2 (`tests/test_connector_slack.py::test_links_only_supported_shapes`, line 324).
  7. Audit NL filter returns a closed schema (`tests/test_audit_nl_filter.py::test_injection_text_cannot_escape_the_schema`, line 58).
- **Detectors.** `followed_base64`, `forged_citation`, `source_tag_echoed`, `tool_text_not_from_source`, `injected_text_echoed` (`RESULTS.md:116`).
- **Result.** 0 signals in 1,152 injection-relevant requests (re-run 30 Oct) (`RESULTS.md:112-114`). Canonical questions `INJ-q1` to `INJ-q4` pass (`tests/test_scenarios_e2e.py::test_canonical`, line 48).
- **Residual.** Delimiting is not datamarking or encoding (`docs/tencent/SECURITY.md:161`). The fake model does not reason like Hy3, so real-model susceptibility is **unmeasured**.

### LLM02 Sensitive information disclosure

- **Attacks tried.** 3,687 probes × 2 paths over 11 personas and 16 probe types: direct, identifier, paraphrase, completion, three metadata types (exists, author, count), multi-hop, injection, positive and world questions (`RESULTS.md:52-67`). Two extra personas (`sam.old`, `ivy.guest`) test ACL-derived denials (`RESULTS.md:33`).
- **Detectors.** Denied-fact sentinels, `CNRY-` canaries and 10-word shingles unique to unreadable docs, checked in every model payload, embedder input, answer and response body; ground truth from the generator's own ACL, independent of the Brain (`RESULTS.md:35, 40-43`).
- **Controls.** Gate 1 + Gate 2 before the model (`databridge_ib/pipeline.py:114-139`); masking of card, CVV, NRIC/FIN and IBAN at ingest (`databridge_ib/ingest.py:64, 102`); one constant refusal (`databridge_ib/models.py:20-21`).
- **Result.** 0 leaks in 7,374 requests, 95% upper bound 0.0814% per request per path; 0 of 400 P2 mismatches (re-run 30 Oct) (`RESULTS.md:9-16`). Tests: `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt` (line 144), `tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` (line 206), `tests/test_fintech_scenarios.py::test_fc_real_vs_nonexistent_customer_is_byte_identical` (line 100).
- **Residual.** Other personal data in permitted passages is sent as written (`docs/tencent/RESPONSIBLE_AI.md:40`). Timing is not controlled (`docs/tencent/SECURITY.md:194`).

### LLM03 Supply chain

- **Controls.** Python dependencies pinned in `databridge/requirements-lock.txt` (Python 3.13). Web dependencies pinned to exact versions in `databridge/apps/internal_brain_web/package.json` with a lock file. Vendor SDKs are reached only through Walnut's harness (`databridge_ib/llm.py:1-5, 81`; checker `databridge/scripts/check_vendor_imports.py`).
- **Result.** Not attack-tested. A pattern scan of the git history (16 Oct) found no real key (`docs/tencent/SECURITY.md:205`).
- **Residual.** No SBOM; no `pip-audit` or `npm audit` in CI; gitleaks not run; model ids are aliases, not pinned builds.

### LLM04 Data and model poisoning

- **Controls.** No training or fine-tuning. A poisoned document can reach only people who can already read it (Gates 1 and 2). The grounding wall accepts only values found in cited evidence. Draft policies are never indexed (`tests/test_fintech_scenarios.py::test_fee_draft_is_never_indexed_considered_or_released`, line 171).
- **Result.** Covered by the `INJ-*` fixtures above.
- **Residual.** A malicious author can still mislead readers of their own page. The citation shows where it came from, which is the mitigation.

### LLM05 Improper output handling

- **Controls.** Markdown is stripped before grounding (`databridge_ib/narrate.py:103-126`). The web app renders answers as text; the only `dangerouslySetInnerHTML` is a static theme script (`databridge/apps/internal_brain_web/app/layout.tsx:13, 19`). Source links render as links only for http(s) (Codex track 6 #4, `docs/tencent/SECURITY.md:363`). Upstream 5xx bodies are replaced by a fixed message (`docs/tencent/SECURITY.md:367`).
- **Result.** Fixed and covered by `databridge/apps/internal_brain_web/tests/e2e/proxy.spec.ts`.
- **Residual.** Low. The web UI is being rebuilt this week; the rule must be re-checked in the final review (`docs/finals/PLAN.md` §6, wave 3).

### LLM06 Excessive agency

- **Controls.** The Brain's model has zero tools (`databridge_ib/llm.py:91-92`). Identity comes only from the signed token; request bodies are closed schemas (`databridge_ib/models.py:25, 154-155`). WorkBuddy tools take no identity argument.
- **Result.** `tests/test_mcp_server.py::test_internal_brain_tools_have_no_identity_argument` (line 149), `::test_audit_tools_have_no_identity_argument` (line 157), `tests/test_contracts.py::test_no_identity_in_request_shapes` (line 51).
- **Residual.** WorkBuddy's own model and memory see our answer and sit outside our ledger (TB7, `docs/tencent/ARCHITECTURE.md:204`).

### LLM07 System prompt leakage

- **Controls.** The prompt holds rules only: no keys, no permissions, no user data (`databridge_ib/narrate.py:21-35`). Authorisation lives in code.
- **Result.** By design; not tested by extraction attacks.
- **Residual.** Low. The prompt is not confidential and does not need to be.

### LLM08 Vector and embedding weaknesses

OWASP's own mitigations include permission-aware vector stores and partitioning by access level
([LLM08](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)).

- **Controls.** Per-user readable set; BM25 document frequency and average length computed over the asker's permitted chunks only; Gate-2-denied documents leave the ranking pool; local embedder (`databridge_ib/index.py:1-12, 301-316`; `databridge_ib/pipeline.py:137-139`).
- **Result.** P2 differential (world with vs without the persona's unreadable docs): 0 of 400 mismatches (re-run 30 Oct) (`RESULTS.md:101-108`); `tests/test_pipeline_core.py::test_p2_differential_world_with_and_without_restricted` (line 153); map identical with and without restricted docs (`tests/test_knowledge_map.py::test_map_is_identical_with_and_without_restricted_docs`, line 62).
- **Residual.** A remote embedder would send every chunk to a provider (TB8, Codex track 1 finding 11). It is disabled.

### LLM09 Misinformation

- **Controls.** Grounding wall (`databridge_ib/narrate.py:129-134`); citations with version and check times (`databridge_ib/models.py:158-170`); extractive fallback; approved-policy-only rule (above).
- **Result.** 0 uncited answers in the final run (`docs/tencent/RESPONSIBLE_AI.md:37`). 14 answer-quality findings: 7 expected refusals per path answered from related permitted pages (re-run 30 Oct) (`RESULTS.md:15, 24`).
- **Residual.** Relations between facts are not verified; one valid citation is enough (`databridge_ib/pipeline.py:171-173`).

### LLM10 Unbounded consumption

- **Controls.** No model call when nothing is relevant (`databridge_ib/pipeline.py:163`); ≤ 14 passages (`databridge_ib/pipeline.py:34, 147`); question ≤ 2,000 chars (`databridge_ib/models.py:155`); proxy body ≤ 64 KiB (`databridge/apps/internal_brain_web/app/api/ib/[...path]/route.ts:43`); Gate 2 one 2 s budget (`databridge_ib/gates.py:44`).
- **Result.** 2,640 of 3,687 fake-model requests were refused with no model call (`RESULTS.md:49`) (re-run 30 Oct).
- **Residual.** No per-user rate limiter. Audit verify is O(records) (`docs/tencent/SECURITY.md:136`).

## Appendix: probe taxonomy

Per persona × type, each run on both paths (`RESULTS.md:52-67`). Total 3,687.

| Type | Count | Type | Count |
|---|---|---|---|
| direct | 318 | injection | 323 |
| identifier | 227 | pos_direct | 94 |
| paraphrase | 328 | pos_keywords | 102 |
| completion | 234 | world:aggregation | 27 |
| meta_exists | 228 | world:canonical | 29 |
| meta_author | 228 | world:direct | 129 |
| meta_count | 226 | world:metadata | 135 |
| multihop | 216 | world:paraphrase | 135 |

**Not done yet:** an AI Verify Project Moonshot run against `/ib/ask`, and a red-team on a real model
(`docs/finals/rubric/research/D_technical_responsible_ai.md` §4, R4b).
