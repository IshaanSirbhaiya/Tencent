# Security Review — FINALS Backend (CodeBuddy CB7)

**Scope (read-only):** the Demo Day build of Walnut DataBridge Internal Brain, specifically the files named in the task:
`authz.py`, `graph.py`, `docview.py`, `app.py` (`/ib/ask/stream`, `/ib/trace/{req_id}`, `/ib/map`, `/ib/act/*`,
plus `demo_login`, `principal_dependency`), `actions.py`, `audit/tamper.py`, `audit/routes.py`, and the
supporting `auth.py`, `gates.py`, `knowledge_map.py`, `models.py`.

**Reference rule ("rule 4"):** a restricted document must be indistinguishable from one that does not exist, in
content, counts, timing, error text and response shape. Identity comes only from the signed token.

**Findings are ranked by severity. No existing file was modified.**

---

## Findings by severity

### [CRITICAL] F1 — `demo_login` impersonation / role escalation when no passcode is set
**File:** `databridge/apps/internal_brain/databridge_ib/app.py:226-247` (passcode check `:232-234`, token mint `:244`, cookie `:245-246`)

`/auth/demo-login` is enabled whenever `IB_DEMO_LOGIN=1`. The passcode guard is:

```python
passcode = os.environ.get("IB_DEMO_PASSCODE", "")
if passcode and not hmac.compare_digest(str(body.get("passcode", "")), passcode):
    raise HTTPException(401, "passcode required")
```

If `IB_DEMO_PASSCODE` is unset or empty, the `if passcode and ...` test is false and **no authentication of the
persona choice is performed at all**. The caller then supplies an arbitrary `persona` and the server mints a real,
properly signed token for that identity (`authmod.mint(... user.id, list(user.roles) ...)`).

**Attack scenario:** On a Demo Day instance with `IB_DEMO_LOGIN=1` and no `IB_DEMO_PASSCODE`, an unauthenticated
attacker posts `{"persona": "compliance_officer"}` (or `"admin_it"`, or any employee id they can guess) to
`/auth/demo-login` and receives a valid session cookie. They can then: reach every `/audit/*` route (read access
decisions, run tamper-demo, reveal answer text), act as that user via `/ib/act/*`, and exercise any employee
route. This breaks the core rule that identity comes only from the signed token — here the token is legitimately
issued for an attacker-chosen identity.

**Why it is critical:** it is a complete authentication bypass for the demo surface and a privilege-escalation path
to `compliance_officer`/`admin_it`. The code *permits* an insecure configuration rather than failing closed.

**Suggested fix:** require a non-empty passcode whenever `demo_login` is on, and fail closed if it is missing:
```python
if not svc.cfg.demo_login:
    raise HTTPException(404, "not found")
if not svc.cfg.demo_login_passcode:        # fail closed: a demo login must have a passcode
    raise HTTPException(503, "demo login not configured")
if not hmac.compare_digest(str(body.get("passcode", "")), svc.cfg.demo_login_passcode):
    raise HTTPException(401, "passcode required")
```
Also consider hashing/rotating the passcode and never allowing `persona` to resolve to `admin_it`/`compliance_officer`
without an additional step.

---

### [HIGH] F2 — `/ib/ask/stream` spawns an unbounded thread per request (availability / self-DoS)
**File:** `app.py:348` (`threading.Thread(target=work, name="ib-ask-stream", daemon=True).start()`), stream generator `:350-361`

Every `POST /ib/ask/stream` call starts a brand-new OS thread that runs the **entire pipeline** (live Gate-2 source
checks on the shared 32-worker pool in `gates.py:18`, optional model call, then streaming) to completion. There is:
- no cap on the number of concurrent streams,
- no per-client connection limit,
- no shared worker pool — one thread per request, and the thread keeps running after the client disconnects
  (intentional, so the ledger is written, but it means a closed tab still consumes a thread + source/model work).

**Attack scenario:** an authenticated user opens, e.g., 200 concurrent SSE connections. The server spawns 200 daemon
threads, each fanning out up to many Gate-2 checks and a model call, exhausting thread stack memory, source-platform
connections / rate limits, and model quota. This is a cheap, authenticated denial-of-service against the Internal
Brain and the upstream source systems.

**Suggested fix:** bound concurrency with a `threading.Semaphore` (or a fixed `ThreadPoolExecutor`) shared across the
process; reject/queue beyond the limit with a 429; optionally cap streams per `user.id`. Track in-flight streams so a
client disconnect can short-circuit the pipeline (the ledger write can still be guaranteed by a final commit step
rather than a whole-thread hold).

---

