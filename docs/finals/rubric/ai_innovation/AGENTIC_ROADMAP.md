# Agentic roadmap

Each item is marked **Planned**, **In progress** or **Built** and moves to **Done** only when its tests pass
and it runs in the demo. Do not show or describe a Planned item as done.

Status on 30 Oct 2026: §1 **Act is built, tested and committed** (`8312864` on `final_v1.2`; draft a reply, create a follow-up ticket; `test_act.py`, `test_act_e2e.py`, e2e `act.spec.ts`; details
and the differences from the plan below). §2 voice is still **Planned**. Neither appeared in the priority
list of `docs/finals/PLAN.md` §9 (P0-P2). Research: `../research/C_ai_interaction_innovation.md` §1.3, §2.3 C-D.

The same hard rules apply to every agentic feature:
1. The model never sees text the asker cannot open (Gate 1 and Gate 2 first).
2. Identity comes only from the signed token. No tool takes a `user` argument.
3. The ledger is written **before** anything happens.
4. A person confirms before anything changes outside the Brain.

## 1. "Act" button: permission-checked actions

**Status: Built, with tests (30 Oct).** Code: `databridge/apps/internal_brain/databridge_ib/actions.py`,
the routes `POST /ib/act/draft`, `/ib/act/ticket/propose`, `/ib/act/ticket/confirm`, `/ib/act/ticket/cancel`
in `app.py`, the Jira write side in `connectors/jira.py` (`projects`, `creatable`, `create_issue`), and on
the mock platforms Jira's `POST /rest/api/3/issue` plus the `CREATE_ISSUES` permission check. Web: the
"Act on this answer" row in `components/user/AnswerCard.tsx` and the sheet in `components/user/ActSheet.tsx`
(EN + 简体中文, `lib/i18n/act.ts`). Contracts: `docs/finals/contracts/backend_v1.md` (v3). Security:
`docs/tencent/SECURITY.md` §15.11.

What was built, step by step:

