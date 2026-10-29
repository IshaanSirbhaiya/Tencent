# Privacy impact summary

> **Status.** 30 Oct 2026. A summary, not a full data protection impact assessment, and not legal advice.
> This build processes **synthetic data only**. The analysis below is what a deploying institution would
> need to confirm before using real data.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part. The main source is
> `docs/tencent/DATA_FLOWS.md`.

## 1. In one paragraph

The Brain reads documents that staff already have access to, and shows each person only what they can
already open. The only document text that leaves the host is the passages one person was allowed to see
for one question, sent to Tencent TokenHub, and only when a key is configured
(`docs/tencent/DATA_FLOWS.md:9-12`). Nothing leaves at indexing, because embeddings are local. Card
numbers, card security codes, NRIC/FIN and IBANs are masked before anything is stored. The questions people
ask are logged, with who asked them, for audit. No retention period is defined yet.

## 2. Personal data inventory

| Data | Whose | Where it lives | Who can see it | Evidence |
|---|---|---|---|---|
| Source documents (masked) | Authors and people mentioned | Index `var/ib/index/index.sqlite` | Each person: only what their source permissions allow | `docs/tencent/DATA_FLOWS.md:35`; `databridge_ib/ingest.py:64, 102` |
| Question text and asker id | The asker | Ledger `var/ib/ledger/access.jsonl` | Compliance officer (audit routes); the asker (own history, own trace) | `databridge_ib/pipeline.py:107-110`; `docs/tencent/DATA_FLOWS.md:36` |
| Ids and titles of documents considered and released; Gate 2 verdicts | Asker; document owners | Ledger | Compliance only (the asker's trace omits the considered set and reasons) | `databridge_ib/pipeline.py:151-158`; `databridge_ib/app.py:179-182` |
| Answer text | Asker | Content store `var/ib/content/answers.sqlite`, separate from the ledger | Compliance, only through a reasoned reveal | `docs/tencent/DATA_FLOWS.md:38`; `databridge_ib/audit/routes.py:183-222` |
| Session token id (`jti`) | Asker | Revocation list `var/ib/revoked.json` | Operator | `docs/tencent/DATA_FLOWS.md:40` |
| Sign-in events | Asker | Ledger (`auth.login`) | Compliance | `tests/test_channel_label.py::test_sign_in_is_recorded_before_the_session_is_issued` (line 35) |

**Not stored in the ledger:** answer text and passage text (`tests/test_audit_ledger.py::test_answer_text_never_in_ledger`, line 103).

## 3. Purposes

| Purpose | Data used | Note |
|---|---|---|
| Answer a staff question | Permitted, masked passages | The content's existing purpose, for people who can already open it |
| Audit and investigation | Ledger metadata; answer text by reasoned reveal only | Required for security logging and accountability |
| Service health and analysis | Counts and timings only (`/audit/stats`) | No question text, answer text or titles (`databridge_ib/audit/stats.py:1-5`) |
| Not used for | Training models; profiling staff; decisions about people | No training code; scope statement (`docs/tencent/RESPONSIBLE_AI.md:14-15`) |

Our reading: this is a minimal-new-purpose use (content used for its existing purpose, to the same
audience). The PDPC Advisory Guidelines on personal data in AI systems (1 Mar 2024) discuss the relevant
exceptions. **Take a legal view before relying on this.**

## 4. Transfers

| Flow | What | To | Control | Status |
|---|---|---|---|---|
| Generation | System prompt + ≤ 14 released passages + the question | Tencent TokenHub, `tokenhub-intl.tencentcloudmaas.com` | Only Gate-2-released passages; masked; no call when nothing is relevant | Configured; **Hy3 not yet called live** (`docs/tencent/DATA_FLOWS.md:18`) |
| Embeddings | Nothing | — | Local hashing embedder | A remote embedder would send every chunk; **not enabled** (`docs/tencent/DATA_FLOWS.md:19`) |
| External ledger head | `{log_id, seq, head, at}` only | A separate git repo or a compliance Slack channel | No content | Default sink is local (`docs/tencent/DATA_FLOWS.md:20`) |
| Gate 2 checks | Asker's platform id + document id | The source platform | One attempt, 2 s budget | Active |
| WorkBuddy | The answer and citations for one persona | WorkBuddy and its model provider | Per-persona token; no identity argument | WorkBuddy's model and memory are **outside our ledger** (`docs/tencent/DATA_FLOWS.md:23`) |

**Provider terms.** TokenHub's data handling, retention, training and hosting terms are as Tencent
publishes them. **We have not verified them** (`docs/tencent/DATA_FLOWS.md:25-26`). This matters for PDPA
s.26 (transfer limitation).

## 5. Minimisation and protection

| Measure | Evidence |
|---|---|
| At most 14 passages, 2 per document, per question | `databridge_ib/pipeline.py:34-35, 147` |
| No model call on refusal (2,296 of 2,979 fake-model requests, re-run pending) | `databridge_ib/pipeline.py:163`; `RESULTS.md:49` |
| Masking of card, CVV, NRIC/FIN and IBAN before the index | `tests/test_fintech_scenarios.py::test_sens_raw_values_appear_nowhere_in_the_index` (line 206); `tests/test_sensitive.py` |
| Ids and hashes, not text, in the ledger | `tests/test_audit_ledger.py::test_answer_text_never_in_ledger` |
| Local embeddings | `databridge_ib/index.py:159-168` |
| Restricted content never reaches the model | `tests/test_pipeline_core.py::test_p1_restricted_never_in_any_prompt` (line 144); 0 leaks / 5,958 (re-run pending) |
| Session token never visible to browser JavaScript | `docs/tencent/SECURITY.md:55` |

**Not covered:** other personal data inside permitted passages (names, phone numbers, addresses) is sent
to the model as written (`docs/tencent/RESPONSIBLE_AI.md:40`).

## 6. PDPA obligations

| Section | Obligation (paraphrase) | Status | Gap → proposal |
|---|---|---|---|
| s.24 | Reasonable security arrangements | Supports | — |
| ss.18, 20 | Purpose limitation; notification | Partial | Show the staff notice in §7 on the sign-in screen |
| ss.21, 22 | Access and correction | Partial | Staff see their own question history (`databridge_ib/app.py:165-174`); no correction route |
| s.25 | Retention limitation | **Gap** | No period defined (`docs/tencent/DATA_FLOWS.md:36-41`). Proposal for a deployer to decide: keep the ledger for the institution's record-keeping period on WORM storage; delete answer text sooner (crypto-shredding is roadmap, `docs/tencent/SECURITY.md:222-224`) |
| s.26 | Transfer limitation | Partial | Verify TokenHub terms and hosting before real data |
| Part 6A | Data breach notification | Process only | [HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md](HUMAN_OVERSIGHT_AND_INCIDENT_RUNBOOK.md) incident 1 |

## 7. Draft staff notice (for the sign-in screen)

> Questions you ask here are recorded with your name, the sources used and the answer, so that access can
> be audited. Answers use only content you can already open. Passages may be sent to Tencent Cloud's AI
> service to write the answer. Compliance can read an answer only with a recorded reason.

Not in the UI yet. The new sign-in page is being built this week (`docs/finals/PLAN.md` §3.1, which lists a
privacy one-liner).

## 8. Synthetic data

Company A is generated from a seed, not derived from real records, so there is no re-identification risk
by construction ([DATA_CARD_COMPANY_A.md](DATA_CARD_COMPANY_A.md) §5).
