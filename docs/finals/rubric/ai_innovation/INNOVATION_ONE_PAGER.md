# Innovation one-pager

**Walnut DataBridge · Internal Brain · Answers you can prove.**

## 1. The problem nobody else states

A company AI must not only hide a secret. It must not reveal **that** the secret exists. In a fintech this is
the law: telling someone that a suspicious-transaction report exists is the tipping-off offence under
Singapore's CDSA (the repo cites s.57 of the 2020 Revised Edition, s.48 before it; confirm on Singapore
Statutes Online before printing). An assistant that says "you are not allowed to see this" has already
tipped someone off.

So we changed the question from "find the answer" to "**prove the answer**": prove it came only from what
you may open, prove a refusal gives nothing away, and prove the record was not edited afterwards.

## 2. Comparison with incumbents (public documentation only)

We compare what each vendor's **public documentation** says. "Not in public docs" means we did not find it
there. It does **not** mean the product cannot do it.

| Property | Walnut DataBridge | Glean | Microsoft 365 Copilot | Atlassian Rovo | Onyx (ex-Danswer) | Elastic DLS |
|---|---|---|---|---|---|---|
| Permission check at answer time | **Live per-passage check with each source; deny on error or timeout** | Index-time ACL sync for most connectors ([docs](https://developers.glean.com/libraries/indexing-sdk/permissions)); live via Slack's RTS API for Slack ([docs](https://docs.glean.com/connectors/native/slack/setup/slack-rts-connector)) | Trimming to the user's access within the M365 tenant ([Microsoft Learn](https://learn.microsoft.com/en-us/training/modules/examine-data-security-microsoft-365-copilot/3-examine-copilot-protects-sensitive-business-data)) | "Always honors the permissions" ([Atlassian](https://www.atlassian.com/blog/rovo/team25-rovo-for-all)); mechanism not detailed | Permission sync, Enterprise Edition ([docs](https://docs.onyx.app/enterprise_edition/permissioning)) | Role-based query filter inside the engine ([Search Guard](https://docs.search-guard.com/latest/search-guard-fine-grained-access)) |
| "Restricted" identical to "does not exist", **tested** | **0 / 400 paired responses differ** | Not in public docs | Not in public docs | Not in public docs | Not in public docs | Not in public docs |
| Write-ahead, hash-chained audit with live verify | **Sealed before retrieval, before the model, before release; 6 / 6 tamper attacks caught** | Not in public docs | Audit logs exist; tamper evidence not in public docs; logging gaps reported in 2025 ([Datadog](https://securitylabs.datadoghq.com/articles/copilot-studio-logging-gaps/)) | Not in public docs | Not in public docs | Not in public docs |
| Anti-tipping-off compartment | **Built: account manager gets the approved script, byte-identical to a non-existent customer** | Not in public docs | Not in public docs | Not in public docs | Not in public docs | Not in public docs |
| Provenance pointer per value, with time travel | **Every value points to its source row and version; time slider in the Walnut engine** | Citations to documents; per-value pointers not in public docs | Citations to documents; per-value pointers not in public docs | Not in public docs | Citations to documents | Not in public docs |

Where an incumbent is ahead, we say so: Glean's live Slack path matches our Gate 2 idea for one source, and
every incumbent has real tenants and scale we do not.

## 3. Our five properties, each with its proof

| # | Property | One sentence | Proof (tests under `databridge/apps/internal_brain/tests/`) |
|---|---|---|---|
| 1 | **Gate 2: live re-check, fail closed** | Every passage is re-checked with its source at answer time; an error, timeout or 429 means no. | `test_scenarios_e2e.py::test_s4_revocation_without_webhook`; `test_pipeline_core.py::test_s4_revocation_at_source_wins_over_stale_index`; `test_contracts.py::test_fail_closed` |
| 2 | **Restricted ≡ non-existent** | A question about a secret and a question about nothing get identical bytes; ranking statistics come from permitted chunks only, so even scores give nothing away. | 0 / 400 (`RESULTS.md`); `test_scenarios_e2e.py::test_s3_existing_vs_nonexistent_identical`; `test_knowledge_map.py::test_map_is_identical_with_and_without_restricted_docs` |
| 3 | **Write-ahead sealed ledger** | Intent is recorded before the answer exists; edit one character and Verify shows where the chain breaks. | `test_pipeline_core.py::test_write_ahead_fail_closed`, `::test_ledger_events_order`; `test_audit_tamper.py`; `test_audit_verify_content.py`; `submission/04_evidence/tamper_demo_output.txt` |
| 4 | **Tipping-off compartment** | Case notes are readable only by the MLRO; anyone else asking about that customer gets the approved script. | `test_fintech_scenarios.py::test_fc_real_vs_nonexistent_customer_is_byte_identical`, `::test_fc_account_manager_gets_only_the_approved_script` |
| 5 | **Provenance with time travel** | Every value on the Walnut engine's graph points to the file, table, column and row it came from; drag the slider back and later facts disappear. | `databridge/apps/frontend/README.md` (`/brain`, `/evidence`); `docs/vault/24 Bi-temporality.md` |

"Unique" here means: **none that we found** in the public docs above. Never say "nobody else can".

## 4. What we reused and what is new

| Reused (Walnut engine, ours from before, used by import and unmodified) | New for this hackathon |
|---|---|
| Sealed `DecisionLedger`, grounding wall, provenance model, model harness | Four permission compilers, Gate 1 and Gate 2, the differential test, the FinCrime compartment, card and ID masking, the audit console, WorkBuddy MCP connectors, the web app, the leak harness |

## 5. 30-second originality script

> "Ken, a contractor, asks about a report he can't see, then about one that doesn't exist. The two replies
> are byte for byte the same, so nobody learns that a secret exists. 400 paired tests, zero differences.
>
> Now I change one character in a copy of the audit log and press Verify. The chain breaks at that line.
>
> Most products we studied copy permissions into an index. We ask the source again, for every passage, every time.
> Answers you can prove."

(About 75 words; 30 seconds at a calm pace. Personas must match the final run sheet; swap names if it changes.
The tamper demo runs on a copy of the ledger, never the live chain: decision D-19.)
