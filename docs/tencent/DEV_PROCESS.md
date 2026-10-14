# How this was built

Who did what, with the evidence for each claim. The hackathon requires proof of CodeBuddy or
WorkBuddy use; this page is that proof in writing, and every row links to a log or a commit you
can check.

## The short version

| | What | Built by |
|---|---|---|
| Foundation | The Walnut evidence engine: provenance pointers, the sealed audit ledger, the grounding wall, the model harness | Walnut team, before this event. **Reused unchanged.** |
| The Internal Brain | Connectors, the two permission gates, the answer pipeline, the audit console, the web app, the evaluation | Claude (Anthropic), directed and reviewed by the team |
| WorkBuddy front door | The two MCP connectors, the token tool, the WorkBuddy setup guide | **CodeBuddy** (sessions CB2, CB3, CB6) |
| Trust-boundary diagram | `docs/tencent/diagrams/trust_boundaries.mmd` | **CodeBuddy** on Tencent Hunyuan `hy3` (CB1) |
| Model-chain tests | hy3 → DeepSeek → deterministic fallback, grounding wall | **CodeBuddy** (CB4) |
| Security reviews | Seven, every finding dispositioned | **CodeBuddy** on `hy3` (CB5) and six Codex passes (tracks 1, 2, 3a, 3b, 4, 5); plus a four-model plan review |

## CodeBuddy sessions

Each session ran in the CodeBuddy CLI against this repository, one at a time. CodeBuddy got a fixed
set of working rules (stay inside the repo, no git commands, touch only the named files, never open
the secrets file) followed by the task. The full machine-readable transcript of every session is
kept, and every result was checked by running its tests before it was committed under a
`codebuddy:` prefix.

| # | Task | Model | Files CodeBuddy wrote | Transcript | Checked by | Commit |
|---|---|---|---|---|---|---|
| CB1 | Trust-boundary diagram, TB1–TB5 | `hy3` | `docs/tencent/diagrams/trust_boundaries.mmd`, `README.md` | [codebuddy_cb1.jsonl](evidence/codebuddy_cb1.jsonl) | Reviewed against the code; two details corrected (see below) | `codebuddy: CB1` |
| CB2 | WorkBuddy MCP servers + tests | default | `databridge_ib/mcp_server.py`, `audit_mcp_server.py`, `tests/test_mcp_server.py` | [codebuddy_cb2.jsonl](evidence/codebuddy_cb2.jsonl) | Tests pass; then driven end to end over stdio against the running Brain | `codebuddy: CB2` |
| CB3 | Token CLI, WorkBuddy guide, per-persona configs | default | `scripts/ib_mint_token.py`, `tests/test_mint_token_cli.py`, `docs/tencent/workbuddy/` | [codebuddy_cb3.jsonl](evidence/codebuddy_cb3.jsonl) | Tests pass | `codebuddy: CB3` |
| CB4 | Model-chain tests | default | `tests/test_llm_chain.py` | [codebuddy_cb4.jsonl](evidence/codebuddy_cb4.jsonl) | Tests pass; checked that they test fallback order, the extractive path and the grounding wall | `codebuddy: CB4` |
| CB5 | Security review (read-only) | `hy3` | `docs/tencent/reviews/codebuddy_security_review.md` | [codebuddy_cb5.jsonl](evidence/codebuddy_cb5.jsonl) | Every finding checked against the code; dispositions in `SECURITY.md` | with the fixes |
| CB6 | Harden its own connectors against the Codex review | default | the CB2/CB3 files | [codebuddy_cb6.jsonl](evidence/codebuddy_cb6.jsonl) | Tests pass | `codebuddy: CB6` |

The exact prompts are in [evidence/codebuddy_prompts/](evidence/codebuddy_prompts/), and the runner
is [evidence/run_codebuddy.sh](evidence/run_codebuddy.sh).

**What went wrong, kept on record.** CB5 lost its network connection twice (the machine could not
resolve `www.codebuddy.ai`). Both broken transcripts are kept
(`codebuddy_cb5_attempt*_network_drop.jsonl`); the review was finished by resuming the same
CodeBuddy session.

**A secrets slip, and what changed because of it.** CB3's task was a CLI that reads the local
secrets file, and CodeBuddy opened that file while testing it, even though its rules said not to.
Its transcript therefore held the local signing keys and the model API key. This was caught by a
secret scan before anything else used the transcript. The values are redacted in the kept
transcripts (`<REDACTED:…>`), the commit carrying them was replaced on the (private) branch, the
signing keys were rotated, and the model key was revoked and reissued. Since then the CodeBuddy
runner moves the secrets file outside the repository for the whole session
([run_codebuddy.sh](evidence/run_codebuddy.sh)), so a session cannot read it even by mistake.

**What Claude changed after CodeBuddy.** CB1's diagram said the public edge rate-limits requests
and exposes `/mcp`; neither is true, so both labels were corrected to match
`deploy/lighthouse/Caddyfile`, and the webhook signature formula was updated to the current one.
The changes are in the CB1 commit message.

## What CodeBuddy's work caught

- **CB5 raised seven findings.** Three are fixed with tests: a deployed build could run demo sign-in
  without a passcode; an edit to a stored answer that nobody revealed went unnoticed by "verify audit
  chain"; a browser user could label their own question as coming from WorkBuddy. Two are documented
  as residual risks and two accepted (SECURITY.md §10).
- **Wiring CB2's connector to the real app exposed a bug in our audit console**: every audit call
  through the real app returned "not authenticated", because the audit routes read the user's id
  from the wrong field. Unit tests had missed it. Fixed, with a test that goes through the real
  app (`tests/test_audit_routes_real_principal.py`).

## WorkBuddy

WorkBuddy (desktop 5.7.6) is the Brain's second front door. Three workspaces, each acting as one
person through its own short-lived token: an engineer (`jdoe`), a contractor (`ken.ext`), and a
compliance officer (`maya.compliance`) who uses WorkBuddy as an audit assistant.
`databridge/scripts/workbuddy_setup.py` writes them; the guide is
[workbuddy/README.md](workbuddy/README.md).

## Reviews

| Review | Scope | Output |
|---|---|---|
| Four-model plan review | The build plan | [reviews/plan-v1/](reviews/plan-v1/), dispositions in [PLAN_REVIEW_DISPOSITION.md](PLAN_REVIEW_DISPOSITION.md) |
| Codex, tracks 1–3 | Core, audit, connectors and masking | [reviews/security-r1/](reviews/security-r1/) |
| Codex, track 4 | CodeBuddy's connectors, the token tool, setup scripts | [codex_track4_mcp_tokens.md](reviews/security-r1/codex_track4_mcp_tokens.md) |
| Codex, track 5 | The knowledge map and the audit content check | [codex_track5_map_audit.md](reviews/security-r1/codex_track5_map_audit.md) |
| CodeBuddy (`hy3`) | App, auth, pipeline, gates, audit | [codebuddy_security_review.md](reviews/codebuddy_security_review.md) |

Every finding has a disposition (fixed with a test, or documented as a limit) in
[SECURITY.md](SECURITY.md).

## How much is new

The Walnut engine is reused as it was. The Internal Brain is added beside it as new apps, fixtures
and an evaluation, so the original system remains the large majority of the code (measured share
in [INTERNAL_BRAIN.md](../../INTERNAL_BRAIN.md)).
