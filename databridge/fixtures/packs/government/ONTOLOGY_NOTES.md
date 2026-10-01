# ONTOLOGY NOTES -- the natural kinds in a municipal government estate

> **SYNTHETIC FIXTURE.** Generated estate; invented agency. Written to be the
> input to a **predefined ontology** for the `government` pack, because unlike
> the finance pack — where the agents discover the graph observably — the
> government graph is built from a declared node/edge library.

Two things this file is trying to get right, because both are expensive to
change once a graph exists:

1. **The kinds are the domain's, not the schema's.** `permits.applications` is
   a table; `Application` is a thing that exists whether or not PERMITWORKS
   does. Six of the eight sources describe the same handful of real-world
   objects in mutually incompatible ways, and an ontology that mirrors any one
   schema will fight the other seven.
2. **Identity is declared, never inferred.** Two node kinds here — `Person` and
   `Organisation` — are things the system may *never* auto-merge. Everything
   about how they are handled follows from saying so once, in the ontology,
   rather than in seven adapters.

---

## Node kinds

### Subject kinds (identity is attested, never computed)

| Kind | What it is | Identifiers seen in this estate | Rule |
|---|---|---|---|
| **`Person`** | a human being: applicant, claimant, appellant, business officer, correspondent | Aldermoor Resident Number (`AM-nnnn-nnnn-X`), Benefits Reference (`Bnnnnnnn`), applicant id, claimant id, legacy key, email address — **none of them universal, none of them reliable** | `never_fuzzy_merge: true`. Candidates only. A named human attests; `requires_human_selection` stays `Literal[True]`. |
| **`Organisation`** | a registered business, a contractor, an advocacy service, another agency | business registration number, name only (for correspondents) | `never_fuzzy_merge: true`. Officer records in the public registry carry **no identifier at all** — name and address are the whole basis for a match, which is precisely why merging must not be automatic. |

`Person` is deliberately one kind, not four. "Applicant", "claimant",
"appellant" and "officer of a company" are **roles a person plays in a
relationship**, not species of person — and modelling them as separate node
kinds is how an estate ends up unable to answer "is this the same human".
The role lives on the edge; see `APPLIED_FOR`, `CLAIMS_UNDER`, `OFFICER_OF`.

**This is the declaration `roles.yaml` binds to.** The pack's `subject_kinds`
are `Person` and `Organisation` — the two kinds whose identity may never be
auto-confirmed (spine D4b) — and nothing else. `applicant` and `claimant` are
**relationship roles**, listed in `roles.yaml: relationship_roles`, and they
describe *access*, not identity. An earlier version of `roles.yaml` declared
`applicant` and `claimant` as the subject kinds instead, which left appellants,
correspondents, company officers and legacy subjects outside the guardrail
entirely and left `Organisation` unprotected by policy despite being
`never_fuzzy_merge` here. The two files now agree, and this file is the one that
decides.

Note also what is *not* an identifier: `application_id` and `claim_id` identify
a **case**, not a person. One person holds many; one application can involve
several. And `permits.applicants.national_id_ref` is nullable and frequently
wrong — it looks like a key and is not one.

### Merge eligibility — candidate generation and automatic merge are different questions

`never_fuzzy_merge` was doing two jobs at once, and separating them is what lets
the resolver be useful without being dangerous. Every kind therefore declares
both:

- **fuzzy candidates** — may a similarity search *propose* that two nodes are
  the same, for a human to look at?
- **automatic merge** — may the system *act* on that proposal without a named
  human attesting?

The answer to the second is `no` for almost everything in this estate.

