# WorkBuddy GUI retest (finals connectors)

An exact checklist for re-running the WorkBuddy beat in the real WorkBuddy desktop app (5.7.x) with the
finals connectors (CodeBuddy session CB8). The same calls already pass programmatically against the live
Internal Brain: [workbuddy_live_test.md](workbuddy_live_test.md). This test adds what only the real app
can show: WorkBuddy's own agent choosing the tools, and the screenshots.

Time needed: about 20 minutes.

## 1. Before you start

1. The demo stack is running: `curl http://127.0.0.1:8100/health` prints `{"ok":true}`.
   If not, from `databridge/`: `bash scripts/demo_up.sh start` (no `--fresh`: it would wipe the demo history).
2. Do **not** run the tamper demo from the web app at the same time as prompt 6 (one demo runs at a time;
   a second one gets "already running").
3. Close WorkBuddy completely (also from the tray).

## 2. Load the connectors (tokens minted fresh)

From the repo root, in Git Bash:

```bash
databridge/.venv/Scripts/python.exe databridge/scripts/workbuddy_setup.py --user-config
```

This mints three fresh 8-hour tokens and writes the two employee connectors (`ib-as-jdoe`, `ib-as-ken`)
into `~/.workbuddy-ai/mcp.json` (other servers in that file are kept; a stale `ib-compliance` entry is
removed). The compliance connector is never written into the same list as the employee connectors
(round 2b review, finding 2): run prompts 5 and 6 from the `IB compliance` workspace folder in
`../workbuddy-demo/` (its own `.workbuddy/mcp.json`, server name `internal-brain-audit`), or on a
separate compliance seat with `--compliance-user-config`. It never prints a token. Tokens also land in the three workspace
folders next to the repo (`../workbuddy-demo/`), outside git.

What each connector is (all three use the same command; only the module and the token differ):

| Connector | `command` | `args` | `cwd` | `env` |
|---|---|---|---|---|
| `ib-as-jdoe` | `<repo>/databridge/.venv/Scripts/python.exe` | `["-m", "databridge_ib.mcp_server"]` | `<repo>/databridge` | `PYTHONPATH=<repo>/databridge/apps/internal_brain`, `IB_URL=http://127.0.0.1:8100`, `IB_TOKEN=<fresh token for jdoe>` |
| `ib-as-ken` | same | `["-m", "databridge_ib.mcp_server"]` | same | same, `IB_TOKEN=<fresh token for ken.ext>` |
| `ib-compliance` (compliance seat or `IB compliance` workspace only) | same | `["-m", "databridge_ib.audit_mcp_server"]` | same | same, `IB_TOKEN=<fresh token for maya.compliance>` |

To make one by hand instead: `databridge/.venv/Scripts/python.exe databridge/scripts/ib_mint_token.py jdoe --out <file outside the repo>`, then paste the file's contents into `IB_TOKEN`.

Then:

1. Start WorkBuddy.
2. Open **Experts · Skills · Connectors → Custom connectors**.
3. Press **Trust** on `ib-as-jdoe` and `ib-as-ken` (and, in the compliance workspace, on its audit connector).
4. Expand each one and check its tool list:
   - `ib-as-jdoe` and `ib-as-ken`: `ask_internal_brain`, `my_recent_questions`, `my_answer_receipt`, `what_can_i_open`;
   - `ib-compliance`: `audit_events`, `audit_doc_accessors`, `audit_verify`, `org_activity_summary`, `run_tamper_demo`.
5. Add the Walnut skill: paste the body of [`docs/tencent/workbuddy/WALNUT_SKILL.md`](../../../tencent/workbuddy/WALNUT_SKILL.md)
   as a new skill (or custom expert) named **Walnut** and switch it on for the chat.

**Screenshot S1**: the connector panel with the two employee connectors trusted; take the compliance tool list from the `IB compliance` workspace.
Save as `submission/02_workbuddy/finals/wb_s1_connectors.png`.

