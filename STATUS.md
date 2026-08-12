# STATUS — Hack4Health Walnut

> Where the build stands. `PLAN.md` says *what* to build, `TASKS.md` says *what next*, this says
> *where we are*. Updated at the end of every section — see `CLAUDE.md` §Build workflow.

**Last updated:** 2026-08-12 (end of day) · **Phases 0–6a + 6.5 complete. Phase 7 largely built.
Six scoped codex reviews run: 18 Critical findings open — see [`REMEDIATION.md`](REMEDIATION.md),
which is the file to read after `CLAUDE.md` in a fresh session.**

⚠️ **The next block of work is FIXING, not building.** Six tracks shipped in parallel on
2026-08-12 and every one came back with Criticals, including both pieces the lead wrote. They are
five recurring shapes, not eighteen bugs; `REMEDIATION.md` §1 names them and §4 gives the order.

**Green:** `python -m pytest -m "not integration"` → **1605 passed, 5 deselected** ·
`npx playwright test` → **18 passed** · `npx tsc --noEmit` clean.
The e2e suite is now genuinely offline — see Gotchas.

---

## Done

- **Phases 0–5** — fixtures, schemas, extraction + grounding wall, resolution (candidates only),
  config-driven eligibility, questionnaire pre-fill, append-only ledger with SQLite triggers,
  billing draft, metrics. **Measured: baseline 27.5 min · mean saved 16.4 min (60%) · ~11 h per
  40-patient morning.**
- **Phase 6a** — the graph is load-bearing. `ClinicHistory` Protocol in `packages/intake`,
  adapter in `packages/graph`, composed at the gateway. `conflicting_document_on_file` comes from
  a traversal: the conflicting insurer appears nowhere in the document being read.
- **Phase 6.5** — BM25 retrieval. EV-RETRIEVAL **19/20**, out-of-domain declined **1.00**.

### 2026-08-12 — review gate + the findings worked

Both reviewers ran concurrently. **codex: 9 findings (5 Critical). `/code-review high`: 12.** Worked:

- **T-01c (CRITICAL, constraint 2)** — `/claim-draft` emitted `submit_to`/`issuer_code`/
  `package_code`/`patient_id`/`line_items` as bare strings from the live draft, including fields no
  human had committed. New `packages/intake/databridge_intake/claim.py` builds it from the **evidence
  ledger only** — `build_claim_draft(intake_id, ledger, table)` has no parameter through which an
  uncommitted value can arrive, asserted by a signature test. 24 tests.
- **The pipeline never used a real model.** `run_intake` defaulted to the fixture-keyed gold backend
  and *no call site ever passed a harness*, so any document that was not one of the ten chits
  extracted to nothing, silently, while `/health` reported a live model. Now composed at the gateway
  (`_extraction_harness`), with the `sonnet` alias remapped to whichever provider holds a key.
- **T-01k (CRITICAL, constraint 1)** — the attestation bug review found on the confirm button
  existed in `identity-panel.tsx` too: `attested` was a bare boolean reset on `[intakeId]`, and
  picking a different candidate does not change the intake id. Now `attestedFor: string | null`,
  `attested` derived. Proven by a Playwright test, which is what the source scans could never do.
- **Re-upload had no compare-and-swap** — found independently by BOTH reviewers, and `TASKS.md`
  T-01d had already written down that this path "has the same window". Now CAS → 409.
- **Bi-temporal was wrong in the DATA: 63 of 111 edges predated one of their own endpoints.**
  Every edge carried the standing-config date while document nodes carried the date on the letter.
  Invisible on screen (the renderer hides an edge unless both endpoints show) and wrong everywhere
  it was *read* — `as_of` replay, the analytics trend, claims defensibility. Now 0, enforced by
  `_reconcile_edge_validity` + two tests. Edge dates now span six distinct dates, so the slider has
  a real build-up to replay.
- Also: claim refuses when the ledger names two selected patients; refuses an ambiguous package-rule
  fallback; price and coverage pointers on every claim line; duplicate tests surfaced not dropped;
  12h shift expiry now actually fires (was evaluated only at mount); T-01l scanner rewritten to
  blank comment *spans*; T-03 Playwright fixed (11 broken → 18 passing) + a new `staff-gate.spec.ts`.

### 2026-08-12 (end of day) — Phase 7 built, then reviewed hard

Six tracks ran in parallel by file ownership: brain UI, OCR/docling, `/evidence`, `/intelligence`,
the eight earlier findings, and the read-only clinic agent — plus the lead's bi-temporal fix and
node evidence sheets. All landed green. **Then one scoped `codex exec` review per track found
18 Critical findings across all six.** Fixed since: `intake_confirm` read `held` outside the lock
while reading `state` inside it (the wrong-subject attestation bug, fourth instance).

Also: product renamed **Front Desk Copilot → Walnut** (15 files; the competition brief in
`Hack4Health/` deliberately untouched). `/artifacts` removed from the nav. `/brain`'s answer was
rendering into a 319×54px box — it always answered, you just could not see it.

### 2026-08-12 — Phase 7 started

