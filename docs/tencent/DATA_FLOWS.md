# Data flows: what leaves the system, and what is kept

> Status as of 15 Oct 2026. Code references were re-checked against commit `8e66591`; the function or
> test name is the stable part. Paths are relative to `databridge/apps/internal_brain/databridge_ib/`
> unless they start with `tests/` (= `databridge/apps/internal_brain/tests/`), `databridge/` or `deploy/`.
> **All data in this repo is synthetic** ("Company A", `databridge/fixtures/enterprise/`). No real company,
> customer or personal data is used, and the deploy kit runs against the mock platforms only.

In short: the only document text that leaves the Internal Brain host is the passages one person was
allowed to see for one question, sent to Tencent TokenHub, and only when a TokenHub key is configured.
Nothing leaves at ingest, because embeddings are computed locally. Answer text is kept in one local
store, separate from the audit log.

## 1. What leaves the Internal Brain host

| Flow | What is sent | Destination | When | Control | Evidence | Status |
|---|---|---|---|---|---|---|
| **Generation** | System prompt + the **released passages for this question only** (at most 14, two per document) + the question | Tencent TokenHub, `https://tokenhub-intl.tencentcloudmaas.com/v1` (`llm.py:24`), which our research records as the Singapore (global) endpoint. Models `hy3`, then `deepseek-v4.1-flash` on the same provider (`llm.py:25`). | Per question that passes the relevance gate | Only Gate-2-released passages; no model call when nothing is relevant; sensitive values already masked at ingest | `pipeline.py:145, 156-162`; `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt`; [leak eval results](../../databridge/evals/internal_brain/RESULTS.md): 0 leaks in 5,958 requests, including 683 where a capturing fake model received passages | Only when `TOKENHUB_API_KEY` is set; off with `IB_FORCE_OFFLINE=1` or no key (`llm.py:28-30, 56`). Configured, but hy3 has not yet been called live (TokenHub account verification pending). |
| **Ingest embeddings** | Nothing today. A remote embedder would send **every chunk of every document**, whoever may read it. | (TokenHub `kinfra-text-embedding-0.6b`, alias defined in `llm.py:33-37`) | (At ingest, before any question) | — | `bootstrap.py:63`; `index.py` `offline_embedder` | **Not enabled.** The index uses the default **offline hashing embedder**, computed locally. A remote embedder would be a separate ingestion boundary outside P1, and would also embed each question before `retrieval.decided` (Codex security review r1, finding 11). This table must change if it is enabled. |
| **External ledger head** | `{log_id, seq, head, at}`: ids and hashes, **no content** | A file in a separately pushed git repo (`IB_ANCHOR_FILE`) and/or a compliance-only Slack incoming webhook (`IB_ANCHOR_SLACK_WEBHOOK`) | At startup and every 900 s by default | Only a verified ledger is published | `audit/anchor.py` `publish_head`; `bootstrap.py:85-137` | The default sink is a local file (`var/ib/external_anchor/heads.jsonl`), so nothing leaves until one is configured |
| **Gate 2 permission checks** | The asker's platform id and the document id | The source platform (the mock today) | Per candidate document per question, and per item when someone opens their knowledge map | One attempt; one 2 s budget per answer batch (45 s for a map) | `gates.py`; `knowledge_map.py` | Active |
| **Audit NL parser (optional model)** | The audit question and the clock only, never a ledger row | A configured model, only if the deterministic rules recognise nothing | Per `/audit/ask` | Closed output schema | `audit/nl_filter.py:1-6`; `tests/test_audit_nl_filter.py::test_model_receives_only_question_and_clock` | No model is wired by default (`bootstrap.py:119-122` passes none) |
| **WorkBuddy (MCP)** | `ask_internal_brain`: Walnut's answer and its citations for one persona. `my_recent_questions`: that persona's own past questions. Audit server: event kinds, people, request ids and times, per-document accessor counts and the chain check result, never answer text. | WorkBuddy, and its own model provider | Per MCP tool call | One token per persona; no identity argument | `mcp_server.py`, `audit_mcp_server.py`; `tests/test_mcp_server.py` | Built and used for the screenshots in `submission/02_workbuddy/`. What WorkBuddy's model and memory do with an answer is **outside our ledger**. |

**Provider terms.** TokenHub's data-handling, retention and training terms are as published by Tencent.
**We have not verified them.** The fallback model runs on the same provider.

## 2. What is stored, and where

All runtime state lives under `var/ib/` (git-ignored), or under `IB_VAR` (`config.py:33, 70-101`). Tests and
evals use their own root.

| Store | Path | Holds | Does not hold | Retention |
|---|---|---|---|---|
| Index | `var/ib/index/index.sqlite` | Document text (sensitive values masked), chunks, vectors, compiled permission expressions, metadata | Raw card numbers, card security codes, NRIC/FIN, IBANs | Until the source deletes the item; it is then removed from the index on the next sync, webhook or drift read-through (`ingest.py:60-66, 99-105`; `pipeline.py:219-222`) |
| Access ledger | `var/ib/ledger/access.jsonl` | Question text, principal, ids and titles of documents considered and released, Gate 2 verdicts and reasons, model id, prompt version, answer hash, citations | Answer text, passage text | **Not defined** (append-only, never pruned) |
| Internal anchors | `var/ib/anchors/access.anchors.jsonl` (a separate directory) | Ledger anchors | Content | Not defined |
| Answer content store | `var/ib/content/answers.sqlite` | Answer text, one row per request | — | **Not defined** |
| Audit projection | Derived SQLite, rebuildable from the ledger | Event metadata for audit queries | Answer text (`audit/query.py:1-12`) | Rebuilt on demand |
| Revocation list | `var/ib/revoked.json` | Revoked token ids | — | Not defined |
| Cursors | `var/ib/cursors.json` | Per-platform sync cursors | Content | Overwritten |

**Sensitive values are masked before anything is stored** (feature F4). The ingestor, and the read-through
on version drift, pass every document through `sensitive.py` `mask_doc` before it reaches the index
(`ingest.py:63-64, 102`; `pipeline.py:224`). Luhn-valid card numbers become first 6 + last 4; card security
codes, Singapore NRIC/FIN and mod-97-valid IBANs are masked too. The rules are data in
`databridge/packs/enterprise/sensitive.yaml`. Because the index holds only the masked form, so do the
prompts, answers and citations built from it (`tests/test_sensitive.py`;
`tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index`). The raw values
remain in the source platforms, which is where a data owner would redact them.

**Not built:** a retention schedule, legal hold, and deletion propagation into the ledger (the ledger is
append-only by design and holds no answer text). Envelope encryption and crypto-shredding of answer text are
roadmap items ([SECURITY.md](SECURITY.md)).

## 3. Synthetic data only

- The world is generated deterministically by `databridge/fixtures/enterprise/generate.py --seed 7
  --now 2026-10-13T09:00:00+08:00 --scale demo`; the sha256 of `world.json` is recorded in `manifest.json`
  ([fixtures README](../../databridge/fixtures/enterprise/README.md)).
- People use the `companya.example` domain; customers and ids are invented. The planted card numbers are
  published test numbers (Visa 4111 1111 1111 1111, Mastercard 5555 5555 5555 4444), the IBAN is the
  standard example IBAN, and the NRIC is checksum-valid but invented (`generate.py`).
- The leak eval derives its ground truth from the generator's own permission model, independent of the
  Brain's index and connectors (`databridge/evals/internal_brain/oracle.py`).