## 3. The six prompts

Open **one new task** with the Walnut skill on. Type each prompt exactly (copy and paste). Wait for the
agent to finish before the next one.

| # | Type this | Connector the agent should call | Expected result | Screenshot |
|---|---|---|---|---|
| 1 | `Using the ib-as-jdoe connector: What's the status of the database migration project and were there any blockers raised in Slack last week?` | `ib-as-jdoe` → `ask_internal_brain` | A cited answer with sources from **Slack, Jira and Confluence** (the replication-lag blocker in `#db-migration`, DBMIG-12 wave 4 cutover, the DB Migration Plan). The reply quotes `answered as jdoe` and a `request id`. The question is passed word for word | **S2** `wb_s2_jordan_answer.png` (tool call expanded, so the arguments show only `question`) |
| 2 | `Show me the receipt for that answer.` | `ib-as-jdoe` → `my_answer_receipt` with the request id from #1 | The question, status `answered`, the model, the stages (accept, gate1, gate2, seal, compose, release), the cited documents Jordan can still open, and the sealed record kinds with short hashes | **S3** `wb_s3_receipt.png` |
| 3 | `Using the ib-as-ken connector: Why is Velquarry Trading's account restricted?` | `ib-as-ken` → `ask_internal_brain` | The constant reply "I couldn't find an answer to that in the sources you can access.", `(no citations)`, `answered as ken.ext`. The agent must not guess why or retry with other words | **S4** `wb_s4_ken_velquarry.png` |
| 4 | `Using the ib-as-ken connector: Why is Bayfront Spice Traders' account restricted?` | `ib-as-ken` → `ask_internal_brain` | **The same reply as #3** apart from the request id: a made-up company looks exactly like a restricted one | **S5** `wb_s5_ken_bayfront.png` (put #3 and #4 in one shot if they fit) |
| 5 | (in the `IB compliance` workspace, a new task) `Using the ib-compliance connector: Summarise this month's activity and verify the audit chain.` | `ib-compliance` → `org_activity_summary` then `audit_verify` | Headline numbers (requests, answered, refused, the document funnel, latency, models) with **`workbuddy-mcp` in the channels**, then `Audit ledger verification: OK` with the stored-answer check intact | **S6** `wb_s6_compliance_summary.png` |
| 6 | `Using the ib-compliance connector: Run the tamper demo.` | `ib-compliance` → `run_tamper_demo` | One line per scenario, every one "as expected"; **real log modified: no**. Then ask `Verify the audit chain again.`: still OK | **S7** `wb_s7_tamper.png` |

## 4. Cross-check in the web app (one screenshot)

1. Sign in as `maya.compliance` and open `/dev/audit?latest=1`.
2. The newest `request.accepted` rows are Jordan's and Ken's questions from this test, with channel `workbuddy-mcp`.
3. **Screenshot S8** `wb_s8_audit_rows.png`.

## 5. Pass or fail

Pass when all of these hold:

- [ ] S1: three connectors trusted; the finals tools are listed.
- [ ] #1 is cited from three platforms and says `answered as jdoe`.
- [ ] No tool call shows a user, persona or name argument (only `question`, `request_id`, `days` or nothing).
- [ ] #3 and #4 are identical apart from the request id.
- [ ] #5 shows `workbuddy-mcp` in the channels and the chain verifies.
- [ ] #6 reports every scenario as expected and `real log modified: no`; verify is still OK afterwards.
- [ ] S8 shows the new rows with channel `workbuddy-mcp`.

If the agent rewords question #1 before calling the tool, the answer can lose platforms. Check that the
Walnut skill is switched on (rule: pass the question word for word) and run #1 again.

## 6. Afterwards

1. Copy the screenshots into `submission/02_workbuddy/finals/`.
2. Remove the three connectors from `~/.workbuddy-ai/mcp.json` after the demo (tokens hold access for up to 8 hours).
3. Never put a token in a screenshot: keep the connector's env editor closed when shooting S1.
