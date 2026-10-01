"""E2E3: write <run>/findings.json (the findings of wiki/40-reviews/E2E-2026-10-01-run3.md)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402

R = "databridge/var/e2e-20261001T0836"
A = f"{R}/artefacts"
F = [
    ("P-01", "High", "gateway brain_delegation + federated planner",
     "/brain/query states a wrong count as fact: Pellucid Freightways has '0 open invoices' (live: 1, INV-2026-0063)",
     "POST /brain/query 'how many open invoices does Pellucid Freightways have?'",
     "routed_to federated; table count_invoice_no = 0; coverage 'uncertain' because sub-task t2 returned no rows; the narration says 'Pellucid Freightways has 0 open invoices'",
     "1, or no number when a link matched nothing (an empty link must not produce a 0)",
     [f"{A}/p3_brain.json (ac-008)", f"{R}/runs/federated/e2e3-analyst/fed-20261001T084428-da312a46.json"], "N-14"),
    ("P-02", "High", "packages/agent_runtime (delegate.ask argument shaping)",
     "A delegating workflow agent compiles (N-04 fixed) but every delegate.ask call it makes is malformed",
     "POST /agents/larkspur_cross_source_qa/run with the D question",
     "5 delegate.ask calls, each refused: op '=' / 'mentioned_in', time_window.axis 'invoice.due_date', group_by a string; outcome value_absent, coverage reduced; raw pydantic error text in the answer",
     "delegate.ask arguments shaped/validated against SubTask before the call (as N-11 does for MCP tools); errors summarised, not pasted",
     [f"{A}/p3_deleg_agent.json"], "N-04, N-11"),
    ("P-03", "High", "packages/materialise",
     "No tabular dataset can be built from English: the labelled request fails its own pointer check",
     "POST /materialise 'One row per invoice at its issue date ... the label is the invoice status 30 days after it was issued, with a maturity lag of P0D.' (the lag answers the planner's question)",
     "refused POINTER_CHECK_FAILED: of 6 grounded cells, 4 pointers (invoice.issued_on, invoice.status, table_cell) do not resolve; nothing published (plan d02fa4f5...)",
     "a published dataset whose pointers resolve (0 mature rows is acceptable data)",
     [f"{A}/p3d_mat2.json (labelled_p0d)"], "N-07"),
    ("P-04", "High", "packages/agent_runtime planner + derivation gate; files_blob write server",
     "human_notified follow-up drafts are never written",
     "Scheduled overdue_invoice_follow_up (ticks 2026-10-02 and 2026-10-05) -> approve a draft",
     "Firing 1: path 'larkspur-followups/INV-2026-0057.txt' (the connection id as a folder) -> approval 409 'parent folder must already exist inside the writable root', left 'approved', retry fails the same. Firing 2: path 'run-<id>/INV-2026-0057_follow_up.txt' -> 409 ungrounded value. Also the compiler forces the draft to human_required ('writes something about a person') although the sentence says 'tell me after'",
     "a draft file in the follow-ups root; a path the runtime builds from cited values is grounded",
     [f"{A}/p3_overdue.json", f"{A}/p3d_crm.json", f"{A}/p2_result.json (overdue rounds)"], "N-06"),
    ("P-05", "Medium", "packages/mcp_ingest build / packages/graph merge (N-16 residual)",
     "Cross-source answer omits Corriedale: files-side 'Corriedale Grocers' / 'Corriedale' carry no canonical id and never join C-1002",
     "POST /federated/ask (D question, explicit windows) + answers",
     "table LA-007 11100 only; independent computation: LA-007 11100 and LA-012 15300 (Q4 2026). Coverage honestly 'uncertain', naming the unlinked mentions",
     "LA-012 15300 present",
     [f"{A}/fed_check3_fed.json", f"{A}/p3_fed.json", f"{A}/graph_audit_final.json (probe corriedale)"], "N-02, N-16"),
    ("P-06", "Medium", "apps/gateway llm.build_context / brain_server lookup",
     "/brain/query graph lookups by record id say 'No entity in the knowledge graph matches' although the node exists",
     "POST /brain/query 'what is the status of invoice INV-2026-0057?' (also ac-002, random q1, q5)",
     "No entity matches; verbatim passages follow (the answer is inside one); ac-004 still will not say Leeds; a question about the blocked HR compensation file does not say it was withheld",
     "the invoice node's status/due date with its pointer; a withheld item named as withheld",
     [f"{A}/p3_brain.json", f"{A}/p3_rq.json"], "N-14"),
    ("P-07", "Medium", "packages/automation prepass (AUTO2 delegate detection)",
     "A paraphrased delegation becomes a request to build a NEW adapter agent",
     "POST /agents/compile 'When I ask about a customer, have the Larkspur adapter agent pull together everything both sources know about them. Read only.'",
     "kind adapter_agent; questions about which connections to keep in the graph",
     "delegate:<slug> question offering larkspur_files_crm_adapter",
     [f"{A}/p3_rq.json (a4_delegate)"], "N-04"),
    ("P-08", "Medium", "packages/federated run record + apps/gateway activity_routes",
     "The Activity delegation tree shows neither the plan's assumptions nor its questions",
     "GET /activity/runs/<fed run>?kind=federated_run",
     "plan.assumptions null ('not recorded: the 5.7 run record does not keep the plan's assumptions'); plan.questions_text null and listed in masked_fields",
     "assumptions and questions shown (brief requirement)",
     [f"{A}/http/0182_p3c_act_run_primary.json"], "N-18"),
    ("P-09", "Medium", "apps/gateway agents_routes (GET /agents/approvals)",
     "Another owner's pending proposals, with full arguments, are listed to a non-owner",
     "GET /agents/approvals as e2e3-second",
     "2 of e2e3-analyst's proposals with args (email body, path) and may_decide false; Activity hides the same runs from this principal",
     "owner-or-operator only, like /activity",
     [f"{A}/http/*_p4_approvals_second.json"], "N-18"),
    ("P-10", "Medium", "apps/gateway materialise_routes + packages/materialise",
     "/materialise questions cannot be answered back; the user must re-word",
     "POST /materialise with 6 English requests",
     "6/6 PlanNeedsInput; no answers field. Run 2's chunk request that built now asks which passage entity",
     "answers accepted as /federated/ask does",
     [f"{A}/p3c_mat.json"], "N-03 (analogue)"),
    ("P-11", "Low", "packages/federated planner",
     "A plan-validation error is shown to the user as a planner question",
     "POST /federated/ask (D question), round 1",
     "status stopped; question 'Cannot order by 'total_gbp': the result's columns are [...]'; nothing read",
     "the planner repairs its own order_by; only real ambiguities reach the user",
     [f"{A}/p3_fed.json (primary.r0)"], "N-03"),
    ("P-12", "Low", "packages/agent_runtime answer.py / federated narrator",
     "Answer-contract false positives remain for numbers from the question and ids in query arguments",
     "D answer; golden ac-006",
     "UNSUPPORTED number_not_in_cited_evidence: 1, 2026, 30, 31 (the user's own date window); '1003' (C-1003) in ac-006",
     "numbers the question itself states, and ids in the cited query, are grounded",
     [f"{A}/p3_fed.json (primary.r1)", f"{A}/s5_golden_larkspur_qa.json"], "N-13"),
    ("P-13", "Low", "apps/gateway /brain/query",
     "A vague question gets 422 UNGROUNDED_OR_NO_MATCH instead of a clarifying question",
     "POST /brain/query 'How are things going with our clients?'",
     "422 'no matching entity in the graph'",
     "a clarifying question",
     [f"{A}/p3_rq.json (q4_vague)"], "-"),
    ("P-14", "Low", "E2E3 harness / environment (not product)",
     "Frontend Playwright run void: the build carried no token and the gateway was killed mid-run",
     "npm run build with NEXT_PUBLIC_WALNUT_TOKEN read via a POSIX path the Windows python could not open; session interruption at 09:00 UTC killed the gateway's background task",
     "every authenticated page 401; then ECONNREFUSED; run stopped at 26/43 (16 failures, all from these two causes). Not rebuilt: HC3 is editing apps/frontend",
     "re-run build + live specs in a fresh run",
     [f"{R}/logs/npm_build.log", f"{R}/logs/playwright_live.log", f"{R}/notes.md"], "N-23, N-24"),
]


def main() -> None:
    out = [dict(zip(("id", "severity", "owner", "title", "repro", "observed", "expected",
                     "evidence", "run2"), f)) for f in F]
    p = e2e_dir() / "findings.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(p, len(out))


if __name__ == "__main__":
    main()
