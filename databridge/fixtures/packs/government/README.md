# The `government` synthetic data estate

> **Everything in this directory is fictional.** The Aldermoor Territory, its
> department, its statutes, its localities, its parcels and every single person
> named anywhere in here were invented for testing. No real permit, claim,
> address, business or human being is represented. Every email address is on a
> `.example` domain and routes nowhere.

Eight heterogeneous sources describing one municipal landscape — permits,
benefits, inspections, case files — as it actually looks: records that are
decades old, formats that never agreed, and most of it with no version history
at all.

This is the **T3-heavy** counterpart to the finance estate. Finance exercises
T0 sources with real snapshots; government exercises the other end, where half
the sources cannot tell you what they said yesterday. An architecture that only
works against Iceberg tables has not been tested.

---

## The sources

| # | Source | Kind | Tier | Records |
|---|---|---|:--:|---:|
| 1 | [`permits_registry/`](permits_registry/) | PostgreSQL DDL + seed | T2 (one T1 stream) | 13,726 |
| 2 | [`benefits_case_management/`](benefits_case_management/) | PostgreSQL DDL + seed | T2 (two T1 streams) | 11,491 |
| 3 | [`case_files/`](case_files/) | OCR'd scanned documents (`.md`/`.txt`) | T3 | 250 |
| 4 | [`legacy_records/`](legacy_records/) | fixed-width flat file + COBOL copybook | T3 | 8,000 |
| 5 | [`correspondence/`](correspondence/) | RFC 5322 `.eml` | T3 as shipped (live contract T1) | 200 |
| 6 | [`gis_parcels/`](gis_parcels/) | CSV | T2 | 7,925 |
| 7 | [`public_registry/`](public_registry/) | JSON API snapshots | T2 live / T0 within the two captures | 1,531 |
| 8 | [`inspection_photos/`](inspection_photos/) | JSON metadata sidecars | T3 | 420 |

**Tiers are declared per stream, not per source, and a source's tier is the
minimum over its streams.** Both PostgreSQL databases have exactly one or two
append-only event tables with a usable forward-only cursor; every other table
is a mutable current-value table with no change feed and no tombstone, so the
source-wide claim is T2. `sources.yaml` says which stream is which and what the
cursor protocol has to be to hold.

**43,543 rows and records across 959 files (~13 MB).** Of those, **15,000 are
case events** — the high-cardinality spine that makes the graph large — spread
across 800 permit applications and 400 benefit claims belonging to **520
distinct people**, who appear **9,870 times** across the estate under names and
identifiers that do not agree.

`SOURCES.md` is the important one to read next — with `sources.yaml` beside it,
which is the same contract in the shape `describe()` actually returns. Between
them they say for each source — pinning tier, whether deletes are detectable, whether
the watermark can be trusted, the *effective* retention rather than the
documented one, and the egress class.

## The other files at this level

| File | What it is |
|---|---|
| **`SOURCES.md`** | per source and per stream: tier, delete detectability, what the watermark can and cannot promise, effective retention, and the three independent origin/trust/egress classifications. The `describe()` contract in prose, written before the adapters exist. |
| **`sources.yaml`** | the same contract machine-readable — the artefact `describe()` returns. Where the two disagree, this one is the contract. Also carries `known_fixture_gaps`: the adapter failure modes this estate does **not** exercise. |
| **`ONTOLOGY_NOTES.md`** | the natural node and edge kinds in this domain, with cardinality and temporality. The government graph is built from a **predefined** ontology; this is it. |
| **`GROUND_TRUTH.md`** | the entity-resolution answer key: who is really who, which traps were planted where, and which pairs of people are confusable but **distinct**. |
| `entity_resolution_truth.csv` | the same mapping, machine-scorable: one row per appearance. |
| `entity_resolution_negatives.csv` | 41 pairs of different people built to look alike. Merging any of them is a hard failure. |
| **`roles.yaml`** | the policy an adapter's Policy layer enforces: evaluation order, role-combination semantics, per-grant purposes, the enforced sensitive overlay, and the three-state existence disclosure. Caseworkers see their own caseload and cannot learn that anyone else's exists; the auditor gets an explicit pseudonymised audit projection rather than `*`; the public-records officer gets a disclosable subset where existence *is* deliberately disclosed. Drives the policy/withholding demo. |
| `correspondence/INJECTIONS.md` | the ten planted prompt-injection payloads, where each one is, and what the system is supposed to do about it. |
| `manifest.json` | every emitted file with size and SHA-256, plus the row counts. Regenerate and diff it to prove determinism. |
| `generate.py` | the generator. Fixed seed, `--scale`, byte-identical output. |

---

## Regenerating

```bash
cd databridge/fixtures/packs/government
python generate.py                    # the committed estate: seed 20260815, scale 1.0
python generate.py --scale 0.05       # a small one, for fast tests (~25s -> ~1s)
python generate.py --scale 3          # a big one
python generate.py --seed 99 --out /tmp/other-estate
```

Output is **deterministic**: same seed and scale, byte-identical files, on any
platform. (Line endings are forced to `\n` and every RNG stream is seeded by
CRC-32 of its name rather than by `hash()`, which Python salts per process.)

