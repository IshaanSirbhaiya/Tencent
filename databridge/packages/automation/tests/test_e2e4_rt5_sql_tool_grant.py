"""E2E run 4 Q-02 (compiler side), fixed by RT5: the compiler never grants a connection's SQL tool
as a `source.read`, even when a crawl plan vouched for it as a read.

Run 4's account briefing (var/e2e-20261001T1255/agents/account_briefing.workflow.yaml) was granted
`source.read:larkspur_db:execute_sql` from the model's proposal; the runtime refuses every
statement sent that way (it is reached only through the checked query path), so the agent carried
a grant that could never succeed. The model pass is driven through the real `Harness` with a
scripted backend; the expectation is derived from the catalogue's own classification (`ToolRef.query`).
"""
from __future__ import annotations

import json

from automation_testing import FILES, catalogue
from databridge_automation import compile_english
from databridge_automation.catalogue import ConnectionInfo, classify_tool
from databridge_automation.compiler import ModelProposal
from databridge_harness import FakeBackend, Harness
from databridge_workflow_spec import parse_tool

#: postgres-mcp's tools as run 4 saw them: the SQL tool vouched as a read by an approved plan.
VOUCHED_DB = ConnectionInfo(
    id="larkspur-db", title="Larkspur CRM database", kind="database",
    tools=(classify_tool("execute_sql", approved_read=True), classify_tool("list_schemas"),
           classify_tool("get_object_details")),
    locations=("public.invoices", "public.customers"), plan_status="approved")
ASK = ("Account briefing: when I ask about a customer, give me a one-page briefing on that account "
       "from larkspur-db - the contract, the projects and the invoices. Read only; cite every fact.")


def _compile():
    proposal = {
        "kind": "agent", "title": "Account briefing", "agent_id": "account_briefing",
        "instructions": ["Use list_schemas and get_object_details to find the invoice table.",
                         "Use source.read:larkspur-db:execute_sql (read-only SELECT statements "
                         "only) or source.query:larkspur-db to fetch the invoices."],
        "sources": ["larkspur-db"],
        "tools": ["source.read:larkspur-db:execute_sql", "source.read:larkspur-db:list_schemas",
                  "source.read:larkspur-db:get_object_details", "source.query:larkspur-db"]}
    fb = FakeBackend(json.dumps(ModelProposal.model_validate(proposal).model_dump()))
    res = compile_english(ASK, catalogue(FILES, VOUCHED_DB), harness=Harness(fb, cache=None))
    return proposal, res


def test_q02_the_sql_tool_is_never_a_read_grant_and_the_query_path_is():
    proposal, res = _compile()
    assert res.spec is not None, [q.text for q in res.open_questions]
    sql_tools = {t.name for t in VOUCHED_DB.read_tools if t.query}
    # non-vacuous: the catalogue does classify the SQL tool a READ, and the model did propose it
    assert sql_tools and any(parse_tool(t)["tool"] in sql_tools for t in proposal["tools"])
    granted = {parse_tool(t)["tool"] for t in res.spec.tools
               if parse_tool(t)["family"] == "source" and parse_tool(t)["verb"] == "read"}
    assert not granted & sql_tools
    assert any(sql in n and "dropped model tool" in n for sql in sql_tools for n in res.notes)
    # the connection is still queryable - through the checked path
    assert any(parse_tool(t)["connection"] == VOUCHED_DB.id
               and (parse_tool(t)["verb"] == "query" or parse_tool(t)["family"] == "facts")
               for t in res.spec.tools)
    # the other vouched reads the instructions use are unaffected
    named = {t.name for t in VOUCHED_DB.read_tools if not t.query}
    assert named and named <= granted
    assert not any(d.field == f"tools[source.read:{VOUCHED_DB.id}:{s}]"
                   for s in sql_tools for d in res.spec.derivations)
