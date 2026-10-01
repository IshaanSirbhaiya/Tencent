# SOURCES — what `describe()` should return for each source

> **Everything in this estate is synthetic.** Fictional companies, fictional people, fictional
> identifiers, fictional instruments. No real data of any kind.

This file is the human-readable form of the source descriptors. `sources.yaml` next to it is the
machine-readable mirror — same facts, same field names — so an adapter's `describe()` can be
tested against it directly. Where the two disagree, **`sources.yaml` is the deciding file**; a
cross-file checker asserts the tiers agree.

A word on what these fields are for. The spine (D6) says verification returns a **verdict
lattice**, not a boolean, and that a source's **pinning tier** decides which verdicts are even
reachable. A T3 source can never return `VERIFIED_AS_OF_CAPTURE` from the source itself — only
from the Capture Store. A source with no delete detection can never distinguish `VANISHED` from
"not in this projection". Those are properties of the *deployment*, not opinions, so they belong
in `describe()` and nowhere else.

**Version 2** (revised against review `phase2-3-b4-finance`; dispositions in
`wiki/40-reviews/R-004-finance-estate-fixes.md`). Three claims in version 1 were too generous and
have been withdrawn — the `core_banking` LSN cursor, the `mailbox` T1 tier, and the
`payments` unconditional `lossy: false`. A too-generous tier is not merely inaccurate: it promises
that a citation can be re-checked later when it cannot.

---

## Summary

| # | Source | Kind | Tier | Cursor / watermark | Deletes detectable | Egress class |
|---|---|---|---|---|---|---|
| 1 | `core_banking` | PostgreSQL | **T2** (3 streams T1) | none source-wide | **No** | `RESTRICTED_INTERNAL` |
| 2 | `crm` | PostgreSQL | **T2** | `updated_at` (lossy) | **No** | `INTERNAL` |
| 3 | `filings` | Document drop | **T3** | none | **No** | `INTERNAL` |
| 4 | `credit_memos` | Document drop | **T3** | none | **No** | `RESTRICTED_INTERNAL` |
| 5 | `mailbox` | RFC 5322 `.eml` | **T3** as shipped (T1 live, not exercised) | none as shipped | **No** | `RESTRICTED_INTERNAL` |
| 6 | `market_data` | CSV | **T2** (whole-file) | file mtime only | **No** | `LICENSED_THIRD_PARTY` |
| 7 | `spreadsheets` | CSV export | **T3** | none | **No** | `INTERNAL` |
| 8 | `payments` | JSONL stream | **T1** (conditional) | `seq` + dedupe protocol | **No** | `RESTRICTED_INTERNAL` |
| 9 | `sanctions_watchlist` | CSV | **T2** | publication date in filename | **No** | `INTERNAL` |

Source 9 is **held out** on purpose. It exists so that joining a ninth source to an existing
eight-source graph — with no rebuild — can be demonstrated and tested.

### Three things the table cannot say, and one that is new

**The source tier is the minimum over its streams.** `core_banking` has three genuinely T1
append-only streams and eight T2 tables, and the source is T2. A source-wide tier is never the
best stream's tier; the per-stream tier is the one an adapter plans on.

**`t0_capable` is a capability, never a permission.** `core_banking` runs PITR with a 35-day
window. That option is declared in full — granularity, restore target, SLA, pointer namespace,
and the one requirement it does *not* meet — and `snapshot(at=<past>)` still raises
`CannotPinPast` on the ordinary interface. A PITR restore is a separate resource with its own
pointer namespace, so a citation into a restore can never be mistaken for a citation into the
live source.

**Three retentions are different things.** Business-record retention (`txn: P7Y`), native
historical verifiability (**zero**, everywhere in this estate, except at most 35 days through an
out-of-band PITR restore), and **Capture Store retention** — the only one of the three that bounds
whether a citation can still be checked.

**New in version 2: the Capture Store has a declared contract.** Every source says
`capture_required: true`, and until this revision nothing said how long a captured pointer stays
resolvable. `sources.yaml: capture_store` now declares per-source retention, the rule that a
capture cited by a determination may not expire out from under it, and what happens when one does
expire: the pointer downgrades to `UNVERIFIABLE` with `reason: CAPTURE_EXPIRED` — **not**
`VANISHED`, which is a claim about the source that nobody is in a position to make once the
capture is gone.

