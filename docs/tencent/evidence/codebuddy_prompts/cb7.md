Security review of the FINALS backend (read-only: do not modify any existing file). This is the code added for the Demo Day build of Walnut DataBridge after your earlier review (CB5). Read:
- databridge/apps/internal_brain/databridge_ib/graph.py (GET /ib/graph, POST /ib/graph/openable: the permission-aware knowledge graph)
- databridge/apps/internal_brain/databridge_ib/docview.py (GET /ib/doc/{doc_id}: open one cited source, passage highlighted)
- databridge/apps/internal_brain/databridge_ib/authz.py (the shared "can this person open these documents now" helper: Gate 1 + live Gate 2)
- databridge/apps/internal_brain/databridge_ib/app.py, in particular POST /ib/ask/stream (Server-Sent Events), GET /ib/trace/{req_id} (the asker's receipt), GET /ib/map, and the /ib/act/* routes
- databridge/apps/internal_brain/databridge_ib/actions.py (Act on an answer: draft a reply, propose/confirm a Jira ticket)
- databridge/apps/internal_brain/databridge_ib/audit/tamper.py and the /audit/tamper-demo and /audit/stats routes in databridge/apps/internal_brain/databridge_ib/audit/routes.py
The product's core rule ("rule 4"): a restricted document must be indistinguishable from one that does not exist, in content, counts, timing, error text and response shape. Identity comes only from the signed token.

List concrete ways a user could (a) see content they are not permitted to open, (b) learn that restricted content exists (counts, timing, error text, ordering, ids, graph edges, stream events), (c) act as someone else or act on someone else's answer, (d) change the real audit log through the tamper demo or any other route, or (e) exhaust the service (unbounded work, locks, threads). For each finding give: severity (critical/high/medium/low), the exact file and line, a concrete attack scenario, and a suggested fix. Also list what you checked and found to be correctly defended. Rank findings by severity. Do not report style issues.

Write the full review to docs/tencent/reviews/codebuddy_cb7_findings.md (this is the only file you may create).