`docs/hackathon/PHASE_7_BRAIN_AND_INTELLIGENCE.md` written: six diagnosed `/brain` defects and the
Phase 8 `/intelligence` + agent + MCP plan, with a **parallel execution plan by file ownership**.
Landed: T-30 (bi-temporal, above), T-31 (build animation removed), T-32 (resizable ask panel),
T-34 (query pipeline made legible), T-35 (connector/MCP nav restored).

## Now

Nothing in flight. **The entire build is still uncommitted** — one commit (`2d54aeb`), ~95 changed
paths, branch `prototype`. Six agents' work sits in that tree with no checkpoint. This is the top
risk and the first item below.

## Next — work `REMEDIATION.md` §4 top-down

1. **Commit in coherent chunks.** A diff-based reviewer cannot see `packages/intake/`,
   `packages/agent/`, `components/frontdesk/` or `e2e/` while they are untracked, so the review
   gate itself is degraded until this lands.
2. **Shape A — a pointer per value** (R-01, R-18, R-21, R-27, R-09). R-01 matters most: node
   evidence sheets feed the LLM's context, so ungrounded attributes there are constraint 2 aimed
   at the model.
3. **Shape B — bind an attestation to its subject** (R-17, R-22). Six instances of this bug so far,
   four fixed.
4. **Shape C — guards before the guarded action** (R-12, R-19), then **R-13** (checkbox fail-closed
   — a declined test can currently be billed).
5. **Shape E — typed agent output** (R-07…R-11). Unblocks the real agent behind `/intelligence`.
6. Then `/intelligence` + MCPJungle, per `REMEDIATION.md` §3.

## Known open issues

- **Open review findings, deliberately deferred:** `billing.py` puts uncatalogued tests in
  `patient_payable_items` but excludes them from the total (a non-empty payable list beside
  "SGD 0"); `clinic_seed._document_date` parses document dates with the **DOB** pivot, so a
  two-digit future year resolves to 1926 (latent — all fixtures print 4-digit years);
  `brain_server` tests package membership globally rather than per-package; a missing
  `candidate_count` renders "1 of None candidate(s)" and makes `eligibility` emit the *stronger*
  unqualified wording; ledger reads bypass their lock; the retrieval canonical-id override cannot
  fire for ids containing `_`; `_LazyPrompt` is dead code.
- **`analytics.py` is still unreferenced** (T-43) and raises `TypeError` on a naive `as_of`.
- **Suggested questions may be unanswerable by this graph** (T-09). Change the questions, not the
  graph.
- **`/ask` still serves old DataBridge SQL questions** (T-21) — deliberately left OUT of the
  restored nav for that reason.
- **§6.1 history fixtures do not exist**, blocking §6.3/§6.4 and the analytics trend.
- `SUBMISSION.md` quoted "1,320 Python tests" against a real 1,430; now a guarded floor.

## Accepted limitations (say these, don't hide them)

- **No authentication behind `staff_id`.** The sign-in screen states this on its face.
- The ledger defaults to `:memory:`; route state is process-local. A POC property.
- Relation extraction cannot detect negation. `sources_for()` is keyed by entity, not version.
- **No OCR, deliberately** — `/intake` promises we never read identity documents or e-cards, and an
  image path invites exactly that (`PLAN.md` open question 3).
- Claim drafts do **not** re-evaluate eligibility; they say so in a caveat rather than implying it
  was checked.

## The ones worth never re-learning

1. **Patients could be auto-merged in the graph.** The registry holds 3 Loh Wei Mings.
   `NEVER_FUZZY_MERGE = {PATIENT}`. Never add name-similarity merging back.
2. **A refusal that had already written** — `is_ready_to_commit()` stays true after a commit.
3. **An attestation outliving its subject** — twice, in two components. Key it to the patient id.
4. **A single newline defeated the entire guardrail** — `.` does not cross a line break.
5. **The queue said "verified in person"** — in green, with a tick. The record is that a named
   person *said* they checked.
6. **A wrong value with a valid citation is worse than an empty box.**
7. **A guard whose failure mode is silence gets reported as coverage.** Three scans this session
   could not fail; one was written the same day.
8. **A constant written against the old seed still compiles against the new one.** Three of four
   `/brain` defects were this. Derive constants from the data, or assert them against it.

## Gotchas

- **Run the suite as `python -m pytest -m "not integration"`.** Without the filter `packages/evals`
  hard-crashes the interpreter and looks exactly like a hang.
- **A green suite proves nothing about whether the app starts.** Run the server.
- **`DATABRIDGE_FORCE_GOLD=1` pins the deterministic backend**, set in `conftest.py` for pytest and
  in `playwright.config.ts` for e2e. It lives inside `detect_llm()` because the first version
  guarded only the intake path and the e2e run then made real `gpt-4o` calls mid-suite.
  `reuseExistingServer` means a gateway you started yourself is NOT covered — stop it first.
- **`codex exec` needs prompt AND sources in ONE stdin stream.** Capture to a file; never `tail`.
- **Do not edit under a running reviewer.**
- **Never write regexes or source through a bash heredoc** — escaping is mangled. Use Write/Edit.
- Test module basenames must be globally unique. Windows console needs
  `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
