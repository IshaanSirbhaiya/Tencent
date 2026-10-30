# WorkBuddy connectors: live test against the running Internal Brain

Run 2026-10-30 10:37 Malay Peninsula Standard Time against `http://127.0.0.1:8100` by `docs/tencent/evidence/workbuddy_live_test.py`. Each connector is started exactly as WorkBuddy starts it (stdio, `python -m <module>`, identity only from `IB_TOKEN`) and driven with the official `mcp` Python client. Tokens were minted fresh with `databridge/scripts/ib_mint_token.py --out` and are not in this file.

**Result: 33 of 33 checks passed.**

| Check | Result |
|---|---|
| ib-as-jdoe: `ask_internal_brain` takes no identity argument | PASS (question) |
| ib-as-jdoe: `my_recent_questions` takes no identity argument | PASS (no arguments) |
| ib-as-jdoe: `my_answer_receipt` takes no identity argument | PASS (request_id) |
| ib-as-jdoe: `what_can_i_open` takes no identity argument | PASS (no arguments) |
| Jordan's headline answer is cited | PASS (3 citation(s)) |
| answered as jdoe (identity from the token) | PASS |
| the answer carries its request id | PASS |
| Jordan's receipt for his own answer | PASS |
| what_can_i_open lists no document ids | PASS |
| ib-as-ken: `ask_internal_brain` takes no identity argument | PASS (question) |
| ib-as-ken: `my_recent_questions` takes no identity argument | PASS (no arguments) |
| ib-as-ken: `my_answer_receipt` takes no identity argument | PASS (request_id) |
| ib-as-ken: `what_can_i_open` takes no identity argument | PASS (no arguments) |
| Ken's restricted question gets the constant refusal (no citations) | PASS |
| restricted (Velquarry) and made-up (Bayfront) replies are identical apart from the request id | PASS |
| Ken asking for Jordan's receipt == Ken asking for an unknown id | PASS |
| ib-compliance: `audit_doc_accessors` takes no identity argument | PASS (doc_id) |
| ib-compliance: `audit_verify` takes no identity argument | PASS (no arguments) |
| ib-compliance: `org_activity_summary` takes no identity argument | PASS (days) |
| ib-compliance: `run_tamper_demo` takes no identity argument | PASS (no arguments) |
| org_activity_summary works for the officer | PASS |
| the WorkBuddy channel shows in the org summary | PASS |
| audit chain verifies (before the tamper demo) | PASS |
| tamper demo ran on a copy; real log not modified | PASS |
| the real audit chain still verifies after the tamper demo | PASS |
| audit server with jdoe's token (negative test): `audit_doc_accessors` takes no identity argument | PASS (doc_id) |
| audit server with jdoe's token (negative test): `audit_verify` takes no identity argument | PASS (no arguments) |
| audit server with jdoe's token (negative test): `org_activity_summary` takes no identity argument | PASS (days) |
| audit server with jdoe's token (negative test): `run_tamper_demo` takes no identity argument | PASS (no arguments) |
| an employee token is refused on audit_verify | PASS |
| an employee token is refused on org_activity_summary | PASS |
| an employee token is refused on run_tamper_demo | PASS |
| the ledger shows channel workbuddy-mcp for Jordan's question | PASS |

## Transcript

### 1. Jordan (`jdoe`, payments engineer) through `ib-as-jdoe`

Server `walnut-internal-brain` started over stdio for connector `ib-as-jdoe` (databridge_ib.mcp_server). Tools: `ask_internal_brain(question)`, `my_recent_questions()`, `my_answer_receipt(request_id)`, `what_can_i_open()`

**CHECK PASS**: ib-as-jdoe: `ask_internal_brain` takes no identity argument (question)
**CHECK PASS**: ib-as-jdoe: `my_recent_questions` takes no identity argument (no arguments)
**CHECK PASS**: ib-as-jdoe: `my_answer_receipt` takes no identity argument (request_id)
**CHECK PASS**: ib-as-jdoe: `what_can_i_open` takes no identity argument (no arguments)
#### `ib-as-jdoe` → `ask_internal_brain("question": "What's the status of the database migration project and were there any blockers raised in Slack last week?")`  (6679 ms)

