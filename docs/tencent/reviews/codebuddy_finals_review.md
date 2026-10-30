# CodeBuddy review of the finals backend (CB7): triage

CodeBuddy session **CB7** (Tencent Hunyuan `hy3`, 30 Oct 2026, read-only) reviewed the code added for the
Demo Day build: the knowledge graph (`graph.py`), the document view (`docview.py`), the shared
authorisation helper (`authz.py`), `POST /ib/ask/stream`, `GET /ib/trace`, `GET /ib/map`, the Act routes
(`actions.py`, `/ib/act/*`), the tamper demo (`audit/tamper.py`) and `/audit/stats`.

- Prompt: [`evidence/codebuddy_prompts/cb7.md`](../evidence/codebuddy_prompts/cb7.md)
- Transcript: [`evidence/codebuddy_cb7.jsonl`](../evidence/codebuddy_cb7.jsonl) (42 turns, 8 min 23 s)
- CodeBuddy's own review, unedited: [`codebuddy_cb7_findings.md`](codebuddy_cb7_findings.md)
- Runner log: [`evidence/run_cb7.log`](../evidence/run_cb7.log)

**On the record:** CodeBuddy wrote the full review file, then its last turn (the short summary reply)
hit a CodeBuddy rate limit (HTTP 429, code 14003, "quota"). The transcript's result line therefore says
`error_during_execution`. The review file itself is complete; nothing was lost.

Every finding below was checked against the code by Claude before any change. Paths are under
`databridge/apps/internal_brain/databridge_ib/`.

## Summary

| # | CodeBuddy's severity | Finding | Our verdict | Disposition |
|---|---|---|---|---|
| F1 | Critical | `/auth/demo-login` mints a token for any persona when no passcode is set | **Already mitigated; severity overstated** | No change. See below |
| F2 | High | `/ib/ask/stream` starts one thread per request with no cap | **Real (availability, authenticated)**; we rate it medium | **Open: patch ready, not applied** (file under active edit by the Act work) |
| F3 | Medium | Timing: an id in the asker's stored readable set gets a live check, an unknown id does not | **Real; already a documented limit** | Documented residual (SECURITY.md §5 "Timing: not controlled") |
| F4 | Medium | One overview graph call can make up to 1,500 live checks; no per-user rate limit | **Real; bounded**; we rate it low | Documented residual (SECURITY.md §15.1) |
| F5 | Medium | `/audit/reveal` shows answer text to a compliance officer whatever the officer's own document access | **By design** | Accepted (needs the compliance role, a written reason, and is ledgered) |
| F6 | Medium/Low | The replay header can write backdated, replay-labelled records | **By design, fenced** | Accepted (SECURITY.md §15.5) |
| F7 | Low | `GET /ib/doc/{doc_id}` has no length cap | **Real, small** | **Fixed**, with tests |
| F8 | Low | The tamper demo is heavy work a compliance officer can repeat | **Real, bounded** | Accepted (one run at a time, 20 s budget, compliance only) |

Result: 1 fixed, 1 open with a ready patch, 6 documented or accepted. No finding shows a way to read
content a person cannot open, to act as someone else, or to change the real audit log.

## Each finding

### F1. Demo sign-in without a passcode (CodeBuddy: critical; ours: already mitigated)

The code is as CodeBuddy describes (`app.py` `demo_login`): with `IB_DEMO_LOGIN=1` and no
`IB_DEMO_PASSCODE`, anyone who can reach the Brain can pick a persona. But:

1. A **deployed** build refuses to start with demo sign-in on and a passcode shorter than 8 characters
   (`config.py`, the check added for CodeBuddy's own CB5 finding 1;
   `tests/test_config_deployable.py`).
2. The Brain listens on `127.0.0.1` unless `IB_HOST` says otherwise (`bootstrap.py`), and not deployed it
   accepts only loopback `Host` headers (`config.trusted_hosts`, SECURITY.md §15.10), so on the demo
   laptop only the laptop itself can reach it.
3. Demo sign-in is the stand-in for SSO over synthetic data only; picking any persona is its purpose on
   the stage laptop.

No change. The finding confirms the CB5 fix was the right one.

### F2. Unbounded stream threads (CodeBuddy: high; ours: medium, open)