`generate.py` cleans only what it owns. The hand-written files — both
`schema.sql` files, `roles.yaml`, `sources.yaml`, `SOURCES.md`,
`ONTOLOGY_NOTES.md` and this README — are never touched.

Both the generator **and** its output are committed, so a checkout is usable
without running anything.

## Bringing the estate up

One command loads both databases and proves the load, with no server and no Docker:

```bash
cd databridge
python scripts/estates.py provision --estate government   # load, then verify
python scripts/estates.py status  --estate government     # all nine sources, readable
python scripts/estates.py status  --estate government --detail
```

It reads `sources.yaml`, follows each source's own `seed/load_all.sql`, and loads into SQLite
under `databridge/var/estates/government/` (gitignored). Measured on a laptop: **25,217 rows
across 18 tables and 2 views in 2.4-7.3 s from cold** (the spread is disk cache and whatever
else is running) — `permits` 13,726 rows / 4.0 MB, `benefits` 11,491 rows
/ 2.6 MB. Re-running is a no-op; `--clean` rebuilds.

The DDL here is PostgreSQL and stays that way. The SQLite path translates it **mechanically on
the way in** — there is no second copy of the schema to drift — and every rewrite is inspectable:

```bash
python scripts/estates.py translate government/benefits_case_management --explain
```

Three constructs need real translation and all three are visible in that output: the schema
qualifier (one SQLite file *is* the schema), the `::date` casts in `v_public_register`, and
`date_trunc('month', ...)` in `v_program_caseload`, which becomes `date(x, 'start of month')`.
`COMMENT ON` is captured into a `_pg_comment` table rather than discarded. To exercise the real
dialect instead:

```bash
docker compose -f docker-compose.estates.yml up -d
python scripts/estates.py provision --backend postgres --estate government
```

`status` also covers the seven non-relational sources without loading any of them: it counts the
case files against `INDEX.csv`, decodes a `LEGACY-MASTER` record using the field offsets in
`legacy_records/LAYOUT.md`, and measures the ~8% legacy orphan rate against
`entity_resolution_truth.csv` — a number the estate declares only in prose, now checked.

The public registry's two captures are counted **per snapshot**, never as a union: `businesses`
is 600 at 2026-05-01 and 602 at 2026-08-01, and that difference is the drift the source exists
to demonstrate.

## Loading the two databases

```bash
createdb aldermoor
psql -v ON_ERROR_STOP=1 -d aldermoor -f permits_registry/schema.sql
psql -v ON_ERROR_STOP=1 -d aldermoor -f permits_registry/seed/load_all.sql
psql -v ON_ERROR_STOP=1 -d aldermoor -f benefits_case_management/schema.sql
psql -v ON_ERROR_STOP=1 -d aldermoor -f benefits_case_management/seed/load_all.sql
```

The loaders use `\ir`, so they resolve relative to themselves and work from any
working directory. Seeds are sharded (2,000 rows per file, 250 rows per
`INSERT`) so a failure names a small file rather than a 10 MB one.

---

## What this estate is built to break

**Cross-source identity is messy on purpose.** The same person appears in
permits, benefits, correspondence, the public registry and the legacy file
under name variants, transposed digits, changed addresses and truncated
surnames. PERMITWORKS keys on the Aldermoor Resident Number; CLAIMLINE never
adopted it and keys on its own Benefits Reference; the legacy file predates
both; the public registry has no identifier on a person at all. There is no
join. `GROUND_TRUTH.md` says who is really who, so resolution can be **scored**
rather than admired — and it also ships 41 pairs of *different* people built to
look alike, because a resolver that is only measured on recall will happily
merge two citizens.

**A resolver that scores 100% here is misconfigured.** Several identities are
genuinely undecidable from the data — an initial, a surname truncated at thirty
characters, and no identifier is not enough. The correct output for those is a
candidate set for a human. Nothing in this estate authorises a machine to
confirm that two records are the same person.

**No source in this estate can detect deletes** — including the two systems of
record, where soft-delete markers cover exactly two tables between them.
Absence is not evidence. An adapter that infers deletion from a row no longer
appearing is fabricating.

**Four sources are untrusted at the source contract level** — correspondence,
case files, inspection photos and the public registry. Ten prompt-injection
payloads are planted across them: four in plain-text email bodies, one in an
HTML alternative part, one in a JSON attachment, one in an OCR'd case file, one
on a photographed site placard, and **two laundered through PostgreSQL** as
claim notes a caseworker pasted in. That last pair is the sharp one: passing through a system of record
does not promote trust. `correspondence/INJECTIONS.md` is the answer key, and
`correspondence/INDEX.csv` deliberately does not flag which messages are
hostile.

**The clocks disagree on purpose.** Counter staff key events in batches, so
`occurred_at` (valid time) routinely precedes `recorded_at` (system time) by up
to eleven days. The benefits event log is truncated at roughly seven years, so
the oldest claims have determinations and payments with no surviving events —
and absence of an event is not evidence the event did not happen.

**The public registry ships two captures** (2026-05-01 and 2026-08-01) that
differ, so `verify()` has something real to return a verdict about:
`DRIFT_MATERIAL`, `DRIFT_BENIGN`, `VANISHED` and plain new rows.
`public_registry/DRIFT.md` says which is which.