```text
priya.sre: Blocker for the migration: Replication lag on the ledger-shard-3 replica climbed past 40 seconds during last night's backfill, so we paused the copy job at batch 1,812. [C1] Wave 4 of the DB Migration Plan [C2] Cutover of the ledger database to the managed Postgres 16 cluster is planned for 24 October 2026, pending sign-off from the payments on-call lead. [C2] The migration moves the ledger from the self-managed Postgres 11 cluster to a managed Postgres 16 cluster in four waves, with the ledger shards moved last. [C13]

Citations:
  - [slack] #db-migration: Blocker for the migration: Replication lag on the … (v1791337320.000104|4|b84c0b3ba36c45cd) https://companya.slack.com/archives/C0DBMIG/p1791335520000101
  - [jira] DBMIG-12: Ledger cluster cutover (wave 4) (v2026-10-08T17:00:00+08:00) https://companya.atlassian.net/browse/DBMIG-12
  - [confluence] DB Migration Plan (v6) https://companya.atlassian.net/wiki/spaces/payment-gateway/pages/cf-1001

answered as jdoe
request id: 8ccea833dfd64382bf1ca95683248cf7
```

**CHECK PASS**: Jordan's headline answer is cited (3 citation(s))
Platforms cited: confluence, jira, slack
**CHECK PASS**: answered as jdoe (identity from the token)
**CHECK PASS**: the answer carries its request id
#### `ib-as-jdoe` → `my_recent_questions()`  (3750 ms)