| Step | What happens | Rule it keeps | Test |
|---|---|---|---|
| 1 | Under a released answer the person presses **Draft a reply** or **Create follow-up ticket** | Identity from the signed token; no body field names a person | `tests/test_act.py::test_bodies_carry_no_identity` |
| 2 | The action context is built only from the person's OWN answer for that request id: ownership checked first, the stored answer must match its sealed digest, only its cited sentences are used, and each cited document is checked again now (Gate 1 + live Gate 2) | Rule 4: another person's request, an unknown id and a refused answer are the same 404; a document revoked since the answer drops out | `::test_someone_elses_request_unknown_and_refused_are_one_404`, `::test_a_revoked_citation_is_dropped_and_all_revoked_is_404`, `::test_restricted_question_equals_a_world_without_it_for_draft_and_propose` |
| 3 | **Draft:** the model chain (Hy3, then DeepSeek) writes the body with **no tools** under the same grounding wall; our code keeps only sentences tagged to the context and adds a fixed greeting and closing. Offline, or when the model output is not grounded, the cited sentences themselves are the body. When a cited sentence gives approved wording ("Tell the customer only that ..."), that wording is used word for word and no model is asked | Filter before the model; the model cannot act | `::test_model_draft_is_grounded_and_sees_only_my_cited_sentences`, `::test_ungrounded_model_draft_falls_back_to_the_template`, `tests/test_act_e2e.py::test_account_manager_draft_is_the_approved_script_only` |
| 4 | **`action.proposed`** (kind, ids, counts, a digest; never the draft text) is sealed BEFORE the draft or proposal is returned; a ledger outage is 503 with no draft | Write-ahead audit | `::test_draft_is_ledgered_first_with_a_digest_and_no_text`, `::test_draft_ledger_down_is_503_and_no_draft` |
| 5 | **Ticket:** our code picks the project from a closed set: the projects the person may create issues in, checked LIVE at the source (Jira `CREATE_ISSUES`), cited projects first. The person may edit the summary and pick another project from that set, then presses **Confirm** or **Cancel** | Human in the loop; the model never chooses a target | `::test_ticket_is_proposed_then_created_only_after_confirm`, `::test_project_is_a_closed_set_and_the_summary_is_validated` |
| 6 | On Confirm: `action.confirmed` is sealed, the create permission is re-checked live, our code calls `POST /rest/api/3/issue` once (reporter = the person's own account), then `action.executed` (or `action.failed`). Cancel writes `action.rejected` and changes nothing | Write-ahead; no privileged choice of identity; one polite message for every failure | `::test_write_denied_at_the_source_is_one_polite_message`, `::test_ledger_down_at_propose_or_confirm_is_503_and_nothing_is_created`, `::test_a_proposal_is_mine_alone`, `tests/test_act_e2e.py::test_follow_up_ticket_is_created_on_the_mock_jira_after_confirm` |

Differences from the plan below, stated plainly:
- **No `tools` for the model.** The plan said Hy3 would be called with `tools` (`draft_reply`,
  `create_ticket`). Built instead: the model only writes draft text; every action is chosen and executed by
  our code from a closed set. This is stricter (the model cannot name a project, a recipient or an action)
  and does not depend on TokenHub's `tools` support.
- **Not over MCP yet.** `ib_propose_action` for WorkBuddy is not built.
- **The outbox package is not imported.** The flow follows its pattern (the model drafts, a named person
  approves, model-free code executes; the executor `connectors/jira.py` imports no model code,
  `::test_the_executor_is_model_free`), but `databridge/packages/outbox` is built around monitor templates
  and e-mail recipients, so it is not reused as a module.
- **The ticket goes to the mock Jira** (real Jira Cloud's request shape); no Jira Cloud sandbox is used.
- **The reply draft is never sent by the Brain.** Copy and "Edit before sending" are local to the page.
- The model-written draft is wired and tested with a test model; like every Hy3 answer it has not run live.

The original plan, kept for reference:

What it does: under an answer, an **Act** menu offers "Draft the customer reply" or "Open a ticket".

| Step | What happens | Rule it keeps |
|---|---|---|
| 1 | The person presses Act and picks an action | Identity from the signed token |
| 2 | Hy3 is called a **second, separate time** with `tools` (`draft_reply`, `create_ticket`) over the **already released passages only** | Filter before the model; the answering call still has no tools |
| 3 | The model returns a **proposal**. Our code, not the model, validates it against a closed schema | The model cannot act |
| 4 | **`action.proposed`** is written to the ledger (who, which answer, which action, digest of the draft) **before** the draft is shown | Write-ahead audit; ledger failure = 503 |
| 5 | The person reads the draft and presses **Confirm** or **Discard** | Human in the loop |
| 6 | `action.confirmed` or `action.rejected` is written; on confirm, our code executes under the asker's token | No privileged service identity in the path |

Design notes:
- **Reuse, not reinvent.** The Walnut engine already has a send gate from before the hackathon:
  `databridge/packages/outbox` (append-only drafts; nothing becomes "sent" without a named human's
  `approved_by`) and a separate, model-free executor, `databridge/apps/dispatcher`
  (`apps/dispatcher/tests/test_dispatcher_is_model_free.py`). The Act button should route confirmed actions
  through that pattern. It is not yet connected to the Internal Brain.
- Ticket target for the demo: a local JSON tracker, or a Jira Cloud sandbox if a key exists [verify].
- The same action is exposed over MCP as `ib_propose_action` next to `ask_internal_brain`, so WorkBuddy can
  trigger it with the same token and the same confirm step.
- If TokenHub's `tools` support for Hy3 is not confirmed, fall back to a JSON-schema prompt without `tools`
  (Tencent's page says tool calling "depends on the model"; third-party hosts show OpenAI-style tools work
  [verify on TokenHub]).
- Tests to write before calling it done: action never proposed without a released answer; ledger write
  precedes any draft in the response; ledger failure gives 503 with no draft; no `user` argument on the tool
  schema; a discarded draft changes nothing; a revoked document between answer and Act denies the action.

## 2. Voice ask (English and Chinese)

**Status: Planned.** Route A is the safer path; Route B is attempted only if it works by the deadline in
the research (Sun 1 Nov, 12:00).

| Route | How | Status |
|---|---|---|
| **A: Tencent ASR + TTS** | Browser mic → Tencent real-time ASR (16k Chinese-English engine) → text shown to the person → normal ask path → first sentences spoken by Tencent TTS ([ASR 2.0 guide](https://intl.cloud.tencent.com/document/product/647/79873), [TTS API](https://intl.cloud.tencent.com/document/api/1154/82360)) | Planned |
| **B: Tencent RTC Conversational AI** | `StartAIConversation` with `LLMConfig` pointing at a small OpenAI-compatible shim in front of `/ib/ask`; the shim treats the `APIKey` as the person's signed token ([RTC integration guide](https://trtc.io/document/75309)) | Planned |

Rules both routes keep:
- **Speech becomes text before any gate.** The ledger stores the text, never audio.
- The person sees the recognised text before it is sent, and can correct it.
- The voice agent's "API key" is the person's own token, so voice cannot widen anyone's access.
- Answers are spoken only after the grounding check passes (decision D-08).

Tests to write before calling it done: the shim rejects a missing or invalid token; the shim never accepts
an identity field; a spoken question and the same typed question produce the same ledger records.

## 3. Smaller items (optional, also not built)

| Item | What | Status |
|---|---|---|
| Explain this answer | A short plain-English narration of the request journey, every number taken from the journey JSON | Planned |
| Faithfulness score | Offline: claims supported by their cited passage, with and without the grounding wall | Planned |
| Model hook for the audit parser | The `llm=` hook in `audit/nl_filter.py` is wired to Hy3 (then DeepSeek) through `audit/nl_llm.py` for questions the rules do not recognise | Wired (hook exists and is tested); not run live |
| Hy4 preview side by side | Same question to `hy3` and `hy4-preview`, both grounded | Only if activated and tested |

## 4. How to talk about it on stage

- Before it is built: "Next, the Brain acts: it drafts the reply, seals the intent in the ledger, and waits
  for you to confirm."
- Never: "It can already act" or "you can talk to it", unless the demo shows it working.