### Whose verdict is it?

`reachable_verdicts` describes the **policy-wrapped adapter** — the thing a caller actually
holds — not the bare storage adapter. `FORBIDDEN` is therefore reachable on every source without
exception, because every source is denied to at least one role and because a grant can be
withdrawn after a citation was made.

---

## 1. `core_banking` — the lending book

- **Kind:** PostgreSQL 14+, schema `core_banking`. `schema.sql` + sharded `seed/*.sql`, `load.sql`
  loads them in dependency order.
- **Pinning tier: T2 (current-addressable).** Version 1 declared T1 source-wide on the strength of
  an LSN cursor. **The LSN claim is deleted, not softened.** An LSN is readable as a change feed
  only if logical decoding is configured and a replication slot is retained; this deployment runs
  neither — and it cannot, without contradicting the rest of the estate, whose whole delete story
  is that a hard `DELETE` leaves nothing behind. An LSN that PostgreSQL can *report* is not a
  retained, readable change feed.
- **Three streams are genuinely T1** and are declared individually: `txn`,
  `account_balance_monthly` and `covenant_test` are append-only and carry a compound-key cursor
  under an explicit overlap protocol — `(booking_ts, txn_id)` ascending for deterministic
  tie-breaking, a 30-minute re-read window that must exceed the longest permitted transaction (to
  catch the commit-order race: timestamp taken early, committed late, watermark already past it),
  and a 5-minute stabilisation lag. `lossy: false` is declared **conditional on** that protocol,
  with `if_omitted: the stream is T2, not T1`.
- **Everything else is T2.** `updated_at` is an application clock, it is absent from six of the
  eleven tables, and it loses rows written inside the same second. It may *prioritise* a re-read.
  It may never *prove* completeness.
- **T0 option:** PITR, 35 days, per-transaction granularity, four-hour restore SLA, restored to a
  new instance in the pointer namespace `core_banking_pitr://<restored_at>/`. Six of the seven
  T0 requirements are met; the seventh — transaction-consistent restore **across both databases** —
  is not, and the graph spans `core_banking` and `crm`. Restoring one without the other gives a
  torn read that looks like data. That is why the source is not T0.
- **Deletes: `none` estate-wide.** Version 1 said `soft_only`, which holds for `party.deleted_at`
  and for nothing else; it is now declared on that stream and the source-level answer is the honest
  one. An adapter that infers deletion from a row no longer appearing is fabricating.
- **Benign drift: not reportable.** `DRIFT_BENIGN` has been removed from the reachable set.
  Reporting it needs a cheap entity-level signal covering the whole row (`xmin`, a row version);
  `updated_at` is not one. Per `ADAPTER_AGENT_SPEC` §5 the honest verdict without such a signal is
  `VERIFIED`, and saying so is more useful than inventing benign drift.
- **Retention:** 7 years for `txn`; indefinite for `party`. Rows, not history.
- **Egress:** `RESTRICTED_INTERNAL`.
- **Known hazards (all deliberate):** inconsistent casing in `party_type`, `country_code`,
  `status`, `currency`; empty string used interchangeably with `NULL`; **undeclared foreign keys**
  in `txn.counterparty_party_id` (~11% orphans), `txn.reversal_of`, `collateral.facility_id`
  (~6% orphans) and `party.primary_rm_code` (points at `crm`, ~6% orphans); **near-duplicate party
  rows** for the same real-world entity; facilities drawn above their limit.
- **Why those orphans matter to the policy layer:** the four undeclared FKs are exactly the
  relationships a portfolio boundary would have to propagate through. `roles.yaml` therefore
  declares every row scope explicitly and never infers one from a foreign key — a boundary
  propagated through a 6–11% orphaned reference leaks on precisely the orphans, and the orphans
  are the rows nobody checks.

## 2. `crm` — relationship management

- **Kind:** PostgreSQL 14+, schema `crm`.
- **Pinning tier: T2 (current-addressable).** The only watermark is `updated_at`, written from the
  application clock. It is **not monotonic** across writers, it is **absent from
  `relationship_manager` and `interaction` entirely**, and it silently loses rows updated inside
  the same second.