| Kind | Fuzzy candidates | Automatic merge | Join by | Why |
|---|:--:|:--:|---|---|
| `Person` | **yes** | **never** | attested `SAME_AS` | the whole point of the estate. Candidates are scored against `GROUND_TRUTH.md`; a named human attests. |
| `Organisation` | **yes** | **never** | attested `SAME_AS` | registry officer records carry no identifier at all; name and address are the entire basis for a match. |
| `Officer` | **no** | **never** | exact `officer_ref` / `caseworker_ref` | staff identity is administrative and *is* keyed. Fuzzy-merging two staff records would silently reassign a caseload and rewrite who decided what — and `ASSIGNED_TO` is an access predicate, so it would rewrite who may read it too. |
| `Parcel` | **no** | **never** | exact `parcel_id`, or a declared renumbering lineage | parcels are renumbered and merged. Two parcels with adjacent ids and similar geometry are *different land*, and similarity is exactly the wrong signal. |
| `AddressPoint` | **no** | **never** | exact source id | a surveyed fact. |
| `Address` | **yes**, onto `AddressPoint` only | **never** | `ADDRESSES` resolution, which may fail | this kind exists **to preserve** stale, abbreviated and mistyped recorded evidence. Merging two `Address` nodes destroys the signal that a person moved — which is a matching signal, not noise. Fuzzy matching an `Address` onto an `AddressPoint` is a different operation and is allowed, and its failure is informative. |
| `Application`, `Claim`, `Permit`, `Decision`, `Determination`, `Condition`, `Inspection`, `Appeal`, `Payment`, `Review`, `Licence`, `BusinessRegistration`, `CaseEvent` | **no** | **never** | exact source identity, or an explicit lineage edge (`SUPERSEDES_*`, `REVERSES`, `GRANTS`) | legal and transactional objects. Two payments of the same amount on the same day on the same claim are two payments. Similarity has no meaning here; the only legitimate link is one the source itself declares. |
| `Document`, `Message`, `Photo`, `LegacyRecord` | **no** | **never** | exact source identity + content digest | artefacts. A re-scan is a *new* observation of the same document, related by lineage and capture digest — never by resembling the old one. |
| `PermitType`, `BenefitProgram`, `ZoningCode`, `Agency`, `Statute` | **no** | **never** | exact code | closed reference vocabularies. A new code is a new code, and merging it into a similar one hides a statutory change. |

The rule underneath the table: **fuzzy candidate generation is a search
affordance; automatic merge is an assertion about the world.** Only the two
subject kinds get the first, and nothing at all gets the second.

### Case kinds (the things a government actually processes)

| Kind | What it is | Primary source | Notes |
|---|---|---|---|
| **`Application`** | a request for a permit, in progress or concluded | `permits.applications` | the spine of the permits graph |
| **`Permit`** | the instrument that exists once an application is granted | `permits.decisions` where `permit_number IS NOT NULL` | **not** the same node as the decision: a permit persists, can be revoked, and can outlive the case |
| **`Decision`** | a determination made on an application at an instant | `permits.decisions` | immutable; corrections are new nodes with `SUPERSEDES_DECISION` |
| **`Condition`** | an obligation attached to a permit | `permits.permit_conditions` | has its own lifecycle (`compliance_due`, `discharged_on`) |
| **`Inspection`** | an attended visit with an outcome | `permits.inspections` | may exist with no outcome (`NO_ACCESS`), which is itself a fact |
| **`Appeal`** | a challenge to a decision | `permits.appeals` | may be `sealed`, which is a policy state, not an absence |
| **`Claim`** | a request for assistance under a programme | `benefits.claims` | the spine of the benefits graph |
| **`Determination`** | an eligibility outcome at an instant | `benefits.eligibility_determinations` | immutable; `SUPERSEDES_DETERMINATION` chains |
| **`Payment`** | money moved on a claim | `benefits.payments` | reversals are new nodes pointing back, never updates |
| **`Review`** | a scheduled or triggered re-examination | `benefits.reviews` | |
| **`CaseEvent`** | something that happened on a case at a time | `permits.case_events`, `benefits.claim_events` | the highest-cardinality kind in the estate by an order of magnitude |

### Place kinds

| Kind | What it is | Primary source |
|---|---|---|
| **`Parcel`** | a unit of land with a boundary and a zoning | `gis_parcels/parcels_*.csv` |
| **`AddressPoint`** | an addressable unit on a parcel | `gis_parcels/address_points_*.csv` |
| **`Address`** | an address *as recorded on a record* | everywhere |

`Address` and `AddressPoint` are separate kinds on purpose. An `AddressPoint`
is a surveyed fact about the world; an `Address` is a string a clerk typed, and
this estate contains abbreviated, stale, unit-prefixed and mistyped versions of
the same one. Collapsing them destroys the evidence that a person moved.

### Instrument and artefact kinds

| Kind | What it is | Primary source |
|---|---|---|
| **`Licence`** | a premises or activity licence issued to an organisation | `public_registry` |
| **`BusinessRegistration`** | the registration record of an organisation | `public_registry` |
| **`Document`** | a scanned case file with an OCR text layer | `case_files` |
| **`Message`** | an item of correspondence | `correspondence` |
| **`Photo`** | a site photograph, present only as metadata + OCR | `inspection_photos` |
| **`LegacyRecord`** | a 220-byte record from the pre-2011 system | `legacy_records` |

