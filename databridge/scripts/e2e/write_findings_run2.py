"""Render the E2E2 (run 2) findings — single source — to <run>/findings.json and
wiki/40-reviews/E2E-2026-10-01-run2.md.   python scripts/e2e/write_findings_run2.py"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import ROOT, e2e_dir  # noqa: E402

E = e2e_dir()
R = "databridge/var/" + E.name
A = f"{R}/artefacts"
H = f"{A}/http"

SCEN = [
    ("1", "Ingest files + DB into one graph, concurrently; screen 11/1/3", "PASS",
     "Files and DB ingested concurrently. Screen exact (11 allow / 1 review / 3 block, matches expected_outcomes.yaml); only allowed items built; files run `ok` (F-16 fixed); 152/152 run pointers resolve; health lists every screen block/review by rule (F-14 fixed). The DB plan needed the approving person to vouch `execute_sql` as a read (N-20), then mapping approval -> run (by design)."),
    ("2", "Graph connectivity across sources", "PARTIAL",
     "106 nodes / 160 edges, 0 dangling edges (F-03 fixed), largest component 93 nodes (88%) spanning both sources. But DB customers/invoices are now separate nodes from their document twins: cross-source entities fell from 6 to 3 (projects only) (N-16). Persons never link (F-19)."),
    ("3", "Traversal", "PASS", "No dangling edges; /brain/graph 200 (F-05 fixed); Playwright graph canvas renders and expands (2 click tests time out, N-23)."),
    ("4", "100% of pointers re-verify", "FAIL",
     "547 graph pointer rows: 529 verified (96.7%), 18 value_not_in_span (F-17, label drift 'Larkspur Analytics' vs 'Larkspur Analytics Ltd'), 0 cell mismatches (F-18 fixed); 185 table cells re-read live from Postgres, 0 value differences."),
    ("5", "Golden set ac-001..010 (all 10 at once) + retrieval + /brain/query", "PARTIAL",
     "10 questions concurrently in 74 s (run 1: 8 min serially). 6/6 values exact and citing their row; 52/52 citations resolve; ac-007 identity candidate outcome PASSES structurally; ac-006/009/010 abstain correctly in prose but FAIL the structured `outcome` fields (N-12). /brain/query answers 200 now (F-04 fixed) but badly (N-14). Retrieval: 8/8 queries return verified passages, but only DB schema cards and one new file: documents were never back-filled (N-01)."),
    ("6a", "Read-only account briefing (English, live)", "FAIL",
     "Compiled and ran (F-01 fixed), but stopped at the 60k-token budget with no answer twice (N-11). After the in-flight secret-scan change it is refused outright (N-10)."),
    ("6b", "Read-write overdue follow-up: schedule -> drafts + CRM task -> approve", "FAIL",
     "Compiled from run 1's FIRST sentence in 2-3 rounds of answered questions (F-08/F-09 fixed). Schedule fired via fast-forward; SQL args are now correct and carry this run's id (F-30 fixed); answer reports refused drafts honestly (F-07 fixed). But drafts are always refused (N-06) and the CRM task can never be approved (N-05): no write landed, so F-23 could not be re-tested."),
    ("6c", "Event trigger: new file -> agent fires once with the event", "PASS",
     "LOI dropped into the corpus copy; one firing per event_id; the planner received the event (F-12 fixed) and summarised the file with citations after its first two tool calls failed on argument names (N-11)."),
    ("7", "Adapter agent from English; sync; health", "PASS",
     "BUILD_PLAN 5.6's own sentence ('Keep the Larkspur files folder and CRM database in the graph; check hourly') compiled (F-09/F-22 fixed; 'CRM' -> larkspur_db), with passages on. Sync + health work; exclusions shown by rule. Passages are not back-filled for already-built items (N-01)."),
    ("8", "Timings", "PASS", f"See {A}/timings.json and the Timings section."),
    ("9", "Traceability", "PARTIAL",
     "report.json and refused.jsonl carry produced_by (F-33 mostly fixed); agent runs record tokens + cost; extraction ran on anthropic/claude-sonnet-5-5 as routed (F-21 fixed). Federated run records tokens 0 / cost 0 despite two Opus calls (N-19)."),
    ("D", "Delegation (/federated/ask)", "PARTIAL",
     "Grant enforcement correct (403 FEDERATED_NOT_GRANTED for an ungranted principal). Single-source table (total invoice amount by customer, this vs last quarter): 4/4 numbers equal an independent live-Postgres computation and every derived pointer expands to EXACTLY its input cells, each re-executed by query_hash on the facts store and re-read live. Cross-source (the FED-style question): the founder phrasing stops with planner questions (account owner not joinable, N-03); the rephrased question returns an EMPTY table with complete coverage although C-1001 qualifies (N-02). delegate.ask cannot be compiled from English (N-04). Activity shows the delegation tree to the asker, 404 to others."),
    ("M", "Materialise (Data Agent + /materialise)", "PARTIAL",
     "chunk_corpus built from English: receipt, plan_verification (hash recomputed, matches), pointer_check 10/10, missingness leak check present, every row re-verified against the passage store and its capture span; a second principal gets 404 on job, receipt and every download. A tabular dataset is unreachable from English (N-07). 'Downloads withheld on a failed check' could not be exercised: no API path produced a built dataset with a failing check (N-09). Pointer manifest is not downloadable (N-08)."),
    ("A", "Adaptation (drift)", "PASS",
     "Added column auto-applied as a DELEGATED approval: mapping approval log entry principal_type DelegatedApproval, authenticated_by `delegated:sha256:82e7...`, delegated_by = the principal who activated the agent, scope additive_within_declared_scope (applied at 01:50 UTC, the first sync after the column was added). Rename -> held (renamed); drop -> held (removed); new .csv -> held (new_file_type), excluded and never built across two syncs (F-24 fixed)."),
    ("S", "Security spot checks", "PASS",
     "14/14 disclosing routes 401 without a token, 401 with a wrong token; a literal secret in a source URL refused (url_credential_query_param) and never echoed; import with a caller path 403; a typed `by` in an approval body is ignored (decided_by = the token's principal); adapter_agents refuses a name as a principal. Command sources over HTTP are disabled (the literal-secret-in-args probe never reached the scanner)."),
    ("F", "Frontend build + Playwright live specs", "PARTIAL",
     "npm run build 206 s, built against :8013 with the run's token. Live specs: 28 passed, 13 failed, 2 skipped (N-23): 5 from GET /sources 500 (N-10), 4 stale specs, 4 canvas/chip clicks never stable."),
]

F = [
    ("N-01", "High", "Passages are never back-filled: an adapter agent with extract.passages on indexes only items built in that sync", "packages/adapter_agents + packages/mcp_ingest runner (passages index step)",
     "Ingest via /sources runs, then save + sync the BUILD_PLAN 5.6 adapter agent (extract.passages: true)",
     "Sync index.json: enabled true, indexed 0, 'nothing built or removed this run'. The files passage store holds 1 document (the LOI added later); the 11 documents built before never get passages. Retrieval answers from DB schema cards only; the federated documents delegate has no text to search.",
     "Unchanged built items are back-filled once when passages are first enabled (F-11's expected behaviour).",
     [f"{R}/ingest/larkspur-files/20261001T013207Z-f172a8/index.json", f"{R}/passages/"], "F-11"),
    ("N-02", "High", "Cross-source federated answer is an empty table with complete coverage although a customer qualifies", "packages/federated (documents delegate entity identifiers / combine canonical_id link)",
     "POST /federated/ask 'What is the total open invoice amount by customer, for customers mentioned in meeting notes, by due date this quarter compared with last quarter?'",
     "Plan: t1 invoices(status=open), t2 larkspur-files meeting/organisation with mentions, link t1.customer_id = t2.organisation.canonical_id. Combine: rows_in 2, rows_out 0, identifiers_on_other_side 0. Answer: 'The computed table has no rows', coverage complete. The graph has kickoff_pellucid.txt -mentions-> organisation:pellucid freightways ltd with canonical_id C-1001, and C-1001 has open INV-2026-0063 (11,100.00, due 2026-10-30).",
     "C-1001 appears (this quarter 11100); a link that finds zero identifiers on one side reduces coverage instead of reading as a true empty answer.",
     [f"{A}/p3c_fed2.json", f"{R}/runs/federated/e2e2-analyst/fed-20261001T015628-b8fb8bb1.json"], ""),
    ("N-03", "Medium", "The founder's delegation question cannot be answered, and the planner's questions cannot be answered back", "packages/federated planner/combine; apps/gateway federated_routes.AskRequest",
     "POST /federated/ask 'total overdue invoice amount by account owner, for customers mentioned in meeting notes, this quarter compared with last quarter' (and the 'open' variant)",
     "Status stopped, nothing read: 'Account owner is stored only on the customers table, and the combine step cannot join it onto invoice rows' / 'Sub-task t1 has no field account_owner'. The route takes {question, wait, sources}: there is no way to answer the three questions it asks; the user must re-word.",
     "A same-source lookup join (invoices.customer_id -> customers.account_owner) is plannable, and /federated/ask accepts answers like /agents/compile does.",
     [f"{A}/p3_fed.json"], ""),
    ("N-04", "High", "A workflow agent using delegate.ask cannot be built from English; the request becomes a new adapter agent", "packages/automation catalogue + compiler (no adapter agents / delegate tools in the catalogue)",
     "POST /agents/compile 'When I ask, answer my questions that need both larkspur_db and larkspur-files by delegating them to the larkspur_graph_sync adapter agent. Read only; cite every number.'",
     "Model question: 'no agent or connection with that name exists in the catalogue'; once answered it compiled kind adapter_agent id larkspur_graph_sync_2 (activatable, saved; E2E2 deleted it via DELETE /adapter-agents). No spec with delegate.ask:<aa> is reachable without hand-writing YAML (not done: no workarounds).",
     "The compiler lists active adapter agents and offers delegate.ask:<id> as a read tool.",
     [f"{A}/p2_result.json", f"{H}/0040_p2_compile_delegate_r0.json"], ""),
    ("N-05", "High", "Human-required writes can never be approved: the agent writes its citation ref into a column and the approval gate rejects it", "packages/agent_runtime (approve() grounding gate; planner prompt for pointer-valued args)",
     "Fire overdue_invoice_follow_up (engine tick 2026-10-05T01:00:20Z) -> POST /agents/approvals/<run>/a1/approve",
     "409 ACTION_REFUSED 'ungrounded values: [\\'p1\\']' (source_pointer = 'p1', the run-local citation ref); attempt 1 also rejected 'overdue_invoice_follow_up' (created_by = the agent's own id). Re-approve -> 409 again. No row reached public.follow_up_tasks, so F-23 (re-ingest loop) could not be re-tested.",
     "A pointer-valued argument is filled from the cited pointer (or the ref is resolved before the check); the agent's own id/run id are recognised as system values.",
     [f"{A}/p3_overdue.json", f"{R}/runs/agent/overdue_invoice_follow_up/run-20261001T015216-adf7f3f3.json"], "F-02"),
    ("N-06", "High", "human_notified draft writes are always refused for derived values", "packages/agent_runtime action gate (action_value_not_in_evidence)",
     "Same scheduled run",
     "Both save_follow_up_email_draft actions refused: values not in evidence ['INV-2026-0057.md', '14'] and ['INV-2026-0063.md', '29'] (a file name built from the invoice number; a day count computed from due_on). Dates now pass (Python-repr part of F-06 fixed). No draft was ever written.",
     "Deterministic derivations of cited values (name templates, date arithmetic) are recognised, or the action downgrades to human_required instead of being dropped.",
     [f"{R}/runs/agent/overdue_invoice_follow_up/run-20261001T015216-adf7f3f3.json"], "F-06"),
    ("N-07", "High", "No tabular dataset can be materialised from English", "packages/materialise (DataAgent model plan schema; offline gold planner)",
     "POST /materialise {request: 'One row per invoice with its customer, project, issue date, due date, net, VAT, total and status ...'} and a labelled variant (MAT's fraud-style example)",
     "Model-authored plan fails validation (ColumnSource.relation 'self' not in the literal set); the gold planner then authors a per_row plan with no decision_time_field (FeatureAxisRefused) — for the plain request it even chose shape graph_tensor. Both requests refused, three runs out of three.",
     "The model prompt matches the plan schema; an unlabelled extract is offered as one; a refusal names what to change.",
     [f"{A}/p3c_mat2.json", f"{A}/p3_mat.json"], ""),
    ("N-08", "Medium", "Materialised datasets ship no per-cell pointers a user can download", "packages/materialise (receipt pointer_manifest.uri null)",
     "Download the chunk_corpus files",
     "Rows carry text and offsets only; receipt.pointer_manifest = {digest, entries: 10, uri: null}. E2E2 verified the rows against the passage store and capture on disk — a downloader cannot.",
     "pointer_manifest is a downloadable file listed in file_roles.",
     [f"{R}/materialise_downloads/", f"{A}/p3c_mat2.json"], ""),
    ("N-09", "Low", "'Download withheld when a check fails' could not be exercised through the API", "packages/materialise / test gap",
     "Try leaky and labelled requests", "Every failing case is refused at compile time (LabelAxisRefused / FeatureAxisRefused); no built dataset with a failing pointer or leakage check is reachable.",
     "A test hook or documented request that builds and then withholds, so the 409 DATASET_WITHHELD path is provable end to end.", [f"{A}/p3_mat.json"], ""),
    ("N-10", "High", "A secret-scan tightening landed mid-run: stored connections declared with ${VAR:-default} credentials now fail to load — GET /sources 500 and DB-reading agents refused", "packages/mcp_ingest connections.load_connection/_refuse_raw + secret_scan (changed 01:51 UTC by a concurrent agent) vs infra/mcp_servers.yaml",
     "Gateway restarted 01:55 UTC (picked up connections.py/secret_scan.py of 01:51:16); then GET /sources and POST /agents/account_briefing/run",
     "GET /sources -> 500 (ConnectionError_: 'larkspur_db_writer' holds a literal secret at env.DATABASE_URI (url_userinfo_password, credential_reference_default)); account_briefing -> refused 'connection larkspur_db could not be described'. The operator's own manifest uses ${WALNUT_DEMO_PG_READER_PASSWORD:-walnut_reader_synthetic}. Ingest-time /sources import had accepted it.",
     "Manifest and scanner agree before the change ships; one bad stored connection is reported per row, never a 500 for the list.",
     [f"{R}/logs/gateway.log", f"{H}/0203_p3d_briefing_retry.json"], ""),
    ("N-11", "High", "Tool arguments are not shaped or validated against the tool's input schema: the briefing burns its token budget, the event agent's first calls fail", "packages/agent_runtime tools/planner",
     "Run account_briefing ('Brief me on the Pellucid Freightways account.'); event run of larkspur_new_file_summariser",
     "Briefing: list_objects called with {} ('schema_name Field required'), 20 tool calls, stopped at 60,000 tokens, no answer (twice). Event agent: stat_file/read_bytes called with {'uri': ...} where the tool needs 'path'; it recovered on retry.",
     "Arguments checked against inputSchema before the call (and the schema shown to the planner); a budget stop still returns what was found.",
     [f"{R}/runs/agent/account_briefing/run-20261001T015214-d987594e.json", f"{R}/runs/agent/larkspur_new_file_summariser/"], "F-01"),
    ("N-12", "Medium", "Golden-set outcomes are not structural: the run record carries no outcome fields and coverage misses screen-withheld and not-connected sources", "packages/agent_runtime runs.py (Coverage) + answer contract",
     "python scripts/e2e/s5_golden.py larkspur_qa --parallel (asserts packs/workspace/evals/agent_contract.jsonl `outcome`)",
     "ac-009: answer names the withheld file and rule (F-34 fixed in prose) but coverage.skipped is empty -> coverage 'complete' (golden: reduced). ac-010: Salesforce not connected in prose, coverage complete (golden: reduced). ac-006: coverage reduced by out_of_scope graph pointers and a candidate claim requires human selection (golden: complete, false). ac-007 passes every structural field.",
     "Coverage records withheld_by_screen / source_not_connected; the record exposes reason_code, human_confirmation_required, coverage_status.",
     [f"{A}/s5_golden_larkspur_qa.json"], "F-34"),
    ("N-13", "Medium", "Answer-contract false positives persist", "packages/agent_runtime answer.py",
     "Golden set + overdue run", "number_not_in_cited_evidence for '1001' (from 'C-1001'), '1003', '2026', '31', '20261001'; candidate_identity_uncited on a contact named from its own cited row (ac-005: 5 of 5 claims UNSUPPORTED).",
     "Numbers inside cited identifiers/dates are found; a person named from their cited row is a cited candidate.", [f"{A}/s5_golden_larkspur_qa.json"], "F-06"),
    ("N-14", "Medium", "/brain/query answers badly and never delegates cross-source questions", "apps/gateway llm.py + brain_server (BRAIN item)",
     "POST /brain/query with the 8 run-1 questions (concurrently)",
     "200 now (F-04 fixed) but: 'The context does not provide information about the city ... It only mentions that Corriedale Grocers Co-operative is located in Leeds'; head of procurement / open invoices / account owner not found; x-3 refused AMBIGUOUS_ENTITY; no answer routed to /federated/ask.",
     "Grounded answers over the graph + facts, cross-source questions delegated.", [f"{A}/p3_brain.json"], "F-04"),
    ("N-15", "Low", "/intelligence/ask refusal speaks of the 'clinic graph' in the workspace pack", "apps/gateway intelligence routes (domain wording in the engine, rule 5)",
     "POST /intelligence/ask 'what is the status of invoice INV-2026-0057?'", "422 NO_GROUNDED_ANSWER 'Nothing in the clinic graph answered that.' (no longer 503 — F-03 analytics fixed)",
     "Pack-neutral wording; an answer from the workspace graph.", [f"{H}/0141_p3_intel_ask.json"], ""),
    ("N-16", "Medium", "DB rows no longer join their document twins: cross-source entities fell from 6 to 3", "packages/mcp_ingest tabular graph writer (row identity / canonical ids)",
     "python scripts/e2e/graph_audit.py final",
     "organisation:larkspur-db-public-customers-c-1001 (canonical_id 'pellucid-freightways.example') sits beside organisation:pellucid freightways ltd (canonical_id 'C-1001'); invoice:larkspur-db-public-invoices-inv-2026-0041 beside invoice:inv-2026-0041. Only the 3 projects are shared. F-03's dangling edges are gone because nothing merges.",
     "Rows join existing entities by their declared identifier (customer_id / invoice_no), edges re-pointed.", [f"{A}/graph_audit_final.json"], "F-03"),
    ("N-17", "Medium", "Node labels still drift from their pointers (18 pointers)", "packages/graph upsert relabel + extractor canonicalisation",
     "python scripts/e2e/pointer_audit.py final", "18 value_not_in_span, e.g. span 'Larkspur Analytics' for node 'Larkspur Analytics Ltd'.", "A pointer grounds what it is shown next to.",
     [f"{A}/pointer_audit_final.json"], "F-17"),
    ("N-18", "Medium", "Ownerless automation runs and their approvals are open to every principal", "apps/gateway activity_routes + agents approvals (ownership being changed)",
     "GET /activity and /activity/runs/<scheduled run> as a second principal; POST reject on the first principal's agent's proposal as the second principal",
     "Second principal sees 9 items (scheduled/event agent runs, approvals, firings) and the run detail (200); it could REJECT the proposal (decided_by recorded correctly as e2e2-second; the typed 'mallory' ignored). Federated runs are owner-only (404 to others).",
     "Recorded as observed; the founder's ownership change decides who may see and decide these.", [f"{A}/p3c_act.json", f"{A}/p3c_sec2.json", f"{A}/p3d_activity_runs.json"], ""),
    ("N-19", "Low", "Federated run records report 0 tokens / 0 cost although the planner and narrator called Opus", "packages/federated service (run record header)",
     "Any /federated/ask", "tokens 0, cost_usd 0.0, models_used [anthropic/claude-opus-5-5].", "Metered tokens and cost as agent runs record them.",
     [f"{R}/runs/federated/e2e2-analyst/fed-20261001T015628-b8fb8bb1.json"], "F-33"),
    ("N-20", "Low", "The DB plan needs a person to vouch execute_sql as a read", "packages/mcp_ingest plans (fail-closed classification) + infra manifest",
     "POST /sources/larkspur_db/plan/approve", "409 PLAN_NOT_APPROVABLE 'execute_sql is unclassified ... the approving person may vouch'; approved with vouch_read_tools=['execute_sql'] (the product's own path).",
     "By design; the operator's manifest could declare the restricted-mode read so the person is not asked to vouch.", [f"{H}/0004_p1_approve_larkspur_db.json"], "F-10"),
    ("N-21", "Low", "The compiler offers read tools as 'write tools' of the writer connection", "packages/automation compiler",
     "Compile the overdue sentence", "'which of larkspur_db_writer's write tools should do this?' options include list_schemas, explain_query, get_top_queries.",
     "Only write tools offered.", [f"{A}/p1_result.json"], ""),
    ("N-22", "Low", "The operator declarations file is fixed at <repo>/infra/mcp_servers.yaml", "packages/mcp_ingest connections.import_declarations",
     "Isolate a second deployment", "WALNUT_MCP_SERVERS is read by nothing; a body path is refused (correct). A second instance isolates only by pointing WALNUT_ROOT at a deployment root (E2E2 did: <run>/root with a junction to packages and a private corpus copy).",
     "A configurable declarations path for the operator.", [f"{R}/root/"], ""),
    ("N-23", "Medium", "Frontend live specs: 13 of 43 fail against a real deployment", "apps/frontend e2e specs + graph canvas",
     "npm run build (NEXT_PUBLIC_GATEWAY_URL=:8013, token) ; playwright with var/<run>/playwright.e2e2.config.ts (live specs only)",
     "5 sources specs (GET /sources 500, N-10); 2 ontology specs (packs.default undefined — the spec reads /ontology/packs without the auth the route now needs); 2 trace-honesty specs expect '/materialise not built' (it is built: stale); 2 graph specs and 2 try-chips specs time out clicking an element that never becomes stable (canvas re-layout / healthcare chips).",
     "Specs follow the auth and Data-page changes; canvas settles.", [f"{R}/logs/playwright_live.log", f"{A}/playwright_live.json"], ""),
    ("N-24", "Low", "Environment: host stall and a background-shell limit", "(not product)",
     "Phase 3 attempt 1, 01:37-01:51 UTC", "Every HTTP call (even 401s) took ~736 s, docker exec timed out, then model calls failed getaddrinfo 11001; attempt 1 kept in artefacts/attempt1 and phase 3 re-run. The harness's background shell limit (30 min) stopped the gateway once; restarted on the same state.",
     "n/a — recorded so nobody chases it.", [f"{A}/attempt1/", f"{R}/notes.md"], "F-35"),
]

# run-1 findings: status in run 2
RUN1 = {
    "F-01": ("fixed", "all four English-compiled agents ran (N-11/N-10 are new causes)"),
    "F-02": ("changed", "args are now one SQL statement and carry the run id; approval is refused before execution (N-05), so the error-as-text path was not reached"),
    "F-03": ("fixed (changed)", "0 dangling edges; /intelligence/ask no longer 503 — but DB rows no longer merge (N-16)"),
    "F-04": ("fixed (changed)", "200 on every question; answer quality N-14"),
    "F-05": ("fixed", "/brain/graph 200 in workspace mode"),
    "F-06": ("changed", "date repr fixed; derived values still refused (N-06) and number false positives remain (N-13)"),
    "F-07": ("fixed", "answer states the drafts were refused"),
    "F-08": ("fixed", "writer tools listed; compile chose execute_sql (N-21 minor)"),
    "F-09": ("fixed", "answers to model questions are consumed; 1-3 rounds"),
    "F-10": ("fixed", "no capability_is_a_write refusals; DB plan needs a vouch (N-20)"),
    "F-11": ("still failing", "N-01: enabled, but no back-fill"),
    "F-12": ("fixed", "the event record is in the planner's question"),
    "F-13": ("fixed", "contact email/phone reported as masked; blocked compensation file never read"),
    "F-14": ("fixed", "health lists screen blocks/reviews by rule"),
    "F-15": ("fixed", "the event agent summarised the file from read_bytes"),
    "F-16": ("fixed", "files run status ok, 152/152 run pointers"),
    "F-17": ("still failing", "N-17 (18 pointers)"),
    "F-18": ("fixed", "0 cell_value_mismatch"),
    "F-19": ("still failing", "persons never linked (deferred upstream)"),
    "F-20": ("fixed", "37 relations accepted vs 24 refused (run 1: 6 vs 73)"),
    "F-21": ("fixed", "extraction on anthropic/claude-sonnet-5-5"),
    "F-22": ("fixed", "'CRM' resolves to larkspur_db; the 5.6 sentence compiles"),
    "F-23": ("not re-tested", "no write could be approved (N-05)"),
    "F-24": ("fixed", ".csv held as new_file_type, never built"),
    "F-25": ("fixed", "/brain/query and /brain/graph 401 without a token"),
    "F-26": ("fixed", "no get_top_queries / analyze_db_health grants"),
    "F-27": ("fixed", "every run metered tokens; no replayed answers"),
    "F-28": ("fixed", "golden cites use larkspur_db"),
    "F-29": ("changed", "the run reports the DB's date while the engine clock is fast-forwarded; not comparable"),
    "F-30": ("fixed", "agent_run_id = this run's id in the proposed SQL"),
    "F-31": ("fixed", "re-syncs extract 0 unchanged items"),
    "F-32": ("fixed", "titles whole ('Keep Larkspur files and CRM database in the graph')"),
    "F-33": ("mostly fixed", "report.json/refused.jsonl/run header carry it; federated tokens 0 (N-19)"),
    "F-34": ("changed", "prose names the withheld file and rule; structural outcome missing (N-12)"),
    "F-35": ("n/a", "this run's environment notes: N-24"),
}


def main() -> None:
    t = json.loads((E / "artefacts" / "timings.json").read_text(encoding="utf-8"))
    steps = {s["step"]: s["secs"] for s in t.get("steps") or []}
    findings = [dict(zip(("id", "severity", "title", "component", "reproduction", "observed", "expected",
                          "evidence", "run1"), f)) for f in F]
    (E / "findings.json").write_text(json.dumps({"run": E.name, "scenarios": [
        dict(zip(("id", "scenario", "result", "evidence"), s)) for s in SCEN],
        "findings": findings, "run1_comparison": RUN1}, indent=2), encoding="utf-8")
    sev = collections.Counter(f["severity"] for f in findings)
    L = [f"# E2E-2026-10-01-run2 — Phase B re-run after the fix wave (+ delegation, materialise, adaptation, security)",
         "",
         f"Run by: E2E2 (claude-opus-5-5). Models: anthropic/claude-opus-5-5 (compiler, agent planner, federated planner/narrator), anthropic/claude-sonnet-5-5 (extraction, as routed), openai/text-embedding-3-small (passages). Date 2026-10-01 01:22-02:30 UTC.",
         "",
         f"**Isolation:** run dir `{R}/` (graph, capture, sources, ingest, facts, passages, runs, agents, automation, materialise). The operator manifest `infra/mcp_servers.yaml` is the only file `POST /sources {{mode: import}}` may read, so the run deploys like a second instance: `WALNUT_ROOT={R}/root` with `packages` a junction to the code and `fixtures/workspace_demo/files` a private corpus copy. Gateway :8013 via `scripts/e2e/serve_e2e.py`. **Auth as a real deployment:** `WALNUT_API_TOKENS` with two principals (e2e2-analyst, e2e2-second; random tokens in `{R}/auth/`), `WALNUT_FEDERATED_GRANTS={{\"e2e2-analyst\": \"*\"}}`, no `WALNUT_DEV_AUTH`. Shared Docker: scratch table `public.e2e2_scratch` created and dropped; `public.follow_up_tasks` untouched (no approval executed).",
         "",
         "**No workarounds.** fix_declaration.py, prune_tools.py, write_tool_adverts.py and s5_index_passages.py were not used; no hand-written YAML, no tool hints in questions. The only human inputs were what a user does: answering the compiler's questions (scripted, recorded per round in `p1_result.json`/`p2_result.json`), vouching `execute_sql` when the plan approval asked (N-20), approving the DB mapping, approving/rejecting proposals. The one product-path deviation is the harness's `POST /__e2e/tick` (engine clock fast-forward, as in run 1).",
         "",
         "## Scenarios", "", "| # | Scenario | Result | Evidence |", "|---|---|---|---|"]
    for sid, name, res, ev in SCEN:
        L.append(f"| {sid} | {name} | **{res}** | {ev} |")
    L += ["", f"**Findings:** {len(findings)} — " + ", ".join(f"{k} {sev[k]}" for k in ("Critical", "High", "Medium", "Low") if sev[k]) + ". No Critical (run 1: 3).",
          "", "## Timings (measured)", "",
          "| Step | Seconds |", "|---|---|"]
    rows = [("Gateway boot (process start -> /health 200)", 8.0)]
    for k, label in [("p1.import", "Register 4 sources (import)"), ("p1.plan_both", "Propose both plans (parallel)"),
                     ("ingest.larkspur-files.run1", "Files ingest, 15 items (parallel with DB + 6 compiles)"),
                     ("ingest.larkspur_db.run1", "DB ingest run 1 (profile, mapping proposal)"),
                     ("ingest.larkspur_db.run2", "DB ingest run 2 (after mapping approval)"),
                     ("compile.newfile", "English compile, fastest"), ("compile.overdue", "English compile, slowest (2 rounds)"),
                     ("p1.ingest_and_compile_parallel", "Phase 1 wall (files ingest + 6 compiles)"),
                     ("p1b.db_ingest_and_recompile_parallel", "Phase 1b wall (DB ingest + 3 recompiles; folded into phase 1 in the script now)"),
                     ("p2.adapter_sync1", "Adapter-agent sync, both sources"),
                     ("p2.parallel", "Phase 2 wall (adapter save+sync, 4 agent saves, overdue compile)"),
                     ("p2.compile.delegate", "Delegating-agent compile (N-04)"),
                     ("p3.golden_all_parallel", "Golden set ac-001..010, all at once"),
                     ("p3.briefing", "Account briefing (stopped at budget)"),
                     ("p3.overdue.tick", "Scheduled overdue run (tick -> drained)"),
                     ("p3.fed.overdue", "Federated ask (planner questions)"), ("p3.fed.open", "Federated ask (planner questions)"),
                     ("p3.chain.a_newfile_addcol", "Drift a: new file + added column, sync"),
                     ("p3.chain.a_event_agent_wait", "Event -> agent run complete"),
                     ("p3.chain.b_rename", "Drift b: rename, sync"), ("p3.chain.c_drop", "Drift c: drop, sync"),
                     ("p3.chain.d_csv", "Drift d: .csv, two syncs"),
                     ("p3.brain_all_parallel", "/brain/query x8, all at once"),
                     ("p3.parallel_all", "Phase 3 wall (ALL of the above concurrently)"),
                     ("p3c.parallel_all", "Phase 3c wall (2 federated asks + 3 materialisations + security)"),
                     ("p3d.fed.single_source_table", "Federated single-source table")]:
        if k in steps:
            rows.append((label, steps[k]))
    for label, s in rows:
        L.append(f"| {label} | {s} |")
    L += ["| npm run build | 206 |", "| Playwright live specs (43) | 588 |", "",
          "**Wall clock.** Sum of the measured phase walls (boot + p1 + p1b + p2 + p3 + p3c + p3d) = **about 7.7 minutes (461 s)** of driving, against **36 minutes** in run 1 (which also ran serially and spent time on workarounds). With the vouch step now inside phase 1, a clean run is boot 8 s + phase 1 ~116 s + phase 2 ~67 s + phase 3 ~121 s + phase 3c ~50 s + checks ~60 s ≈ **7 minutes**, plus 3.4 min frontend build and 9.8 min Playwright. Calendar time was longer: a ~12-minute host stall voided phase-3 attempt 1 (N-24), and investigation happened between phases. Per-step start/end (overlap = parallelism) in `timings.json` → `steps`.",
          "", "## Findings", ""]
    for f in findings:
        L += [f"### {f['id']} [{f['severity']}] {f['title']}", "",
              f"- **Owning component:** {f['component']}",
              f"- **Reproduction:** {f['reproduction']}",
              f"- **Observed:** {f['observed']}",
              f"- **Expected:** {f['expected']}",
              f"- **Evidence:** " + "; ".join(f"`{e}`" for e in f["evidence"])]
        if f["run1"]:
            L.append(f"- **Run 1:** {f['run1']}")
        L.append("")
    L += ["## Run-1 findings (35), status in run 2", "", "| Run 1 | Status | Note |", "|---|---|---|"]
    for k, (st, note) in RUN1.items():
        L.append(f"| {k} | {st} | {note} |")
    cnt = collections.Counter(v[0].split(" (")[0] for v in RUN1.values())
    L += ["", "Tally: " + ", ".join(f"{k} {v}" for k, v in cnt.most_common()) + ".",
          "", "## Where everything is", "",
          f"- Run directory `{R}/`: `findings.json`, `notes.md`, `auth/` (token directory + grants; tokens are run-scoped), `artefacts/` (`http/` every request/response, `p1_result.json` … `p3d_*.json` per phase, `s5_golden_larkspur_qa.json`, `fed_check_*.json`, `graph_audit_final.json`, `pointer_audit_final.json`, `timings.json`, `timings_steps.jsonl`, `playwright_live.json`, `attempt1/`), `logs/` (gateway, each phase, npm build, Playwright), `materialise_downloads/`, `playwright.e2e2.config.ts`.",
          "- Harness (parallel): `databridge/scripts/e2e/run2_phase1.py`, `run2_phase1b.py`, `run2_phase2.py`, `run2_phase3.py`, `run2_phase3c.py`, `run2_lib.py` (thread pool + step timings), `run2_compile.py` (scripted user answering compiler questions), `fed_check.py` (independent computation + pointer expansion), `s5_golden.py` (concurrent; asserts the structured `outcome` fields), `collect_timings.py`, `write_findings_run2.py`.",
          "- Assertions: `databridge/tests/e2e/test_phase_b_e2e.py` (`WALNUT_E2E_DIR=var/" + E.name + " python -m pytest tests/e2e -m integration`): **15 passed, 7 failed** — the failures are N-17 (pointers), N-11 (briefing), N-02 (cross-source table), N-04 (delegate compile), N-07 (tabular), N-12 (golden outcomes), N-05 (approved write).",
          ""]
    out = ROOT.parent / "wiki" / "40-reviews" / "E2E-2026-10-01-run2.md"
    out.write_text("\n".join(L), encoding="utf-8", newline="\n")
    print(out, len(findings), dict(sev))


if __name__ == "__main__":
    main()
