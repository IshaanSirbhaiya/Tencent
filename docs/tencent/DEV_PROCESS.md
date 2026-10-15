# How this was built

Who built what, and the evidence for each claim. The hackathon requires proof of CodeBuddy or WorkBuddy
use; this page is that proof in writing. Every row points to a transcript, a review file or a commit you
can check.

## The short version

| Part | What | Built by |
|---|---|---|
| Foundation | The Walnut evidence engine: provenance pointers, the sealed audit ledger, the grounding wall, the model harness | The Walnut team, before this event (base commit `078572e`). **Reused unchanged.** |
| The Internal Brain | Connectors, the two permission gates, the answer pipeline, the audit console, the web app, the mock platforms, the evaluation | Claude (Anthropic), directed and reviewed by the team |
| WorkBuddy front door | The two MCP connectors (ask, compliance audit), the token tool, the WorkBuddy setup guide | **CodeBuddy** (sessions CB2, CB3, CB6) |
| Trust-boundary diagram | [diagrams/trust_boundaries.mmd](diagrams/trust_boundaries.mmd) | **CodeBuddy** on Tencent Hunyuan `hy3` (CB1) |
| Model-chain tests | `hy3` → DeepSeek → deterministic fallback, and the grounding wall | **CodeBuddy** (CB4) |
| Security reviews | One by CodeBuddy, eight by Codex, plus final read-throughs by Claude models | **CodeBuddy** on `hy3` (CB5); Codex (tracks 1, 2, 3a, 3b, 4, 5, 6, 7) |
| Second front door, in use | Three WorkBuddy workspaces, each acting as one person | **WorkBuddy** desktop 5.7.6 |

Hunyuan `hy3` ran in CodeBuddy sessions CB1 and CB5, through CodeBuddy's own model access. The Brain
itself is configured for `hy3` through TokenHub, but that account is still waiting on Tencent's
verification, so `hy3` has not answered live inside the Brain yet.

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

Paths in the "Files" column are under `databridge/apps/internal_brain/` unless they start with
`docs/` or `scripts/` (= `databridge/scripts/`).

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

**What Claude changed after CodeBuddy.** CB1's diagram said the public edge rate-limits requests and
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
| Codex, track 5 | The knowledge map and the audit content check | [codex_track5_map_audit.md](reviews/security-r1/codex_track5_map_audit.md) | [SECURITY.md](SECURITY.md) §11 |
| Codex, track 6 | The web app (proxy, compare, map, tour) | [codex_track6_web.md](reviews/security-r1/codex_track6_web.md) | [SECURITY.md](SECURITY.md) §13 |
| Codex, track 7 | Re-check of the track 6 fixes | [codex_track7_fix_verification.md](reviews/security-r1/codex_track7_fix_verification.md) | [SECURITY.md](SECURITY.md) §14 |
| Claude models (Opus, Sonnet, Haiku) | Security, accuracy, first run, UX | Recorded in SECURITY.md | [SECURITY.md](SECURITY.md) §12, §14 |

## How much is new

The Walnut engine is reused as it was. The Internal Brain is added beside it as new apps, fixtures and
an evaluation, so the original system is still 93% of today's code by line count
(`databridge/scripts/measure_original_share.py`; details in [INTERNAL_BRAIN.md](../../INTERNAL_BRAIN.md)).
