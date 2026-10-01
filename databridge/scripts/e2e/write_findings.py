"""Render the E2E findings (single source) to wiki/40-reviews/E2E-2026-10-01.md and
<e2e>/findings.json.   python scripts/e2e/write_findings.py"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import ROOT, e2e_dir  # noqa: E402

E = e2e_dir()
ER = "databridge/var/e2e-20261001T0356"
H = f"{ER}/artefacts/http"

SCENARIOS = [
    ("1", "Ingest larkspur-files + larkspur_db into ONE graph; screen 11/1/3; blocked never reach the model", "PARTIAL",
     "Screen EXACT: 11 allow / 1 review / 3 block, every file matches expected_outcomes.yaml incl. fired rules, redactions and the kept `DE44 LSPR 2026 0457 11`; build.jsonl holds only the 11 allowed items (blocked/review never built, never sent). One shared store (104 nodes / 132 edges after both sources). BUT the files run ends `unverified` (F-16), the DB needs run -> approve mapping -> run again, and health/evidence surfaces report 0 exclusions (F-14)."),
    ("2", "Graph connectivity across sources", "PARTIAL",
     "Final: 111 nodes / 140 edges, 18 components, largest = 87 nodes (78%) and it spans both sources. 6 cross-source entities: Pellucid Freightways Ltd, Corriedale Grocers Co-operative, Tamsinbay Water Authority, INV-2026-0041, Route Optimisation Pilot, Demand Forecasting Rollout. NOT linked: Rowan Achterberg (3 per-document doc nodes + 1 isolated DB contact), INV-2026-0063 (DB-only, degree 0), all 5 DB contacts. 19 dangling DB edges (F-03)."),
    ("3", "Traversal customer -> contract PDF", "PARTIAL",
     "/graph/path organisation:pellucid freightways ltd -> MSA PDF document: found, 1 hop (mentions). From DB-only nodes (INV-2026-0063, contact Rowan, PRJ-103): `disconnected` because their FK edges dangle (F-03)."),
    ("4", "100% of pointers re-verify (hash + span; table_cell via facts + live)", "FAIL",
     "Independent audit of all 544 pointer rows: 499 verified (91.7%), 0 hash_mismatch, 0 capture_missing, 0 text_unbound, 0 out_of_bounds, 17 value_not_in_span (F-17), 28 cell_value_mismatch (F-18). 133 table cells checked against facts store and LIVE Postgres: 0 differ. Product verify_run separately flags 2 PDF spans (F-16)."),
    ("5", "Answering: /brain/query, hybrid retrieval (real embeddings), golden set ac-001..010", "PARTIAL",
     "/brain/query 500 on every question (F-04). Retrieval only after a manual index (F-11): real openai/text-embedding-3-small, 8 questions x 5 passages, 40/40 passage pointers resolve. Golden set through a read-only English-compiled agent (Opus 5.5): 6/6 expected values exact, 5/6 cite the expected row, 4/4 abstentions held (ac-009 abstains but cannot say 'withheld'), 45/45 citations resolve; answer-contract flags produced false positives (F-06)."),
    ("6a", "Read-only 'Account briefing' from English, live on Opus 5.5, >= 2 sources", "PARTIAL",
     "Compiled in 9.5 s as `activatable`, saved active, then REFUSED at run (F-01, F-10). After regenerating the declaration with the runtime builder and pruning 5 DB read tools: completed in 67 s, 19 tool calls, 59k tokens, 13 citations from larkspur_db AND larkspur-files, all resolve. Unmasked contact email/phone in output (F-13)."),
    ("6b", "Read-write 'Overdue invoice follow-up': schedule -> drafts (human_notified) + CRM task (human_required) -> approve once -> re-ingest", "PARTIAL",
     "Needed 7 rewordings + 3 workarounds to compile (F-08, F-09). Schedule fired via engine fast-forward (tick at 09:00 SGT; idempotency key recorded). Drafts REFUSED every run (F-06) while the answer claims they were saved (F-07). CRM task stayed proposed; 1st approval recorded `executed` but the INSERT failed (F-02). With a SQL-shaped hint: approval executed once, row task_id=1 verified in public.follow_up_tasks, re-approve -> 409, reject works. Next sync: row in facts store, NOT in graph (F-23)."),
    ("6c", "EVENT trigger: new file -> item_added -> fires once", "PARTIAL",
     "Dropped LOI-2026-003 (md) into the private corpus copy; sync emitted item_added; engine fired the agent exactly once per event (pending+succeeded, idempotency key per event_id). But the agent never received the event's item/uri and answered 'No item_added event has arrived' (F-12); a 2nd event replayed a cached identical answer (F-27)."),
    ("7", "Adapter agent from English; sync; health; drift", "PARTIAL",
     "The plan's own sentence never compiled (F-09, F-22); an id-explicit rewording compiled. Sync + health work (freshness, per-connection counts), but exclusions show 0 (F-14) and extract.passages is ignored (F-11). Drift on a scratch table: add column -> added_column auto-applied; rename -> pending approval (always_ask); drop -> removed pending; new .csv -> new_file_type/new_mime auto-applied and ingested (F-24). Scratch table dropped afterwards."),
    ("8", "Timings per stage + wall clock + model calls/tokens", "PASS",
     f"See {ER}/artefacts/timings.json and the Timings section."),
    ("9", "Traceability (produced_by, BUILD_PLAN 5.8)", "PARTIAL",
     "Agent steps + answers carry produced_by (agent, model anthropic/claude-opus-5-5, prompt_version, run_id); run header lists models_used; 254/254 model-extracted graph pointers carry produced_by in `derivation`; compiler derivations name rule/model/config per field; approvals record the authenticated principal. Missing: report.json + refused.jsonl produced_by, per-passage embedding model, run-level tokens/cost (F-33)."),
]

F = []


def f(sev, title, component, repro, observed, expected, evidence, workaround=""):
    F.append({"id": f"F-{len(F) + 1:02d}", "severity": sev, "title": title, "component": component,
              "reproduction": repro, "observed": observed, "expected": expected,
              "evidence": evidence, "workaround": workaround})


# ------------------------------------------------------------------------------------ Critical
f("Critical", "Agents compiled from English are saved as `activatable` but the runtime refuses every run",
  "packages/automation/databridge_automation/declaration.py `default_builder._external` calls `declaration_from_workflow(spec)`; packages/agent_runtime/databridge_agent_runtime/mapping.py:258 needs `(spec, pack, governance)`",
  "POST /agents/compile {text: 'Account briefing: when I ask about a customer, give me a one-page briefing ... from larkspur_db and larkspur-files ...'} -> POST /agents {draft_id, activate:true} -> POST /agents/account_briefing/run {wait:true}",
  "Compile note: \"the databridge_agent_runtime declaration builder failed (declaration_from_workflow() missing 2 required positional arguments: 'pack' and 'governance'); used the local one\". compilation.outcome=compiled, activatable=true. The local builder emits capabilities `larkspur_db.query`, `larkspur_files.read_bytes`; the runtime requires `larkspur_db.sql`, `larkspur_files.tool_read_bytes` -> run status `refused`, 0 steps, for every agent that reads a source (4/4 agents built).",
  "One declaration builder; a draft marked activatable runs.",
  [f"{H}/0048_s6_compile_briefing.json", f"{ER}/runs/agent/account_briefing/run-20260930T201436-2138e60a.json"],
  "scripts/e2e/fix_declaration.py regenerates <agent>.agent.yaml with the runtime builder (original kept as .compiler-original).")
f("Critical", "A human-approved write is recorded `executed` although the source returned an error; nothing was written",
  "packages/agent_runtime/databridge_agent_runtime/runtime.py `_execute` (trusts `is_error` only); tools.py `McpSourceGateway._result`; no validation of action args against the tool's input schema",
  "Scheduled run of overdue_invoice_followup proposes insert_follow_up_task with args {table, customer_id, invoice_no, ...} (no `sql`) for source.write:larkspur_db_writer:execute_sql -> POST /agents/approvals/run-20260930T202338-3b7ebc7a/a1/approve",
  "HTTP 200, action status `executed`, detail `executed`; captured result text is `Error: syntax error at or near \"all\"` (postgres-mcp reports SQL errors as text with isError=false). public.follow_up_tasks had 0 rows. Re-approve refused (executed), so the human believes the task exists.",
  "Treat an error payload as a failed write (`execution_failed`, stays approved); validate args against the tool inputSchema at proposal time; never show `executed` without evidence of the effect.",
  [f"{ER}/runs/agent/overdue_invoice_followup/run-20260930T202338-3b7ebc7a.actions.jsonl", f"{ER}/capture/45/45524460ccbdf76b4315c42da9be987a52b205c57caeda337ae768863486079d", f"{H}/0078_s6b_approve_a1.json"],
  "Manual fire with a question telling the planner the tool takes one `sql` argument; that approval inserted task_id=1.")
f("Critical", "DB rows merged onto document nodes leave all their FK edges dangling (19), orphaning DB-only facts and crashing /intelligence/ask",
  "packages/mcp_ingest/databridge_mcp_ingest/tabular (graph row writer / mapping edge endpoints) — edges keep row-key ids after the node merges onto an existing id",
  "Ingest larkspur-files, then larkspur_db (approve mapping, run) -> python scripts/e2e/graph_audit.py",
  "Nodes `organisation:larkspur-db-public-customers-c-1001`, `invoice:larkspur-db-public-invoices-inv-2026-0041`, `project:...-prj-101/102` do not exist (merged into `organisation:pellucid freightways ltd`, `invoice:inv-2026-0041`, ...), but 19 live edges still point at them (billed_to, part_of, works_for, manages). INV-2026-0063 has degree 0; all 5 DB contacts are isolated; report note says '0 dangling edges dropped'. POST /intelligence/ask -> 503 (KeyError 'invoice:larkspur-db-public-invoices-inv-2026-0041' in databridge_graph/analytics.py:413).",
  "Edges re-pointed to the merged node id (or no merge); report counts dangling edges truthfully; analytics tolerate a dangling edge.",
  [f"{ER}/artefacts/graph_audit_final.json", f"{H}/0134_s5_intel_ask.json", f"{ER}/logs/gateway.log (KeyError traceback)"])
# ------------------------------------------------------------------------------------ High
f("High", "/brain/query returns 500 in workspace (KG) mode",
  "apps/gateway/databridge_gateway/llm.py:17-32 (`SYSTEM_PROMPT` from `databridge_intake.assistant_config`) + packages/intake/databridge_intake/pack.py:121",
  "POST /brain/query {question:'what is the status of invoice INV-2026-0057?'} with DATABRIDGE_PACKS=workspace and an LLM key present",
  "500 for all 8 questions: PackConfigError 'none of the enabled packs [workspace] ships a config/ directory ... (coverage, questionnaires, baselines)'. The main Ask path is unusable in the knowledge-platform mode the launcher starts.",
  "A pack-neutral system prompt; the brain answers over the workspace graph.",
  [f"{H}/0023_s5_brain_nopassages_ac-001.json", f"{ER}/artefacts/s5_brain_nopassages.json"], "Answered through agents + retrieval instead.")
f("High", "/brain/graph 500: engine code branches on a healthcare kind (NodeKind.PATIENT)",
  "apps/mcp_server/brain_server.py:236 `node_to_dict` (`if node.kind is NodeKind.PATIENT`) — violates CLAUDE.md rule 5 (no per-domain branching)",
  "GET /brain/graph with DATABRIDGE_PACKS=workspace",
  "500 AttributeError: NodeKind has no member 'PATIENT': no enabled pack declares that node kind.",
  "No domain kind named in the engine; route works for any pack.",
  [f"{ER}/logs/gateway.log"])
f("High", "Answer-contract and write gate compare against Python-repr DB evidence: every date/number from larkspur_db looks unsupported, so human_notified writes are always refused",
  "packages/agent_runtime/databridge_agent_runtime/answer.py (number_not_in_cited_evidence) + runtime action gate (action_value_not_in_evidence); live SQL results are captured as postgres-mcp repr text",
  "Fire overdue_invoice_followup (schedule or manual). Evidence text: `'due_on': datetime.date(2026, 10, 15), 'net_gbp': Decimal('12750.00')`",
  "save_follow_up_email_draft refused in both runs: \"values appear in none of the cited evidence: ['09']\". No draft was ever written to larkspur-followups. Same false positive marks correct claims UNSUPPORTED in the briefing ('05' from 2026-05-12) and golden answers (ac-003, ac-010).",
  "Render SQL rows canonically (ISO dates, plain decimals) before capture/check, or normalise both sides.",
  [f"{ER}/runs/agent/overdue_invoice_followup/", f"{ER}/capture/a3/a3caa2ebbefd998c4b67292f5d957e6340d28f56c96f427f1a98ec89f24293be.txt"])
f("High", "The agent's answer states that refused writes happened",
  "packages/agent_runtime/databridge_agent_runtime/runtime.py (answer assembly vs action outcomes)",
  "Scheduled run run-20260930T202338-3b7ebc7a",
  "Coverage header says both drafts were refused, yet the body says 'I have saved follow-up email drafts as INV-2026-0057.md and INV-2026-0063.md ... This is your notification.' Nothing was saved.",
  "Claims about actions are checked against the action log; a refused action is reported as refused.",
  [f"{ER}/runs/agent/overdue_invoice_followup/run-20260930T202338-3b7ebc7a.json"])
f("High", "The English compiler can never see the write tools of write connections, so read-write automations cannot compile",
  "packages/mcp_ingest/databridge_mcp_ingest/connections.py `import_declarations` (drops `tools_hint`, `writes_for`; SourceConnection forbids them); packages/automation/databridge_automation/catalogue.py (tools only from plan tools_seen or an `<id>.tools.yaml` nothing writes)",
  "POST /sources {mode:import} of infra/mcp_servers.yaml -> GET /platform/catalogue",
  "larkspur_db_writer: tools [], writes_for []; larkspur-followups: tools [], purpose ingest. Compile asks 'which of larkspur_db_writer's write tools?' with no write tools listed, or routes the files draft to larkspur_db_writer.",
  "Write connections advertise their live tools (tools/list) and `writes_for` to the catalogue.",
  [f"{H}/0009_s6_catalogue_pre.json", f"{ER}/sources/larkspur_db_writer.yaml", f"{H}/0060_s6b_compile_v2.json"],
  "scripts/e2e/write_tool_adverts.py writes `<id>.tools.yaml` from live tools/list and sets purpose agent_write on larkspur-followups.")
f("High", "Model open questions can never be answered; drafts stay blocked until the user rewrites the sentence",
  "packages/automation/databridge_automation/compiler.py ~L495 (every proposal.open_question appended as `model:<sha1>`; `answers` never consulted) — the model also echoes each rule question",
  "POST /agents/compile {text:'Keep the Larkspur files folder and CRM database in the graph; check hourly', answers:{model:42863843:'the whole connection', ...}}",
  "Same questions returned (from harness cache, 676 ms). The BUILD_PLAN 5.6 example sentence never compiles; adapter agent needed 4 attempts, the automation 7.",
  "Model questions are answerable (answers fed into the model pass / cache key) and not duplicated.",
  [f"{H}/0031_s7_compile_adapter.json", f"{H}/0033_s7_compile_adapter_answered2.json", f"{H}/0072_s6b_compile_v7.json"])
f("High", "Runtime classifies postgres-mcp read tools as WRITES; compiler classifies them as reads and grants them",
  "packages/agent_runtime/databridge_agent_runtime/tools.py `classify_tools` (no annotations -> write) vs packages/automation/databridge_automation/catalogue.py `classify_tool` (by name)",
  "Run account_briefing after F-01 workaround",
  "Refused: capability_is_a_write for larkspur_db.tool_list_schemas, tool_list_objects, tool_get_object_details, tool_analyze_db_health, tool_get_top_queries (postgres-mcp publishes no readOnlyHint).",
  "One classifier shared by compiler and runtime.",
  [f"{ER}/runs/agent/account_briefing/run-20260930T201527-9fa83787.json"],
  "scripts/e2e/prune_tools.py removed `source.read:larkspur_db:*` from the workflows (SQL via source.query kept).")
f("High", "Passages are never indexed through any product path (adapter agent extract.passages ignored)",
  "packages/adapter_agents (no reference to `passages`), start-walnut-kg.ps1 (no WALNUT_INGEST_PASSAGES), sources_routes `_ingest_job`",
  "Adapter agent spec with extract.passages: true -> POST /adapter-agents/<id>/sync; also POST /sources/<id>/runs",
  "index.json: enabled=false, 'passages off'. retrieve.search / hybrid answering have an empty index.",
  "The spec's extract.passages reaches run_ingest; unchanged items are back-filled.",
  [f"{ER}/ingest/larkspur-files/20260930T201144Z-085e9d/index.json"],
  "scripts/e2e/s5_index_passages.py indexed the 11 built docs with the gateway harness (real embeddings).")
f("High", "Event-triggered agents do not receive the event (item, uri, title)",
  "apps/gateway/databridge_gateway/platform_runtime.py `make_engine_runner` (passes only event_id) + agent_runtime planner input",
  "Drop contracts/Tamsinbay/LOI-2026-003_Tamsinbay_Water.md into the corpus copy -> adapter sync -> engine fires larkspur_new_file_summariser",
  "Fired exactly once, but the answer is 'No item_added event has arrived from larkspur-files, so there is no file address to look up'.",
  "The planner sees the event record it was fired for.",
  [f"{ER}/runs/agent/larkspur_new_file_summariser/run-20260930T202759-9cda5859.json", f"{ER}/automation/firings.jsonl"])
f("High", "Live source reads bypass the ingestion screen and redaction",
  "packages/agent_runtime/databridge_agent_runtime/tools.py (source.read / source.query: no screen, no redaction)",
  "Run larkspur_new_file_summariser: 'The new file is engineering/deploy_notes.txt ... read it with read_bytes'; run account_briefing",
  "read_bytes on the blocked deploy_notes.txt (planted private key) is `permit`ted and captured; only F-15 stopped the text reaching the model. The briefing prints contact emails/phones that ingestion masks (P13).",
  "The pack screen applies to live reads; blocked items are withheld and reported as withheld.",
  [f"{ER}/runs/agent/larkspur_new_file_summariser/run-20260930T202903-629b12f4.json", f"{ER}/runs/agent/account_briefing/run-20260930T201551-6746fef2.json"])
f("High", "Health and evidence surfaces report 0 exclusions although the screen blocked 3 and held 1 for review",
  "packages/adapter_agents (manager.health `exclusions_by_rule`), apps/gateway/databridge_gateway/evidence_routes.py — both count excluded.jsonl/blocked.jsonl only; screen decisions live in governance.jsonl",
  "GET /adapter-agents/larkspur_files_db_graph_sync/health; GET /evidence/sources/larkspur-files",
  "excluded 0, blocked 0, exclusion_reasons [] for a run whose governance.jsonl has block x3 (private_key_block, confidentiality_marking, pricing_heading+currency_table) and review x1.",
  "Screen blocks/reviews are counted and listed with rule id and reverse text.",
  [f"{H}/0039_s7_health1.json", f"{H}/0135_s1_evidence_files.json"])
# ------------------------------------------------------------------------------------ Medium
f("Medium", "read_bytes output is not rendered as text for the planner", "packages/agent_runtime/databridge_agent_runtime/tools.py (GatewayResult.data captured, text empty)",
  "Any agent calling source.read:larkspur-files:read_bytes", "Planner sees empty text ('read_bytes returned no text'); agents cannot read file contents live.",
  "Extract text (same extractor as ingest) and screen it (F-13).", [f"{ER}/runs/agent/larkspur_new_file_summariser/run-20260930T202903-629b12f4.json"])
f("Medium", "verify_run rejects PDF spans that contain line breaks -> every building files run ends `unverified`",
  "packages/mcp_ingest/databridge_mcp_ingest/runner.py `verify_run` (literal `label in slice`, no whitespace normalisation)",
  "Files run 20260930T195922Z-b9e8f3", "2 of 117 pointers 'do not resolve' (location 'Unit 4 Carrow Mill, Thornbury Lane, Bristol' vs text 'Unit 4\\n\\nCarrow Mill, ...'); status unverified. Whitespace-normalised re-check verifies both.",
  "Verify with the grounding wall's own match rule.", [f"{ER}/ingest/larkspur-files/20260930T195922Z-b9e8f3/report.json"])
f("Medium", "Node labels drift away from their pointers; identity splits for the same entity",
  "packages/graph (upsert relabel) + extractor canonicalisation",
  "python scripts/e2e/pointer_audit.py final", "17 pointers whose span no longer contains the node's current label (organisation:c-1001 labelled 'Pellucid Freightways Ltd', span 'C-1001'; 'Larkspur Analytics' -> 'Larkspur Analytics Ltd'). Splits: contract:msa-2026-014 vs contract:master services agreement msa-2026-014; organisation:pellucid / pellucid planning desk / c-1001 beside organisation:pellucid freightways ltd.",
  "A pointer grounds what it is shown next to; aliases resolve to one node.", [f"{ER}/artefacts/pointer_audit_final.json", f"{ER}/artefacts/graph_audit_final.json"])
f("Medium", "table_cell pointers claim cells for derived and masked values", "packages/mcp_ingest/databridge_mcp_ingest/tabular (row writer)",
  "pointer_audit.py", "13 row-key canonical_ids ('larkspur_db/public.contacts/1') point at the pk cell (value 1); 15 masked attributes ('[EMAIL]****') point at cells holding the raw value; no `derivation` recorded.",
  "Derived/masked values carry kind=derived with the transform named.", [f"{ER}/artefacts/pointer_audit_final.json"])
f("Medium", "Masking withholds DB canonical ids, so no person links across sources", "tabular writer + screen (email redaction) ",
  "DB run note: '10 canonical id(s) withheld because the value was masked'", "Rowan Achterberg: 3 per-document doc nodes + DB contact node, 0 links; MANIFEST expects email-keyed links (human-confirmed).",
  "Keyed candidate links via a hashed identifier, confirmed by a human.", [f"{ER}/ingest/larkspur_db/20260930T200528Z-862298/report.json"])
f("Medium", "Relation yield is very low: 73 refused vs 6 accepted", "packages/graph extractor + packs/workspace/ontology.yaml (party_to direction)",
  "Files run refused.jsonl", "47 'nominated evidence passage does not mention' + 26 off-ontology (e.g. contract -party_to-> organisation reversed).",
  "Most true relations survive grounding (known gap 3).", [f"{ER}/ingest/larkspur-files/20260930T195922Z-b9e8f3/refused.jsonl"])
f("Medium", "Gateway extraction ran on openai/gpt-4o, not Sonnet 5.5 as routed", "apps/gateway/databridge_gateway/llm.py `build_extraction_harness` (remaps alias `sonnet` to the detected OpenAI model)",
  "GET /health; build.jsonl produced_by", "entity_extraction and schema_annotation used openai/gpt-4o (27,684 tokens, 11 calls, mean latency 18.7 s); agents/compiler used anthropic/claude-opus-5-5.",
  "config/models.yaml routing honoured when the routed provider's key is present.", [f"{ER}/ingest/larkspur-files/20260930T195922Z-b9e8f3/build.jsonl"])
f("Medium", "Catalogue cannot tell larkspur_db is a database with facts", "packages/automation/databridge_automation/catalogue.py (reads plan step `kind`; plans use `strategy`)",
  "GET /platform/catalogue", "larkspur_db kind '' facts false -> 'database'/'CRM' in English are unresolved (root cause of the failing adapter-agent sentence).",
  "kind=database, facts=true from the tabular step.", [f"{H}/0009_s6_catalogue_pre.json"])
f("Medium", "Agent writes re-ingest into the facts store only, not the graph; no item_changed event", "tabular mapping (follow_up_tasks `unmapped`) + runner events",
  "Approve insert (task_id=1) -> adapter sync", "Row present in facts sqlite; no graph node; events show item_changed 0 for the changed table.",
  "The loop closes in the graph (mapping proposal for the new table) and emits item_changed.", [f"{ER}/facts/larkspur_db_facts.sqlite", f"{ER}/ingest/larkspur_db/20260930T202657Z-3c46be/report.json"])
f("Medium", "Adapter agent auto_additive silently widened the declared file-type scope and ingested a .csv",
  "packages/adapter_agents adapt policy (new_mime / new_file_type under auto_additive)",
  "Drop sales/store_units_2026.csv -> sync twice", "Sync 1: excluded (file_type:.csv); sync 2: new_mime applied, csv captured and built into the graph with no human approval.",
  "Scope widening (P5 declared scope) asks a human.", [f"{H}/0101_s7_drift_baseline.json"])
f("Medium", "/brain/query and /brain/graph take no authentication", "apps/gateway/databridge_gateway/main.py:293,363",
  "curl -X POST :8012/brain/query without Authorization", "Handler runs (500 from F-04) instead of 401; /graph/search correctly returns 401.",
  "require_principal on every disclosing route.", [f"{ER}/logs/gateway.log"])
f("Medium", "Compiler grants every read tool the model lists (least privilege)", "packages/automation/databridge_automation/compiler.py",
  "Compile account_briefing", "Granted get_top_queries (pg_stat_statements), analyze_db_health for a briefing agent.", "Only tools the instructions use.", [f"{H}/0048_s6_compile_briefing.json"])
f("Medium", "Planner model outputs are replayed from cache across runs", "packages/harness cache on task_class agent",
  "Second event firing (csv)", "0 tokens, 70 ms, answer identical to the previous run.", "Agent runs are not cached across firings.", [f"{ER}/runs/agent/larkspur_new_file_summariser/run-20260930T202951-f2a99a40.json"])
# ------------------------------------------------------------------------------------ Low
f("Low", "Golden set and spec examples name `larkspur-db`; the declared connection is `larkspur_db`", "packs/workspace/evals/agent_contract.jsonl; BUILD_PLAN 5.6",
  "Read agent_contract.jsonl", "Cites like 'larkspur-db.invoices/INV-2026-0057#status' can never match a real pointer.", "One id.", ["packs/workspace/evals/agent_contract.jsonl"])
f("Low", "Automation's 'today' is the DB's UTC CURRENT_DATE", "agent planner / instructions", "Scheduled run at 09:00 SGT 1 Oct", "'As of 2026-09-30'.", "Deployment timezone.", [f"{ER}/runs/agent/overdue_invoice_followup/"])
f("Low", "Proposed rows carry agent_run_id NULL", "agent_runtime planner context", "Approved row task_id=1", "agent_run_id empty although the sentence asked for this run's id.", "Run id available to the planner.", ["public.follow_up_tasks task_id=1"])
f("Low", "Blocked/review items are re-extracted on every sync", "packages/mcp_ingest/databridge_mcp_ingest/runner.py `_extract` (state only for built items)",
  "Any re-sync", "4/15 items re-extracted each time (SOW docx took 99 s uncached).", "Unchanged decided items skipped.", [f"{ER}/ingest/larkspur-files/20260930T201144Z-085e9d/extracted.jsonl"])
f("Low", "Truncated titles/descriptions in compiled specs", "compiler", "Adapter-agent and automation compiles",
  "'...hourly sync with search passag'; action description cut at '(' ('table follow_up_tasks').", "Whole words / whole clause.", [f"{H}/0035_s7_compile_adapter_v3.json"])
f("Low", "Traceability gaps vs 5.8", "mcp_ingest runner report; retrieval Passage; agent RunRecord",
  "Read report.json, refused.jsonl, retrieval.search output, run header", "report.json produced_by null; refused.jsonl lacks produced_by; passages lack embedding model; run header lacks tokens/cost (only in answer footer).",
  "Fields present as 5.8 lists.", [f"{ER}/ingest/larkspur-files/20260930T195922Z-b9e8f3/report.json"])
f("Low", "ac-009: agent abstains but cannot say the document was withheld by the screen", "agent_runtime tools (no exclusion awareness)",
  "golden ac-009", "'no information on 2026 compensation bands'.", "'withheld by the screen (confidentiality_marking)'.", [f"{ER}/artefacts/s5_golden_larkspur_qa.json"])
f("Low", "Environment: another agent's scratch schema (w4_scratch_*) appeared in the shared demo DB during the run", "(not product) shared Docker Postgres",
  "DB sync 20260930T203028Z-728583", "3 tables inventoried and correctly excluded (not_in_tables_include).", "n/a — recorded so nobody chases it.", [f"{ER}/ingest/larkspur_db/20260930T203028Z-728583/excluded.jsonl"])

md = []
md.append("# E2E-2026-10-01 — first end-to-end proof (Phase B)\n")
md.append("Run by: E2E (claude-opus-5-5); models: anthropic/claude-opus-5-5 (English compiler `agentic_orchestration`, agent planner `agent`), openai/gpt-4o (ingest `entity_extraction` + `schema_annotation` via the gateway harness), openai/text-embedding-3-small (passages). Date: 2026-10-01 (SGT).\n")
md.append(f"Isolation: every runtime path under `{ER}/` (graph, capture, sources, ingest, events, runs, passages, cache, facts, agents, adapter_agents, automation, ontology), a private copy of the corpus (`corpus/files`) and a private `mcp_servers.yaml` pointing larkspur-files at it and larkspur-followups at `followups/`. Gateway on :8012 via `scripts/e2e/serve_e2e.py` (adds one e2e-only route, `POST /__e2e/tick`, to fast-forward the trigger engine clock). Docker demo Postgres/MCPJungle untouched except: 1 row in public.follow_up_tasks (task_id=1, the intended loop) and a scratch table `public.e2e_scratch` created and dropped for drift. Product code unchanged.\n")
md.append("## Scenarios\n\n| # | Scenario | Result | Evidence summary |\n|---|---|---|---|")
for s in SCENARIOS:
    md.append(f"| {s[0]} | {s[1]} | **{s[2]}** | {s[3]} |")
c = collections.Counter(x["severity"] for x in F)
md.append(f"\n**Findings:** {len(F)} — Critical {c['Critical']}, High {c['High']}, Medium {c['Medium']}, Low {c['Low']}.\n")
md.append("## Timings (measured; machine shared with other agents' pytest runs)\n")
md.append("""| Stage | Time |
|---|---|
| Gateway boot | ~20 s |
| Import declarations | 1.5 s |
| Propose plan: larkspur-files / larkspur_db | 2.4 s / 28.1 s (uvx spawn) |
| Files ingest (15 items) wall | 3 min 03 s: collect 0.36 s, extract 127.4 s (docling 126.8 s for 6 PDF/DOCX; SOW docx 98.9 s), screen 0.35 s, build 54.5 s (model wait 53.6 s; 11 calls, 27,684 tokens, mean 18.7 s, max 37.7 s), verify 0.24 s |
| DB ingest after mapping approval | 13.1 s: profile 4.2 s, mapping 5.3 s, graph run 2.9 s, facts 0.66 s, cards 0.16 s |
| Adapter-agent sync, nothing changed (both sources) | 13–25 s (files ~0.5 s, DB ~6 s + profiling) |
| Passage indexing (11 docs, real embeddings) | 11.2 s |
| English compile | 9.5–37 s per sentence (Opus 5.5) |
| Account briefing | 66.3 s, 7 model calls, 19 tool calls, 59,334 tokens |
| Golden set ac-001..010 | 8 min 01 s total, 15–38 s each, 10–48k tokens each |
| Scheduled automation run | 44.1 s (13,148 tokens); manual re-fire 60.5 s (19,331 tokens) |
| Approve -> INSERT executed | 0.4–1.0 s |
| Event (item_added) -> agent fired | < 5 s (engine poll) ; run 21.6 s |
| End-to-end wall clock | ~36 min (19:56–20:32 UTC), including every workaround |

