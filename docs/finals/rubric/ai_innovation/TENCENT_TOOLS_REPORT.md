# Tencent tools: requirements audit (finals)

Walnut DataBridge, Tencent Cloud AI CAN DO IT Hackathon Singapore 2026, FinTech finalist. Demo Day 3 Nov.
Written 30 Oct 2026. Every row points to a file, a test or a transcript.

**Why this page exists.**
1. The rules say a project must be built on **CodeBuddy or WorkBuddy**, with proof (at least three chat
   screenshots, API call logs, or a written description of the development process), or it is not scored.
2. The finals judges are WorkBuddy/CodeBuddy and Tencent RTC experts. The organisers asked teams to
   "showcase how Tencent Cloud products are used in your solution and include more live demonstrations of
   these products".
3. Until today, all six CodeBuddy sessions (CB1–CB6, 14–15 Oct) predated the finals build, and the
   WorkBuddy connectors did not know the finals features.

## 1. Summary

| Tencent product | Requirement or judge expectation | Status after today | Biggest remaining gap |
|---|---|---|---|
| **CodeBuddy** | Proof of building on CodeBuddy | **Met.** Nine sessions with full transcripts; three (CB7–CB9) on the finals code | CB7 and CB8 ended on CodeBuddy rate limits (429): recorded, work completed |
| **WorkBuddy** | Proof of use; a live Tencent moment on stage | **Met in code and live-tested programmatically**; the GUI retest with the finals tools is still to do | The real WorkBuddy app run (checklist ready) |
| **Hunyuan Hy3 / TokenHub** | "Tencent AI inside the product" | **Partly met.** Hy3 ran inside CodeBuddy (CB1, CB5, CB7, CB9); WorkBuddy's own agent model calls our tools; the Brain is wired for Hy3 on TokenHub but every call returns 402 (code 401006) until the account is activated | TokenHub activation (founder) |
| **Miora** | Tencent tool use (bonus) | **Met.** Cover art from Miora: two sessions for the submission plus a finals session on 30 Oct (the chosen cover is in `submission/06_cover/finals/`) | None |
| **Tencent RTC** | RTC experts on the panel | **Not used.** Our voice dictation is the browser's own speech recognition, not Tencent | See §6 for a one-day add |
| **Lighthouse** | Tencent Cloud hosting (optional) | **Not used live.** A hardened deploy kit exists; the demo runs on the laptop (decision D-03) | None for Demo Day |

## 2. CodeBuddy

### Requirement → evidence

| # | Requirement or expectation | Our evidence | Verdict |
|---|---|---|---|
| C1 | Built on CodeBuddy, with proof | Nine headless CodeBuddy CLI sessions, each with the exact prompt (`docs/tencent/evidence/codebuddy_prompts/`), the full stream-json transcript (`codebuddy_cb1.jsonl` … `codebuddy_cb9.jsonl`) and the runner log | Met |
| C2 | At least three chat screenshots | `submission/01_codebuddy/cb1…cb9_session_overview.png` and `_full.png`, rendered from the transcripts (nothing added or edited) | Met (18 images) |
| C3 | A written development-process description | [`docs/tencent/DEV_PROCESS.md`](../../../tencent/DEV_PROCESS.md): who built what, session table, what went wrong | Met |
| C4 | One-file conversation history for the form | `submission/01_codebuddy/conversation_history_codebuddy_workbuddy_miora.pdf`, rebuilt today with CB1–CB9, the WorkBuddy shots and Miora (`docs/tencent/evidence/build_conversation_pdf.py`) | Met |
| C5 | CodeBuddy used on the **finals** code, not only the first round | CB7 security review of the finals backend; CB8 the WorkBuddy connector upgrade; CB9 the WorkBuddy Walnut skill | Met as of today |
| C6 | CodeBuddy's output is checked, not trusted | CB7: every finding triaged against the code ([codebuddy_finals_review.md](../../../tencent/reviews/codebuddy_finals_review.md)); CB8: reviewed, then driven live over stdio ([workbuddy_live_test.md](workbuddy_live_test.md)) | Met |

### Sessions

