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

- **Confluence:** a reader needs read access to the space (users or groups). They must also appear in the read restriction of the page and of every ancestor page that has one. These restrictions only narrow access, so they are intersected. A `trashed` or `archived` page is readable by nobody.
- **Jira:** a reader needs Browse on the project. Browse comes from the actors of each role listed in `browse.roles`, from `browse.users` and `browse.groups`, and from being the issue's reporter or assignee when those flags are on. If the issue has a security level, the reader must also hold that level. The level's holders are its users, its groups, the actors of its roles, and the reporter or assignee when those flags are on.
- **Slack:** for a public channel, readers are every active user of kind `member` plus the channel's own members. Guests and Slack Connect users only see channels they belong to. For a private or connect channel, readers are the channel's members.
- **Drive:** readers come from the file's own permissions plus each ancestor folder's permissions, then the shared drive's members. A folder with `inherited_permissions_disabled: true` stops this climb: its own permissions still apply, but nothing above it (including drive members) does. A `domain` permission matches users by email domain. If a file has any `anyone` permission, the IB denies the whole file.
- **Every platform:** inactive users are denied.

## Facts

Each fact has a natural-language sentinel sentence that appears in exactly one document.

- **Canaries.** A fact whose document is not readable by every active workspace member also has a `CNRY-xxxxxx` canary in the text. No other document contains that token.
- **`permitted` and `denied`.** These partition the seven personas: `jdoe`, `priya.sre`, `ken.ext`, `sara.sec`, `maya.compliance`, `leo.ops` and `admin.it`. They describe the **initial** state.
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