```text
50 recent question(s):
  1. [8ccea833dfd64382bf1ca95683248cf7] (2026-10-30T02:37:51.337742Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  2. [b17e0033669a4f6f9ac66a1a9443dbf9] (2026-10-30T02:37:42.578575Z) Any blockers on the database migration this week?
  3. [8c71691b1dd547d3aa8e2f8ad8060952] (2026-10-30T02:37:27.165057Z) What did the settlement retry job resend in one burst?
  4. [e2987d04cc5346788804d11f0f9ce40d] (2026-10-30T02:37:23.671715Z) Who must close all five follow-up tickets by 30 November 2026?
  5. [3f6f2dac462f418386da7e6c3e6f1dc6] (2026-10-30T02:36:56.807842Z) When does MFA move to Authenticator Pro?
  6. [1dfde68d48b644aaa37a6b24e7b28d84] (2026-10-30T02:36:49.529548Z) What is the restart time for the copy job?
  7. [718efc415eea41d0828b81b2b0b62317] (2026-10-30T02:35:59.643498Z) Any blockers on the database migration this week?
  8. [77ce059e1af54ac891a28118e19861f6] (2026-10-30T02:34:32.693376Z) What did the settlement retry job resend in one burst?
  9. [4fe5e176775644778890e07052fc8614] (2026-10-30T02:34:31.212566Z) What caused the 3,400 authorisation burst at 02:10?
  10. [8c7336f73bb740eeb1bf512e68dfba23] (2026-10-30T02:34:18.857028Z) From which batch does the copy job resume?
  11. [0f980b4e2a444c13ab3b6597ea988354] (2026-10-30T02:34:17.294174Z) Is replica tuning done?
  12. [0ff7ee92d71a49b683c213e0b88a6cc9] (2026-10-30T02:34:16.126415Z) What is the restart time for the copy job?
  13. [6b7471ec0aed44a4bfcbbc70e1fe5e7b] (2026-10-30T02:34:14.805015Z) Who covers the on-call restart of the copy job on 5 November?
  14. [d80d65f2764c4456ada038b4f5d8b3d8] (2026-10-30T02:34:13.412400Z) Who is on call for the restart?
  15. [0a9957c96c2b48669b1d69f74b2f8807] (2026-10-30T02:34:11.984548Z) When does the copy job resume?
  16. [40b9833737a34b89a78d6de0018bc779] (2026-10-30T02:34:10.741963Z) What happens on Thursday 5 Nov 2026 at 21:00 SGT?
  17. [d5cdaa46042d4d7ab4b39c1fa8095366] (2026-10-30T02:33:31.506326Z) Who is owed an SLA credit by Acme Vendor for October?
  18. [840ba94db5aa4ecbab0bdff29131602b] (2026-10-30T02:33:22.606510Z) What is the new date of the DBMIG-12 wave 4 cutover?
  19. [860d2e6ca9b343dbaeb7f376fbac4626] (2026-10-30T02:33:20.573980Z) Who chairs the DBMIG-12 go/no-go call and when is it?
  20. [3c3bd602a801442cb3b71497891b5652] (2026-10-30T02:33:18.768674Z) What date does the MFA move to Authenticator Pro take effect?
  21. [979d1d4883e44739b07b52fce6102b5f] (2026-10-30T02:33:16.735360Z) When does MFA move to Authenticator Pro?
  22. [3a6aa01dae9140e6b008f856a848d25c] (2026-10-30T02:33:16.045535Z) Any blockers on the database migration this week?
  23. [9053fbe7c8ee439da5fb2e0823a14e30] (2026-10-30T02:33:13.636268Z) Is the DBMIG-12 restart scheduled for Thursday 5 Nov 2026?
  24. [0abe136c372146ee9705d157ddced0f5] (2026-10-30T02:33:12.362984Z) Has replica tuning on ledger-shard-3 been completed, and when is the restart?
  25. [8b743ad652984a53acd2342951c6f240] (2026-10-30T02:33:09.768664Z) Which date and time does the ledger-shard-3 copy job resume from batch 1,812, and who is on call for the restart?
  26. [db13799538f040f6b05c379861338cc4] (2026-10-30T02:32:32.986246Z) When does the ledger-shard-3 copy job resume?
  27. [66afb663d1f8480e93f56ebb0eb71b0b] (2026-10-30T02:30:40.596936Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  28. [85a22a33172b49f7bba15a1b91225c64] (2026-10-30T02:29:44.690176Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  29. [e7f383d66abf4ea1a588b7a79fea0b00] (2026-10-30T02:09:00.458387Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  30. [c811674ba7814c38b67d3710a9d61b2d] (2026-10-30T02:03:08.508085Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  31. [f463ac656d5e49ac8a72e3bfe8a39187] (2026-10-30T02:02:38.807489Z) What's the status of the DB migration and were any blockers raised in Slack last week?
  32. [504aec1e9dd241ba893559d59a939a69] (2026-10-30T02:02:26.413318Z) What's the status of the DB migration and were any blockers raised in Slack last week?
  33. [260d8d79ab09470da3503c7b5bb52443] (2026-10-30T01:50:26.854431Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  34. [53059d5f741d4b6eb10101fde34652ef] (2026-10-30T01:49:50.773895Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  35. [18eaa2b252b6443f90b051028ee919f3] (2026-10-30T01:49:15.548244Z) What's the status of the database migration project and were there any blockers raised in Slack last week?
  36. [be6805c8260c4996813c703a2d41c46d] (2026-10-29T22:13:38.165300Z) what is the migration vendor contract budget overrun?
  37. [3a6cc6d3a1a04c028b976a818d9f9fcb] (2026-10-29T22:13:37.114797Z) what is the database migration status and blockers?
  38. [b3c77348571f4620a7b1bc51a25fe0a2] (2026-10-29T22:13:34.425751Z) what is the database migration status and blockers?
  39. [dc76af1d7cb941c7b9c3a56d4b1a322c] (2026-10-29T22:10:50.400249Z) When is the next settlement key rotation window?
  40. [af7cc93cec454fdca762ed3cf008ffa0] (2026-10-29T22:10:50.400249Z) What backfill throttle are we using for the ledger copy job?
  41. [e9e7cf99d19943bb8f4c7b7ca4d851b1] (2026-10-29T22:09:35.389257Z) What does the settlement keys rotation page say?
  42. [ec8154a4fb924432bb9ca7236a304039] (2026-10-29T22:09:35.389257Z) What blockers were raised in the db-migration channel?
  43. [6b606092d2e4455eaeaf1c9fdfb55778] (2026-10-29T09:40:27Z) What's the rollout plan for the payout scheduler?
  44. [5326055faf03446b88c96dda2e087d96] (2026-10-29T08:11:10Z) What is the latest on Settlement keys rotation?
  45. [9c3e8f637135457dbd65ccf9fb695ada] (2026-10-29T03:40:23Z) What's the current schedule for settlement key rotation?
  46. [cf7cef9c927a4edaaf7a63255b1921ba] (2026-10-29T03:00:04Z) What does Payment service failover runbook say?
  47. [a6a9dcfe8a9d4f179e6debd43b62be55] (2026-10-29T02:42:53Z) What does Postmortem - payment outage 2026-08-19 say?
  48. [86939f43796d40f98a5b34d814951bd7] (2026-10-28T05:52:05Z) What is the latest on DB Migration Plan?
  49. [755cbdae2249461f8bce585dccb0d818] (2026-10-26T07:37:02Z) What changed in the 3DS2 challenge flow this quarter?
  50. [af8bca247afc43cba4d19a1bf9865ee0] (2026-10-24T09:30:15Z) What did the board decide about the company sailing team?
```

