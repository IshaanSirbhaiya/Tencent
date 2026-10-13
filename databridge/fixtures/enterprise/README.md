# Company A: the Internal Brain's synthetic world

Company A is a fictional fintech on the `companya.example` domain. Everything in this folder is invented.
The contract is `docs/tencent/CONTRACTS.md` §1.

```powershell
# from databridge/
.venv\Scripts\python.exe fixtures\enterprise\generate.py --seed 7 --now 2026-10-13T09:00:00+08:00 --scale demo
.venv\Scripts\python.exe fixtures\enterprise\generate.py --scale company-a --out <dir>   # benchmark, never committed
.venv\Scripts\python.exe -m pytest fixtures\enterprise\tests -q
```

| File | What it is |
|---|---|
| `generate.py` | The deterministic generator. It uses only the stdlib, takes every date from `--now`, and writes JSON with sorted keys and LF line endings. Running it twice with the same arguments gives byte-identical output. |
| `content.py` | The sentence and noun pools the filler text is built from. |
| `world.json` | The demo estate: directory, confluence, jira, slack, drive, facts, questions and audit_replay. |
| `manifest.json` | The arguments, the generator version, the sha256 of `world.json`, counts, facts, questions, `s2_update_body`, `demo_actions`, `s4_actions` and `audit_questions`. |

## Access rules the permitted and denied lists are computed from (plan §4.5)

`generate.Acl` applies these rules. The tests check its results against a second evaluator, written separately.

- **Confluence:** a reader needs read access to the space (users or groups). They must also appear in the read restriction of the page and of every ancestor page that has one. These restrictions only narrow access, so they are intersected. A `trashed`, `archived` or `draft` page is readable by nobody.
- **Jira:** a reader needs Browse on the project. Browse comes from the actors of each role listed in `browse.roles`, from `browse.users` and `browse.groups`, and from being the issue's reporter or assignee when those flags are on. If the issue has a security level, the reader must also hold that level. The level's holders are its users, its groups, the actors of its roles, and the reporter or assignee when those flags are on.
- **Slack:** for a public channel, readers are every active user of kind `member` plus the channel's own members. Guests and Slack Connect users only see channels they belong to. For a private or connect channel, readers are the channel's members.
- **Drive:** readers come from the file's own permissions plus each ancestor folder's permissions, then the shared drive's members. A folder with `inherited_permissions_disabled: true` stops this climb: its own permissions still apply, but nothing above it (including drive members) does. A `domain` permission matches users by email domain. If a file has any `anyone` permission, the IB denies the whole file.
- **Every platform:** inactive users are denied.

## Facts

Each fact has a natural-language sentinel sentence that appears in exactly one document.

- **Canaries.** A fact whose document is not readable by every active workspace member also has a `CNRY-xxxxxx` canary in the text. No other document contains that token.
- **`permitted` and `denied`.** These partition the nine personas: `jdoe`, `priya.sre`, `ken.ext`, `sara.sec`, `maya.compliance`, `leo.ops`, `admin.it`, `amy.am` and `mlro.tan`. They describe the **initial** state.
- **`S2-step-4b`.** It has `applies_after: "s2_update"`. It is not in any document until the demo applies `s2_update_body`.

Document ids:

| Platform | Format |
|---|---|
| Confluence | `confluence:<page id>` |
| Jira | `jira:<issue key>` |
| Slack | `slack:<channel id>:<thread ts>` |
| Drive | `drive:<file id>` |

## Questions

Questions use the contract's keys plus three more:

| Key | Values |
|---|---|
| `kind` | `canonical`, `direct`, `paraphrase`, `metadata` or `aggregation` |
| `applies_after` | `null`, `s2_update`, `s4_restrict_page` or `s4_remove_member` |
| `expect_refusal` | `true`, `false`, or `null` when not asserted |

`must_not_contain_facts` always includes every fact the persona is denied in the initial state. It also includes the facts that the question's `applies_after` action revokes.

## The fintech layer (FC, MK, FEE, SENS)

`Builder.build_fintech()` runs **after** all filler, so it only adds: every earlier id, filler text and
random draw is unchanged (`tests/test_fintech_layer.py::test_fintech_layer_only_adds` checks this against
the same world built with the layer switched off). The two pre-existing documents it touches only gain a
first-line market tag (`Ops-policy-confluence`, `Ops-pricing-drive`).

| Scenario | What is planted | Questions |
|---|---|---|
| **FC** FinCrime compartment (tipping-off-safe refusal) | Group `grp-fincrime` (`mlro.tan`, `noor.a`, `daniel.g`); Confluence space `fincrime` read by that group only, with case notes about the **fictional** customer "Harbourline Trading Pte Ltd" (`FINCRIME-case-confluence`, `FINCRIME-str-confluence`); private channel `#fincrime-cases` (`FINCRIME-case-slack`); Drive folder "FinCrime case files" with inheritance stopped (`FINCRIME-case-drive`). Space `cx` (accounts, support, product) holds the approved customer script (`CX-review-script`). The customer's name appears only in documents the FinCrime team alone can read. | `FC-q1`/`FC-q2` (`amy.am`: the real customer vs the non-existent "Bayfront Spice Traders": same answer, from the script), `FC-q3` (`mlro.tan`: the case notes), `FC-q4`/`FC-q5` (`amy.am`: the STR question, refused identically) |
| **MK** market scope as content | Product pages whose first line is `[Applies to: SG entity]` and similar (`MK-sg-mca`, `MK-id-mca`, `MK-yield-sg`, each with `markets`). Market tags are never an access dimension. | `MK-q1` (Singapore), `MK-q2` (Indonesia), `MK-q3` (yield) |
| **FEE** approved-only | `Fee schedule: SG (v3)` is `current` (`FEE-v3`); `Fee schedule: SG (DRAFT v4)` has `status: draft` (`FEE-draft`, readable by nobody, so never indexed). Facts carry `policy_status`. | `FEE-q1` |
| **SENS** ingest masking | Published test values only: PANs `4111 1111 1111 1111` (Jira `PAY-236`) and `5555 5555 5555 4444` (`#product`), `CVV: 123`, the example IBAN `GB82 WEST 1234 5698 7654 32`, and the invented NRIC `S1234567D`. Each fact records `sensitive_kind`, `raw` and the expected `masked` form (`databridge_ib/sensitive.py`, rules in `packs/enterprise/sensitive.yaml`). | `SENS-q1`, `SENS-q2` |

New personas: `amy.am` (account manager: `grp-accounts`, inside `grp-staff`; reads `cx`, not `fincrime`) and
`mlro.tan` (MLRO, roles `["employee"]`). Filler users `joel.s`, `noor.a` and `daniel.g` support them.

`manifest.json` also lists `p2_pairs`: question pairs whose two responses must be byte-identical apart from
the request id and timestamps, with the same citations.