### [MEDIUM] F3 — Timing oracle: live Gate-2 check reveals existence of doc ids that were once shared with the user
**Root cause:** `authz.authorize` (`authz.py:165`) drops ids that are **not** in the stored readable set *before*
any source call, but for ids that **are** in `readable` it performs a live `gates.check_all` (`authz.py:173-176`,
`gates.py:42-43`). Hence:
- a never-indexed id → dropped at Gate 1 → fast 404 / fast omission.
- an id that is in the stored readable set (i.e. a real document that was at some point shared with this user),
  whether later allowed or live-denied → a live source check first → slower 404 / slower decision.

Affected endpoints that take attacker-supplied ids and call `authz.authorize`:
- `GET /ib/doc/{doc_id}` — `docview.py:52-57` (ledger append, then `authorize`, then `NotFound`→404).
- `POST /ib/graph/openable` — `graph.py:394-406` (per-id live check; omitted ids are indistinguishable in the
  *output* but distinguishable in *latency*).
- `GET /ib/trace/{req_id}` (citation re-check), `build_graph` focus mode, `actions.py:257` (context re-check).

**Attack scenario:** Jira/Confluence ids are structured and partly guessable (`jira:PROJ-123`, `confluence:cf-1001`).
The attacker submits a batch of candidate ids to `/ib/graph/openable` (or probes `/ib/doc/<id>`) and measures
latency. Ids that were ever indexed as readable to them take measurably longer than ids that were never indexed,
leaking which documents *exist and were shared with them* — a violation of rule 4's "indistinguishable … in timing".

**Why not "high":** the oracle only distinguishes "indexed-as-readable-to-me" from "not indexed"; it does not, by
itself, expose a doc the user was *never* granted. Still, it discloses the existence of real documents and is a
clean timing side channel.

**Suggested fix:** make the unauthorized/unknown path constant-time with respect to existence: either (a) always run a
bounded existence probe for any supplied id (so both branches do equal work), or (b) cap the observable delta by
performing the Gate-1 membership test and the Gate-2 check on the same timeline and returning after a fixed minimum
floor; at minimum, document that "in my readable set" is the leaked signal and ensure readable-set membership is the
only thing revealed (it currently is). For `/ib/doc`, consider returning the same synthetic delay for any id not in
readable as for a live-denied id.

---

### [MEDIUM] F4 — Overview graph amplification: up to 1500 live source checks per request, no per-user rate limit
**Files:** `graph.py:55` (`MAX_CHECKS_OVERVIEW = 1500`), `authz.py:41` (`VIEW_POOL` = 6 workers, shared across *all*
view calls), `app.py:503-518` (`/ib/graph` route).

A single `/ib/graph` (overview) call checks up to **1500** documents live against the source platforms
(`build_graph` step 2). The 6-worker `VIEW_POOL` is a *process-global* shared between graph, doc, trace, openable and
action views, with no per-user rate limiting and no global ceiling on concurrent graph calls.

**Attack scenario:** an authenticated employee issues many parallel `/ib/graph` overview requests, or one request
against the widest persona. Each fans out up to 1500 outbound Gate-2 calls to the source systems; the shared 6-worker
pool plus the per-call 20 s deadline means the source platforms are hit with a large, attacker-amplified request
volume (self-DoS / upstream rate-limit exhaustion). Memory also grows: each call queues up to `cap` futures.

**Suggested fix:** lower `MAX_CHECKS_OVERVIEW` to a realistic demo bound, add per-user and global rate limiting on
view endpoints, and consider a dedicated, size-capped pool with a hard global in-flight-check budget so concurrent
calls cannot multiply source load without bound. The `complete=false` / `null` count behaviour already degrades
gracefully, so a stricter cap is safe.

---

### [MEDIUM] F5 — `/audit/reveal` returns full answer text to `compliance_officer` regardless of the officer's own document permissions
**File:** `audit/routes.py:220-259` (`audit_reveal`), gated by `COMPLIANCE_ROLE` (`:51`, `:90`).

`/audit/reveal` returns `stored.answer` (the full released answer, which embeds grounded passages from the cited
source documents) for any `req_id` that has an `answer.released` record, after verifying the sealed sha256 matches.
It does **not** re-check whether the *requesting compliance officer* can individually open the underlying cited
documents.

**Attack scenario / concern:** a holder of the `compliance_officer` token (or anyone who obtains such a token, see
F1) can read answer text that may contain content from documents they personally are not permitted to open. This is
role-gated and likely intended for audit, but it is a path to "see content they are not permitted to open" outside
the per-document authorization model, and it is exactly the kind of cross-role content exposure rule 4 is meant to
prevent for the *subject*. The exposure is bounded to already-released answers and requires the compliance role.

**Suggested fix:** confirm this is an accepted, tightly-controlled exception. If not, scope reveal to answers whose
cited documents the officer can also open now, or redact cited passages the officer cannot open. At minimum, ensure
`compliance_officer` tokens are short-lived and narrowly issued, and that F1 cannot grant them trivially.

---

