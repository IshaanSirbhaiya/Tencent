# The Walnut synthetic finance estate

> **Everything here is fictional.** Invented company names, invented people, invented registration
> numbers (`SYN-CRN-…`), invented national identifiers (`SYNID-…`), invented instruments
> (`SYNX:…`), and every email domain under the RFC 2606 reserved `.example` TLD. No real
> organisation, person, identifier or price appears in this estate, and none was used to build it.
> The adversarial text planted in it is **inert test content** — see `GROUND_TRUTH.md`.

This is not a demo table. It is a **data estate**: nine heterogeneous sources of the shape a
mid-size commercial lender actually has, most of which disagree with each other, none of which
shares an identifier space with the others, and each of which sits in a different pinning tier.
A fleet of adapters is supposed to connect these into one temporal knowledge graph. This estate
exists so that claim can be tested rather than asserted.

## What is here

| # | Source | Kind | Tier | Volume |
|---|---|---|---|---|
| 1 | `core_banking/` | PostgreSQL DDL + sharded seed | T2 (3 streams T1; T0 option declared, not met) | 466 parties · 640 accounts · 320 facilities · **24,132 transactions** · 14,400 monthly balances · 380 collateral · 520 covenants · 2,096 covenant tests |
| 2 | `crm/` | PostgreSQL DDL + seed | T2 | 40 RMs · 329 organisations · 900 contacts · 4,200 interactions · 700 opportunities |
| 3 | `filings/` | Document drop (`.md`/`.txt`) | T3 | 95 annual reports and financial statements |
| 4 | `credit_memos/` | Document drop (`.md`) | T3 | 75 underwriting memoranda |
| 5 | `mailbox/` | RFC 5322 `.eml` | T3 as shipped (T1 live, not exercised) | 210 messages, threaded, some multipart |
| 6 | `market_data/` | CSV | T2 | 15,960 prices · 4,256 FX rates · 600 rating actions · **39 restated rows** |
| 7 | `spreadsheets/` | CSV exports + sidecar | T3 | 1,633 rows across 4 sheets |
| 8 | `payments/` | JSONL event stream | T1 (conditional on a dedupe + reorder protocol) | **12,120 events** in 7 shards |
| 9 | `sanctions_watchlist/` | CSV — **held out** | T2 | 180 entries (the scoring key lives in `ground_truth/`) |

Totals: **445 files, ~18 MB, 170 documents, 1,368 canonical entities, 3,775 ground-truth entity
records, 26 planted prompt injections, 462 pieces of planted personal data.**

`SOURCES.md` describes each source the way `describe()` should: pinning tier and *why*, cursor and
whether it is lossy, whether deletes are detectable, retention, egress class, and which verdicts in
the D6 lattice it can actually produce. `sources.yaml` is the machine-readable mirror, and it is
the deciding file where the two differ.

**The tiers here are deliberately unflattering.** A too-generous tier is not merely inaccurate: it
promises that a citation can be re-checked later when it cannot. Version 2 of the contract
withdrew three claims that could not be honoured — the `core_banking` LSN cursor (no logical
decoding, no retained replication slot, so there is no LSN to consume), the `mailbox` T1 tier (what
ships is a static directory that cannot exercise a cursor at all), and the `payments` unconditional
`lossy: false` (which sat beside a declared gap and an at-least-once guarantee). It also declared
the **Capture Store's** retention, which is the only one of this estate's three retentions that
actually bounds whether a citation stays checkable.

## The four documents that matter

| File | What it is for |
|---|---|
| **`SOURCES.md`** / `sources.yaml` | The source descriptors. Tier, cursor, delete detectability, retention, egress class, hazards. |
| **`GROUND_TRUTH.md`** / `ground_truth/` | The answer key: the true entity mapping, the pairs that must **not** be merged, every planted injection with its exact locator, every planted piece of personal data. |
| **`roles.yaml`** | Access control as (who, on-behalf-of, **purpose**), with table, column and row rules, masks, evaluation order, role-combination semantics, the result model, and the demonstration cases the policy layer should be able to pass. |
| **`generate.py`** | The generator. Seeded, deterministic, `--scale`-able. |

## Regenerating

```bash
cd databridge/fixtures/packs/finance
python generate.py --clean          # exactly reproduces the committed estate
python generate.py --clean --scale 4   # a 4x estate: same seed, same traps, more rows
python generate.py --out /tmp/estate   # write somewhere else
```

Standard library only — no dependencies. The seed is fixed at `20260815`, so a re-run reproduces
the estate **byte for byte**; `MANIFEST.json` records the sha256 and size of all 445 files, and
`ground_truth/expected_counts.json` records every row count. Both are committed, so a test can
assert determinism without regenerating.

`--scale` multiplies every row target. The traps scale with it: more duplicate parties, more
near-name twins, more orphans, proportionally more injections. What does *not* change is the
character of the estate — at scale 10 it is still nine sources that disagree with each other.

## Bringing the estate up

One command loads both databases and proves the load, with no server and no Docker:

```bash
cd databridge
python scripts/estates.py provision --estate finance   # load, then verify
python scripts/estates.py status  --estate finance     # all nine sources, readable
python scripts/estates.py status  --estate finance --detail
```