| # | Date | Task | Model | Outcome |
|---|---|---|---|---|
| CB1–CB6 | 14–15 Oct | Trust boundaries, both MCP connectors, token CLI, model-chain tests, security review, hardening | `hy3` (CB1, CB5), default | See DEV_PROCESS.md |
| **CB7** | 30 Oct | Security review of the finals backend (graph, doc view, authz, SSE stream, trace, map, Act, tamper demo, stats) | `hy3` | 8 findings; 1 fixed (F7), 1 patch ready (F2), 6 documented or accepted. Last turn hit a 429 after the review file was written |
| **CB8** | 30 Oct | WorkBuddy connector upgrade: `my_answer_receipt`, `what_can_i_open`, `org_activity_summary`, `run_tamper_demo`, request id on answers, content check in `audit_verify` | default | Done. CodeBuddy wrote the code and the 14 tests over three runs of one session (two stopped on 429s, the third finished). Review fixed two test bugs; 40 MCP tests pass; live over stdio 33/33 |
| **CB9** | 30 Oct | The WorkBuddy "Walnut" skill: system prompt and six demo questions (`docs/tencent/workbuddy/WALNUT_SKILL.md`) | `hy3` | Done (17 turns, no rate limit). Review replaced a guessed refusal text with the real one and fixed a typo |

### Gaps and actions

| Gap | Action | Owner |
|---|---|---|
| The finals UI (Next.js) was built without CodeBuddy | Say it plainly if asked: CodeBuddy built and reviewed the Tencent-facing parts (both connectors, the skill) and reviewed the finals backend; Claude built the UI. DEV_PROCESS.md says the same | Presenter |
| CodeBuddy quota (429, code 14003) hits after bursts of turns | Short, single-purpose prompts; resume the same session after a pause (as done for CB5 and CB8) | Coordinator |
| The runner parks `databridge/.env.ib.local` during a session; a demo-stack restart in that window starts the Brain on dev keys (happened once on 30 Oct; no damage, fixed by the next restart) | Never start or restart the stack while a CodeBuddy session runs. Better: `demo_up.sh` should refuse to start when `.env.ib.local` is missing (a one-line guard; not changed here because another agent owns the demo scripts) | Coordinator |
| CB7 finding F2 (unbounded stream threads) is open | The Act work on `app.py` has landed (30 Oct); the ready patch is still not applied (no stream cap in `app.py` at `1076517`). Apply it with its test (both in the triage file) | Coordinator |

## 3. WorkBuddy

### Requirement → evidence

| # | Requirement or expectation | Our evidence | Verdict |
|---|---|---|---|
| W1 | WorkBuddy used for real, with proof | Three connectors (`ib-as-jdoe`, `ib-as-ken`, `ib-compliance`) in WorkBuddy desktop 5.7.6; screenshots in `submission/02_workbuddy/` (15 Oct) | Met |
| W2 | A **live** Tencent product moment on stage | The run sheet's WorkBuddy beat at 2:35 (`docs/finals/rubric/demo_story/RUN_SHEET.md`): Jordan asks from WorkBuddy, then the `workbuddy-mcp` row in the audit view | Planned; rehearsal pending |
| W3 | The connectors cover the finals features | CB8 added the receipt, "what can I open", the org summary and the tamper demo; `audit_verify` now shows the stored-answer check | Met in code |
| W4 | Identity cannot be chosen by the agent | No tool takes a user, persona or name; the token is the person. Checked live: every tool's schema, and Ken asking for Jordan's receipt gets the same sentence as an unknown id; an employee token is refused (403) on every audit tool | Met ([workbuddy_live_test.md](workbuddy_live_test.md)) |
| W5 | The connectors work against the live Brain | Every tool of both servers called over stdio with the official `mcp` client, as WorkBuddy runs them | **Met: 33 of 33 checks** (Jordan cited from Slack, Jira and Confluence; Ken's Velquarry and Bayfront replies identical; Maya's summary shows `workbuddy-mcp`; tamper demo on a copy, real log not modified, chain still verifies; the ledger row has channel `workbuddy-mcp`) |
| W6 | The agent uses the tools well | The Walnut skill (CB9) tells WorkBuddy's agent to pass the question word for word, relay citations exactly, treat the scope note as the whole answer, and never name a person | Written; to be tried in the GUI retest |
| W7 | Screenshots of the finals tools in the real app | [WORKBUDDY_GUI_TEST.md](WORKBUDDY_GUI_TEST.md): configs, six prompts, expected results, eight screenshots | **Gap: not run yet** |

### Gaps and actions