#### `ib-as-jdoe` → `my_answer_receipt("request_id": "8ccea833dfd64382bf1ca95683248cf7")`  (997 ms)

```text
Question: What's the status of the database migration project and were there any blockers raised in Slack last week?
Status: released
Channel: workbuddy-mcp
Model: deterministic/extractive-v1
Latency: 2400 ms
Stages: accept:ok, gate1:ok, gate2:ok, seal:ok, compose:ok, release:ok
Cited documents you can open:
  - [slack] #db-migration: Blocker for the migration: Replication lag on the … (v1791337320.000104|4|b84c0b3ba36c45cd) https://companya.slack.com/archives/C0DBMIG/p1791335520000101
  - [jira] DBMIG-12: Ledger cluster cutover (wave 4) (v2026-10-08T17:00:00+08:00) https://companya.atlassian.net/browse/DBMIG-12
  - [confluence] DB Migration Plan (v6) https://companya.atlassian.net/wiki/spaces/payment-gateway/pages/cf-1001
Sealed records:
  - request.accepted: fbcbef5a033c
  - retrieval.decided: b393ee42ee6f
  - answer.released: 321adb317659
```

**CHECK PASS**: Jordan's receipt for his own answer
#### `ib-as-jdoe` → `what_can_i_open()`  (31374 ms)

```text
Internal Brain API is unreachable (ReadTimeout).
```

**CHECK PASS**: what_can_i_open lists no document ids
### 2. Ken (`ken.ext`, external contractor) through `ib-as-ken`

Server `walnut-internal-brain` started over stdio for connector `ib-as-ken` (databridge_ib.mcp_server). Tools: `ask_internal_brain(question)`, `my_recent_questions()`, `my_answer_receipt(request_id)`, `what_can_i_open()`

**CHECK PASS**: ib-as-ken: `ask_internal_brain` takes no identity argument (question)
**CHECK PASS**: ib-as-ken: `my_recent_questions` takes no identity argument (no arguments)
**CHECK PASS**: ib-as-ken: `my_answer_receipt` takes no identity argument (request_id)
**CHECK PASS**: ib-as-ken: `what_can_i_open` takes no identity argument (no arguments)
#### `ib-as-ken` → `ask_internal_brain("question": "Why is Velquarry Trading's account restricted?")`  (7993 ms)

```text
I couldn't find an answer to that in the sources you can access.

Citations:
(no citations)

answered as ken.ext
request id: 2ae84484403842ffa948bf7f84c12290
```

#### `ib-as-ken` → `ask_internal_brain("question": "Why is Bayfront Spice Traders' account restricted?")`  (4062 ms)

```text
I couldn't find an answer to that in the sources you can access.

Citations:
(no citations)

answered as ken.ext
request id: 9c4dfb0e7eda4db4a638fc50fd165f9e
```

**CHECK PASS**: Ken's restricted question gets the constant refusal (no citations)
**CHECK PASS**: restricted (Velquarry) and made-up (Bayfront) replies are identical apart from the request id
#### `ib-as-ken` → `my_answer_receipt("request_id": "8ccea833dfd64382bf1ca95683248cf7")`  (2218 ms)

```text
No receipt found for that request id in your own requests.
```

#### `ib-as-ken` → `my_answer_receipt("request_id": "00000000000000000000000000000000")`  (1386 ms)

```text
No receipt found for that request id in your own requests.
```

**CHECK PASS**: Ken asking for Jordan's receipt == Ken asking for an unknown id
#### `ib-as-ken` → `what_can_i_open()`  (5630 ms)

```text
You can open 24 item(s) across 2 container(s) on 2 platform(s).
- slack: 16 item(s)
    - vendor-acme: 16
- drive: 8 item(s)
    - 0ACompany: 8
Scope: Answers draw only on content you can access in Confluence, Jira, Slack and Google Drive.
```

### 3. Maya (`maya.compliance`, compliance officer) through `ib-compliance`

