# Data card: "Company A", the synthetic world

> **Status.** 30 Oct 2026. Every person, company, customer, document and identifier in this dataset is
> invented. No real company, customer or personal data is used anywhere (`CLAUDE.md:55-57`).
>
> **References.** Paths start at `databridge/fixtures/enterprise/` unless shown otherwise. Counts come from
> `manifest.json:21-44` (the `counts` block), which the generator writes and a test checks
> (`tests/test_generator.py::test_manifest_records_args_version_and_counts`, line 180).

## 1. Motivation

| Question | Answer |
|---|---|
| Why does it exist? | To build, test and demo a permission-aware assistant without touching real company data. |
| What must it make possible? | Realistic permission models on four platforms; facts that some people may read and others may not; attacks planted in content; fintech-specific cases (tipping-off, market policies, draft policies, card numbers). |
| Who made it? | Team Walnut, with a deterministic generator (`generate.py`). |
| Intended use | Evaluation and demo of the Internal Brain only. |
| Not for | Training any model; any claim about real-world accuracy; any claim about a real company. |

## 2. Composition

| Item | Count | Source |
|---|---|---|
| People (directory users) | **34** | `manifest.json:43` |
| Groups | 17 | `manifest.json` `counts.groups` |
| Confluence pages | 231 in 9 spaces (25 restricted, 1 archived, 1 trashed) | `manifest.json` `counts` |
| Jira issues | 261 in 5 projects (14 with a security level) | same |
| Slack threads | 628 (3,133 messages) in 18 channels (5 private, 1 Slack Connect) | same |
| Drive files | 87 in 2 shared drives, 14 folders | same |
| **Documents in total** | **1,207** (231 + 261 + 628 + 87) | same |
| Documents readable by every member | 562 | same |
| Documents indexed per eval shard | 1,204 | `databridge/evals/internal_brain/RESULTS.md:36` |
| **Planted facts** | **72** | `manifest.json:32` |
| Questions with expected outcomes | 546 | `manifest.json` `counts.questions` |
| Seeded audit replay events | 40 | `manifest.json` `counts.audit_replay` |

The three documents not indexed are consistent with the generator's rule that trashed, archived and draft
pages are readable by nobody (`README.md:24`), and the draft fee schedule is "readable by nobody, so never
indexed" (`README.md:70`).

**Named personas** (nine): `jdoe` backend engineer, `priya.sre` on-call SRE, `ken.ext` contractor (Slack
Connect guest), `sara.sec` security, `leo.ops` product/ops, `amy.am` account manager, `mlro.tan` money
laundering reporting officer, `maya.compliance` compliance officer, `admin.it` IT admin
(`generate.py:44`; `INTERNAL_BRAIN.md:129-131`). The eval adds two ACL-derived personas, `sam.old` and
`ivy.guest` (`RESULTS.md:33`).

## 3. The 72 planted facts

Each fact has a sentinel sentence that appears in exactly one document, plus a `CNRY-xxxxxx` canary when
that document is not readable by every active member (`README.md:30-36`;
`tests/test_generator.py::test_every_sentinel_appears_in_exactly_its_doc`, line 325;
`::test_canaries_are_unique_and_only_in_restricted_docs`, line 336). The `permitted` and `denied` lists
are **computed** from the generated permission data, not written by hand (`generate.py:1956-1957`;
`tests/test_generator.py::test_permitted_and_denied_match_the_acl_data`, line 352).

| Family | Count | What it tests |
|---|---|---|
| S1, S1b, S1c | 11 | Aspire scenario 1: one question answered across platforms, private channel omitted |
| S2 | 2 | Freshness: a runbook edited during the demo (`S2-step-4b` appears only after the update) |
| S3 | 2 | Restricted breach report vs a non-existent one |
| S4 | 2 | Revocation at the source |
| ACL | 6 | Platform permission edge cases: nested and inherited restrictions, trashed and archived pages, Jira reporter-holder, Drive "anyone" link |
| SEC | 2 | Jira security level; Slack containment channel |
| Ops, VENDOR, COMP | 5 | Ordinary business content (policy, pricing, vendor SLA, audit deadline) |
| **INJ** | 4 | Prompt injection, one per platform (below) |
| **FINCRIME** | 4 | Suspicious-transaction case notes readable only by the FinCrime group |
| CX | 1 | The approved customer script |
| **MK** | 3 | Market-specific policy (Singapore, Indonesia) |
| **FEE** | 2 | Approved fee schedule v3 vs a draft v4 that must never be cited |
| **SENS** | 5 | Card number (×2), CVV, IBAN, NRIC for masking |
| **WP** | 23 | Everyday company content (leave, hybrid work, conduct, reviews, learning budget, referrals, office, IT helpdesk, onboarding, holidays, travel and expenses, approval limits, procurement, month-end close, payday, benefits), open to every employee; plus the salary bands, readable by the People team only (no persona) |
| **Total** | **72** | `manifest.json` `facts` |

