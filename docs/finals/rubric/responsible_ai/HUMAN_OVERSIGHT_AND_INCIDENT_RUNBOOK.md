# Human oversight and incident runbook

> **Status.** 30 Oct 2026. The oversight mechanisms in section 1 are built and tested. The incident
> procedures in section 3 are **written, not rehearsed**, and assume a deployment that does not exist yet
> (today the system runs on a laptop with synthetic data). Not legal advice.
>
> **References.** `databridge_ib/…` = `databridge/apps/internal_brain/databridge_ib/…`; `tests/…` =
> `databridge/apps/internal_brain/tests/…`. Line numbers refer to commit `e5406b9` (read them with `git show e5406b9:<path>`); the code is being edited this week, so the function or test name is the stable part. Commands run from
> `databridge/`.

## 1. Who oversees what

| Who | Can do | Cannot do | Evidence |
|---|---|---|---|
| **Data owners** (in each platform) | Grant and revoke access at the source. The Brain honours a revocation on the very next question, even if the webhook is lost. | — | `databridge_ib/gates.py:1-5`; `tests/test_scenarios_e2e.py::test_s4_revocation_without_webhook` (line 83) |
| **Compliance officer** (`compliance_officer` role) | Search every event; list who received a document; ask in plain English through a closed filter; reveal an answer's text with a reason; verify the chain; see `/audit/stats` and `/audit/journey` | See content beyond their own source permissions; reveal without a reason | `databridge_ib/audit/routes.py:1-20, 46, 84`; `tests/test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered` (line 119) |
| **IT admin** (`admin_it`) | Run the fixed simulator actions; set the kill switch and keys (operator) | Use audit routes | `databridge_ib/sim.py:29`; `tests/test_audit_routes.py::test_non_compliance_roles_forbidden` (line 68) |
| **Every asker** | See their own history and how their own answer was made (`/ib/trace`) | See anyone else's requests, counts or denial reasons | `databridge_ib/app.py:165-203`; `tests/test_dev_views.py::test_trace_is_own_requests_only_and_hides_gate2_reasons` (line 93) |

**Every oversight action is itself logged first.** Audit calls append `audit.query` before they return,
including failed calls (`docs/tencent/SECURITY.md:111-117`;
`tests/test_audit_routes.py::test_failed_audit_calls_are_ledgered`, line 204). A reveal appends
`audit.reveal` before the text is read (`databridge_ib/audit/routes.py:202-206`).

## 2. Automatic escalation (no human in the loop yet)

| Trigger | What happens | Evidence |
|---|---|---|
| Nothing relevant the asker may open | Constant refusal; no model call | `databridge_ib/pipeline.py:163, 180` |
| Model error, timeout, 429 or empty text | Next model in the chain, then the extractive answer | `databridge_ib/llm.py:93-104` |
| Grounding violation | Same: next model, then extractive | `tests/test_llm_chain.py::test_ungrounded_number_refused_by_grounding_wall` (line 198) |
| Model answer cites no released passage | Extractive answer, `fallback_used=true` | `databridge_ib/pipeline.py:171-179` |
| Model repeats a sentence addressed to an AI | Sentence dropped; count ledgered | `databridge_ib/pipeline.py:169-170, 196` |
| Ledger cannot be written | 503; nothing is answered | `tests/test_app_routes.py::test_ledger_failure_is_503` (line 93) |
| Gate 2 cannot confirm access | Document denied | `databridge_ib/gates.py:49-53` |

