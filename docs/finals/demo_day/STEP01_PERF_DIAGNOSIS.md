# Presenter step 01 got slower: diagnosis (30 Oct 2026)

Branch `final_v1.2`. Read-only diagnosis: no product code was changed, nothing was committed, and the shared stack (3100/8100/8200/8300) was not called. Measurements are in-process, against a throwaway ledger on the E: drive.

## 1. Short answer

Every write to the audit ledger re-verifies the whole ledger first. Step 01 (Compare Amy vs Rachel) writes 8 ledger records, one after another. So step 01 gets slower in direct proportion to the number of records already in the ledger. Nothing tonight made a single request heavier; the ledger just got bigger (about 1,100 to about 2,900 records). The slowdown is the ledger size, not a new feature.

## 2. Where the time goes (file:line)

Per Compare run, the browser does (components/compare/CompareView.tsx, `askSide`, around line 49):

| Call | Ledger appends |
|---|---|
| 2 x `POST /auth/demo-login` (one per column) | 1 each = 2 (`auth.login`, app.py:245) |
| 2 x `POST /ib/ask/stream` | 3 each = 6 (`request.accepted`, `retrieval.decided`, `answer.released`; pipeline.py, steps 2, 8 and 11) |
| 2 x `GET /auth/whoami` | 0 |
| **Total** | **8 appends, strictly one after the other** (the ledger lock serialises the two columns) |

Each append is `AccessLedger.append` (databridge_ib/audit/ledger.py, `self._dl.append(payload)`), which calls `DecisionLedger.append` (packages/core/databridge_core/decision_ledger.py:827). Under the lock it does:

1. `self._scan()` (decision_ledger.py:624): reads the whole log file, `json.loads` every line, recomputes the SHA-256 digest and the HMAC seal of every record (the loop at about line 670), and (line 627) `AnchorLedger.latest()` which re-verifies every line of the anchor ledger (`_lines`, line 454).
2. Writes the record (`_write`, line 768), then `AnchorLedger.anchor()` (line 488), which takes the anchor lock and calls `_lines()` AGAIN (line 490): a second full anchor-ledger verification.

So one append costs about 1 pass over N log records plus 2 passes over N anchor lines (the anchor ledger gets one line per append, so it is as long as the log). The cost is O(N) CPU (JSON parse plus hash plus HMAC per record), not disk: a single fsync measured 1.0 to 1.3 ms.

The module docstring already says this (ledger.py, "Performance note ... an append costs O(records)").

## 3. What I ruled out