| Gap | Action | Owner |
|---|---|---|
| No WorkBuddy GUI screenshots of the finals tools | Run [WORKBUDDY_GUI_TEST.md](WORKBUDDY_GUI_TEST.md) on the stage laptop (about 20 minutes) and save into `submission/02_workbuddy/finals/` | Coordinator / founder |
| Clip C (backup video) is an in-app stand-in, not WorkBuddy | Record the real WorkBuddy beat during the GUI retest and replace clip C (`BACKUP_PLAN.md`) | Coordinator |
| WorkBuddy's agent may reword the headline question; the short form ("DB migration") cites Jira only, the long form cites Slack, Jira and Confluence | The skill says "word for word"; the run sheet already uses the long form; check it in the GUI retest | Coordinator |

## 4. Hunyuan Hy3 and TokenHub

| # | Expectation | Evidence | Verdict |
|---|---|---|---|
| H1 | Tencent's model in the product | `llm.py` `DEFAULT_CHAIN = ("hy3", "deepseek-v4.1-flash")`, the TokenHub Singapore endpoint, tests `test_llm_chain.py`, `test_model_config.py`; `scripts/hy3_check.py`; `demo_up.sh` switches Hy3 on when it answers | **Wired, not live**: every call returns 402 (code 401006) until TokenHub is activated (STATUS.md) |
| H2 | Hy3 used during development | CodeBuddy sessions CB1, CB5, CB7 and CB9 ran on `hy3` (the `model` field in each transcript) | Met |
| H3 | Honest labelling | Every answer names its model; today the live Brain's answers say `deterministic/extractive-v1` | Met |

Action: the founder activates Hy3 in the TokenHub console (MORNING_BRIEF.md steps); then run
`scripts/hy3_check.py`, restart with `demo_up.sh`, and re-run the live WorkBuddy test so the receipt shows
`hy3`.

## 5. Miora

| Expectation | Evidence | Verdict |
|---|---|---|
| Tencent design tool used | `submission/06_cover/miora_session1.png`, `miora_session2.png`, `cover_master_miora.png`; pages in the conversation-history PDF | Met |
| A finals cover | Prompt in `docs/finals/COVER_MIORA_PROMPT.md`; session `submission/06_cover/finals/miora_session_finals.png`; outputs and sizes in `submission/06_cover/finals/` | Met (30 Oct) |

## 6. Tencent RTC (not used)

We do not use Tencent RTC. The web app's voice dictation (commit `1bc6b4c`) is the browser's own speech
recognition; it is not a Tencent product and we do not claim it is. With RTC experts on the panel, say
this plainly if asked, and name the plan below.

**A one-day add (Route A, the safer one):** Tencent Cloud real-time speech recognition (ASR) in place of the
browser engine for the Ask composer, English and 简体中文.
1. A small `/ib/asr/token` route on the Brain that returns a short-lived, signed ASR request for the
   signed-in person (no Tencent keys in the browser).
2. The composer streams microphone audio to Tencent ASR over its WebSocket API and shows the recognised
   text in the box; the person reads it and presses Send (it never auto-sends, as today).
3. Text then goes through the same two gates and ledger; no audio is stored.
4. Tests: the token route refuses a missing session; the recognised text is never sent without a click.

**Route B (1.5–2 days, higher risk):** Tencent RTC Conversational AI (`StartAIConversation`) with its LLM
pointed at a small OpenAI-compatible shim in front of `/ib/ask` that treats the API key as the person's
signed token (`AGENTIC_ROADMAP.md` §B). It needs a live network on stage and a TTS key; with three days to
Demo Day we would not put it on the demo path.

## 7. Lighthouse

A hardened one-command deploy kit (`deploy/lighthouse/`: Caddy HTTPS, passcode-gated demo, only ports 80
and 443). No live instance: the demo runs on the laptop (decision D-03). Nothing to do for Demo Day.

## 8. What to say on stage (one breath each)

- **CodeBuddy:** "CodeBuddy built both WorkBuddy connectors, upgraded them for the finals, and reviewed our
  finals backend on Hunyuan Hy3; we fixed what it found and kept every transcript."
- **WorkBuddy:** "WorkBuddy's agent calls our tool with Jordan's signed token, not a name it chose. Same
  gates, same ledger, channel 'WorkBuddy'."
- **RTC (only if asked):** "Not in today's build. The one-day add is Tencent ASR in the composer; the text
  still goes through both gates."