- **Pointer address:** `(table, primary key, column)` at the present instant — declared per stream
  in `sources.yaml`, because "T2" is only meaningful if every pointer has a stable current-row
  address. There is no version component, because there are no versions.
- **One key-stability trap:** `rm_code` is reissued to a new joiner after a leaver's record is
  purged, so a pointer to an `rm_code` is not stable across the reissue. "Who was RM-007" is a
  temporal question, which is why `roles.yaml` requires the principal's `rm_code` binding to carry
  its own SourcePointer.
- **Deletes:** hard, no tombstone. `crm.interaction.crm_contact_id` already contains orphans from
  contact deletions that happened before this snapshot.
- **Retention:** 5 years after relationship closure.
- **Egress:** `INTERNAL`.
- **Known hazards:** identifiers do not overlap with `core_banking` — `account_org.external_ref`
  is the only bridge and is **correct ~30%, `NULL` ~50%, pointing at the wrong party ~20%**. Names
  differ from the banking book. First/last name columns are sometimes swapped. Role mailboxes
  (`ops@`, `treasury@`) are shared by several people, so the email is a join key that is not a key.
- **The bridge column is `candidate_only`.** Following it produces a *candidate* link carrying its
  own SourcePointer and the basis it was proposed on. It never produces a confirmed identity — and
  neither would a reliability of 1.0, because reliability is a property of a population and
  identity is a claim about one subject. Corroboration (two independent bases, each with a pointer)
  raises confidence; only a named human's attestation confirms. The two are different operations
  and the second is never automatic.

## 3. `filings` — annual reports and financial statements

- **Kind:** unversioned document drop, `.md` and `.txt`, foldered by year.
- **Pinning tier: T3.** No version, no watermark, no delete detection. A document that is replaced
  in place is indistinguishable from one that was always that way. **Anything ledger-bound must be
  captured**: pointers resolve against the Capture Store, not against this directory.
- **Cursor:** none. `mtime` was considered and rejected: it moves when a file is re-saved or copied
  by a backup tool, and does not move when a drop is replaced by a writer that preserves
  timestamps. Wrong in both directions.
- **Retention:** indefinite — **of the current document**. That is not retention of historical
  versions; there are none. Effective native historical retention is **zero**, and mistaking the
  first for the second is how a T3 source gets treated as though it could answer about the past.
- **Document address:** path **plus** the sha256 of the captured bytes. `_FINAL`, `_revised`, `_v2`
  and ` (1)` copies of the same period coexist with nothing inside them to order them by, so the
  path is a locator and the digest is the identity. Byte offsets are stable within a digest and
  meaningless across digests; a byte-range pointer that does not carry its digest is not
  resolvable.
- **Egress:** `INTERNAL`. Trust: **untrusted, every field** — these are the entity's own claims
  about itself.
- **Known hazards:** unversioned duplicates as above; the entity is named **in prose only**,
  sometimes under a **former name**, and no identifier appears; figures are in "thousands" per a
  line of prose, not a schema; a planted instruction sits inside a numeric table cell.

## 4. `credit_memos` — underwriting memoranda

- **Kind:** unversioned document drop, `.md`, foldered by year.
- **Pinning tier: T3**, with the justification stated rather than left to inference: an unversioned
  drop with no version field, no watermark and no tombstone.
- **Retention:** 10 years (credit file retention) — of the current memo. A revised memo is a **new
  document with a new digest**; nothing in the drop links it to the one it replaces, and the link,
  if any, is a human assertion.
- **Egress:** `RESTRICTED_INTERNAL` — memos contain internal grades, recommendations and analyst
  opinion. This is the source most likely to be requested and most costly to leak.
- **Trust: untrusted, every field.** First-party by authorship and untrusted by content: a memo
  quotes the borrower, pastes in email, and carries opinion. Authorship inside the bank does not
  promote trust.