### [MEDIUM/LOW] F6 — Replay seeding switch can write backdated entries into the live audit ledger
**File:** `app.py:143-165` (`_replay_time`), used by `_ask_kwargs` (`:272-282`) for `/ib/ask` and `/ib/ask/stream`.

When `X-IB-Replay-Key` is supplied and **all** of {server has `IB_REPLAY_KEY` ≥24 chars, `not deployed`, bearer
token, `request.client.host in LOOPBACK`, key matches via `hmac.compare_digest`} hold, the request's ledger events
are written with `event_time` set to an attacker-chosen time within the last 400 days and `channel="replay"`.

**Attack scenario:** on a non-deployed demo with `IB_REPLAY_KEY` set (needed for the seed script), any bearer-token
holder on loopback can backdate their own `request.accepted` / `doc.viewed` / `graph.viewed` events to arbitrary past
times. This pollutes the integrity of the audit timeline (e.g. to make activity appear historical) and, because the
records are real ledger appends, can distort `/audit/stats` and journey/reveal provenance. Exploitability is limited
by the loopback + key + not-deployed conditions, which plausibly all hold on a Demo Day machine.

**Suggested fix:** treat replay as a script-only, out-of-band capability (e.g. a separate admin command) rather than
an HTTP header on user-facing ask endpoints; or require an explicit, separately-secret, non-loopback-gated admin
scope. Ensure `deployed=True` in any environment reachable by untrusted clients.

---

### [LOW] F7 — `doc_id` path parameter has no length/format cap
**File:** `app.py:533-534` (`doc_id: str` with no `max_length`; only `q` is capped at 500).

Other id-bearing inputs are bounded (`openable` ids ≤300, `/ib/trace` req_id ≤200, `proposal_id` ≤200, `doc_id` in
`docview` ledger is sliced to 300). The `/ib/doc/{doc_id}` path segment is unbounded. It is passed to
`authz.authorize` → `index.doc` / `gates.check_all`. A very long id is not a direct auth bypass, but it is an
unvalidated input that can be used to probe storage/connector behaviour and is inconsistent with the other caps.

**Suggested fix:** validate `doc_id` with the same `ID_RE` (`actions.py:77`, `^[A-Za-z0-9_-]{1,200}$`) or a
`max_length=300` as elsewhere.

---

### [LOW] F8 — `/audit/tamper-demo` is CPU/IO heavy and could be used for limited resource exhaustion
**File:** `audit/routes.py:319-343`, `audit/tamper.py:186-292` (8 scenarios, each copies + verifies the full ledger,
bounded by `budget_s=20`).

Mitigations already in place: `TamperDemoBusy` (`_RUN_LOCK`, `tamper.py:345-346`, released on all paths) serialises
runs and returns 409 to concurrent callers; it is `compliance_officer`-only. Residual: a compliance officer can
repeatedly trigger the single serialised run, each doing up to ~20 s of ledger verification work.

**Suggested fix:** add a short minimum interval between runs per principal, and/or cap `budget_s` more tightly for
the live endpoint. Low priority given the role gate and lock.

---

## What was checked and found correctly defended

These are the places the review specifically verified as safe against the listed abuse classes — listed so the
findings above are not mistaken for gaps here.

**Identity / (c) act as someone else**
- **Token verification is sound** (`auth.py:45-80`): HMAC-SHA256 over canonical JSON, `aud=="ib"` enforced, `exp`
  integer + expiry checked, `jti` revocation checked, `sub` required. No algorithm confusion (symmetric HMAC, not
  asymmetric — no "none"/RSA confusion). Roles are taken from the **directory**, not the token claims
  (`app.py:115` `svc.directory.get(claims["sub"])` then `user.roles`), so a token cannot self-assert roles.
- **Acting on someone else's answer is blocked**: `actions.py:234` `projection.owns(req_id, user.id)` before
  reconstruction, re-checked at `:237` (`res.get("sub") != user.id`), and the proposal is bound to the owner:
  `_take` (`:404-414`) rejects `p.sub != user.id`. `ID_RE` validation (`:230`, `:406`) prevents malformed ids from
  reaching reconstruction.
- **Bearer vs cookie channel** is correctly scoped: the `workbuddy-mcp` channel label is honoured only on a bearer
  call, never on the browser cookie session (`app.py:276-278`) — prevents a cookie user from spoofing the MCP
  channel.
- **Logout** revokes the actually-used `jti` (`app.py:255-258`).

**(a) content leakage through the views**
- `public_meta` (`authz.py:232-236`) whitelists exactly metadata fields; no ACL, no full text, no raw link ids leave
  the helpers. Graph/doc/trace/action outputs all go through it.
- Document excerpt is the **masked** snapshot text (`docview.py:59`); `mask_doc` runs at ingest and on read-through
  (`authz.py:121`). No full unmasked text is returned.