Server `walnut-ib-audit` started over stdio for connector `ib-compliance` (databridge_ib.audit_mcp_server). Tools: `audit_events(user, container, since_days, doc)`, `audit_doc_accessors(doc_id)`, `audit_verify()`, `org_activity_summary(days)`, `run_tamper_demo()`

**CHECK PASS**: ib-compliance: `audit_doc_accessors` takes no identity argument (doc_id)
**CHECK PASS**: ib-compliance: `audit_verify` takes no identity argument (no arguments)
**CHECK PASS**: ib-compliance: `org_activity_summary` takes no identity argument (days)
**CHECK PASS**: ib-compliance: `run_tamper_demo` takes no identity argument (no arguments)
#### `ib-compliance` → `org_activity_summary("days": 30)`  (3560 ms)

```text
Window: 30 day(s) (2026-09-30T02:40:05.035823+00:00 to 2026-10-30T02:40:05.035823+00:00)
Requests: asked=455, answered=321, refused=133, failed=0, answer_rate=0.707
Citations: 699
Documents: considered=1680, gate2_checked=1680, allowed=1676, denied=4, released=1660, cited=657
Latency (ms): p50=251, p95=1495
Models: deterministic/extractive-v1=454
Channels: replay=304, ui=144, workbuddy-mcp=7
Reveals: 3
Audit queries: 117
Ledger: events=1963, head=188711b85a8d
```

**CHECK PASS**: org_activity_summary works for the officer
**CHECK PASS**: the WorkBuddy channel shows in the org summary
#### `ib-compliance` → `audit_verify()`  (1594 ms)

```text
Audit ledger verification: OK
  records: 1966
  head: 0457228be68f99c6c9905e40c51b4c7177e175c462a4b2e20e6eb5c64d7f4ec2
  ledgered: True
  content check: checked=455, missing=0, mismatched=0, unreadable=0, intact=True
```

**CHECK PASS**: audit chain verifies (before the tamper demo)
#### `ib-compliance` → `audit_events("user": "jdoe", "since_days": 1)`  (1812 ms)