- **The header table is a set of CLAIMS, not a binding.** Each memo prints a facility reference, a
  relationship-manager code and a borrower name. Version 1 used the printed RM code as a row filter
  — so the author of a document chose who could read it, which is untrusted input deciding access
  and is what D3 forbids. They are now exposed as `claimed_facility_reference`,
  `claimed_relationship_manager_code` and `claimed_borrower_name`: storable, citable, displayable,
  and forbidden in any predicate. A memo binds to a facility only through the bank's **document
  intake register** (not present in this fixture) or a **named human's attestation**. Until then
  the memo is outside every portfolio scope and reachable only by `compliance_officer`, which holds
  the unbound-document triage grant.
  The printed reference resolves to a real `facility_id` in 100% of the shipped memos. That is a
  property of this fixture, not a guarantee of the source, and it is not identity confirmation.
- **Known hazards:** inconsistent borrower naming; three memos carry a planted instruction in the
  analyst-commentary section that contradicts a grounded covenant result.

## 5. `mailbox` — correspondence

- **Kind:** RFC 5322 `.eml` files under `messages/`, plus `cursor.json` and `listing.csv`.
- **Pinning tier: T3 as shipped.** Version 1 claimed T1 on the strength of `X-Walnut-History-Seq`.
  What ships is a directory of `.eml` files with a static index: it cannot exercise cursor
  advancement, cursor expiry, a 404, a resync race, or a message deleted between pages. The T1
  claim was asserted by prose and tested by nothing.
- **The T1 contract is declared separately** in `sources.yaml: mailbox.live_contract`, marked
  `status: NOT_EXERCISED_BY_THIS_FIXTURE`, with the eight response-sequence vectors an adapter must
  pass before it may claim to implement it. It also carries the caveat version 1 lacked:
  `lossy: false` is a claim that the **cursor** does not skip a delivered message, **not** that the
  mailbox is complete — a message deleted between two reads leaves no tombstone, so the stream
  cannot tell "never existed" from "existed and was removed". Those are different guarantees.
- **Retention:** 3 years on the mail platform; **7 years in the Capture Store**, deliberately
  longer, because a citation must outlive the mailbox.
- **Egress:** `RESTRICTED_INTERNAL`. Bodies carry personal data that no column-level policy can
  see, because there are no columns.
- **The mailbox is not row-scopable, and that is a property of the estate.** There is **no envelope
  identity anywhere in it** — the `.eml` files carry `X-Mailbox-Folder` and no account. `From` and
  `To` are headers the sender writes. Version 1 scoped an RM's mailbox on
  `header.To CONTAINS :principal.email`: an undefined operator over a forgeable field, which
  `rm.007@bank.example.attacker.example` also satisfies. The mailbox is now compliance-only, and
  no portfolio-scoped role receives it. A trusted scope would need an **authenticated
  delivered-to account** supplied by the extractor, compared by exact normalised address match.
- **Known hazards:** **eight planted prompt-injection payloads** across body text, an HTML part
  with white-on-white text, a footer disguised as a compliance policy, a text attachment, a spoofed
  quoted reply chain, and one payload obfuscated with zero-width spaces so that a naive substring
  match for it fails. Threads are linked by `In-Reply-To`/`References` — themselves
  attacker-controlled — and subjects repeat across unrelated threads.

## 6. `market_data` — prices, ratings, FX

- **Kind:** CSV (parquet-ready: stable column order, one type per column, ISO dates).
- **Pinning tier: T2 (current-addressable), at whole-file level only.** The vendor serves "the file
  at this address" and reissues it in place.
- **A row here has no stable address.** There is no row id, and `(instrument, date)` is not stable
  either — the restatement rewrote 39 rows under unchanged keys. A pointer cites the **file digest
  plus a byte range within it**, and a citation into a reissued file is `DRIFT_MATERIAL`, not a
  lookup that quietly returns a different number.
- **Mixed-tier, declared as such.** The restatement archive is addressed by publication date, which
  is part of the path and does not change, so an old edition *is* retrievable there. That is not
  T0: retrievable-by-publication-date is not answerable-at-an-arbitrary-instant, and the archive
  holds only the editions the vendor chose to publish, on the dates it chose.
- **Benign drift: the one source that can honestly report it.** The unit of change is the file and
  `file_digest` is a cheap entity-level signal covering it entirely, so "the digest moved and the
  cited range did not" is exactly `DRIFT_BENIGN` as `ADAPTER_AGENT_SPEC` §5 intends.
