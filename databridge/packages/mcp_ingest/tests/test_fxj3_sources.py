"""FXJ3 — the mcp_ingest "sources" track after Codex's SECOND (final) review (f2-mi-sources, R-022
round 2). One test (or a small group) per finding, each written to fail on the round-1 code:

 #1 generic executors are never vouchable reads; SQL is held to a closed read-only grammar
 #2 the read-only client re-reads the live tools before its first call
 #3 `${CREDENTIAL:-default}` is refused (a dev flag is the only way back)
 #4 concatenated credential names (`PGPASSWORD`)          #5 MCPJungle URL credentials
 #6 approval evidence is compared field-by-field against a hash-chained log
 #7 stored connections are scanned on load                #8 factories never echo a secret
 #9 the planner sees only masked fields                   #10 registration is fail-closed
 #11 write registration includes its advert               #12 resource plans see live resources
 #13 an unjournaled list_changed is not delivered         #14 a later templates page fails loudly
 #15 close_pool retires leased sessions
Everything synthetic; no network, no model.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
import yaml

from databridge_mcp_ingest import client as mc
from databridge_mcp_ingest import jungle as jg
from databridge_mcp_ingest.client import McpClientError, ReadOnlyClient, ResourceInfo, ToolInfo
from databridge_mcp_ingest.connections import (ConnectionError_, RegistrationIncomplete,
                                               RegistrationRefused, SourceConnection,
                                               connection_path, http_connection,
                                               import_declarations, load_connection,
                                               register_connection, register_connections,
                                               stdio_connection)
from databridge_mcp_ingest.plans import (ApprovalRefused, CrawlPlan, PlanError, StepSpec,
                                         approve_plan, classify_plan_tools, is_generic_executor,
                                         load_plan, plan_approvals_path, plan_path,
                                         planner_catalogue, read_only_sql_refusal, save_plan,
                                         step_tool_digests, tools_digest, verify_plan_approval)
from databridge_mcp_ingest.secret_scan import (DEV_ALLOW_DEFAULTS_ENV, find_literal_secrets,
                                               is_credential_name)

from conftest import test_principal as _issued

pytestmark = pytest.mark.real_approval

SECRET = "Zq7xVv0pLm3T"          # a synthetic literal the tests look for in outputs
_OBJ = {"type": "object", "properties": {"path": {"type": "string"}}}
_SQL = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
PKG = Path(__file__).resolve().parents[1] / "databridge_mcp_ingest"
MANIFEST = Path(__file__).resolve().parents[3] / "infra" / "mcp_servers.yaml"


class _Server:
    def __init__(self, tools, resources=None, supports=True):
        self.tools = list(tools)
        self.resources = resources
        self.supports_resources = supports
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return list(self.tools)

    def list_resources(self):
        if isinstance(self.resources, Exception):
            raise self.resources
        return list(self.resources or [])

    def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        return mc.ToolResult(parts=(mc.ContentPart(kind="text", text="ok"),))


def _docs_tools(extra=()):
    return [ToolInfo("list_docs", "List documents", dict(_OBJ)),
            ToolInfo("read_doc", "Read one document", dict(_OBJ)), *extra]


def _save(sid, steps, tools) -> CrawlPlan:
    plan = CrawlPlan(source_id=sid, origin="manual", steps=steps,
                     tools_seen=sorted(t.name for t in tools), tools_digest=tools_digest(tools),
                     step_tool_digests=step_tool_digests(steps, tools),
                     tool_classes=classify_plan_tools(steps, tools))
    save_plan(plan)
    return plan


def _docs_step(list_tool="list_docs", read_tool="read_doc"):
    return StepSpec.model_validate({
        "id": "docs", "strategy": "documents",
        "list": {"tool": list_tool, "args": {"path": "{path}"}, "root_arg_default": "",
                 "parse": {"mode": "lines", "patterns": [{"regex": "^(?P<name>.+)$"}]}},
        "read": {"text_tool": read_tool, "text_args": {"path": "{path}"}}})


def _sql_step(tool="execute_sql"):
    return StepSpec.model_validate({"id": "tab", "strategy": "tabular",
                                    "sql": {"tool": tool, "dialect": "information_schema"}})


def _conn(sid="src", **kw) -> SourceConnection:
    return register_connection({"id": sid, "transport": {"type": "stdio", "command": "x"}, **kw},
                               advertise=False)


# === #1 generic executors ===========================================================================
def _dialect_statements():
    """Every full statement in every bundled `*.sql.yaml`, rendered with neutral values."""
    stmt = re.compile(r"^\s*(select|with|show|set|pragma)\b", re.I)

    def render(t):
        t = re.sub(r"\{[a-z_]+\|ident\}", '"x"', t)
        t = re.sub(r"\{[a-z_]+\|literal\}", "'v'", t)
        t = re.sub(r"\{[a-z_]+\|int\}", "10", t)
        t = t.replace("{measures}", ', COUNT("c") AS m').replace("{group_cols}", '"c"')
        return t.replace("{order_by}", '"c"').replace("{p}", "c0")

    def walk(v, where):
        if isinstance(v, dict):
            for k, x in v.items():
                if k != "tool_hints":
                    yield from walk(x, f"{where}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                yield from walk(x, f"{where}[{i}]")
        elif isinstance(v, str) and stmt.match(v):
            yield where, render(v)

    for f in sorted((PKG / "profiles").rglob("*.sql.yaml")):
        yield from walk(yaml.safe_load(f.read_text(encoding="utf-8")), f.name)


def test_1_every_bundled_dialect_statement_fits_the_read_only_grammar():
    stmts = list(_dialect_statements())
    assert len(stmts) > 20                                               # non-vacuous
    refused = {w: read_only_sql_refusal(s) for w, s in stmts if read_only_sql_refusal(s)}
    assert refused == {}


@pytest.mark.parametrize("sql", [
    "DELETE FROM invoices", "UPDATE t SET a = 1", "SELECT 1; DROP TABLE t",
    "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "SELECT my_writing_proc()",
    "SELECT * INTO t2 FROM t", "SELECT * FROM t FOR UPDATE", "CALL p()", "SELECT nextval('s')",
    "SELECT $$x$$", "SET search_path = evil", "EXPLAIN ANALYZE DELETE FROM t", ""])
def test_1_writes_and_unknown_functions_are_refused(sql):
    assert read_only_sql_refusal(sql)


def test_1_an_executor_is_never_vouchable_as_a_list_or_read_tool(walnut_env):
    tools = _docs_tools([ToolInfo("execute_sql", "Run SQL", dict(_SQL))])
    assert is_generic_executor(tools[-1]) and is_generic_executor("runQuery")
    assert not is_generic_executor(ToolInfo("search", "", {"type": "object", "properties": {
        "query": {"type": "string"}}}))                                 # search text, not SQL
    _conn()
    _save("src", [_docs_step(read_tool="execute_sql")], tools)
    with pytest.raises(PlanError, match="generic executor"):
        approve_plan("src", principal=_issued("dana"), client=_Server(tools),
                     vouch_read_tools=["execute_sql"])


def test_1_a_vouched_sql_tool_runs_only_closed_grammar_reads(walnut_env):
    tools = [ToolInfo("execute_sql", "Run SQL", dict(_SQL))]
    _conn()
    _save("src", [_sql_step()], tools)
    plan = approve_plan("src", principal=_issued("dana"), client=_Server(tools),
                        vouch_read_tools=["execute_sql"])
    assert plan.tool_classes["execute_sql"] == "read:sql_guarded:vouched_by:dana"
    server = _Server(tools)
    ro = ReadOnlyClient(server, plan=plan)
    assert ro.call_tool("execute_sql", {"sql": "SELECT COUNT(*) FROM t"}).text == "ok"
    for bad in ({"sql": "DELETE FROM t"}, {"sql": "SELECT my_proc()"},
                {"sql": "SELECT 1", "mode": "write"}, {}):
        with pytest.raises(McpClientError, match="runs whatever its arguments say"):
            ro.call_tool("execute_sql", bad)
    assert [c[0] for c in server.calls] == ["execute_sql"]              # only the read ran
    # without a plan (the tabular pipeline's wrapper) the statement is still held to it
    with pytest.raises(McpClientError, match="not a read"):
        ReadOnlyClient(_Server(tools)).call_tool("execute_sql", {"sql": "DROP TABLE t"})


# === #2 the live tool list is re-read before the first call ========================================
def _approved_docs(tools):
    _conn()
    _save("src", [_docs_step()], tools)
    return approve_plan("src", principal=_issued("dana"), client=_Server(tools))


def test_2_a_tool_flipped_to_write_after_approval_is_refused_without_list_tools(walnut_env):
    plan = _approved_docs(_docs_tools())
    flipped = [_docs_tools()[0], ToolInfo("read_doc", "Read one document", dict(_OBJ),
                                          read_only=False)]
    server = _Server(flipped)
    with pytest.raises(McpClientError, match="can change the source"):
        ReadOnlyClient(server, plan=plan).call_tool("read_doc", {})     # no list_tools first
    assert server.calls == []


def test_2_a_changed_tool_is_refused_but_an_added_tool_is_not(walnut_env):
    plan = _approved_docs(_docs_tools())
    changed = [_docs_tools()[0], ToolInfo("read_doc", "Now also deletes", dict(_OBJ))]
    with pytest.raises(McpClientError, match="changed on the server"):
        ReadOnlyClient(_Server(changed), plan=plan).call_tool("read_doc", {})
    added = _docs_tools([ToolInfo("get_stats", "", dict(_OBJ))])
    assert ReadOnlyClient(_Server(added), plan=plan).call_tool("read_doc", {}).text == "ok"


# === #3 credential defaults ========================================================================
@pytest.mark.parametrize("env", [
    {"DB_PASSWORD": "${DB_PASSWORD:-" + SECRET + "}"},
    {"DATABASE_URI": "postgresql://u:${PG_READER_PASSWORD:-" + SECRET + "}@h/db"},
    {"DATABASE_URI": "postgresql://u:${SOME_VAR:-" + SECRET + "}@h/db"},       # position
    {"OPTS": "--token ${API_TOKEN:-" + SECRET + "}"}])
def test_3_a_defaulted_credential_is_refused(walnut_env, monkeypatch, env):
    monkeypatch.delenv(DEV_ALLOW_DEFAULTS_ENV, raising=False)
    with pytest.raises(ConnectionError_) as err:
        register_connection({"id": "d", "transport": {"type": "stdio", "command": "x",
                                                       "env": env}})
    assert SECRET not in str(err.value)
    assert not connection_path("d").exists()
    monkeypatch.setenv(DEV_ALLOW_DEFAULTS_ENV, "1")                     # the explicit dev flag
    assert register_connection({"id": "d", "transport": {"type": "stdio", "command": "x",
                                                          "env": env}}).id == "d"


def test_3_non_credential_defaults_stay_legal_and_the_manifest_has_none(walnut_env, monkeypatch):
    monkeypatch.delenv(DEV_ALLOW_DEFAULTS_ENV, raising=False)
    assert find_literal_secrets({"type": "stdio", "command": "${WALNUT_PYTHON:-python}",
                                 "env": {"P": "${WALNUT_DEMO_PG_PORT:-5433}"}}) == []
    text = MANIFEST.read_text(encoding="utf-8")
    refs = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-", text)
    assert refs                                                          # non-vacuous
    assert [r for r in refs if is_credential_name(r)] == []
    monkeypatch.setenv("WALNUT_DEMO_PG_READER_PASSWORD", "synthetic")
    monkeypatch.setenv("WALNUT_DEMO_PG_WRITER_PASSWORD", "synthetic")
    assert import_declarations(MANIFEST, register=False)                 # the demo still imports


# === #4 concatenated credential names ==============================================================
def test_4_pgpassword_is_a_credential_everywhere(walnut_env):
    assert is_credential_name("PGPASSWORD") and is_credential_name("APITOKEN")
    assert not is_credential_name("WALNUT_DEMO_PG_PORT") and not is_credential_name("TOKENIZER")
    with pytest.raises(ConnectionError_) as err:
        register_connection({"id": "p", "transport": {"type": "stdio", "command": "psql-mcp",
                                                       "env": {"PGPASSWORD": SECRET}}})
    assert SECRET not in str(err.value)
    raw = SourceConnection.model_construct(
        id="p", via="mcpjungle", jungle_server=None, title="p",
        transport=mc.StdioTransport.model_construct(type="stdio", command="psql-mcp", args=[],
                                                    env={"PGPASSWORD": SECRET}, env_from={},
                                                    cwd=None))
    with pytest.raises(jg.JungleError) as jerr:
        jg.body_from_connection(raw)
    assert SECRET not in str(jerr.value)
    ok = stdio_connection("p", "psql-mcp", env={"PGPASSWORD": "${PGPASSWORD}"}, via="mcpjungle")
    assert jg.body_from_connection(ok)["env"] == {"PGPASSWORD": "${PGPASSWORD}"}


# === #5 MCPJungle never stores a URL credential ====================================================
@pytest.mark.parametrize("url", [f"https://user:{SECRET}@mcp.example/mcp",
                                 f"https://mcp.example/mcp?token={SECRET}",
                                 f"https://mcp.example/mcp?access_token={SECRET}"])
def test_5_body_from_connection_refuses_url_credentials(url):
    conn = SourceConnection.model_construct(
        id="h", via="mcpjungle", jungle_server=None, title="h",
        transport=mc.HttpTransport.model_construct(type="http", url=url, headers={},
                                                   headers_from={}))
    with pytest.raises(jg.JungleError) as err:
        jg.body_from_connection(conn)
    assert SECRET not in str(err.value)


# === #6 approval evidence is compared field by field, against a hash-chained log ===================
def _edit_plan(**approval):
    data = yaml.safe_load(plan_path("src").read_text(encoding="utf-8"))
    data["approval"].update(approval)
    plan_path("src").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.mark.parametrize("edit", [{"dev_only": True}, {"dev_only": False},
                                  {"authenticated_by": "someone-else"},
                                  {"principal_type": "Admin"},
                                  {"vouched_read_tools": ["frobnicate"]},
                                  {"tools_digest": "0" * 64}])
def test_6_an_edited_approval_field_is_not_an_approval(walnut_env, edit):
    _approved_docs(_docs_tools())
    assert verify_plan_approval(load_plan("src"))["principal_id"] == "dana"
    before = load_plan("src").approval
    if all(before.get(k) == v for k, v in edit.items()):
        edit = {k: (not v if isinstance(v, bool) else v) for k, v in edit.items()}
    _edit_plan(**edit)
    with pytest.raises(ApprovalRefused, match="IDENTICAL"):
        verify_plan_approval(load_plan("src"))


def test_6_the_log_is_hash_chained_and_an_edited_line_breaks_it(walnut_env):
    tools = _docs_tools()
    _approved_docs(tools)
    approve_plan("src", principal=_issued("erin"), client=_Server(tools))
    log = plan_approvals_path("src")
    lines = log.read_text(encoding="utf-8").splitlines()
    recs = [json.loads(x) for x in lines]
    assert recs[0]["prev"] == "genesis" and recs[1]["prev"] == recs[0]["hash"]
    got = verify_plan_approval(load_plan("src"))
    assert got["principal_id"] == "erin" and "hash" not in got          # the LOGGED record
    recs[0]["dev_only"] = not recs[0]["dev_only"]                        # forge the older line
    log.write_text("\n".join([json.dumps(recs[0]), lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(PlanError, match="hash chain is broken"):
        verify_plan_approval(load_plan("src"))


# === #7 stored connections are scanned on load =====================================================
@pytest.mark.parametrize("transport", [
    {"type": "http", "url": f"https://h.example/mcp?token={SECRET}"},
    {"type": "stdio", "command": "x", "env": {"PGPASSWORD": SECRET}},
    {"type": "stdio", "command": "x", "env": {"DB_PASSWORD": "${DB_PASSWORD:-" + SECRET + "}"}}])
def test_7_a_hand_edited_file_with_a_secret_is_refused_at_load(walnut_env, monkeypatch, transport):
    monkeypatch.delenv(DEV_ALLOW_DEFAULTS_ENV, raising=False)
    p = connection_path("legacy")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"id": "legacy", "transport": transport}), encoding="utf-8")
    with pytest.raises(ConnectionError_) as err:
        load_connection("legacy")
    assert SECRET not in str(err.value)


# === #8 public factories never echo a refused secret ===============================================
def test_8_factories_refuse_without_echoing(walnut_env):
    for make in (lambda: stdio_connection("f", "x", env={"API_KEY": SECRET}),
                 lambda: http_connection("f", "https://h.example/mcp",
                                         headers={"Authorization": SECRET}),
                 lambda: http_connection("f", f"https://h.example/mcp?api_key={SECRET}")):
        with pytest.raises(ConnectionError_) as err:
            make()
        assert SECRET not in str(err.value) and SECRET not in repr(err.value)


# === #9 the planner sees only masked fields ========================================================
def test_9_schema_and_mime_content_reaches_the_model_masked_or_not_at_all():
    from databridge_mcp_ingest.screen import load_screen, screen_text

    cfg = load_screen(packs=[])
    card = "4111 1111 1111 1111"                                         # Luhn-valid, synthetic
    probe = screen_text(f"default {card}", cfg)
    assert probe.decision == "allow" and card not in probe.masked_text  # derived: mask-only
    schema = {"type": "object", "properties": {"path": {"type": "string",
                                                        "default": f"card {card}"}}}
    tools = [ToolInfo("list_docs", "List documents", schema),
             ToolInfo("delete_doc", "Remove a document", dict(_OBJ))]
    resources = [ResourceInfo(uri="a://1", mime="application/pdf"),
                 ResourceInfo(uri="a://2", mime=f"text/{card.replace(' ', '')}")]
    catalogue, summary, notes = planner_catalogue(tools, resources, screen=cfg)
    blob = json.dumps([catalogue, summary])
    assert [t["name"] for t in catalogue] == ["list_docs"]
    assert card not in blob and card.replace(" ", "") not in blob
    assert any("delete_doc" in n and "change the source" in n for n in notes)
    assert summary["mime_types"] == ["application/pdf"]


# === #10 registration is fail-closed unless the DEPLOYMENT says operator ===========================
def test_10_registration_authority_comes_from_the_deployment_not_the_caller(walnut_env,
                                                                            monkeypatch):
    import sys

    spec = {"id": "cmd", "transport": {"type": "stdio", "command": sys.executable,
                                       "args": ["server.py"]}}
    monkeypatch.delenv("WALNUT_SOURCE_REGISTRATION", raising=False)
    monkeypatch.delenv("WALNUT_COMMAND_SOURCES", raising=False)
    for origin in (None, "operator"):                                    # the caller's say-so
        with pytest.raises(RegistrationRefused) as err:
            register_connection(spec, origin=origin)
        assert "WALNUT_SOURCE_REGISTRATION" in str(err.value)
    assert not connection_path("cmd").exists()
    with pytest.raises(RegistrationRefused):
        import_declarations(MANIFEST, only=["workspace_files"])
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")
    assert register_connection(spec).id == "cmd"
    with pytest.raises(RegistrationRefused):                             # narrowing still holds
        register_connection(spec, origin="remote")


# === #11 a write registration is complete only with its advert =====================================
def test_11_write_registration_reports_a_failed_advert_as_incomplete(walnut_env):
    from databridge_mcp_ingest.plans import tool_advert_path

    class _Down:
        def list_tools(self):
            raise McpClientError("server not reachable")

    spec = {"id": "w", "purpose": "agent_write", "transport": {"type": "stdio", "command": "x"}}
    with pytest.raises(RegistrationIncomplete) as err:
        register_connection(spec, client=_Down())
    assert err.value.failures[0]["id"] == "w" and "not reachable" in str(err.value)
    assert not tool_advert_path("w").exists()
    got = register_connections([spec, {**spec, "id": "r", "purpose": "ingest"}])
    assert [(g["id"], g["ok"], g["incomplete"]) for g in got] == [("w", False, True),
                                                                   ("r", True, False)]
    register_connection(spec, client=_Server([ToolInfo("insert_row", "", dict(_OBJ))]))
    assert "insert_row" in tool_advert_path("w").read_text(encoding="utf-8")


# === #12 a resources plan is approved against the LIVE resources ===================================
def _resources_plan():
    tools = _docs_tools()
    _conn()
    _save("src", [StepSpec(id="res", strategy="resources")], tools)
    return tools


def test_12_resources_are_listed_through_the_same_session_at_approval(walnut_env):
    tools = _resources_plan()
    with pytest.raises(PlanError, match="lists no resources"):
        approve_plan("src", principal=_issued("dana"), client=_Server(tools, resources=[]))
    with pytest.raises(PlanError, match="could not be listed"):
        approve_plan("src", principal=_issued("dana"),
                     client=_Server(tools, resources=McpClientError("denied")))
    with pytest.raises(PlanError, match="cannot list resources"):
        approve_plan("src", principal=_issued("dana"), client=_Server(tools, supports=False))
    plan = approve_plan("src", principal=_issued("dana"),
                        client=_Server(tools, resources=[ResourceInfo(uri="a://1")]))
    assert plan.approval["resources"] == {"supports": True, "count": 1}
    assert verify_plan_approval(load_plan("src"))["resources"]["count"] == 1


# === #13 an event that cannot be journaled is not delivered ========================================
@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_LIST_CHANGED_QUEUE", str(tmp_path / "lc.jsonl"))
    monkeypatch.setenv("WALNUT_LIST_CHANGED_DEBOUNCE_S", "0")
    monkeypatch.setenv("WALNUT_LIST_CHANGED_RETRY_S", "0.05")
    mc.clear_list_changed_listeners()
    yield tmp_path / "lc.jsonl"
    mc.clear_list_changed_listeners()


def test_13_no_listener_runs_until_the_event_is_durable(journal, monkeypatch):
    real = mc._journal
    disk_full = [True]
    monkeypatch.setattr(mc, "_journal", lambda rec: False if disk_full[0] else real(rec))
    got: list = []
    done = threading.Event()
    mc.add_list_changed_listener(lambda cid, kind: (got.append((cid, kind)), done.set()))
    assert mc.notify_list_changed("s", "tools") == 0
    time.sleep(0.15)
    assert got == [] and [e["id"] for e in mc.undurable_list_changed()]
    disk_full[0] = False                                                 # the disk recovers
    assert done.wait(5)
    assert got == [("s", "tools")] and mc.undurable_list_changed() == []
    deadline = time.monotonic() + 5            # `delivered` is journaled after the listener ran
    while mc.pending_list_changed() and time.monotonic() < deadline:
        time.sleep(0.02)
    events =[json.loads(x)["event"] for x in journal.read_text(encoding="utf-8").splitlines()]
    assert events == ["received", "delivered"]


# === #14 a later templates page that fails is a failure ============================================
class _Err(Exception):
    def __init__(self, code=None):
        super().__init__("boom")
        self.error = type("E", (), {"code": code})()


def test_14_method_not_found_means_none_only_on_the_first_page():
    from types import SimpleNamespace

    def sdk(pages):
        c = object.__new__(mc.SdkClient)
        c._session = None
        it = iter(pages)

        def _call(factory):
            nxt = next(it)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        c._call = _call
        return c

    first = SimpleNamespace(resourceTemplates=[SimpleNamespace(uriTemplate="a://{x}", name="a",
                                                               mimeType=None)],
                            nextCursor="c2")
    with pytest.raises(McpClientError, match="page after 1"):
        sdk([first, _Err(code=-32601)]).list_resource_templates()
    assert sdk([_Err(code=-32601)]).list_resource_templates() == []


# === #15 close_pool retires sessions leased at the time ============================================
def test_15_a_session_leased_during_close_pool_is_closed_on_release(walnut_env, monkeypatch):
    class _Fake:
        alive = True
        suspect = False

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    conn = SourceConnection.model_validate({"id": "pool", "transport": {"type": "stdio",
                                                                        "command": "x"}})
    monkeypatch.setattr(mc, "_pool", mc.SessionPool(factory=lambda c, timeout: _Fake()))
    old = mc.get_pool()
    with old.lease(conn) as leased:
        mc.close_pool()
    assert leased.closed and old.idle_count() == 0
    assert mc.get_pool() is not old
    mc.close_pool()


# === E2E run 2, N-10: ONE secret policy for import and load; a refused file never breaks a list ===
def _stored(sid, transport):
    p = connection_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"id": sid, "transport": transport}), encoding="utf-8")


def test_n10_a_refused_stored_connection_is_reported_not_raised_by_the_list(walnut_env,
                                                                            monkeypatch):
    from databridge_mcp_ingest.connections import connection_problems, list_connections

    monkeypatch.delenv(DEV_ALLOW_DEFAULTS_ENV, raising=False)
    _conn("good")
    _stored("legacy", {"type": "stdio", "command": "x", "env": {
        "DATABASE_URI": "postgresql://r:${PG_READER_PASSWORD:-" + SECRET + "}@h/db"}})
    assert [c.id for c in list_connections()] == ["good"]               # no exception
    problems = connection_problems()
    assert [p["id"] for p in problems] == ["legacy"]
    assert "credential" in problems[0]["error"] and SECRET not in json.dumps(problems)


def test_n10_import_and_load_apply_the_same_policy(walnut_env, tmp_path, monkeypatch):
    monkeypatch.delenv(DEV_ALLOW_DEFAULTS_ENV, raising=False)
    decl = tmp_path / "servers.yaml"
    env = {"DATABASE_URI": "postgresql://r:${PG_READER_PASSWORD:-" + SECRET + "}@h/db"}
    decl.write_text(json.dumps({"version": 1, "servers": [
        {"id": "db", "direct": {"transport": "stdio", "command": "x", "env": env}}]}),
        encoding="utf-8")
    with pytest.raises(ConnectionError_):                                # import refuses...
        import_declarations(decl)
    _stored("db", {"type": "stdio", "command": "x", "env": env})
    with pytest.raises(ConnectionError_):                                # ...and so does load
        load_connection("db")
    monkeypatch.setenv(DEV_ALLOW_DEFAULTS_ENV, "1")                      # ...and both allow it
    assert [c.id for c in import_declarations(decl)] == ["db"]           # behind the dev flag
    assert load_connection("db").id == "db"


# === E2E run 2, N-22: the operator's declarations file is configurable ============================
def test_n22_the_declarations_file_comes_from_the_environment(walnut_env, tmp_path, monkeypatch):
    from databridge_mcp_ingest.connections import declarations_path

    assert declarations_path() == MANIFEST.resolve()                     # the default
    decl = tmp_path / "other.yaml"
    decl.write_text(json.dumps({"version": 1, "servers": [
        {"id": "elsewhere", "direct": {"transport": "stdio", "command": "x"}}]}),
        encoding="utf-8")
    monkeypatch.setenv("WALNUT_MCP_DECLARATIONS", str(decl))
    assert declarations_path() == decl
    assert [c.id for c in import_declarations(register=False)] == ["elsewhere"]
    reg = jg.JungleRegistry(api=type("A", (), {"servers": lambda self: []})())
    assert reg.register_declarations() == [{"name": "elsewhere", "status": "skipped",
                                            "reason": "no mcpjungle block"}]


# === FXJ4 cross-track: ClickHouse has a session read-only guard the grammar must accept =========
def test_fxj4_clickhouse_read_only_session_and_its_check_are_reads():
    assert read_only_sql_refusal("SET readonly = 1") is None
    assert read_only_sql_refusal("SELECT getSetting('readonly') AS v") is None
    assert read_only_sql_refusal("SET readonly = 0")                    # widening is refused