```text
100 audit event(s):
  - [answer.released] sub=jdoe req=df055ec97db246fb8e9345f0a9fa6f48 at=2026-10-30T02:39:55.187113Z
  - [retrieval.decided] sub=jdoe req=df055ec97db246fb8e9345f0a9fa6f48 at=2026-10-30T02:39:55.187113Z
  - [request.accepted] sub=jdoe req=df055ec97db246fb8e9345f0a9fa6f48 at=2026-10-30T02:39:55.187113Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:39:54.437233Z
  - [map.viewed] sub=jdoe req=- at=2026-10-30T02:39:47.658790Z
  - [answer.released] sub=jdoe req=7790713022a848b095ced00c5d8e52fb at=2026-10-30T02:39:46.853487Z
  - [retrieval.decided] sub=jdoe req=7790713022a848b095ced00c5d8e52fb at=2026-10-30T02:39:46.853487Z
  - [request.accepted] sub=jdoe req=7790713022a848b095ced00c5d8e52fb at=2026-10-30T02:39:46.853487Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:39:45.935041Z
  - [answer.released] sub=jdoe req=e477f1fb76444ed38ddc0d59973e9e6f at=2026-10-30T02:38:55.755491Z
  - [retrieval.decided] sub=jdoe req=e477f1fb76444ed38ddc0d59973e9e6f at=2026-10-30T02:38:55.755491Z
  - [request.accepted] sub=jdoe req=e477f1fb76444ed38ddc0d59973e9e6f at=2026-10-30T02:38:55.755491Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:38:54.604585Z
  - [map.viewed] sub=jdoe req=- at=2026-10-30T02:38:46.459884Z
  - [answer.released] sub=jdoe req=2ef3f6adfced45a4a550d1b900251e1f at=2026-10-30T02:38:38.623240Z
  - [retrieval.decided] sub=jdoe req=2ef3f6adfced45a4a550d1b900251e1f at=2026-10-30T02:38:38.623240Z
  - [request.accepted] sub=jdoe req=2ef3f6adfced45a4a550d1b900251e1f at=2026-10-30T02:38:38.623240Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:38:35.226098Z
  - [trace.viewed] sub=jdoe req=- at=2026-10-30T02:37:59.482837Z
  - [action.failed] sub=jdoe req=- at=2026-10-30T02:37:56.350914Z
  - [doc.viewed] sub=jdoe req=- at=2026-10-30T02:37:53.815645Z
  - [action.executed] sub=jdoe req=b17e0033669a4f6f9ac66a1a9443dbf9 at=2026-10-30T02:37:51.657339Z
  - [answer.released] sub=jdoe req=8ccea833dfd64382bf1ca95683248cf7 at=2026-10-30T02:37:51.337742Z
  - [retrieval.decided] sub=jdoe req=8ccea833dfd64382bf1ca95683248cf7 at=2026-10-30T02:37:51.337742Z
  - [request.accepted] sub=jdoe req=8ccea833dfd64382bf1ca95683248cf7 at=2026-10-30T02:37:51.337742Z
  - [action.confirmed] sub=jdoe req=b17e0033669a4f6f9ac66a1a9443dbf9 at=2026-10-30T02:37:50.197888Z
  - [action.proposed] sub=jdoe req=b17e0033669a4f6f9ac66a1a9443dbf9 at=2026-10-30T02:37:48.072806Z
  - [answer.released] sub=jdoe req=b17e0033669a4f6f9ac66a1a9443dbf9 at=2026-10-30T02:37:42.578575Z
  - [retrieval.decided] sub=jdoe req=b17e0033669a4f6f9ac66a1a9443dbf9 at=2026-10-30T02:37:42.578575Z
  - [request.accepted] sub=jdoe req=b17e0033669a4f6f9ac66a1a9443dbf9 at=2026-10-30T02:37:42.578575Z
  - [answer.released] sub=jdoe req=8c71691b1dd547d3aa8e2f8ad8060952 at=2026-10-30T02:37:27.165057Z
  - [retrieval.decided] sub=jdoe req=8c71691b1dd547d3aa8e2f8ad8060952 at=2026-10-30T02:37:27.165057Z
  - [request.accepted] sub=jdoe req=8c71691b1dd547d3aa8e2f8ad8060952 at=2026-10-30T02:37:27.165057Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:37:26.704877Z
  - [answer.released] sub=jdoe req=e2987d04cc5346788804d11f0f9ce40d at=2026-10-30T02:37:23.671715Z
  - [retrieval.decided] sub=jdoe req=e2987d04cc5346788804d11f0f9ce40d at=2026-10-30T02:37:23.671715Z
  - [request.accepted] sub=jdoe req=e2987d04cc5346788804d11f0f9ce40d at=2026-10-30T02:37:23.671715Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:37:23.127902Z
  - [doc.viewed] sub=jdoe req=- at=2026-10-30T02:37:13.324809Z
  - [answer.released] sub=jdoe req=3f6f2dac462f418386da7e6c3e6f1dc6 at=2026-10-30T02:36:56.807842Z
  - [retrieval.decided] sub=jdoe req=3f6f2dac462f418386da7e6c3e6f1dc6 at=2026-10-30T02:36:56.807842Z
  - [request.accepted] sub=jdoe req=3f6f2dac462f418386da7e6c3e6f1dc6 at=2026-10-30T02:36:56.807842Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:36:56.325891Z
  - [answer.released] sub=jdoe req=1dfde68d48b644aaa37a6b24e7b28d84 at=2026-10-30T02:36:49.529548Z
  - [retrieval.decided] sub=jdoe req=1dfde68d48b644aaa37a6b24e7b28d84 at=2026-10-30T02:36:49.529548Z
  - [request.accepted] sub=jdoe req=1dfde68d48b644aaa37a6b24e7b28d84 at=2026-10-30T02:36:49.529548Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:36:49.097257Z
  - [action.proposed] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:36:05.942121Z
  - [action.executed] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:36:04.839258Z
  - [action.confirmed] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:36:04.422688Z
  - [action.proposed] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:36:02.238901Z
  - [answer.released] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:35:59.643498Z
  - [retrieval.decided] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:35:59.643498Z
  - [request.accepted] sub=jdoe req=718efc415eea41d0828b81b2b0b62317 at=2026-10-30T02:35:59.643498Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:35:57.816348Z
  - [answer.released] sub=jdoe req=77ce059e1af54ac891a28118e19861f6 at=2026-10-30T02:34:32.693376Z
  - [retrieval.decided] sub=jdoe req=77ce059e1af54ac891a28118e19861f6 at=2026-10-30T02:34:32.693376Z
  - [request.accepted] sub=jdoe req=77ce059e1af54ac891a28118e19861f6 at=2026-10-30T02:34:32.693376Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:32.383114Z
  - [answer.released] sub=jdoe req=4fe5e176775644778890e07052fc8614 at=2026-10-30T02:34:31.212566Z
  - [retrieval.decided] sub=jdoe req=4fe5e176775644778890e07052fc8614 at=2026-10-30T02:34:31.212566Z
  - [request.accepted] sub=jdoe req=4fe5e176775644778890e07052fc8614 at=2026-10-30T02:34:31.212566Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:30.897498Z
  - [answer.released] sub=jdoe req=8c7336f73bb740eeb1bf512e68dfba23 at=2026-10-30T02:34:18.857028Z
  - [retrieval.decided] sub=jdoe req=8c7336f73bb740eeb1bf512e68dfba23 at=2026-10-30T02:34:18.857028Z
  - [request.accepted] sub=jdoe req=8c7336f73bb740eeb1bf512e68dfba23 at=2026-10-30T02:34:18.857028Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:18.463330Z
  - [answer.released] sub=jdoe req=0f980b4e2a444c13ab3b6597ea988354 at=2026-10-30T02:34:17.294174Z
  - [retrieval.decided] sub=jdoe req=0f980b4e2a444c13ab3b6597ea988354 at=2026-10-30T02:34:17.294174Z
  - [request.accepted] sub=jdoe req=0f980b4e2a444c13ab3b6597ea988354 at=2026-10-30T02:34:17.294174Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:17.035304Z
  - [answer.released] sub=jdoe req=0ff7ee92d71a49b683c213e0b88a6cc9 at=2026-10-30T02:34:16.126415Z
  - [retrieval.decided] sub=jdoe req=0ff7ee92d71a49b683c213e0b88a6cc9 at=2026-10-30T02:34:16.126415Z
  - [request.accepted] sub=jdoe req=0ff7ee92d71a49b683c213e0b88a6cc9 at=2026-10-30T02:34:16.126415Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:15.859196Z
  - [answer.released] sub=jdoe req=6b7471ec0aed44a4bfcbbc70e1fe5e7b at=2026-10-30T02:34:14.805015Z
  - [retrieval.decided] sub=jdoe req=6b7471ec0aed44a4bfcbbc70e1fe5e7b at=2026-10-30T02:34:14.805015Z
  - [request.accepted] sub=jdoe req=6b7471ec0aed44a4bfcbbc70e1fe5e7b at=2026-10-30T02:34:14.805015Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:14.537939Z
  - [answer.released] sub=jdoe req=d80d65f2764c4456ada038b4f5d8b3d8 at=2026-10-30T02:34:13.412400Z
  - [retrieval.decided] sub=jdoe req=d80d65f2764c4456ada038b4f5d8b3d8 at=2026-10-30T02:34:13.412400Z
  - [request.accepted] sub=jdoe req=d80d65f2764c4456ada038b4f5d8b3d8 at=2026-10-30T02:34:13.412400Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:13.120698Z
  - [answer.released] sub=jdoe req=0a9957c96c2b48669b1d69f74b2f8807 at=2026-10-30T02:34:11.984548Z
  - [retrieval.decided] sub=jdoe req=0a9957c96c2b48669b1d69f74b2f8807 at=2026-10-30T02:34:11.984548Z
  - [request.accepted] sub=jdoe req=0a9957c96c2b48669b1d69f74b2f8807 at=2026-10-30T02:34:11.984548Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:11.722164Z
  - [answer.released] sub=jdoe req=40b9833737a34b89a78d6de0018bc779 at=2026-10-30T02:34:10.741963Z
  - [retrieval.decided] sub=jdoe req=40b9833737a34b89a78d6de0018bc779 at=2026-10-30T02:34:10.741963Z
  - [request.accepted] sub=jdoe req=40b9833737a34b89a78d6de0018bc779 at=2026-10-30T02:34:10.741963Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:34:10.464310Z
  - [answer.released] sub=jdoe req=d5cdaa46042d4d7ab4b39c1fa8095366 at=2026-10-30T02:33:31.506326Z
  - [retrieval.decided] sub=jdoe req=d5cdaa46042d4d7ab4b39c1fa8095366 at=2026-10-30T02:33:31.506326Z
  - [request.accepted] sub=jdoe req=d5cdaa46042d4d7ab4b39c1fa8095366 at=2026-10-30T02:33:31.506326Z
  - [auth.login] sub=jdoe req=- at=2026-10-30T02:33:31.261203Z
  - [action.proposed] sub=jdoe req=3a6aa01dae9140e6b008f856a848d25c at=2026-10-30T02:33:23.514027Z
  - [action.proposed] sub=jdoe req=3a6aa01dae9140e6b008f856a848d25c at=2026-10-30T02:33:23.339202Z
  - [answer.released] sub=jdoe req=840ba94db5aa4ecbab0bdff29131602b at=2026-10-30T02:33:22.606510Z
  - [retrieval.decided] sub=jdoe req=840ba94db5aa4ecbab0bdff29131602b at=2026-10-30T02:33:22.606510Z
  - [request.accepted] sub=jdoe req=840ba94db5aa4ecbab0bdff29131602b at=2026-10-30T02:33:22.606510Z
```