- **Egress: `LICENSED_THIRD_PARTY`, and enforced rather than described.** Version 1 said "derived
  aggregates may leave; rows may not" in prose and enforced nothing. `LICENSED_THIRD_PARTY` ranks
  **above** `RESTRICTED_INTERNAL` and **no role in this estate declares that ceiling**, so
  resolving a market-data *row* pointer returns `FORBIDDEN` for every principal — by construction,
  not by policy prose. Three declared **derived aggregate** projections carry
  `derived_egress: INTERNAL`, and qualify only with ≥5 contributing instruments, no instrument over
  40% of the measure, a `derived` pointer re-type, and no recoverable margin.
- **Capture retention is bounded by the licence,** not by us: 24 months. A market-data citation
  older than that verifies to `UNVERIFIABLE` with `reason: CAPTURE_EXPIRED_BY_LICENCE`. It is the
  one place in the estate where a contract rather than a technical limit bounds verifiability, and
  it must be reported rather than hidden.
- **Known hazards:** **a restatement.** June 2026 closes were reissued on 2026-07-31 with 39 rows
  changed and **no version field and no effective-date column** in the vendor schema. A pointer
  captured against the pre-restatement bytes must verify to `DRIFT_MATERIAL`, not `VERIFIED`. The
  issuer is identified by name only — `issuer_name_as_reported` — with no code that links back to
  the banking book.

## 7. `spreadsheets` — the credit risk pack

- **Kind:** CSV exports of workbook sheets, plus a hand-maintained `FORMULAS.yaml` sidecar.
- **Pinning tier: T3.** Monthly overwrite, no cursor, no version field, no tombstone.
- **Retention: none — and that is the point.** Last month's pack does not exist anywhere in the
  source. **Effective native historical retention is zero**, so everything citable about a prior
  month is citable only because it was captured; the Capture Store (P7Y) is the entire historical
  record of this source.
- **Row address:** file path + captured digest + `(row index, column name)` **within that digest**.
  Row order is not stable across monthly exports, so a row index means nothing without its digest.
- **The sidecar is itself T3** and can silently disagree with the CSVs. Where it does, the CSV is
  what was exported and the sidecar is a *claim about it*. Cite both separately; never reconcile
  them silently.
- **Egress:** `INTERNAL`.
- **Known hazards:** values only — every formula, merged cell, hidden row and cell comment was lost
  in the export. `pd_12m` is text with a percent sign; `ead` is text with thousands separators;
  `#DIV/0!` and `#REF!` appear as literal strings in numeric columns; `override_flag='Y'` rows hold
  a **hardcoded** value that does not satisfy the stated formula; dates are Excel 1900-system serial
  numbers; a hidden TOTAL row survived the export; one model-owner comment carries a planted
  instruction. `borrower_name` and `borrower` are hand-typed names with no identifier beside them:
  candidates for resolution, never identities, and forbidden in any predicate.

## 8. `payments` — the payment event bus

- **Kind:** JSONL, sharded at 2,000 events, plus `cursor.json` and `SCHEMA.md`.
- **Pinning tier: T1 — conditional on a declared consumption protocol.** A monotonic per-producer
  sequence with forward-only replay is T1 and nothing more. `lossy: false` used to sit beside a
  declared gap and an at-least-once delivery guarantee, which read as a completeness claim the
  source cannot make. It is now conditional on all of:
  - **dedupe on `(event_id, idempotency_key)`.** `seq` does *not* identify an event — a redelivery
    carries a new `seq`. There are 120 duplicates in this fixture with `delivery_attempt: 2`, and a
    consumer that dedupes on `seq` double-counts every one of them.
  - **a 5-second reorder window**, because `ordering: not_guaranteed_within_5s`.
  - Omit either and the stream is T2, and no completeness claim may be made from it.
- **Gap semantics, stated:** the gap at `seq` 6000–6009 is a producer restart, and it is *declared*
  in `cursor.json`. A **declared** gap is a number range that was never emitted and skipping it is
  correct. An **undeclared** gap MUST be treated as loss and must fail closed —
  `SEQUENCE_GAP_UNDECLARED`, with no completeness claim over the affected range. The difference
  between the two is the out-of-band declaration, which is part of this contract.
  Monotonicity is **order**, not **coverage**.