`LegacyRecord` is a node kind rather than "just a source of attributes", and
that is a deliberate call: a legacy row is often the *only* evidence that
something happened, it frequently cannot be attributed to a `Person` at all
(~8% are orphans), and dissolving it into attributes would silently discard the
ones that resolve to nobody.

### Reference kinds

| Kind | What it is |
|---|---|
| **`PermitType`** | the catalogue entry a permit is issued under |
| **`BenefitProgram`** | the programme a claim is made under |
| **`ZoningCode`** | a zoning classification |
| **`Officer`** | an agency staff member (a `Person` in the world, but held separately: staff identity is administrative, not a subject) |
| **`Agency`** | DCSP, ATBA, and the external bodies that appear as consultees |
| **`Statute`** | the statutory reference a permit type, programme or decision hangs from |

`Statute` earns its place because every `PermitType`, `BenefitProgram` and
`Decision` in this estate cites one, and "which decisions rest on the provision
that changed" is the question a government graph exists to answer.

---

## Edge kinds

Cardinality is written from the left. Two temporality markers, and the
difference between them is a correctness property rather than a nicety:

- **`T` — genuinely bi-temporal.** The source supplies *events*: valid time
  (when it was true in the world) **and** a recording time. The two routinely
  disagree here by days, which is the point.
- **`O` — capture-time observation only.** The source supplies a **current
  value** and no events. The graph may record "observed to be X when we looked,
  at capture time C", with valid time `from: unknown, to: open`. It may **not**
  manufacture a valid-time interval by differencing two successive reads of a
  current-state table: that fabricates a transition date the source never
  asserted, and it silently loses every change that happened between the reads.

An `O` edge becomes `T` the day its source supplies events — an assignment log,
a retained prior GIS cut, a CDC feed with tombstones — and not a day before.