- Linked/neighbouring documents are shown only when they pass Gate 2 *now* (`docview._linked`, `graph` step 3 using
  `ok_ids`); a restricted target is simply absent.

**(b) learning that restricted content exists (output shape / counts / ordering)**
- **Graph counts are null when not exact**: `degree_hidden_safe` is `None` and `more` is `None` unless every
  candidate was decided live (`graph.py:264-277`, `:350-353`); container/person/platform `count` is `None` unless
  `exact` (`graph.py:354-358`). `complete` is false and counts are `null` when the budget runs out
  (`graph.py:224`).
- **Same negative answer**: focus unknown / not-openable and focus non-existent both raise `NotFound` → identical 404
  body `NOT_FOUND` with no id echo (`graph.py:286-288`, `docview.py:56-57`, `app.py:514-515`).
- **Trace records leak no verdicts**: `/ib/trace` returns only `{"ordinal","kind","hash"}` per event
  (`app.py:435-436`); the `retrieval.decided` `gate2` verdicts, reason codes and `considered` sets stay in the
  compliance journey, never in the subject's receipt.
- **Search re-ranks after Gate 2**: a live-denied hit leaves the corpus and search re-runs (`graph.py:156-180`), so a
  revoked doc never takes a hit slot from a permitted one, and never appears.
- **`openable` output** omits both non-existent and restricted ids with no distinguishing signal in the body (only the
  timing signal of F3 remains).

**SSE stream (`/ib/ask/stream`) — rule-4 timing**
- `gate1` and `gate2` stages are **held** and released together only at `sealed` (`app.py:327-335`), so the
  gate1→gate2 interval (the live permission checks) is not exposed more finely than the whole response already is.
  Exactly seven fixed stage names are emitted for every successful request (`accepted…released`), with no counts, ids
  or reasons. Refusals use the identical `REFUSAL_TEXT` (`models.py:21`), the same for restricted / non-existent /
  not-relevant.

**Write-ahead / fail-closed**
- Doc view (`docview.py:49`), graph and openable (`graph.py:409-413`), trace (`app.py:398-400`), every action
  (`actions.py:320,374,434,453,481`) and every audit route (`audit/routes.py`) append their ledger event *before*
  the decision/response; a ledger failure becomes a 503 and nothing is returned. This keeps the 404/503 answer
  identical across permitted/restricted/unknown.

**(d) tampering with the real audit log**
- **`/audit/tamper-demo` cannot modify the real ledger.** `run_on_live_copy` (`audit/tamper.py:336-380`) snapshots
  the live log, anchor ledger and external head *under the ledger's append lock* into a temp dir, and all scenarios
  run on a *copy* (`run_scenarios` → `shutil.copytree` of the copy, `tamper.py:218`). The live files are only ever
  read. After the run it re-reads the live files and confirms `real_log_modified` is false (byte-for-byte for the
  prefix that existed at snapshot; append-only growth is tolerated, `:323-333`). `_RUN_LOCK` is released on **every**
  path including a failed `mkdtemp` (`tamper.py:347-380`), fixing the earlier "stuck busy" bug.
- **`/audit/verify` `_verify_content`** (`audit/routes.py:370-403`) re-hashes every stored answer against the sealed
  `answer_sha256` and reports only counts/problems — it cannot be used to alter content, only to detect mismatch.
- All audit routes **append only**; there is no delete/edit path, and appended records are integrity-sealed. A
  compliant-shaped `audit.query`/`audit.reveal` record an officer chooses to write is the only "change", which is by
  design and tamper-evident.

**Route allowlist / deployment hardening**
- Only the approved prefixes are mounted, enforced at startup (`app.py:587-589`), not merely documented.
- DNS-rebinding guard via `TrustedHostMiddleware` with `trusted_hosts()` (`app.py:135`).
- `403/401` for auth/role failures happen *before* the SSE stream starts (`app.py:320-321`), so they cannot be
  confused with a streamed error.

---

## Summary of deliverables
- **Files reviewed:** `authz.py`, `graph.py`, `docview.py`, `app.py`, `actions.py`, `audit/tamper.py`,
  `audit/routes.py`, plus `auth.py`, `gates.py`, `knowledge_map.py`, `models.py` (for context). No files modified.
- **Findings:** 1 critical (F1 demo-login impersonation), 1 high (F2 unbounded SSE threads), 4 medium
  (F3 timing oracle, F4 graph amplification, F5 compliance reveal, F6 replay backdating), 2 low (F7 doc_id cap,
  F8 tamper-demo resource use).
- **Correctly defended:** token integrity & directory-sourced roles, act-ownership/anti-impersonation, output-shape
  rule-4 (null counts, same 404, held SSE stages, whitelisted metadata, masked excerpts), and the tamper-demo's
  read-only snapshot guarantee.
