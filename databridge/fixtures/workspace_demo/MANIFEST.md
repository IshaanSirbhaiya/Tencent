# Workspace demo corpus — Larkspur Analytics (synthetic)

**Everything here is invented.** Larkspur Analytics, Pellucid Freightways, Corriedale Grocers
Co-operative and Tamsinbay Water Authority are fictional; domains use the reserved `.example` TLD;
phone numbers are in the UK drama range (020 7946 0xxx); the IBANs are published documentation
examples; the card is the standard Luhn-valid test number; the "private key" body is filler text.

- `files/` — 15 documents (md, txt, pdf, docx). Regenerate with `python fixtures/workspace_demo/_generate.py`.
- `db/01_schema_and_data.sql` — Postgres init (employees, customers, contacts, projects, invoices,
  contact_notes, with FKs; read-only role `walnut_reader`). Loaded by `infra/docker-compose.walnut.yml`.
- `expected_outcomes.yaml` — machine-readable screen expectations, asserted by
  `packages/graph/tests/test_workspace_pack_loads.py`.

## Entities (recurring across files and database)

| Entity | DB | Documents |
|---|---|---|
| Larkspur staff: Mira Oyelaran (LA-001), Tobias Reinholt (LA-007), Ines Marchetti-Holm (LA-012), Callum Driscoll-Park (LA-015), Ada Nwachukwu-Barr (LA-019) | `employees` | briefs, notes, meetings, MSA, SOW, invoices, HR, roadmap |
| C-1001 Pellucid Freightways Ltd — contacts Rowan Achterberg, Selin Kaya-Morrow | `customers`, `contacts` | MSA, route-optimisation brief/notes, kickoff, INV-2026-0041 |
| C-1002 Corriedale Grocers Co-operative — contacts Hamish Oduya, Petra Lindqvist-Shaw | `customers`, `contacts` | SOW, demand-forecasting brief/notes, steering, INV-2026-0057 |
| C-1003 Tamsinbay Water Authority — contact Oluwaseun Pettifer | `customers`, `contacts` | **only** `planning/Q3 #roadmap/roadmap.md` |
| PRJ-101 / PRJ-102 / PRJ-103 | `projects` | briefs, roadmap, invoices |
| MSA-2026-014, SOW-2026-021 | `projects.contract_ref` | contract files |
| INV-2026-0041, INV-2026-0057 | `invoices` | invoice PDFs |
| INV-2026-0063 | `invoices` | **none** — DB-only fact |

## Planted cases and expected outcomes (screen = `packs/workspace/screen.yaml`)

| File | Expected | Why |
|---|---|---|
| `engineering/deploy_notes.txt` | **block** (`private_key_block`) | planted fake `-----BEGIN OPENSSH PRIVATE KEY-----` block; decisive |
| `hr/Compensation_Bands_2026.md` | **block** (`confidentiality_marking`) | "STRICTLY CONFIDENTIAL" on its own line; decisive |
| `sales/Rate_Card_Draft_v2.md` | **block** (score 3: `pricing_heading` 1 + `currency_table` 2) | corroborated block from two independent signals, no decisive rule |
| `contracts/Corriedale/SOW-2026-021_Demand_Forecasting.docx` | **review** (score 2: `contract_value_wording`) | "Total contract value" — excluded but listed for owner override |
| `hr/Remote_Working_Policy.docx` | **allow** (score 1) | "handled in a strictly confidential manner" is prose, NOT a marking |
| `contracts/Pellucid/MSA-2026-014_Pellucid_Freightways.pdf` | **allow** (score 1) | confidentiality clause; GBP 5,000,000 liability cap scores nothing (wording, not magnitude) |
| `finance/invoices/INV-2026-0041.pdf` | **allow**; redact IBAN, email, phone | IBAN passes mod-97 |
| `finance/invoices/INV-2026-0057.pdf` | **allow**; redact IBAN, card, email, phone; **keep** `DE44 LSPR 2026 0457 11` | part number is IBAN-shaped but fails mod-97 — must not be masked |
| `meetings/2026-07-14_kickoff_pellucid.txt` | **allow**; redact email, phone | contact identifiers masked, names kept (P13) |
| everything else | **allow** | |

Expected totals: 15 files → 11 allow, 1 review, 3 block. Every block/review must appear in the
exclusions ledger with its rule id and `reverse` text.

## Collection traps

- **Duplicate filenames:** `projects/route-optimisation/brief.md` vs `projects/demand-forecasting/brief.md`
  (and `notes.md` in both). Identity must be the hashed full path — two documents, two citations.
- **`#` in a folder name:** `planning/Q3 #roadmap/`. A collector that URL-addresses folders gets
  zero files with success; the roadmap (the only document naming Tamsinbay / PRJ-103) is then lost.
  Verified 2026-10-01 through MCPJungle + server-filesystem: `list_directory` returns `roadmap.md`.

## Expected cross-source links (files ↔ database)

- People in documents ↔ `employees` / `contacts` rows by **email** (subject kind `person` —
  never merged by name; a human confirms through the verified channel).
- Customers by name/domain ↔ `customers.customer_id` (C-1001/C-1002/C-1003).
- Invoice PDFs ↔ `invoices.invoice_no`; projects ↔ `projects.project_id`; contracts ↔ `projects.contract_ref`.
- DB-only: INV-2026-0063 (no document). Docs-only facts: meeting decisions, the MSA liability terms.
- Blocked/review documents contribute **nothing** to the graph (e.g. the rate card's prices, the SOW's value).
