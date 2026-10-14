# Data flows: what leaves the system, and what is kept

> Status as of 13 Oct 2026; `file:line` references are to commit `4d1887e`. Paths are relative to
> `databridge/apps/internal_brain/databridge_ib/` unless they start with `tests/` or `databridge/`.
> **All data in this repo is synthetic** ("Company A", `databridge/fixtures/enterprise/`). No real company,
> customer or personal data is used, and the public demo never connects to real workspaces (plan §5.3).

## 1. What leaves the Internal Brain host

| Flow | What is sent | Destination | When | Control | Evidence | Status |
|---|---|---|---|---|---|---|
| **Generation** | System prompt + the **released passages for this query only** (at most 14, two per document) + the question | Tencent TokenHub, `https://tokenhub-intl.tencentcloudmaas.com/v1` (`llm.py:24`), which our research records as the Singapore (global) endpoint. Models `hy3`, then `deepseek-v4.1-flash` on the same provider (`llm.py:25`). | Per question that passes the relevance gate | Only Gate-2-released passages; no model call when nothing is relevant | `pipeline.py:144, 155-161`; `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt`; RESULTS.md (0 payload leaks in 526 model calls, fake model path) | Active when `TOKENHUB_API_KEY` is set; off with `IB_FORCE_OFFLINE=1` or no key (`llm.py:28-31, 55-57`) |
| **Ingest embeddings** | Would be **every chunk of every document**, whoever may read it | TokenHub `kinfra-text-embedding-0.6b` (alias defined in `llm.py:33-37`) | At ingest, before any query | — | — | **Not enabled.** `bootstrap.py:63` builds the index with the default **offline hashing embedder**, computed locally (`index.py:158-160, 167`). A remote embedder would be a separate ingestion boundary outside P1, and would also embed each query before `retrieval.decided` (Codex security review r1 finding 11). This table must change if it is enabled. |
| **External ledger head** | `{log_id, seq, head, at}`: ids and hashes, **no content** | A file in a separately pushed git repo (`IB_ANCHOR_FILE`) and/or a compliance-only Slack incoming webhook (`IB_ANCHOR_SLACK_WEBHOOK`) | At startup and every 900 s by default | Only a verified ledger is published | `audit/anchor.py:1-25, 129-148`; `bootstrap.py:85-137` | The default sink is a local file (not off-host) until configured |
| **Gate 2 permission checks** | The asker's platform id and the document id | The source platform (the mock today) | Per candidate document per query | One attempt, 2 s budget | `gates.py` | Active |
| **Audit NL parser (optional model)** | The audit question and the clock only, never a ledger row | A configured model, only if the deterministic rules recognise nothing | Per `/audit/ask` | Closed output schema | `audit/nl_filter.py:1-20`; `tests/test_audit_nl_filter.py::test_model_receives_only_question_and_clock` | No model is wired by default (`bootstrap.py:121-122` passes none) |
| **WorkBuddy (planned)** | Walnut's answer and citations for one persona | WorkBuddy and its model provider | Per MCP tool call | One token per persona; no identity argument (plan §4.9) | — | **Not built.** Once built, what WorkBuddy's model does with the answer is outside our ledger. |

**Provider terms.** TokenHub's data-handling, retention and training terms are as published by Tencent.
**We have not verified them.** The fallback model runs on the same provider.

## 2. What is stored, and where

All runtime state lives under `var/ib/` (git-ignored), or under `IB_VAR` (`config.py:33, 61-91`). Tests and
evals use their own root.

| Store | Path | Holds | Does not hold | Retention |
|---|---|---|---|---|
| Index | `var/ib/index/index.sqlite` | Document text, chunks, vectors, compiled ACL expressions, metadata | — | Until the source deletes or trashes the item; it is then removed from the index (`pipeline.py:215-218`, `ingest.py`) |
| Access ledger | `var/ib/ledger/access.jsonl` | Question text, principal, ids and titles of documents considered and released, Gate 2 verdicts and reasons, model id, prompt version, answer hash, citations | Answer text | **Not defined** (append-only, never pruned) |
| Internal anchors | `var/ib/anchors/access.anchors.jsonl` (a separate directory) | Ledger anchors | Content | Not defined |
| Answer content store | `var/ib/content/answers.sqlite` | Answer text, one per request, write-once | — | **Not defined** |
| Audit projection | Derived SQLite, rebuildable from the ledger | Event metadata for queries | Answer text (`audit/query.py:1-12`) | Rebuilt on demand |
| Revocation list | `var/ib/revoked.json` | Revoked token ids | — | Not defined |
| Cursors | `var/ib/cursors.json` | Per-platform sync cursors | Content | Overwritten |

**Not built:** a retention schedule, legal hold, and deletion propagation into the ledger (the ledger is
append-only by design and holds no answer text). Envelope encryption and crypto-shredding of answer text are
roadmap (plan review disposition).

**Sensitive values at ingest (feature F4, in progress).** `sensitive.py` (uncommitted at the time of writing)
masks Luhn-valid card numbers to first 6 + last 4, card security codes, Singapore NRIC/FIN and mod-97-valid
IBANs, with rules as data in `databridge/packs/enterprise/sensitive.yaml`. Once the ingestor calls it, the index,
embeddings, prompts, answers and citations would hold only the masked form. Until then, permitted passages are
stored and sent as they are.

## 3. Synthetic data only

- The world is generated deterministically by `databridge/fixtures/enterprise/generate.py --seed 7
  --now 2026-10-13T09:00:00+08:00`; its sha256 is recorded in `manifest.json`
  (`databridge/fixtures/enterprise/README.md`).
- People use the `companya.example` domain; customers and ids are invented. The rule for feature F4 is that
  any planted card number is a card network's published test number, never a real one.
- The leak eval derives its ground truth from the generator's own ACL evaluator, independent of the Brain's
  index and connectors (`databridge/evals/internal_brain/`).