Cost in USD is not reported by any artefact (see F-33).
""")
md.append("## Findings\n")
for x in F:
    md.append(f"### {x['id']} [{x['severity']}] {x['title']}\n")
    md.append(f"- **Owning component:** {x['component']}")
    md.append(f"- **Reproduction:** {x['reproduction']}")
    md.append(f"- **Observed:** {x['observed']}")
    md.append(f"- **Expected:** {x['expected']}")
    md.append(f"- **Evidence:** " + "; ".join(f"`{e}`" for e in x["evidence"]))
    if x["workaround"]:
        md.append(f"- **Workaround used:** {x['workaround']}")
    md.append("")
md.append("## Where everything is\n")
md.append(f"""- Run directory: `{ER}/` — `findings.json`, `notes.md`, `artefacts/` (every HTTP request/response under `artefacts/http/`, `graph_audit_*.json`, `pointer_audit_*.json`, `s5_*.json`, `timings.json`), `logs/gateway.log`.
- Harness scripts: `databridge/scripts/e2e/` (serve_e2e, e2e_env, e2e_client, graph_audit, pointer_audit, s5_brain, s5_retrieval, s5_index_passages, s5_golden, run_agent, fix_declaration, prune_tools, write_tool_adverts, aa_sync, collect_timings, write_findings).
- Assertions over a completed run: `databridge/tests/e2e/test_phase_b_e2e.py` (marker `integration`, skipped unless `WALNUT_E2E_DIR` is set). Against this run: 4 pass, 2 fail (pointers 100%, no dangling edges) — the failures are F-03/F-19/F-20.
- Workarounds touched only the e2e dir: regenerated declarations (`*.agent.yaml.compiler-original` kept), pruned workflows (`*.workflow.yaml.orig` kept), `sources/*.tools.yaml`, `purpose: agent_write` on the e2e copy of larkspur-followups.
""")
out_md = ROOT.parent / "wiki" / "40-reviews" / "E2E-2026-10-01.md"
out_md.write_text("\n".join(md), encoding="utf-8")
(E / "findings.json").write_text(json.dumps({"run_by": "E2E (claude-opus-5-5)", "scenarios": [
    {"id": s[0], "name": s[1], "result": s[2], "summary": s[3]} for s in SCENARIOS],
    "counts": dict(c), "findings": F}, indent=2), encoding="utf-8")
print(out_md, dict(c), len(F))