| Edge | From → To | Card. | T | Notes |
|---|---|:--:|:--:|---|
| `APPLIED_FOR` | Person → Application | 1:N | | the applicant role |
| `CLAIMS_AGENT_FOR` | Organisation → Application | N:N | O | **proposal, not assertion.** Derived from `applications.agent_of_record`, which is untrusted free text keyed at a counter. It is a mention until a named officer confirms it, at which point it is re-emitted as `AGENT_FOR` carrying the attesting principal. Never traversed for access or for a determination. |
| `ABOUT_PARCEL` | Application → Parcel | N:1 | | **may dangle**: a small tail of parcel ids resolve to nothing in the GIS |
| `OF_TYPE` | Application → PermitType | N:1 | | |
| `ASSIGNED_TO` | Application → Officer | N:1 | **O** | reassignment is common and `permits.applications` is a **current-value table** with no assignment events. The graph knows "observed assigned to X at capture time", not when that became true or whom it superseded. |
| `DECIDED_BY` | Decision → Officer | N:1 | | |
| `DECISION_ON` | Decision → Application | N:1 | | |
| `GRANTS` | Decision → Permit | 1:0..1 | | only grants create a permit |
| `SUPERSEDES_DECISION` | Decision → Decision | N:0..1 | T | corrections are new rows, via `decisions.superseded_by`. With `SUPERSEDES_DETERMINATION`, the only native history in the estate. |
| `HAS_CONDITION` | Permit → Condition | 1:N | T | conditions are discharged over time |
| `INSPECTS` | Inspection → Application | N:1 | | |
| `INSPECTED_BY` | Inspection → Officer | N:1 | | |
| `AT_PARCEL` | Inspection → Parcel | N:1 | | |
| `EVIDENCED_BY` | Inspection → Photo | 1:N | | via `photo_batch_ref` |
| `APPEALS` | Appeal → Decision | N:1 | | |
| `LODGED_BY` | Appeal → Person | N:1 | | |
| `CLAIMS_UNDER` | Person → Claim | 1:N | | the claimant role |
| `UNDER_PROGRAM` | Claim → BenefitProgram | N:1 | | |
| `CASEWORKED_BY` | Claim → Officer | N:1 | **O** | caseload transfers happen, and `benefits.claims.caseworker_ref` is a current-value column with no transfer events. Same correction as `ASSIGNED_TO`. |
| `DETERMINED` | Determination → Claim | N:1 | T | valid time = `effective_from/to`; system time = `determined_on` |
| `SUPERSEDES_DETERMINATION` | Determination → Determination | N:0..1 | T | via `eligibility_determinations.supersedes`. A **distinct edge kind** from `SUPERSEDES_DECISION`, rather than one union signature over two unrelated node kinds — which would be an invitation to traverse from a decision into a determination chain. |
| `PAYS` | Payment → Claim | N:1 | T | valid time = `period_start/end`; system time = `posted_at` |
| `REVERSES` | Payment → Payment | N:0..1 | | never an update |
| `REVIEWS` | Review → Claim | N:1 | | |
| `EVENT_ON` | CaseEvent → Application \| Claim | N:1 | T | `occurred_at` trails `recorded_at` by days; getting this backwards loses rows |
| `RESIDES_AT` | Person → Address | N:N | T | **the most temporal edge in the estate**: stale addresses are everywhere and are a matching signal, not noise |
| `ADDRESSES` | Address → AddressPoint | N:0..1 | | the resolution of a typed string onto a surveyed point; may fail, and failure is informative |
| `ON_PARCEL` | AddressPoint → Parcel | N:1 | | |
| `ZONED` | Parcel → ZoningCode | N:1 | **O** | rezoning happens, and the GIS ships a **current-only cut** with no retained prior cuts. Successive reads of a current-state file are observations, not a history. |
| `OFFICER_OF` | Person → Organisation | N:N | T | `appointed_on` / `resigned_on` |
| `REGISTERED_AS` | Organisation → BusinessRegistration | 1:N | T | **not 1:1.** Organisations re-register, change legal form, and register in more than one jurisdiction. At most one registration is `current` per jurisdiction; the rest are historical and must stay reachable, because a permit granted in 2014 cites the registration that existed then. |
| `LICENSED` | Licence → Organisation | N:1 | T | issue/expiry |
| `LICENCE_AT` | Licence → Parcel | N:1 | | premises |
| `EVIDENCES` | Document → Application \| Claim | N:1 | | |
| `MENTIONS` | Document \| Message \| Photo → Person | N:N | | **untrusted**; an OCR mention is a hypothesis, not a link |
| `CORRESPONDS_ON` | Message → Application \| Claim | N:1 | O | **requires a trusted binding** — an agency outbound-thread record or a human attestation. Never created from an inbound header. See `CLAIMS_CASE_REF`. |
| `CLAIMS_CASE_REF` | Message \| Document → case reference (a string) | N:1 | O | **proposal, not assertion.** The inbound `X-Case-Reference` header, or a reference OCR'd off a scanned form. Attacker-controlled. Stored, citable, displayable; never traversed for access, routing or a determination. |
| `SENT_BY` / `SENT_TO` | Message → Person \| Organisation \| Agency | N:1 | | |
| `IN_THREAD` | Message → Message | N:0..1 | | `In-Reply-To` / `References` |
| `LEGACY_FOR` | LegacyRecord → Person | N:0..1 | | **often absent by design** — ~8% resolve to nobody |
| `CITES` | Decision \| PermitType \| BenefitProgram → Statute | N:1 | | |
| `SAME_AS` | S → S, for any protected subject kind S | N:N | T | Person→Person and Organisation→Organisation. Never across kinds. See below. |

### `SAME_AS` is not an ordinary edge — and it covers both subject kinds

It is the output of identity resolution, and it is the one edge in this ontology
that a machine may **propose** but never **assert**.

It is **generalised over the protected subject kinds**, not written once for
people. `Organisation` is declared `never_fuzzy_merge` for an even stronger
reason than `Person` is — officer records in the public registry carry *no
identifier at all*, so name and address are the whole basis for a match — and
organisations must be reconciled across permits, correspondence and the public
registry. Without a `SAME_AS` that covers them, organisation resolution would
have had no attestation mechanism at all, and would have happened informally
somewhere else.

`SAME_AS` never crosses kinds: a `Person` is never `SAME_AS` an `Organisation`,
however a sole trader appears in the data. Its required attributes:

```yaml
edge: SAME_AS
domain: [Person, Organisation]        # this pack's protected subject kinds
constraint: from.kind == to.kind      # never across kinds
attributes:
  confidence:        float          # the resolver's score, never a decision
  evidence:          [SourcePointer]  # what supported it, per attribute
  attested_by:       principal_id   # the named human. REQUIRED.
  attested_at:       timestamp      # REQUIRED
  attestation_kind:  in_person | verified_channel | registry_document
                     #   Person:       in_person | verified_channel
                     #   Organisation: registry_document | verified_channel
                     #   ("in person" is not meaningful for a company; the
                     #    attestable evidence is a registry document)
  requires_human_selection: true    # Literal[True]. There is no other value.
```