Confirmed. `ask_stream` starts a daemon thread per call (`threading.Thread(target=work, ...)`) and the
thread keeps running after a disconnect (deliberate: the ledger must record the whole request). `/ib/ask`
runs in the server's bounded thread pool; the stream's pipeline thread does not. An authenticated person
can open many streams at once and tie up threads, live source checks and model calls.

**Not applied now** because `app.py` is being edited in the working tree by the Act work at the same time,
and a second writer on that file risks losing one side's change. The patch is small:

```python
# module level, next to HELD_STAGES
STREAM_MAX_INFLIGHT = 16
_STREAMS = threading.BoundedSemaphore(STREAM_MAX_INFLIGHT)

# in ask_stream, after the role check and _ask_kwargs, before queue/threads:
if not _STREAMS.acquire(blocking=False):
    return JSONResponse(status_code=503, content={"error": "temporarily unavailable"})

# in work(), in the existing finally:
    finally:
        _STREAMS.release()
        events.put(None)
```

Test to add with it: hold 16 streams open (a pipeline stub that blocks on an event), then the 17th
`POST /ib/ask/stream` is a 503 with the standard body, and after releasing one, a new stream succeeds.
The 503 depends only on load, never on content, so rule 4 is unaffected.

### F3. Timing of the live check (documented residual)

Confirmed in principle: `authz.authorize` drops ids outside the asker's stored readable set before any
source call, and checks the rest live, so an id in that set takes longer. The set is the asker's own
Gate 1 view (what they were granted at the last sync), so the signal is "this id was shared with me",
which the person already knew. SECURITY.md §5 lists timing as **not controlled**, with timing parity on
the roadmap (§8). No change.

### F4. Overview graph checks (documented residual)

Confirmed: `graph.MAX_CHECKS_OVERVIEW = 1500`. The live checks for every view run on one shared pool of
6 workers (`authz.VIEW_POOL`) under one 20 s deadline per view, so source load is capped at 6 checks in
flight however many graph calls arrive. SECURITY.md §15.1 already records the cap, its cost (about 3.4 s
for jdoe) and that it is not rate limited. Per-person rate limits belong on the public edge (roadmap §8).

### F5. Reveal shows answer text to the compliance officer (by design)

`POST /audit/reveal` needs the `compliance_officer` role and a written reason, checks the stored text
against the sealed hash, and writes an `audit.reveal` record before answering
(`tests/test_audit_routes.py::test_reveal_requires_reason_and_is_ledgered`). Reading what the Brain told
someone is the compliance job (Aspire scenario 3). Accepted; tokens are 8 hours at most.

### F6. Replay header (by design, fenced)

Every condition CodeBuddy lists is required (key of 24+ characters, not deployed, bearer, loopback,
matching key, time within 400 days), and every such record is labelled `channel: "replay"` and
`synthetic_replay: true` in the ledger and the UI (decision D-10, SECURITY.md §15.5,
`tests/test_graph_doc.py::test_replay_switch_refuses_every_unsafe_call`). On the stage laptop the key is
only in the seeding script's environment. Accepted.

### F7. No length cap on `/ib/doc/{doc_id}` (fixed)

`docview.view_doc` now refuses an empty id or one longer than `MAX_DOC_ID` (300) right after the
write-ahead `doc.viewed` record and before any index or source lookup. The answer is the same 404 as a
restricted or unknown id.

Tests: `tests/test_codebuddy_finals_review_fixes.py`
- `::test_overlong_doc_id_is_the_same_404_and_still_ledgered` (same status, body and headers as a
  restricted and an unknown id; the attempt is ledgered first);
- `::test_overlong_doc_id_never_reaches_the_index`;
- `::test_a_permitted_doc_still_opens`.

### F8. Tamper demo cost (accepted)

One run at a time (`TamperDemoBusy`, a 409 for a second caller), a 20 s budget, compliance role only, and
every run is ledgered write-ahead. Accepted.

## What CodeBuddy checked and found sound

Its "correctly defended" list matches our tests: roles come from the directory, never the token; Act
checks ownership before reconstruction and binds a proposal to its owner; the `workbuddy-mcp` label is
honoured only on bearer calls; graph counts are exact or `null`; restricted and unknown ids give the same
404; the trace receipt carries no verdicts; the stream holds `gate1` and `gate2` until `sealed`; and the
tamper demo only reads the live files.