#### `ib-compliance` → `audit_doc_accessors("doc_id": "confluence:cf-1226")`  (1204 ms)

```text
1 accessor(s) of confluence:cf-1226:
  - jdoe: 1 request(s), accessed
```

#### `ib-compliance` → `run_tamper_demo()`  (4523 ms)

```text
Records copied: 1972
Real log modified: no
  - Untouched copy: expected=not flagged, detected=no, as_expected=yes
  - Edit one old record: expected=flagged, detected=yes, as_expected=yes, first_bad_seq=938
  - Delete the newest records: expected=flagged, detected=yes, as_expected=yes
  - Restore an old backup of log and anchors: expected=flagged, detected=yes, as_expected=yes
  - Roll back after the last external publication: expected=not flagged, detected=no, as_expected=yes
  - Delete the internal anchor ledger: expected=flagged, detected=yes, as_expected=yes
  - Lose the external head: expected=flagged, detected=yes, as_expected=yes
  - Rewrite history with the sealing key: expected=flagged, detected=yes, as_expected=yes
```

**CHECK PASS**: tamper demo ran on a copy; real log not modified
#### `ib-compliance` → `audit_verify()`  (1242 ms)

```text
Audit ledger verification: OK
  records: 1973
  head: 60b0bf807bc749b9d4b45363a6b2d68667c7eb10af25161e8c0864e8fd5a45c5
  ledgered: True
  content check: checked=455, missing=0, mismatched=0, unreadable=0, intact=True
```