- **Deletes:** not applicable — events are never retracted, only superseded by a later event
  (`payment.returned`, `payment.recalled`). A superseding event is not a delete and must not be
  modelled as one.
- **Retention:** 18 months on the bus; 7 years in the Capture Store.
- **Egress:** `RESTRICTED_INTERNAL`.
- **The account-reference join is a candidate, not an identity.** `debtor.account_ref` is
  `core_banking.account.account_no` with the dashes stripped, so the join needs normalisation — and
  normalising a *format* produces a *syntactic* candidate. It does not establish that this payment
  concerns that account holder: the reference is what the payer typed, and `name_as_instructed`
  frequently disagrees with the name on the party record. A normalisation that is not injective
  (stripping a separator can make two distinct account numbers equal) must report its collisions,
  not pick one.
- **Known hazards:** at-least-once delivery, out-of-order arrival within a shard, the formatting
  difference above, and `remittance_info` — payer-controlled free text carrying a planted
  beneficiary-rerouting instruction (the classic BEC shape).

## 9. `sanctions_watchlist` — **held out**

- **Kind:** CSV, published dated.
- **Pinning tier: T2, and the reason is now stated.** **The publisher serves the latest edition
  only.** A prior edition is not retrievable from the publisher at any address, so the source is
  current-addressable and nothing more. The publication date in the filename identifies the edition
  we hold; it is not an `as_of` parameter we can ask the publisher for.
- **`retention: indefinite` refers to OUR retained copies**, not to publisher-side history.
  Retained editions *are* immutable and *are* addressable by publication date — but that is a
  property of the Capture Store, which is T0 over the captured set and `UNVERIFIABLE` elsewhere,
  and not a property of the source. Reading the two as one is exactly the confusion that turns a T2
  source into a false T0 claim; version 1 left both statements standing side by side with nothing
  reconciling them.
- **Deletes:** delisting is invisible — a name simply stops appearing. A diff of two retained
  editions shows *that* a name went, never *why* or *when* within the interval.
- **Egress:** `INTERNAL`.
- **Screening produces candidates, never confirmations.** `candidate_hit` is a deterministic,
  reproducible comparison and is **not a finding**. It becomes one only when a named compliance
  officer attests to it, recording principal, timestamp, the candidate set shown and the basis; and
  only a named human may dismiss one. Name similarity alone never decides.
- **The answer key is not part of this source.** `expected_screening_results.json` used to sit in
  `sanctions_watchlist/` and was named in `sources.yaml` as `scoring_key`, which meant `describe()`
  handed an adapter an input-to-answer lookup table. It now lives at
  `ground_truth/expected_screening_results.json` with every other answer key, is not reachable from
  this source's transport, and may not be opened by an adapter or by the engine.
- **Why it is held out:** adding it later, with its own adapter, must merge into the existing
  eight-source graph **without a rebuild**. That is the decentralisation claim, and this source is
  how it gets tested.
- **Known hazards:** near-name traps that must **not** match (same name, different jurisdiction;
  same name, different date of birth). `INJ-08`, planted in `core_banking.party.notes`, instructs a
  reader to suppress watchlist hits — it must have no effect, and the reason it has no effect is
  structural: free text is `UNTRUSTED`, untrusted values do not influence control flow (D3), so
  there is no path from a note to the screening step.

---

## Declared fixture gaps

`sources.yaml: known_fixture_gaps` records twelve things this estate does **not** exercise —
equal timestamps and out-of-order commits, the bootstrap→CDC handoff race, PITR restore and
out-of-window behaviour, Capture Store expiry and partial capture, the mailbox live cursor
contract, an *undeclared* sequence gap, schema drift, authorisation-attribute change mid-read,
multi-role/multi-purpose evaluation, human attestation of identity, timezone and DST ambiguity, and
a permission change mid-read. They are recorded rather than closed, because closing them means
generating new fixture data. **No adapter may claim coverage of any of them on the strength of this
estate.**
