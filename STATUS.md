# STATUS — Hack4Health Walnut

> Where the build stands. `PLAN.md` says *what* to build, `TASKS.md` says *what next*, this says
> *where we are*. Updated at the end of every section — see `CLAUDE.md` §Build workflow.

**Last updated:** 2026-08-13 · **Phases 0–7 built. The full registry is seeded (300 patients), the
identity caveat is keyed to the patient KIND on every surface including the model's prompt, the
harness actually sends its schema, and every surface has one-click trials.**

**Green:** `python -m pytest -m "not integration"` → **2474 passed, 2 skipped, 5 deselected** ·
`npx tsc --noEmit` clean · `npx playwright test` → 14 passed, 6 pre-existing `frontdesk.spec.ts`
failures when run against a **live-model** gateway (the documented `reuseExistingServer` divergence
— see Gotchas; they pass on the gold path).

---

## Done

- **Phases 0–5** — fixtures, schemas, extraction + grounding wall, resolution (candidates only),
  config-driven eligibility, questionnaire pre-fill, append-only ledger with SQLite triggers,
  billing draft, metrics. **Measured: baseline 27.5 min · mean saved 16.3 min (59%) · ~11 h per
  40-patient morning.** (16.3, not the 16.4 several docs carried: `test_submission_figures.py`
  recomputes it from the corpus and pins it to `SUBMISSION.md`, so that is the checkable one.)
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
the Critical findings across all six.** Fixed since: `intake_confirm` read `held` outside the lock
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

### 2026-08-12 (later still) — the review gate on that fix found 13 more

`cb9a622` was reviewed by both. **codex: 11. `/code-review high`: 4 more.** They overlapped on
almost nothing. Nine fixed in `cb117b9`, four carried forward as R-30…R-33 in `REMEDIATION.md`
§2.7 — including **R-33, which blocks §3.1**: `_text()` is an HTML/markdown encoder, not a
prompt-injection boundary, and the model-facing consumer must delimit untrusted evidence itself.

The lesson worth keeping: **I made version scoping opt-in and three call sites did not opt in**,
one of them eight lines below a comment claiming the defect was fixed. A safety property that has
to be requested will be missed. Scoping is now the default and pooling must be named.

**1770 passed.**

### 2026-08-12 (evening) — Shape A is closed, and the billing round was reviewed too

**R-18 + R-16 together** (`22d48e9`), then **R-21** (`9b2e9dd`), then a scoped codex pass on both
which found **5 more, all real** (`fa7e991`):

- An unresolved COVERAGE still printed a confident amount. The completeness check looked only at
  the price side, so a merely-PARTIAL determination produced a figure with the reason in prose
  beside it. R-16 again by a different route — **the number is what gets read**.
- A citation that contradicted its own value: `in_package` cited `tests_from_document`, so the
  line emitted `false` next to a YAML cell reading `true`. The pointer belonged to a different
  claim, which now has its own name (`covered_by_rule`).
- `canonical` cited the document span — "the letter says FBC" offered as evidence for "FBC means
  full_blood_count".
- The totals had no provenance at all, on the artefact where the number is the point.
- `/intelligence/subgraph` still failed open on an empty pointer read, and **its test was vacuous**
  — it asserted only inside a branch `GraphNode`'s own validator makes unreachable.

**Shape A is now closed everywhere except R-09**, which lives in `packages/agent` and goes with
Shape E.

**1775 passed.**

### 2026-08-12 (night) — Shape B, Shape E, R-34, and MCP is REAL

Four tracks landed, three of them run in parallel by file ownership (the rule that makes that
safe: disjoint files, never "different features").

- **Shape B** (`8901341`) — R-17/R-22. Attestations bind to a `subject_fingerprint` over document
  revision + selected patient + **sorted** candidate ids. The pre-existing
  `confirmation.intake_id != record.intake_id` check is kept with a comment explaining it could
  never have caught this: `intake_id` is `sha256(document_id + text sha)` and is identical on both
  sides of a re-extraction. **A guard that looks like the guard you need is worse than none.**
- **R-34** (`f41a689`, `0b4ba7f`) — the MCP server is two processes with separate registries,
  sealed against a capability manifest at construction. Read-ness is which process a tool lives
  in, not an `effect: "read"` label on it.