It reads `sources.yaml`, follows each source's own `load.sql`, and loads into SQLite under
`databridge/var/estates/finance/` (gitignored). Measured on a laptop: **50,855 rows across
16 tables in 4.4-7.6 s from cold** (the spread is disk cache and whatever else is running) — `core_banking` 44,686 rows / 11 tables / 7.1 MB, `crm` 6,169 rows /
5 tables / 1.5 MB. Re-running is a no-op; `--clean` rebuilds.

The DDL here is PostgreSQL and stays that way. The SQLite path translates it **mechanically on
the way in** — there is no second copy of the schema to drift — and every rewrite is inspectable:

```bash
python scripts/estates.py translate finance/core_banking --explain
```

`COMMENT ON` has no SQLite equivalent, so the comments are not dropped: they are loaded into a
`_pg_comment` table in each database, which is where the `SENSITIVE` and `UNDECLARED FK` notes
end up. To exercise the real dialect instead:

```bash
docker compose -f docker-compose.estates.yml up -d
python scripts/estates.py provision --backend postgres --estate finance
```

`verify` checks every table against `ground_truth/expected_counts.json`, checks that all 14
declared foreign keys hold, and **measures the planted orphan rates** on the four undeclared FKs
that `sources.yaml` declares — because a load that quietly loses the deliberate orphans has
destroyed the fixture, not fixed it.

## Loading the two databases

```bash
psql -v ON_ERROR_STOP=1 -d walnut -f core_banking/load.sql
psql -v ON_ERROR_STOP=1 -d walnut -f crm/load.sql
```

`load.sql` runs `schema.sql` and then every seed shard in dependency order. The DDL is PostgreSQL
14+ and carries `COMMENT ON` metadata on the sensitive and the deliberately-broken columns, so a
schema reader picks up the hazards without reading this file.

Connect adapters with a **read-only role**. There is no write path into a source, ever.

## What is deliberately wrong with this data

Everything below is planted, documented and scoreable. Full detail in `GROUND_TRUTH.md`.

- **No shared identifiers.** `core_banking` uses `CB-P-…`, `crm` uses `CRM-ORG-…`, `market_data`
  and `spreadsheets` and `filings` use names and nothing else. The one bridge column,
  `crm.account_org.external_ref`, is correct 30% of the time, `NULL` 50%, and **wrong 20%**.
- **The same company, spelled six ways.** Legal-form noise, casing, abbreviation, initialisms,
  transposition typos, and a dozen companies that were renamed mid-period so the filings carry the
  old name and the database carries the new one.
- **Twelve near-name twins that are different companies.** Merging them is the expensive failure,
  and it is measured separately from recall.
- **34 duplicate party rows inside `core_banking`**, with `registration_no` left `NULL` on the
  second copy — the strong key missing exactly where it is needed.
- **Undeclared foreign keys with orphans**: `txn.counterparty_party_id` (~11% dangling),
  `collateral.facility_id` (~6%), `party.primary_rm_code` (points into `crm`, ~6%).
- **Free text that hides things.** `party.notes`, `interaction.summary`, `remittance_info` and
  email bodies carry personal data no column-level policy can see, and 26 prompt-injection
  payloads across seven sources — including one obfuscated with zero-width spaces so a naive
  substring search for it fails.
- **Temporal hazards.** A vendor restatement with no version field, a payment sequence gap, 120
  at-least-once duplicate deliveries, out-of-order arrival, and `_FINAL`/`_revised`/`_v2` copies of
  the same filing with different figures and nothing to order them by.
- **Excel damage.** Numbers stored as text, `#DIV/0!` and `#REF!` as literal strings, hardcoded
  cells that break the formula the sidecar claims produced them, serial-number dates, and a hidden
  TOTAL row that survived the export.

## Why the ninth source is held out

`sanctions_watchlist/` is excluded from the first eight so that adding it later — with its own
adapter, against a graph that already exists — exercises the incremental-join claim: **a new source
merges without a rebuild.** Its scoring key —
which entities genuinely are on the list, and which are near-name traps that must not match — is
`ground_truth/expected_screening_results.json`. It deliberately does **not** live in the source
directory and is not named in `sources.yaml`: an adapter that can read its own answer key is not
being tested. `INJ-08`,
planted in `core_banking.party.notes`, instructs a reader to suppress watchlist hits; it must have
no effect, because screening is a deterministic step that free text cannot reach.

## Layout

```
finance/
  README.md  SOURCES.md  sources.yaml  roles.yaml  GROUND_TRUTH.md
  generate.py  MANIFEST.json
  core_banking/   schema.sql  load.sql  seed/*.sql            (21 files)
  crm/            schema.sql  load.sql  seed/*.sql             (8 files)
  filings/        <year>/*.md|*.txt                           (95 files)
  credit_memos/   <year>/*.md                                 (75 files)
  mailbox/        messages/*.eml  cursor.json  listing.csv    (212 files)
  market_data/    instruments.csv  prices/  ratings/  fx/      (8 files)
  spreadsheets/   *.csv  FORMULAS.yaml  README.md              (6 files)
  payments/       events/*.jsonl  cursor.json  SCHEMA.md       (9 files)
  sanctions_watchlist/  watchlist_*.csv  README.md
  ground_truth/   entity_map.csv  entity_clusters.json  non_matches.csv
                  intra_source_duplicates.csv  injections.json
                  pii_in_free_text.csv  expected_screening_results.json
                  expected_counts.json  README.md
```