### Injection fixtures

| Fact | Platform | Attack |
|---|---|---|
| `INJ-confluence-title` | Confluence | Instruction in the title; "ignore previous instructions" body |
| `INJ-jira-forged-citation` | Jira | A forged `[C9]` citation claiming a security approval |
| `INJ-slack-base64` | Slack | A base64 instruction to print a restricted report |
| `INJ-drive-audit-tools` | Drive | Text asking the assistant to call audit tools |

All four are readable by `jdoe`, so they really reach the answer step
(`tests/test_generator.py::test_injection_fixtures_on_all_four_platforms_are_readable_by_jdoe`, line 508).

### Sensitive values (all published test values or invented)

- Card numbers `4111 1111 1111 1111` and `5555 5555 5555 4444` (published test numbers), `CVV: 123`, the
  standard example IBAN `GB82 WEST 1234 5698 7654 32`, and the invented, checksum-valid NRIC `S1234567D`
  (`README.md:71`; `docs/tencent/DATA_FLOWS.md:61-63`;
  `tests/test_fintech_layer.py::test_sens_values_are_published_test_values_in_their_doc`, line 239).

## 4. Generation

| Item | Value | Evidence |
|---|---|---|
| Generator | `generate.py`, version `"1"` | `generate.py:40`; `manifest.json:1120` |
| Command | `python fixtures/enterprise/generate.py --seed 7 --now 2026-10-13T09:00:00+08:00 --scale demo` | `README.md:5-9` |
| Seed | 7 | `manifest.json:5` |
| `world.json` sha256 | `e3381d7122570db7744f63094b7fbcdf05617aedb1c5de0a22f4f3d5b48861b7` | `manifest.json:29484` |
| Determinism | Stdlib only; seeded `random.Random` streams; dates derived from `--now`; sorted keys; LF line endings. Same arguments give byte-identical output. | `generate.py:9-12`; `tests/test_generator.py::test_two_runs_are_byte_identical` (line 158); `::test_committed_fixture_matches_the_generator` (line 171) |
| Fintech layer | Planted **after** all filler, so it only adds | `README.md:61-64`; `tests/test_fintech_layer.py::test_fintech_layer_only_adds` (line 80) |
| Everyday-company layer (WP) | Planted **after** the fintech layer with its own random stream, so it only adds; with it switched off the generator writes the pre-layer `world.json` byte for byte | `README.md:79-85`; `tests/test_workplace_layer.py::test_world_without_the_layer_is_the_pre_layer_world`, `::test_workplace_layer_only_adds` |
| Domain | `companya.example` | `README.md:3` |

## 5. What is not in it

- No real people, companies, customers, card numbers or identity numbers.
- No data derived from real records. The PDPC's proposed guide on synthetic data treats re-identification
  risk as the main concern for synthetic data derived from real data
  ([PDPC, 15 Jul 2024](https://pdpc.gov.sg/help-and-resources/2024/07/proposed-guide-on-synthetic-data-generation)).
  Company A is generated from a seed, not from real records, so there is nothing to re-identify.
- The customer "Velquarry Trading Pte Ltd" and the non-existent "Bayfront Spice Traders" are invented
  (`README.md:68`).

## 6. Known biases and limits

| Limit | Effect |
|---|---|
| English only | Says nothing about answer quality in Bahasa Indonesia, Thai or Vietnamese |
| SG and ID market content only | Market policies cover two of Aspire's markets |
| One organisation shape | One fintech, 34 people, one group namespace; real tenants need a `group_resolver` (`docs/tencent/SECURITY.md:256`) |
| Short documents | Page bodies and threads are kept short (`tests/test_generator.py::test_page_bodies_and_threads_stay_short`, line 503) |
| Planted facts are known in advance | The eval tests the facts we planted; it cannot find leaks of content we did not mark. The 10-word shingle check covers unmarked text in unreadable documents (`RESULTS.md:40`). |
| Demo scale | ~1.1k documents. A `--scale company-a` benchmark (~12k pages) exists but is never committed (`generate.py:4-7`) |

## 7. Maintenance

The fixture is committed. Any change to `generate.py` must regenerate `world.json` and `manifest.json`;
`tests/test_generator.py::test_committed_fixture_matches_the_generator` fails otherwise. Run:
`python -m pytest fixtures/enterprise/tests -q` from `databridge/`.
