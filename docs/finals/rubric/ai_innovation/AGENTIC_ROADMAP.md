# Agentic roadmap

**Nothing on this page is built yet.** Each item is marked **Planned** or **In progress** and moves to
**Done** only when its tests pass and it runs in the demo. Do not show or describe any of it as done.

Status on 30 Oct 2026: both items are **Planned**. Neither appears in the priority list of
`docs/finals/PLAN.md` §9 (P0-P2), so they are stretch work after the cut line unless the coordinator says
otherwise. Research: `../research/C_ai_interaction_innovation.md` §1.3, §2.3 C-D.

The same hard rules apply to every agentic feature:
1. The model never sees text the asker cannot open (Gate 1 and Gate 2 first).
2. Identity comes only from the signed token. No tool takes a `user` argument.
3. The ledger is written **before** anything happens.
4. A person confirms before anything changes outside the Brain.

## 1. "Act" button: permission-checked actions

**Status: Planned.**

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