**Not built:** a human review queue for low-confidence answers; feedback routing ("wrong source", "out of
date"); dual-control ("never alone") reveal; alerting. The MAS AI guidelines §5.9(c) ask for escalation
to human review; ours escalates to a deterministic answer instead
(`docs/tencent/MAS_TRM_MAPPING.md:35`).

## 3. Incident runbook

Each incident: detect, contain, investigate, recover, notify. The **owner** is a role in the deploying
institution. Notification rules are for the institution's legal and compliance team to confirm; the ones
below are pointers, marked **[verify]**.

### Incident 1: suspected leak (someone received content they should not have)

| Step | Action | Route or command | Owner |
|---|---|---|---|
| 1 Contain | Revoke the person's access **at the source platform**. The Brain honours it on the next question. | Source platform admin | Data owner |
| 2 Scope | List everyone who received the document through the Brain. | `GET /audit/doc/{id}/accessors` (`databridge_ib/audit/routes.py:141-153`) | Compliance |
| 3 Scope | List that person's requests in the window. | `GET /audit/events?user=&since=&until=` (`databridge_ib/audit/routes.py:114-139`) | Compliance |
| 4 Investigate | Open each request end to end: considered set, Gate 2 verdicts, released set, model. | `GET /audit/journey/{req_id}` (`databridge_ib/audit/routes.py:249-264`) | Compliance |
| 5 Investigate | Read the exact answer only if needed, with a reason. | `POST /audit/reveal` (`databridge_ib/audit/routes.py:183-222`) | Compliance |
| 6 Preserve | Verify the chain and record the result before anything else changes. | `GET /audit/verify` | Compliance |
| 7 Notify | If personal data: assess whether it is a notifiable breach under PDPA Part 6A (notify PDPC within 3 calendar days of assessing it as notifiable) **[verify]**. If FinCrime content: tell the MLRO at once (tipping-off risk, `docs/tencent/MAS_TRM_MAPPING.md:53`). Check the institution's MAS incident-reporting obligations **[verify]**. | — | DPO, MLRO, Compliance |

Content already delivered cannot be retracted (`docs/tencent/MAS_TRM_MAPPING.md:20`).

### Incident 2: the model misbehaves (bad answers, following injected text)

| Step | Action | Route or command | Owner |
|---|---|---|---|
| 1 Contain | Set `IB_FORCE_OFFLINE=1` and restart. Every answer becomes extractive. | `databridge_ib/llm.py:78-79`; `tests/test_model_config.py::test_force_offline_wins_over_any_key` | IT admin |
| 2 Investigate | Find affected answers: filter `answer.released` by model and time; open journeys. | `/audit/events?kind=answer.released`; `/audit/journey/{req_id}` | Compliance |
| 3 Investigate | Re-run the eval to reproduce. | `python -m evals.internal_brain.run` (exits 1 on any leak) | Engineering |
| 4 Recover | Fix (prompt, grounding, model choice). Bump `PROMPT_VERSION` (`databridge_ib/narrate.py:19`) so the ledger separates before and after. | — | Engineering |

Limit: the switch is global and needs a restart (`docs/tencent/MAS_TRM_MAPPING.md:36`).

### Incident 3: ledger tamper alert (`/audit/verify` fails)

| Step | Action | Route or command | Owner |
|---|---|---|---|
| 1 Contain | Freeze: copy `var/ib/ledger/`, `var/ib/anchors/` and the external head before anything else. | File copy | IT admin |
| 2 Investigate | Compare with the off-host head; the report names the problem and the seq. | `GET /audit/verify`; `python scripts/tamper_demo.py --var <copy> --external <heads.jsonl>` (`databridge/scripts/tamper_demo.py:1-23`) | Compliance |
| 3 Escalate | Treat as a security incident; suspect a key holder if the internal check passes but the external head fails (`docs/tencent/SECURITY.md:79-80`). | — | CISO |
| 4 Recover | Start a fresh ledger directory with a new key; keep the old one read-only as evidence. | `IB_VAR`, `IB_LEDGER_KEY` | IT admin |

Limit: rollback after the last published head is not detected (the stated window,
`tests/test_audit_tamper.py::test_rollback_after_last_publication_is_the_stated_window`, line 106).

### Incident 4: a prompt-injection document is found in a source

| Step | Action | Route or command | Owner |
|---|---|---|---|
| 1 Contain | Restrict or remove the document at the source. Gate 2 denies it on the next question. | Source platform | Data owner |
| 2 Scope | Find every answer that released it. | `GET /audit/doc/{id}/accessors` | Compliance |
| 3 Investigate | Check those answers for followed instructions (reveal with a reason if needed). | `/audit/journey/{req_id}`; `/audit/reveal` | Compliance |
| 4 Learn | Add the shape to the eval's injection fixtures. | `databridge/fixtures/enterprise/generate.py` (`INJ-*`) | Engineering |

### Incident 5: key compromise

| Key | Action | Effect | Evidence |
|---|---|---|---|
| `IB_TOKEN_KEY` | Rotate and restart | Every session token stops verifying (HMAC under the old key), so everyone signs in again | `databridge_ib/auth.py:41, 62-63` |
| A single token | Revoke its `jti` (logout does this) | That token is refused; an unreadable revocation list fails closed | `databridge_ib/app.py:115-130`; `databridge_ib/auth.py:87, 134` |
| `IB_LEDGER_KEY` | Publish the current head off-host first, then rotate into a new ledger directory | The old ledger cannot be extended with a wrong key | `tests/test_audit_ledger.py::test_wrong_key_cannot_read_or_extend`; `docs/tencent/SECURITY.md:129` |
| Webhook secret | Rotate on both sides | Forged change events are refused; events only invalidate, never grant | `docs/tencent/SECURITY.md:244-246, 306` |

A deployed build refuses to start on the public dev keys (`databridge_ib/config.py:48-61`;
`tests/test_config_deployable.py::test_deployed_dev_keys_refused`, line 40).

## 4. What a judge can see live

1. **Revocation on the next question:** Simulator → "Remove jdoe from #db-migration" → ask again → refusal (`INTERNAL_BRAIN.md:45`).
2. **Verify chain, then tamper:** Audit → Verify audit chain → "Chain intact"; then `python scripts/tamper_demo.py` (`INTERNAL_BRAIN.md:172`).
3. **Fail closed:** `tests/test_app_routes.py::test_ledger_failure_is_503`.