An unattested `SAME_AS` is a **candidate**, held in a separate proposal space
and never traversed by a query that produces a determination. This is the whole
reason the estate ships `../GROUND_TRUTH.md`: so that resolution quality can be
*measured* without ever being *trusted*.

---

## Temporality

**Four** clocks, and conflating any two of them corrupts the graph. The
distinction between the middle two is the easiest to miss and the most expensive
to unpick later:

| Clock | Meaning | Where it lives here |
|---|---|---|
| **valid time** | when the fact was true in the world | `occurred_at`, `effective_from/to`, `period_start/end`, `valid_from/until`, `appointed_on/resigned_on` |
| **recording clock** | when an application *wrote a timestamp* into a row | `recorded_at`, `posted_at`, `determined_on`, `created_at`, `updated_at` |
| **system time** | when the estate **durably observed** the fact | an ingest or commit position. **This estate has none** — no retained replication slot, no ingest log |
| **capture time** | when *we* took a copy that can be re-read | Capture Store metadata, for every T3 pointer |

A recording clock is not system time. `posted_at` is a value some code wrote: it
can be backdated, it can be identical across rows, and a transaction can take
one early and commit late. System time is a position in a durable, ordered log —
and the absence of one is exactly why both PostgreSQL sources are **T2** rather
than T1 source-wide (see `SOURCES.md`).

**In practice, in this estate: system time = capture time.** Nothing else is
available. Record that, rather than promoting a recording clock into the
system-time slot, because a graph that believes it knows when it learned things
will answer "what did we know on date D" with a confidence it has not earned.

The estate is built so the first two visibly disagree: counter staff key events
in batches, so `permits.case_events.occurred_at` routinely precedes
`recorded_at` by up to eleven days. Materialisation should compute features on
**system time** and labels on **valid time**, or the model learns from facts
that had not been recorded yet.

---

## Where each kind comes from

| Node kind | permits | benefits | case_files | legacy | mail | gis | registry | photos |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `Person` | ● | ● | ○ | ● | ○ | | ● | ○ |
| `Organisation` | ○ | ○ | | | ● | | ● | |
| `Application` | ● | ○ | ○ | ○ | ○ | | | ○ |
| `Permit` | ● | | ○ | ○ | | | | ○ |
| `Decision` | ● | | ○ | | ○ | | | |
| `Claim` | | ● | ○ | ○ | ○ | | | |
| `Payment` | | ● | | ○ | | | | |
| `Parcel` | ○ | | | ○ | | ● | ○ | ○ |
| `Document` | | | ● | | | | | |
| `Message` | | | | | ● | | | |
| `LegacyRecord` | | | | ● | | | | |
| `Licence` | | | | | | | ● | |

● **locally authoritative** — the source is the system of record for *its own*
record of this kind: PERMITWORKS is authoritative that applicant `APL-00412`
exists, is spelled that way, and holds that address.
○ mentioned, and a mention is a **hypothesis with a pointer**, never a fact.

**`●` is not authority over identity.** A source can be authoritative for its
local person record and hold no authority whatever on the question of whether
that record denotes the same human being as a row in another source. Those are
different claims, and the estate holds five disagreeing versions of the first
precisely so that nothing is tempted to assert the second. Every `●` in the
`Person` and `Organisation` rows reads as: *authoritative for its own record; a
candidate, never an answer, as to cross-source identity*.

Read the column pattern for `Person`: five sources assert one, none of them
agree on a key, and two of them (case files, photos) can only offer a name
recovered from damaged OCR. That single row is the argument for the whole
provenance design — every attribute of every `Person` node carries a pointer to
the span it came from, so the graph can hold five disagreeing versions without
having to pick one prematurely.

---

## Growth protocol

The government graph is built from this predefined library, but the library is
not frozen. When an adapter agent meets something that does not fit:

1. it **proposes** a node or edge kind, with the pointers that motivated it;
2. the main agent validates the proposal against this ontology and the existing
   graph;
3. inside policy, it is auto-approved and recorded;
4. when the guardrail fires — anything touching `Person`, `Organisation`,
   `SAME_AS`, or a subject identifier — it **escalates to a human**.

Candidates already visible in this estate and deliberately left out, so the
protocol has something real to chew on: `Household` (implied by
`benefits.claimants.household_size` but never enumerated), `Objection` (visible
only as a `CaseEvent` type), `Consultee` (an `Agency` in a role), and
`EnforcementNotice` (present only as a document kind).