- **Shape E + R-33** (`5c402f2`) — the model can no longer author facts or identity. It returns
  references; code resolves them to stored values and their own pointers. `AgentAnswer.answer` is
  a property with `extra="forbid"`, so uncited prose is unrepresentable rather than merely
  screened. 133 → 229 tests in that package.
- **Real MCP** — a self-hosted MCPJungle aggregator fronts the reader server. `brain_query` was
  invoked **through the aggregator** and returned real graph data with resolving pointers;
  `brain_ingest` is unreachable ("Unknown tool"). See `docs/hackathon/MCP_RUNBOOK.md` for the
  executed commands and the four failure modes hit on the way.

**1938 passed, 5 deselected.**

### 2026-08-13 — the agent does things now, and the MCPs are real connections

The build moved from "an agent that reports" to **an agent that acts**, and from "an MCP server
exists" to **an aggregator you can connect anything to**.

**Real MCP, proven end to end.** A self-hosted MCPJungle aggregator fronts our read-only clinic
server; `brain_query` was invoked *through it* against real clinic data, and `brain_ingest` is
unreachable from it ("Unknown tool"). Then the harder proof: a **third-party** MCP server
(`@modelcontextprotocol/server-memory`) was bridged from stdio to streamable HTTP, registered, and
invoked — so this is a general connector, not a demo wired to one endpoint.

**The trap worth knowing:** most published MCP servers are stdio, and an aggregator in Docker
cannot spawn a process on the host. The bridge is one line
(`npx -y supergateway --stdio "<cmd>" --outputTransport streamableHttp --port N`), and the failure
without it looks like a protocol fault rather than a transport mismatch. `MCP_RUNBOOK.md` §4 records
that and the three other failure modes hit on the way — including the MCP SDK's DNS-rebinding
protection returning **421** for `host.docker.internal`, which is invisible from the host because
local curl succeeds.

**The capability model is written down** (`AGENT_CAPABILITIES.md`) and it is the design everything
follows: the agent may **propose anything and perform nothing**. Tier 1 reads run immediately;
Tier 2 writes a draft to an append-only outbox; Tier 3 is a **model-free dispatcher** that executes
approved rows through act-capable servers the agent's aggregator does not front. An instruction
injected into a referral letter can at worst produce a draft a human reads.

**`/intelligence` was naming the people it counted.** The review found the citation drill-down
reached named, scored candidates one click from an answer. Working it found worse: `GET
/intelligence` published patient names with **no click at all** — `conflicts.members` are patient
node ids and `entities` carried their labels. Both closed structurally: a dedicated traversal built
from allow-lists that default shut (a *filter* would expose every node kind added later), no
`attrs` emitted at all, and labels gated on **provenance** — a label prints only if every pointer
grounding it is a cell in `coverage_rules.yaml`, which catches the employer name and the clinic
location as well as the filename.

**OCR is in the UI, and R-13 was fixed in the same change.** R-13 was deferred on exactly one
ground — no UI reached OCR — so adding the button removed the ground. The rule: *a checkbox-derived
field may be grounded only from characters that were **read**, not **recognised***. Origin proves
completeness; no OCR output can. Verified against a real scan where the engine genuinely drops every
glyph. **Cost, stated in `POC_SCOPE.md` §2a:** a scanned document yields no package code, name,
requested tests or add-ons — a scan alone cannot drive the coverage determination.

**2131 passed.**

### 2026-08-13 (late) — the registry is fully seeded, and that exposed one bug in four places

Three tracks ran in parallel by file ownership (graph/MCP · `TESTING.md` · frontend chips), then the
lead worked what they surfaced. **The whole registry is now seeded — 300 patients, `graph_nodes`
69 → 358** — with `registry_patients="referenced"` kept and tested so "the graph holds this
morning's paperwork and nothing else" stays a reachable state. No `FOR_PATIENT` edge is created for
an on-file patient, asserted by an edge-set-identical test across both options. Seed cost: **+25–35
ms**, measured.

**The identity caveat was implemented on the EDGE PATH in four separate components**, and seeding
289 edge-less patients made all four visible at once. It was invisible before because every seeded
patient happened to have a document naming them:

| Where | What it did |
|---|---|
| `query.nl_query` | caveat emitted inside the `FOR_PATIENT` loop → 289 patients answered with a bare name + DOB |
| `node_sheet` | caveat block gated on `if claims:` — and these sheets go into the **model's** context |
| `brain_server.node_detail` | no caveat at all; and `brain_neighbors`/`brain_path`/`graph()` return patient nodes that never pass through it |
| `gateway/llm.build_context` | same `if claims:` gate, on the prompt itself — **the worst one**, because it is the branch that runs whenever a key is present |

