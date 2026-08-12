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

### 2026-08-12 (later) — the tree is committed, and Shape A landed in the graph

**§4 item 1 done.** 14 commits on `prototype`, working tree clean. Grouped by area — docs,
competition data, the Acme demo retirement, then one commit per package. A diff-based reviewer can
now see `packages/intake/`, `packages/agent/`, `components/frontdesk/` and `e2e/`.

**Found while committing: `packages/agent` was in neither `pythonpath` nor `testpaths`, so its
133 tests had never run** — `test_injection.py` and `test_no_send_capability.py` among them, the
two standing behind constraint 1 and the read/send capability boundary. They pass. But "1605
passed" had been reported as covering the agent for as long as the package has existed. That is
lesson 7 again: a guard whose failure mode is silence gets reported as coverage. `serve.py` has
its own path list and a test that pins the two together, which caught the second half of this.

**§4 item 2, the graph half — R-01, R-03, R-06, R-27 fixed** (`cb9a622`). Fixed at the schema
rather than at each renderer:

- `GraphNode`/`GraphEdge` carry `attr_sources` and **refuse construction** when an attribute has a
  value and no pointer of its own. A `None` value needs none and is named by `unresolved`; a
  pointer for an absent value is refused too, since a citation beside "not found" reads as though
  the absence had been established.
- The `sources` table gains `field` **and `version_pk`**. It was keyed by entity id alone, so a
  superseded value's citation rendered beside its replacement — a pointer resolving to a span that
  no longer says what the value says, which is exactly the kind that survives a spot check.
- Node sheets: values render beside their own pointer, ungrounded ones move to *Unresolved*, each
  edge renders its own evidence, and the heading claiming "every value above is traceable to one of
  these" is gone. It was false.
- All foreign text goes through one encoder. The old escaper handled ``[]()*_` `` and not newlines
  or raw HTML, so a label with a blank line and `<!--` could comment out the identity caveat. The
  caveat is now a separate trusted block emitted before any untrusted string.
- `as_of` rewinds the whole sheet. It filtered edges only, so a replay showed today's labels.

**1758 passed** (1605 + the 133 that never ran + 20 new), 5 deselected.

## Now

Review gate running on `cb9a622`: `/code-review high` and one scoped `codex exec`, launched
together. **Do not start the next section until both land.**

## Next — work `REMEDIATION.md` §4 top-down

1. ~~Commit in coherent chunks.~~ **Done.**
2. **Shape A — the rest.** R-18 (`billing.py`: `billed_to`, `tpa_claim_required`, the total and
   every `_line()` field behind one `source_field` string) and R-21 (`/intelligence` returns values
   after catching a pointer-lookup failure and setting `sources = {}`). Do R-18 **together with
   R-16**, which is the same file and the same shape pointing the other way: an all-uncatalogued
   document shows **SGD 0** beside a non-empty `unknown_coverage_items`. R-09 belongs with the
   agent work below — same file.
3. **Shape B — bind an attestation to its subject** (R-17, R-22). Six instances so far, four fixed.
4. **Shape C — guards before the guarded action** (R-12, R-19), then **R-13** (checkbox fail-closed
   — a declined test can currently be billed).
5. **Shape E — typed agent output** (R-07…R-11). Unblocks the real agent behind `/intelligence`.
6. Then `/intelligence` + MCPJungle, per `REMEDIATION.md` §3.

**Sequencing note:** R-01, R-03 and R-06 were fixed in one pass rather than three, because all
three live in `node_sheet.py`. The ownership rule exists to stop two tracks editing one file
concurrently, not to force three visits to the same file.

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