| Suspect | Finding |
|---|---|
| `/audit/stats`, `/ib/trace`, journey, hash-chain verify called from Compare or from presenter step 01 | **No.** A search of components/compare/* and presenter/script.ts finds none. Step 01 is `ctx.go(peopleUrl)` then `ctx.call('compare-run', SPEC_PEOPLE, 15_000)`. |
| `/ib/trace` catch-up (`_trace_sync`, app.py:197) | Not on the ask path. It is rate-limited to once per 15 s anyway (TRACE_SYNC_TTL_S). |
| `/ib/status` ledger check (`_ledger_ok`, app.py:176) | Cached 15 s; not called by Compare. |
| Projection subscriber (`AuditProjection.apply`, audit/query.py:237) | One SQLite transaction per append; constant time. |
| `action.requested`, open-link map | Not on the ask path (they are in the act and open routes). Not a per-ask cost. |
| Live-poll rows (`GraphViewed`, live.py:274) | **Indirect only.** Each `GET /ib/graph/changes` poll inside a window writes one more O(N) append (`outcome="checking"`, live.py:247), every 2 s in Stage. It is only mounted on the graph explorer (GraphExplorer.tsx:381), not on Compare, so it does not slow step 01 unless a graph tab is still open (then each poll takes the ledger lock for the same 0.2 to 3 s and queues in front of Compare's appends). It is also a likely reason the ledger grew so fast today. |
| Tonight's code changes | No per-request ledger read was added. The only growth driver is record count. |
| Page-load warm-up | Not a factor: waiting after `P` did not help, and the model below fits without it. |

## 4. Measurements

Harness: `C:\Users\Ishaan Sirbhaiya\AppData\Local\Temp\claude\E--Tenecent-Hack-Walnut\f4677d8d-b78b-4919-bb9c-c5594ee47239\scratchpad\step01perf\perf.py` and `parts.py`. It pre-builds a sealed, anchored ledger of N records (same hashing and sealing as the real one, one anchor line per record), builds the real app with a TestClient, then runs the step 01 sequence (2 logins, 2 asks, 2 whoami) twice. The temp ledger was on E:, the same drive as the demo. Mock connectors, so non-ledger time is small; the stage also pays for real Gate 2 calls and the browser, a fixed extra.

| Ledger records | One append | Step 01 equivalent (8 appends + asks), in-process | Real stage figure |
|---|---|---|---|
| 1,100 | 0.21 to 0.27 s | **1.34 to 1.98 s** | 1.5 to 1.9 s (this morning) |
| 2,900 | 0.48 to 0.69 s | **2.84 to 3.73 s** | 2.7 to 3.8 s (tonight), 5.8 s once |
| 5,000 | 0.5 to 1.8 s | 10 to 14 s (a loaded run; a straight line gives about 5 to 6 s) | not measured on stage |
| 10,000 | 2.9 to 3.2 s | 17 to 19 s (loaded run; a straight line gives about 9 to 10 s) | not measured on stage |

The 1,100 and 2,900 rows reproduce the stage numbers almost exactly, which confirms the cause. Component timings (`parts.py`, one run each): at 2,900 records the log scan is about 370 ms and one anchor pass about 135 ms; at 10,000 they are about 2.0 s and 0.7 s. The 5,000 and 10,000 rows are noisy because the box was busy with the final tests (run times vary 2 to 3x), so read them as "at least a straight line, possibly worse (larger files, cache pressure, antivirus scanning)". Rule of thumb from the two clean points: about 0.9 ms per ledger record per step 01, plus roughly 0.75 s fixed.

Step 02 (Ken) has the same 8 appends and also rose, from 1.4 to 1.7 s to 1.2 to 2.8 s, but less than step 01. I could not fully explain the extra weight of step 01 (Rachel's answer has more documents and links, so more Gate 2 and ranking work, and it is the first Compare after `P`); the ledger cost is the dominant term in both.

## 5. Is the stage at risk?

Two hard limits sit on top of the slow-down:

* The presenter waits at most **15 s** for the Compare run (`ctx.call('compare-run', SPEC_PEOPLE, 15_000)`, script.ts). Past that, step 01 shows as failed.
* A ledger append waits at most **10 s** for the lock (`lock_timeout=10.0` in `AccessLedger.__init__`, ledger.py). With two columns (and any open graph tab polling) queued behind appends that each take 2 to 3 s, a waiter can time out and the ask fails closed with a 503.

| Ledger size | Step 01 | Risk |
|---|---|---|
| ~3,000 | 2.7 to 3.8 s | **Low.** Slow, still green, visible as a pause of 3 to 4 s. Already the case. |
| ~5,000 | about 5 to 6 s (straight line); up to 10 to 14 s measured under load | **Medium.** A long dead pause, and under load close to the 15 s limit. |
| ~10,000 | about 9 to 10 s (straight line); 17 to 19 s measured under load | **High.** Likely past the 15 s step limit, and ledger lock timeouts become possible. Do not go on stage like this. |

Rehearsals add 8 records per Compare step plus 3 or more for every other ask, plus a row per sign-in and page view. Expect the ledger to pass 5,000 after a day or two of heavy rehearsal.

## 6. Proposed fix (needs approval; not applied)

Goal: make an append cost O(1) in the common case, without weakening what the audit proof shows.

**Fix A (low risk, small): do not verify the anchor ledger twice per append.** In `DecisionLedger.append`, `_scan()` has just verified the anchor ledger under the same lock. Let `_write` hand that verified last anchor line (lseq and record_digest) to `AnchorLedger.anchor()` so it appends without calling `_lines()` again. Saves about one third of an append. Risk: the core is shared with other Walnut logs; keep the old path as the default and pass the verified tail only from `append`. Tests: the existing `packages/core` decision-ledger tests (tamper, rollback, torn append, pending recovery) must pass unchanged; add one that tampers with the anchor file between scan and write and checks it is still refused.

**Fix B (the real cure, medium risk): verified-prefix cache.** Remember in the `DecisionLedger` instance the point already verified (file size, `st_mtime_ns`, file identity, last seq, last digest, and the same for the anchor file). On the next `append`, if both files are unchanged since that point, skip the full re-hash and only read and verify the new tail plus the anchor tail; if size or mtime differ, or the cache is older than a bound (say 30 s, in line with the 15 s TTLs already used in app.py), do the full verify. Full verification is always used by `read`, `events`, `verify`, the tamper demo and `/audit/verify`, so the "someone edited the file" proof still fires. Risk: an attacker who rewrites the file and restores size and mtime inside the window would be caught only at the next full verify (within 30 s; the external anchor sink catches it too). Appends from a second process (MCP server or another worker) change size and mtime, forcing a tail verify. Tests: new unit tests for (1) append after an untouched file hashes only the tail (count records hashed), (2) a flipped byte in the middle is refused on the next full verify and on the next append after the cache expires, (3) an append from a second process is picked up, (4) torn and pending states still recover, (5) `test_audit_tamper.py`, `test_audit_ledger.py` and the e2e tamper step pass. Expected effect: step 01 back to about 1.2 to 1.5 s at any size.

**Not recommended:** fewer appends per Compare run. The three-record write-ahead per ask is a published guarantee (plan 4.2), and dropping the login row would change the audit story. Changing the `checking` live-poll rows needs the same review as when they were added (rule 4); safer to just not leave a graph tab open.

With the demo close, Fix A is the only one I would consider if tests are quick. Fix B touches the shared core and the tamper proof; do not ship it in the last 24 hours without the coordinator's sign-off and a full ledger and tamper test run. Otherwise rely on the stage routine below.

## 7. Practical stage tips (no code change)

1. **Reset the ledger on the morning of Demo Day.** Run `Reset Walnut Demo.cmd --fresh` (about 3 minutes; it wipes and reseeds the history, and refuses to wipe while a port is held), or `demo_up.sh start --fresh`. This returns the ledger to a small size and step 01 to about 1.3 to 1.5 s. Do it once, early, then rehearse only lightly (each full walk adds roughly 40 to 60 records).
2. Do the final full rehearsal BEFORE the reset, and a single short check after it (`P`, Space to step 02) so the ledger stays small.
3. Close every graph and Grow tab when presenting Compare. Their 2-second live polling writes ledger rows and takes the ledger lock.
4. Do not run a second presenter walk or the test suites against the same stack while on stage.
5. If the ledger is already large and there is no time to reseed, a spoken line covers the 3 to 4 s pause ("it is writing each step to a tamper-evident log"); that is true and fits the story.
6. Backup: if step 01 shows failed, Space again usually succeeds; clip A (`B`) is the fallback.

## 8. How to re-check later

Run `perf.py 1100 2900` from the scratchpad folder above with the repo venv (`databridge\.venv\Scripts\python.exe`); set `TEMP` to a folder on the same drive as the demo. It does not touch the running stack.