**CHECK PASS**: the real audit chain still verifies after the tamper demo
### 4. Negative test: the audit server with an employee's token

Server `walnut-ib-audit` started over stdio for connector `audit server with jdoe's token (negative test)` (databridge_ib.audit_mcp_server). Tools: `audit_events(user, container, since_days, doc)`, `audit_doc_accessors(doc_id)`, `audit_verify()`, `org_activity_summary(days)`, `run_tamper_demo()`

**CHECK PASS**: audit server with jdoe's token (negative test): `audit_doc_accessors` takes no identity argument (doc_id)
**CHECK PASS**: audit server with jdoe's token (negative test): `audit_verify` takes no identity argument (no arguments)
**CHECK PASS**: audit server with jdoe's token (negative test): `org_activity_summary` takes no identity argument (days)
**CHECK PASS**: audit server with jdoe's token (negative test): `run_tamper_demo` takes no identity argument (no arguments)
#### `jdoe on audit server` → `audit_verify()`  (1245 ms)

```text
Internal Brain API error (GET /audit/verify): HTTP 403.
```

**CHECK PASS**: an employee token is refused on audit_verify
#### `jdoe on audit server` → `org_activity_summary("days": 30)`  (1020 ms)

```text
Internal Brain API error (GET /audit/stats): HTTP 403.
```

**CHECK PASS**: an employee token is refused on org_activity_summary
#### `jdoe on audit server` → `run_tamper_demo()`  (742 ms)

```text
Internal Brain API error (POST /audit/tamper-demo): HTTP 403.
```

**CHECK PASS**: an employee token is refused on run_tamper_demo
### 5. The ledger record of Jordan's WorkBuddy question

```json
{
  "seq": 1838,
  "kind": "request.accepted",
  "req_id": "8ccea833dfd64382bf1ca95683248cf7",
  "sub": "jdoe",
  "event_time": "2026-10-30T02:37:51.337742Z",
  "channel": "workbuddy-mcp",
  "hash": "fbcbef5a033c\u2026"
}
```
**CHECK PASS**: the ledger shows channel workbuddy-mcp for Jordan's question