Fixed at the KIND, not at each renderer: new `packages/graph/databridge_graph/identity.py` holds
the caveat wording and the same-label rule; `brain_server` and `nl_query` both delegate to it;
`node_to_dict` carries it so every tool that returns a patient gets it.

**A name also resolved to one of several people.** The registry holds three "Loh Wei Ming", four
"Rahman Zhi Hao", four "Tan Mei Ling". Both `/brain/query` branches now refuse and return the
candidate ids — and `_named_outright` makes asking again with an id work, so the refusal is a fork
rather than a wall. The LLM branch was the important half: `top_k=8` put all three in one prompt
and let the model pick.

**D-1 — the product did not work with the model it advertised.** `caps_for("openai/*")
.guided_decoding` is `False`, so L3 was skipped and `backends.generate` sent only
`response_format={"type":"json_object"}` — **the schema never reached the prompt.** gpt-4o returned
`patient_name`/`patient_id`/`patient_dob` against a schema wanting `claimed_name`/
`claimed_id_number`/`claimed_dob`; Pydantic ignores unknown keys, so `IntakeDraft` validated
cleanly with **every field `None`** — no `ValidationError`, so no L2 retry, so no error anywhere.
All ten chits extracted to a blank card while `/health` reported a live model. L1 now puts the
schema in the prompt (`client.schema_instruction`). Also: `seed` was sent regardless of
`Caps.seed`, so an Anthropic-only key raised `UnsupportedParamsError` and the gateway silently
degraded to gold.

**The identity screen was measurably too narrow** — nine phrasings probed, five missed.
*"Validate the insurance e-card"* walked through while *"is this e-card valid?"* was refused,
because every pattern required a validity word trailing the document. New rule: a verification
verb aimed at an identity document, with the transcription carve-out kept.

**Also:** "let the patient know…" reached no send pattern (they all wanted a sending verb), so the
console routed a request to contact a patient to the READ path and answered `NO_GROUNDED_ANSWER`;
one-click trial chips on `/intelligence`, `/evidence`, `/intake`, `/connectors`; `TESTING.md`
rewritten as **66 trials, 63 executed**; OCR builder 2 → 5 chits × 3 shapes (its new page guard
found chit_07/08 render to two pages, so their "scan" would have shipped half a document);
`SCRIPT.md` (demo video) and `COPILOT_TRANSITION_GUIDE.md` written.

## Now

Nothing in flight. Working tree is committed; the branch is ahead of `origin/hack4health_v1.0`.

## Next

1. **Re-verify the pipeline against a real key** now that the schema reaches the prompt. D-1 was
   found by uploading all ten chits to a live gateway and reading `candidates: 0` — repeat exactly
   that, and treat an all-`None` draft as an alarm rather than a pass.
2. Record the demo video from `SCRIPT.md`.
3. The gateway would need `GET /<surface>/suggested` to serve the trial chips from config the way
   `/brain/suggested` already does — the intake fixture names and evidence entity ids in
   `try-prompts.ts` are duplicating server-side facts today.
4. Carried findings R-30…R-33 in `REMEDIATION.md` §2.7.

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
9. **A caveat attached to a RELATIONSHIP disappears when the relationship does.** The identity
   caveat rode on the `FOR_PATIENT` edge in four components and looked correct for weeks, because
   every seeded patient happened to have one. Attach a guarantee to the KIND, never to a branch.
10. **A guarantee implemented on one branch is gone the moment the other branch runs.** `nl_query`
    refused ambiguous names; the LLM branch beside it did not — and the LLM branch is the one that
    runs when a key is present, i.e. in every demo. Fix both, or fix it below both.
11. **"Valid JSON" is not "the right fields".** `response_format: json_object` guarantees syntax
    and nothing else; the model renamed every field, Pydantic dropped the unknowns, and an
    all-`None` object validated cleanly. **An all-null parse must be an alarm, not a pass.**
12. **Changing what a message list contains breaks whoever was reading position `-1`.** The gold
    backend dispatched on the last message; inserting the schema after it silently emptied every
    extraction in the offline suite. 215 failures from one appended list element.

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
