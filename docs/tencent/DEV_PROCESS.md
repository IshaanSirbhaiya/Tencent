# How this was built

Who built what, and the evidence for each claim. The hackathon requires proof of CodeBuddy or WorkBuddy
use; this page is that proof in writing. Every row points to a transcript, a review file or a commit you
can check.

## Build tools: the one statement

Which AI tools built, reviewed and tested this project. The Tencent tools come first.

| Tool | Role | Evidence |
|---|---|---|
| **CodeBuddy** (Tencent) | Nine recorded sessions (CB1–CB9), each with its prompt and full transcript. They drew the trust boundaries, built both WorkBuddy connectors and the token tool, wrote the model-chain tests and the WorkBuddy skill, and ran two security reviews (CB5, CB7) that found real bugs, which were fixed with tests | [CodeBuddy sessions](#codebuddy-sessions), `docs/tencent/evidence/`, `submission/01_codebuddy/` |
| **WorkBuddy** (Tencent) | The Brain's second front door through MCP connectors (ask as a person; compliance audit). Driven live over stdio: 33 of 33 checks passed. The retest inside the WorkBuddy app is still to do | [workbuddy_live_test.md](../finals/rubric/ai_innovation/workbuddy_live_test.md), [workbuddy/](workbuddy/README.md) |
| **Miora** (Tencent) | The cover art, from our written brief; we added the title | `submission/06_cover/` |
| **Hunyuan Hy3 / TokenHub** (Tencent) | Hy3 ran inside CodeBuddy sessions CB1, CB5, CB7 and CB9. In the Brain it is wired in through TokenHub and tested with a capturing test model, not run live (activation pending) | `llm.py`, `tests/test_llm_chain.py` |

Other assistants used in development, each named once: Claude (Anthropic) was the coding assistant for the
Internal Brain, the web app and the documents, directed and reviewed by the team; Codex was a read-only
security and code reviewer (tracks 1 to 7 and the later rounds); Claude Opus, Sonnet and Haiku and Fable 5.1
served as independent review seats (plans, security, UX, claims, pitch). Every review is written down with its
disposition ([SECURITY.md](SECURITY.md), `docs/finals/reviews/`). No human third party has reviewed it.

## The short version

| Part | What | Built by |
|---|---|---|
| Foundation | The Walnut evidence engine: provenance pointers, the sealed audit ledger, the grounding wall, the model harness | The Walnut team. The Internal Brain imports parts of it unchanged: `core` (ledger, source pointers, value forms), `harness`, `convert` (file readers) and the gateway's grounding wall. It keeps its own retrieval index (`databridge_ib/index.py`) and does not use `packages/retrieval`. |
| The Internal Brain | Connectors, the two permission gates, the answer pipeline, the audit console, the web app, the mock platforms, the evaluation | Claude (Anthropic), directed and reviewed by the team |
| WorkBuddy front door | The two MCP connectors (ask, compliance audit), the token tool, the WorkBuddy setup guide | **CodeBuddy** (sessions CB2, CB3, CB6) |
| WorkBuddy finals upgrade | Four new connector tools (answer receipt, what can I open, org activity summary, tamper demo) and their tests | **CodeBuddy** (CB8), reviewed and corrected by Claude |
| WorkBuddy "Walnut" skill | The instructions and six demo questions for WorkBuddy's agent | **CodeBuddy** on `hy3` (CB9) |
| Trust-boundary diagram | [diagrams/trust_boundaries.mmd](diagrams/trust_boundaries.mmd) | **CodeBuddy** on Tencent Hunyuan `hy3` (CB1) |
| Model-chain tests | `hy3` → DeepSeek → deterministic fallback, and the grounding wall | **CodeBuddy** (CB4) |
| Security reviews | Two by CodeBuddy, eight by Codex, plus final read-throughs by Claude models | **CodeBuddy** on `hy3` (CB5; CB7 on the finals backend); Codex (tracks 1, 2, 3a, 3b, 4, 5, 6, 7) |
| Second front door, in use | Three WorkBuddy workspaces, each acting as one person | **WorkBuddy** desktop 5.7.6 |

Hunyuan `hy3` ran in CodeBuddy sessions CB1, CB5, CB7 and CB9, through CodeBuddy's own model access. The Brain
itself is configured for `hy3` through TokenHub, but that account is still waiting on Tencent's
verification, so `hy3` has not answered live inside the Brain yet.

## Tencent tools at a glance

| Tencent tool | How we used it | Evidence |
|---|---|---|
| **CodeBuddy** | Nine build sessions. First round (CB1–CB6): drew the trust boundaries, built both WorkBuddy connectors and the token tool, wrote the model-chain tests, ran a security review, then fixed its own code after a Codex review. Finals (CB7–CB9): reviewed the finals backend, upgraded the connectors for the finals features, wrote the WorkBuddy skill | [DEV_PROCESS.md](#codebuddy-sessions), transcripts in `docs/tencent/evidence/`, screenshots in `submission/01_codebuddy/` |
| **WorkBuddy** | The Brain's second front door: three connectors (ask as Jordan, ask as Ken, compliance audit), each carrying its own signed token | [WorkBuddy guide](workbuddy/README.md), screenshots in `submission/02_workbuddy/` |
| **Hunyuan Hy3** | The model behind CodeBuddy sessions CB1, CB5, CB7 and CB9; first in the Brain's answer chain | CodeBuddy transcripts; `databridge/apps/internal_brain/databridge_ib/llm.py` |
| **TokenHub** | The Brain's model gateway (Hy3, then DeepSeek as backup). Wired in and tested; live calls wait on Tencent's account verification | `llm.py`, `tests/test_llm_chain.py`, `tests/test_model_config.py` |
| **Lighthouse** | A hardened one-command deploy kit (HTTPS, passcode-gated demo, only ports 80/443 open); no live instance because Lighthouse was unavailable during the event | [deploy/lighthouse/](../../deploy/lighthouse/README.md) |
| **Miora** | Tencent's AI design agent made the cover art from our written brief (two rounds of prompts, four variations each); we added the title | `submission/06_cover/`: the Miora screenshots, its raw image and the final cover |

## CodeBuddy sessions

1. Each session ran in the CodeBuddy CLI against this repository, one at a time.
2. CodeBuddy got a fixed set of working rules ([_rules.md](evidence/codebuddy_prompts/_rules.md): stay
   inside the repo, no git commands, touch only the named files, never open the secrets file), followed
   by the task.
3. The full machine-readable transcript of every session is kept.
4. Every result was checked by running its tests before it was committed under a `codebuddy:` prefix.

| # | Task | Model | Files CodeBuddy wrote | Transcript | Checked by | Commit |
|---|---|---|---|---|---|---|
| CB1 | Trust-boundary diagram, TB1–TB5 | `hy3` | `docs/tencent/diagrams/trust_boundaries.mmd`, `README.md` | [codebuddy_cb1.jsonl](evidence/codebuddy_cb1.jsonl) | Reviewed against the code; two details corrected (see below) | `c50b928` |
| CB2 | WorkBuddy MCP servers + tests | CodeBuddy default | `databridge_ib/mcp_server.py`, `audit_mcp_server.py`, `tests/test_mcp_server.py` | [codebuddy_cb2.jsonl](evidence/codebuddy_cb2.jsonl) | Tests pass; then driven end to end over stdio against the running Brain | `7848d46` |
| CB3 | Token CLI, WorkBuddy guide, per-person configs | CodeBuddy default | `scripts/ib_mint_token.py`, `tests/test_mint_token_cli.py`, `docs/tencent/workbuddy/` | [codebuddy_cb3.jsonl](evidence/codebuddy_cb3.jsonl) | Tests pass | `f549802` |
| CB4 | Model-chain tests | CodeBuddy default | `tests/test_llm_chain.py` | [codebuddy_cb4.jsonl](evidence/codebuddy_cb4.jsonl) | Tests pass; checked that they test fallback order, the extractive path and the grounding wall | `d37bad2` |
| CB5 | Security review (read-only) | `hy3` | `docs/tencent/reviews/codebuddy_security_review.md` | [codebuddy_cb5.jsonl](evidence/codebuddy_cb5.jsonl) | Every finding checked against the code; dispositions in [SECURITY.md](SECURITY.md) §10 | `9ab4f41`, with the fixes |
| CB6 | Harden its own connectors against Codex track 4 | CodeBuddy default | the CB2 and CB3 files | [codebuddy_cb6.jsonl](evidence/codebuddy_cb6.jsonl) | Tests pass | `74d53f8` |
| CB7 | **Finals:** security review of the finals backend (graph, doc view, authz, SSE stream, trace, map, Act, tamper demo, stats), read-only | `hy3` | `docs/tencent/reviews/codebuddy_cb7_findings.md` | [codebuddy_cb7.jsonl](evidence/codebuddy_cb7.jsonl) | Every finding checked against the code; triage in [codebuddy_finals_review.md](reviews/codebuddy_finals_review.md): F7 fixed with tests, F2 patch ready, six documented or accepted | (30 Oct, not yet committed) |
| CB8 | **Finals:** WorkBuddy connector upgrade: `my_answer_receipt`, `what_can_i_open`, `org_activity_summary`, `run_tamper_demo`, the request id on answers, the content check in `audit_verify` | CodeBuddy default | `databridge_ib/mcp_server.py`, `audit_mcp_server.py`, `tests/test_mcp_finals_tools.py` | [codebuddy_cb8.jsonl](evidence/codebuddy_cb8.jsonl), resumed in [codebuddy_cb8_part3.jsonl](evidence/codebuddy_cb8_part3.jsonl) | Reviewed; two test bugs fixed (below); 40 MCP tests pass; every tool driven live over stdio, 33/33 checks ([workbuddy_live_test.md](../finals/rubric/ai_innovation/workbuddy_live_test.md)) | (30 Oct, not yet committed) |
| CB9 | **Finals:** the WorkBuddy "Walnut" skill (agent instructions and six demo questions) | `hy3` | `docs/tencent/workbuddy/WALNUT_SKILL.md` | [codebuddy_cb9.jsonl](evidence/codebuddy_cb9.jsonl) | Read against the tools and the real refusal text; two corrections (below) | (30 Oct, not yet committed) |

Paths in the "Files" column are under `databridge/apps/internal_brain/` unless they start with
`docs/` or `scripts/` (= `databridge/scripts/`).

CB7–CB9 ran on 30 Oct against the finals code, with the same runner, rules file and secrets parking as
CB1–CB6. The finals web app itself (the Next.js screens) was built by Claude, not CodeBuddy; CodeBuddy's
finals work is the Tencent-facing part (the connectors and the skill) and the review of the finals backend.

More evidence for the same sessions:
- The exact prompts: [evidence/codebuddy_prompts/](evidence/codebuddy_prompts/).
- The runner: [evidence/run_codebuddy.sh](evidence/run_codebuddy.sh), with its run logs
  [run_cb2-5.log](evidence/run_cb2-5.log), [run_cb5_retry.log](evidence/run_cb5_retry.log) and
  [run_cb6.log](evidence/run_cb6.log). Each session's stderr is kept next to its transcript.
- The screenshots in `submission/01_codebuddy/` are rendered from these transcripts by
  [render_transcripts.py](evidence/render_transcripts.py) and
  [shoot_sessions.cjs](evidence/shoot_sessions.cjs), so they show exactly what the logs hold.

### What went wrong, kept on record

**Network drops in CB5.** CB5 lost its network connection twice (the machine could not resolve
`www.codebuddy.ai`). Both broken transcripts are kept
([attempt 1](evidence/codebuddy_cb5_attempt1_network_drop.jsonl),
[attempt 2](evidence/codebuddy_cb5_attempt2_network_drop.jsonl)). The review was finished by resuming
the same CodeBuddy session.

**A secrets slip, and what changed because of it.**
1. CB3's task was a CLI that reads the local secrets file. CodeBuddy opened that file while testing,
   even though its rules said not to, so its transcript held the local signing keys and the model API
   key.
2. A secret scan caught this before anything else used the transcript.
3. The values are redacted in the kept transcript (`<REDACTED:…>`), and the commit carrying them was
   replaced while the branch was still private.
4. The signing keys were rotated, and the model key was revoked and reissued.
5. Since then the runner moves the secrets file outside the repository for the whole session
   ([run_codebuddy.sh](evidence/run_codebuddy.sh)), so a session cannot read it even by mistake.

**Rate limits in the finals sessions (30 Oct).** CodeBuddy answered HTTP 429 ("too many requests",
code 14003, category "quota") after bursts of turns:
1. CB7 wrote its whole review, then its last turn (the summary reply) hit the limit.
2. CB8's first start was refused at once ([attempt 1](evidence/codebuddy_cb8_attempt1_429.jsonl)). The
   retry made 100 turns and changed both servers, then stopped before the tests
   ([codebuddy_cb8.jsonl](evidence/codebuddy_cb8.jsonl)).
3. We resumed the same CodeBuddy session with a short prompt
   ([cb8_resume.md](evidence/codebuddy_prompts/cb8_resume.md)). The first resume stopped again after a
   few turns ([part 2](evidence/codebuddy_cb8_part2.jsonl)); the second, after a pause, wrote and ran the
   tests ([part 3](evidence/codebuddy_cb8_part3.jsonl), [log](evidence/run_cb8_resume.log)).
4. CB9 ran without a limit.

**Parking the secrets file has a side effect.** While a session runs, `databridge/.env.ib.local` is moved
out of the repository. On 30 Oct a demo-stack restart by another process happened inside that window, so
the Brain started on its built-in development keys: real tokens were refused and Verify reported a broken
chain. Nothing was written under the wrong key (appends failed closed), the ledger verified intact with the
real key, and the next restart restored it. Rule since then: do not start or restart the demo stack while
a CodeBuddy session is running.

**What Claude changed after CodeBuddy (finals).**
- CB8's tamper-demo test set `ledger._external`, a field that does not exist, so the external head sink
  was never attached; changed to `ledger.external`. The test now also checks that every scenario behaved
  as expected, and the receipt test checks the `workbuddy-mcp` channel. The receipt gained a "Channel"
  line (the trace already returns it).
- CB9's skill guessed the refusal text as "you are not permitted to see...", which would itself hint that
  something is hidden. Replaced with the Brain's real constant reply (`REFUSAL_TEXT` in `models.py`);
  one typo fixed.
- CB7's findings were each checked against the code before any change (triage file above).

**What Claude changed after CodeBuddy (first round).** CB1's diagram said the public edge rate-limits requests and
exposes `/mcp`. Neither is true, so both labels were corrected to match `deploy/lighthouse/Caddyfile`,
and the webhook signature formula was updated to the current one. The CB1 commit message (`c50b928`)
lists the changes.

## What CodeBuddy's work caught

- **CB5 raised seven findings.** Three are fixed with tests:
  1. a deployed build could run demo sign-in without a passcode;
  2. an edit to a stored answer that nobody had revealed went unnoticed by "Verify audit chain";
  3. a browser user could label their own question as coming from WorkBuddy.

  Two are documented as residual risks and two are accepted ([SECURITY.md](SECURITY.md) §10).
- **Wiring CB2's connector to the real app exposed a bug in our audit console.** Every audit call
  through the real app returned "not authenticated", because the audit routes read the user's id from
  the wrong field. Unit tests had missed it. It is fixed, with a test that goes through the real app
  (`databridge/apps/internal_brain/tests/test_audit_routes_real_principal.py`).

## WorkBuddy

WorkBuddy (desktop 5.7.6) is the Brain's second front door. Three workspaces each act as one person,
through that person's own short-lived token:
- an engineer (`jdoe`);
- a contractor (`ken.ext`);
- a compliance officer (`maya.compliance`), who uses WorkBuddy as an audit assistant.

`databridge/scripts/workbuddy_setup.py` writes the three configurations; the guide is
[workbuddy/README.md](workbuddy/README.md). Screenshots of all three in use are in `submission/02_workbuddy/`.

**Finals.** The connectors gained four tools (CB8), and WorkBuddy's agent gets the Walnut skill (CB9).
Every tool was driven live over stdio against the running Brain with the official `mcp` client, exactly as
WorkBuddy starts them: 33 of 33 checks passed
([workbuddy_live_test.md](../finals/rubric/ai_innovation/workbuddy_live_test.md), script
[workbuddy_live_test.py](evidence/workbuddy_live_test.py)). The retest inside the WorkBuddy app itself is a
checklist for the team ([WORKBUDDY_GUI_TEST.md](../finals/rubric/ai_innovation/WORKBUDDY_GUI_TEST.md)).

## Codex and the other reviews

Codex was used as a read-only reviewer: each run got a bundle of files on stdin and no tools, and its
output was checked before anything was changed.

| Review | Scope | Output | Disposition |
|---|---|---|---|
| Codex, track 1 | Gates, pipeline, ledger, identity | [codex_track1.md](reviews/security-r1/codex_track1.md) | [SECURITY.md](SECURITY.md) §7 (fixes in `d69ab97`; a written disposition of all 35 findings is still to do) |
| Codex, track 2 | The audit log | [codex_track2.md](reviews/security-r1/codex_track2.md) | [SECURITY.md](SECURITY.md) §3 |
| Codex, tracks 3a and 3b | Connectors and masking | [codex_track3a_connectors.md](reviews/security-r1/codex_track3a_connectors.md), [codex_track3b_connectors_masking.md](reviews/security-r1/codex_track3b_connectors_masking.md) | [SECURITY.md](SECURITY.md) §9 |
| Codex, track 4 | CodeBuddy's connectors, the token tool, setup scripts | [codex_track4_mcp_tokens.md](reviews/security-r1/codex_track4_mcp_tokens.md) | [SECURITY.md](SECURITY.md) §10 |
| CodeBuddy (`hy3`) | App, auth, pipeline, gates, audit | [codebuddy_security_review.md](reviews/codebuddy_security_review.md) | [SECURITY.md](SECURITY.md) §10 |
| CodeBuddy (`hy3`), CB7 | The finals backend: graph, doc view, authz, SSE stream, trace, map, Act, tamper demo, stats | [codebuddy_cb7_findings.md](reviews/codebuddy_cb7_findings.md) | [codebuddy_finals_review.md](reviews/codebuddy_finals_review.md) |
| Codex, track 5 | The knowledge map and the audit content check | [codex_track5_map_audit.md](reviews/security-r1/codex_track5_map_audit.md) | [SECURITY.md](SECURITY.md) §11 |
| Codex, track 6 | The web app (proxy, compare, map, tour) | [codex_track6_web.md](reviews/security-r1/codex_track6_web.md) | [SECURITY.md](SECURITY.md) §13 |
| Codex, track 7 | Re-check of the track 6 fixes | [codex_track7_fix_verification.md](reviews/security-r1/codex_track7_fix_verification.md) | [SECURITY.md](SECURITY.md) §14 |
| Claude models (Opus, Sonnet, Haiku) | Security, accuracy, first run, UX | Recorded in SECURITY.md | [SECURITY.md](SECURITY.md) §12, §14 |

