"""FXI3 — the mcp_ingest "sources" track after Codex's FIRST review (f1-mi-sources, R-022).

One test (or a small group) per finding, each written to fail on the pre-fix code:
approvals only by an authenticated principal (#1), annotations are claims and SQL executors are
not blessed (#2), approval is decided against the live server and bound to a digest (#3), the
planner model sees only screened metadata and no identifiers (#4), the secret scanner's bypasses
(#5), import never echoes a secret (#6), MCPJungle never receives a resolved credential (#7/#12),
the remote registration policy (#8), write adverts (#9), resource-list failures (#10), strict
MCPJungle resource URIs (#11), strict base64 (#13), durable + retried list_changed (#14).
Everything synthetic; no network, no model.
"""
from __future__ import annotations

import base64
import json
import threading
import time

import pytest

from databridge_mcp_ingest import client as mc
from databridge_mcp_ingest import jungle as jg
from databridge_mcp_ingest.client import McpClientError, ReadOnlyClient, ToolInfo
from databridge_mcp_ingest.connections import (ConnectionError_, RegistrationRefused,
                                               SourceConnection, import_declarations,
                                               register_connection, registration_refusal,
                                               save_connection)
from databridge_mcp_ingest.plans import (ApprovalRefused, CrawlPlan, PlanError, StepSpec,
                                         approve_plan, classify_plan_tools, load_plan,
                                         plan_approvals_path, plan_path, planner_catalogue,
                                         require_authenticated_approver, save_plan,
                                         tools_digest, verify_plan_approval)

from conftest import test_principal as _issued

pytestmark = pytest.mark.real_approval

SECRET = "Zq7xVv0pLm3T"          # a synthetic literal the tests look for in outputs


# --- a small fake server + a saved plan -------------------------------------------------------------
_OBJ = {"type": "object", "properties": {"path": {"type": "string"}}}


def _tools(read_only_list=None, extra=()):
    return [ToolInfo("list_docs", "List documents", dict(_OBJ), read_only=read_only_list),
            ToolInfo("read_doc", "Read one document", dict(_OBJ)),
            ToolInfo("frobnicate", "Frobnicate a node", dict(_OBJ)), *extra]


class _Server:
    supports_resources = False

    def __init__(self, tools):
        self.tools = tools
        self.calls: list[str] = []

    def list_tools(self):
        return list(self.tools)

    def list_resources(self):
        return []

    def call_tool(self, name, args):
        self.calls.append(name)
        return mc.ToolResult(parts=(mc.ContentPart(kind="text", text="ok"),))


def _plan(sid: str, tools, *, read_tool="read_doc") -> CrawlPlan:
    step = StepSpec.model_validate({
        "id": "docs", "strategy": "documents",
        "list": {"tool": "list_docs", "args": {"path": "{path}"}, "root_arg_default": "",
                 "parse": {"mode": "lines", "patterns": [{"regex": "^(?P<name>.+)$"}]}},
        "read": {"text_tool": read_tool, "text_args": {"path": "{path}"}}})
    plan = CrawlPlan(source_id=sid, origin="manual", steps=[step],
                     tools_seen=sorted(t.name for t in tools), tools_digest=tools_digest(tools),
                     tool_classes=classify_plan_tools([step], tools))
    save_plan(plan)
    return plan


def _conn(sid: str = "src", **kw) -> SourceConnection:
    return register_connection({"id": sid, "transport": {"type": "stdio", "command": "x"}, **kw},
                               advertise=False)


# === #1 approvals are made only by an authenticated principal ======================================
@pytest.mark.parametrize("who", ["Alice", {"principal_id": "alice"}, None])
def test_1_a_name_a_dict_or_nothing_never_approves(walnut_env, who):
    _conn()
    tools = _tools()
    _plan("src", tools)
    with pytest.raises(ApprovalRefused):
        approve_plan("src", approved_by=who, client=_Server(tools))
    assert load_plan("src").status == "proposed"                      # nothing was written
    assert not plan_approvals_path("src").exists()


def test_1_a_hand_built_approver_is_a_claim_not_an_authentication():
    from databridge_agent_runtime.runtime import Approver

    with pytest.raises(ApprovalRefused):
        require_authenticated_approver(Approver("alice", "made-up"))
    got = require_authenticated_approver(_issued("alice"))
    assert got["principal_id"] == "alice" and got["authenticated_by"]


def test_1_an_issued_principal_approves_and_the_record_is_bound_and_appended(walnut_env):
    _conn()
    tools = _tools()
    _plan("src", tools)
    p = _issued("dana")
    plan = approve_plan("src", principal=p, client=_Server(tools))
    assert plan.status == "approved" and plan.approved_by == "dana"
    rec = verify_plan_approval(load_plan("src"))
    assert rec["principal_id"] == "dana" and rec["authenticated_by"] == p.authenticated_by
    lines = plan_approvals_path("src").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event"] == "plan_approved"


def test_1_a_name_that_is_not_the_principal_is_refused(walnut_env):
    _conn()
    tools = _tools()
    _plan("src", tools)
    with pytest.raises(ApprovalRefused, match="not the authenticated"):
        approve_plan("src", principal=_issued("dana"), approved_by="Mallory",
                     client=_Server(tools))


def test_1_an_edited_yaml_is_never_an_approval(walnut_env):
    import yaml

    _conn()
    tools = _tools()
    _plan("src", tools)
    # hand-typed status: approved
    data = yaml.safe_load(plan_path("src").read_text(encoding="utf-8"))
    data.update({"status": "approved", "approved_by": "Mallory"})
    plan_path("src").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ApprovalRefused, match="no authenticated approval"):
        verify_plan_approval(load_plan("src"))
    # a real approval, then an edited step
    _plan("src", tools)
    approve_plan("src", principal=_issued("dana"), client=_Server(tools))
    data = yaml.safe_load(plan_path("src").read_text(encoding="utf-8"))
    data["steps"][0]["read"]["text_tool"] = "frobnicate"
    plan_path("src").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ApprovalRefused, match="changed after it was approved"):
        verify_plan_approval(load_plan("src"))


def test_1_an_approval_missing_from_the_append_only_log_is_refused(walnut_env):
    _conn()
    tools = _tools()
    _plan("src", tools)
    approve_plan("src", principal=_issued("dana"), client=_Server(tools))
    plan_approvals_path("src").unlink()
    with pytest.raises(ApprovalRefused, match="append-only"):
        verify_plan_approval(load_plan("src"))


def test_1_the_cli_refuses_a_typed_name(walnut_env, capsys):
    from databridge_mcp_ingest.cli import main

    _conn()
    _plan("src", _tools())
    assert main(["approve", "src", "--by", "Alice"]) == 2
    assert main(["approve", "src"]) == 2
    assert load_plan("src").status == "proposed"
    assert "not an authenticated principal" in capsys.readouterr().out


# === #2 annotations are claims; SQL executors are not blessed; runtime allow-list ===================
def test_2_a_readonly_claim_alone_is_not_a_read():
    tools = [ToolInfo("mutate_account", "", dict(_OBJ), read_only=True),
             ToolInfo("list_docs", "", dict(_OBJ))]
    step = StepSpec.model_validate({"id": "s", "strategy": "documents",
                                    "list": {"tool": "list_docs"},
                                    "read": {"text_tool": "mutate_account"}})
    classes = classify_plan_tools([step], tools)
    assert classes["mutate_account"].startswith("unclassified:unknown")
    assert "not a grant" in classes["mutate_account"]
    assert classes["list_docs"].startswith("read:")                     # read verb: still a read
    # a WRITE claim still narrows
    flipped = [ToolInfo("list_docs", "", dict(_OBJ), read_only=False)]
    assert classify_plan_tools([step], flipped)["list_docs"].startswith("write:")


def test_2_the_sql_tool_of_a_tabular_step_is_not_blessed():
    tools = [ToolInfo("execute_sql", "", {"type": "object",
                                          "properties": {"sql": {"type": "string"}}})]
    step = StepSpec.model_validate({"id": "t", "strategy": "tabular",
                                    "sql": {"tool": "execute_sql"}})
    cls = classify_plan_tools([step], tools)["execute_sql"]
    assert not cls.startswith("read:") and cls.startswith("unclassified:unknown")


def test_2_an_approved_plan_lets_the_crawl_call_only_its_classified_reads(walnut_env):
    _conn()
    tools = _tools()
    _plan("src", tools)
    plan = approve_plan("src", principal=_issued("dana"), client=_Server(tools))
    server = _Server(tools)
    ro = ReadOnlyClient(server, plan=plan)
    assert ro.call_tool("read_doc", {}).text == "ok"
    with pytest.raises(McpClientError, match="classifies as a read"):
        ro.call_tool("frobnicate", {})                                 # not in the plan
    assert server.calls == ["read_doc"]


# === #3 approval re-checks the live server; edited classes are discarded ===========================
def test_3_an_annotation_flip_after_proposal_blocks_approval(walnut_env):
    _conn()
    _plan("src", _tools())
    flipped = _tools(read_only_list=False)                               # now claims to write
    with pytest.raises(PlanError, match="changed since it was proposed"):
        approve_plan("src", principal=_issued("dana"), client=_Server(flipped))


def test_3_a_class_edited_into_the_yaml_is_discarded(walnut_env):
    import yaml

    _conn()
    tools = _tools()
    _plan("src", tools, read_tool="frobnicate")
    data = yaml.safe_load(plan_path("src").read_text(encoding="utf-8"))
    data["tool_classes"]["frobnicate"] = "read:annotation"               # the edit
    plan_path("src").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PlanError, match="frobnicate"):
        approve_plan("src", principal=_issued("dana"), client=_Server(tools))
    # vouching for it BY NAME is the only way, and it is recorded against the principal
    plan = approve_plan("src", principal=_issued("dana"), client=_Server(tools),
                        vouch_read_tools=["frobnicate"])
    assert plan.tool_classes["frobnicate"] == "read:vouched_by:dana"


# === #4 the planner model sees screened metadata and no identifiers ================================
def test_4_the_planner_sees_no_resource_identifiers_and_no_flagged_tool():
    from databridge_mcp_ingest.client import ResourceInfo
    from databridge_mcp_ingest.screen import load_screen, screen_text

    cfg = load_screen(packs=[])
    key = ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAAfakefake\n"
           "-----END OPENSSH PRIVATE KEY-----")
    assert screen_text(key, cfg).decision != "allow"                    # derived from the screen
    tools = [ToolInfo("list_docs", "List documents", dict(_OBJ)),
             ToolInfo("read_doc", f"Reads a doc. {key}", dict(_OBJ))]
    resources = [ResourceInfo(uri="file:///blocked/payroll/secrets.pdf", name="secrets.pdf",
                              mime="application/pdf")]
    catalogue, summary, notes = planner_catalogue(tools, resources, screen=cfg)
    assert [t["name"] for t in catalogue] == ["list_docs"]
    assert any("read_doc" in n and "withheld" in n for n in notes)
    blob = json.dumps([catalogue, summary])
    assert "payroll" not in blob and "secrets.pdf" not in blob
    assert summary == {"count": 1, "mime_types": ["application/pdf"]}


# === #5 the scanner's bypasses ======================================================================
@pytest.mark.parametrize("transport", [
    {"type": "stdio", "command": "srv", "args": ["--api-key", SECRET.upper()]},
    {"type": "stdio", "command": "srv", "args": [f"--api-key={SECRET}${{IGNORED}}"]},
    {"type": "http", "url": "https://h.example/mcp",
     "headers": {"Authorization": f"Bearer {SECRET}${{X}}"}},
])
def test_5_scanner_bypasses_are_refused(walnut_env, transport):
    with pytest.raises(ConnectionError_) as exc:
        register_connection({"id": "leak", "transport": transport})
    assert SECRET not in str(exc.value) and SECRET.upper() not in str(exc.value)


def test_5_every_persisted_field_is_scanned_and_save_connection_enforces_it(walnut_env, tmp_path):
    with pytest.raises(ConnectionError_, match="tools_hint"):
        register_connection({"id": "leak", "transport": {"type": "stdio", "command": "srv"},
                             "tools_hint": {"api_token": SECRET}})
    conn = SourceConnection.model_validate({"id": "leak2",
                                            "transport": {"type": "stdio", "command": "srv"}})
    conn.transport.args.extend(["--token", SECRET])                    # mutated after validation
    with pytest.raises(ConnectionError_):
        save_connection(conn, tmp_path)
    assert not (tmp_path / "leak2.yaml").exists()


def test_5_references_and_names_are_still_fine(walnut_env):
    ok = [{"type": "stdio", "command": "srv", "args": ["--password-env", "PG_PASSWORD"]},
          {"type": "stdio", "command": "srv", "args": ["--api-key", "${API_KEY}"]},
          {"type": "http", "url": "https://h.example/mcp",
           "headers": {"Authorization": "Bearer ${TOKEN}"}}]
    for i, t in enumerate(ok):
        register_connection({"id": f"fine{i}", "transport": t})


# === #6 import never echoes a refused literal =======================================================
def test_6_import_refuses_without_echoing_the_value(walnut_env, tmp_path, capsys):
    import yaml

    from databridge_mcp_ingest.cli import main

    decl = {"version": 1, "servers": [{"id": "leaky", "direct": {
        "transport": "stdio", "command": "srv", "env": {"API_TOKEN": SECRET}}}]}
    path = tmp_path / "decl.yaml"
    path.write_text(yaml.safe_dump(decl), encoding="utf-8")
    with pytest.raises(ConnectionError_) as exc:
        import_declarations(path)
    assert SECRET not in str(exc.value) and "env.API_TOKEN" in str(exc.value)
    assert main(["connect", "--from-infra", str(path)]) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


# === #7 / #12 MCPJungle never receives a resolved credential ======================================
def test_7_env_from_is_sent_as_a_reference_never_a_value(monkeypatch):
    from databridge_mcp_ingest.connections import stdio_connection

    monkeypatch.setenv("FXI3_HOST_TOKEN", SECRET)
    conn = stdio_connection("mem", "srv", env_from={"API_TOKEN": "FXI3_HOST_TOKEN"},
                            via="mcpjungle")
    body = jg.body_from_connection(conn)
    assert body["env"] == {"API_TOKEN": "${FXI3_HOST_TOKEN}"}
    assert SECRET not in json.dumps(body)


def test_12_http_headers_are_refused_not_silently_dropped():
    from databridge_mcp_ingest.connections import http_connection

    conn = http_connection("h", "https://h.example/mcp", headers_from={"X-API-Key": "K"},
                           via="mcpjungle")
    with pytest.raises(jg.JungleError, match="X-API-Key"):
        jg.body_from_connection(conn)


def test_7_an_aggregator_error_body_is_redacted():
    import httpx

    key = "ghp_" + "A" * 36
    api = jg.JungleAPI("http://jungle.test", transport=httpx.MockTransport(
        lambda req: httpx.Response(400, json={"error": f"bad token {key}"})))
    with pytest.raises(jg.JungleError) as exc:
        api.servers()
    assert key not in str(exc.value) and "[REDACTED]" in str(exc.value)


# === #8 the remote registration policy lives in the package ========================================
def test_8_remote_registration_refuses_commands_and_private_destinations(walnut_env, monkeypatch):
    monkeypatch.delenv("WALNUT_COMMAND_SOURCES", raising=False)
    monkeypatch.delenv("WALNUT_SOURCE_ALLOWED_HOSTS", raising=False)
    cmd = {"id": "pwn", "transport": {"type": "stdio", "command": "powershell.exe",
                                      "args": ["-Command", "whoami"]}}
    with pytest.raises(RegistrationRefused) as exc:
        register_connection(cmd, origin="remote")
    assert exc.value.code == "COMMAND_SOURCES_DISABLED"
    for url in ("http://169.254.169.254/latest/meta-data", "http://127.0.0.1:9000/mcp",
                "http://10.0.0.5/mcp", "file:///etc/passwd"):
        refused = registration_refusal({"id": "x", "transport": {"type": "http", "url": url}},
                                       origin="remote")
        assert refused and refused[0] == "SOURCE_DESTINATION_NOT_ALLOWED", url
    assert registration_refusal(cmd, origin="operator") is None       # the operator's own CLI
    from databridge_mcp_ingest.connections import connection_path

    assert not connection_path("pwn").exists()


def test_8_an_allow_listed_runner_still_needs_the_risk_acceptance(monkeypatch):
    import sys

    monkeypatch.setenv("WALNUT_COMMAND_SOURCES", sys.executable)
    monkeypatch.delenv("WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK", raising=False)
    spec = {"id": "py", "transport": {"type": "stdio", "command": sys.executable}}
    assert registration_refusal(spec, origin="remote")[0] == "COMMAND_SOURCES_DISABLED"
    monkeypatch.setenv("WALNUT_COMMAND_SOURCES_ACCEPT_RUNNER_RISK", "1")
    assert registration_refusal(spec, origin="remote") is None
    env_spec = {"id": "py", "transport": {"type": "stdio", "command": sys.executable,
                                          "env": {"NODE_OPTIONS": "--require x"}}}
    assert registration_refusal(env_spec, origin="remote")[0] == "COMMAND_ENV_NOT_ALLOWED"


# === #9 a write connection gets its advert, and a failure is reported ==============================
def test_9_write_connections_are_advertised_after_registration(walnut_env):
    from databridge_mcp_ingest.plans import advertise_write_tools, tool_advert_path

    _conn("writer", purpose="agent_write")
    _conn("reader")
    got = advertise_write_tools(client=_Server([ToolInfo("update_row", "", dict(_OBJ))]))
    assert [r["id"] for r in got] == ["writer"] and got[0]["ok"]
    assert "update_row" in tool_advert_path("writer").read_text(encoding="utf-8")

    class _Down:
        def list_tools(self):
            raise McpClientError("server not reachable")

    bad = advertise_write_tools(["writer"], client=_Down())
    assert bad[0]["ok"] is False and "not reachable" in bad[0]["error"]


# === #10 a failed resource listing is unobserved, never "no resources" =============================
class _Err(Exception):
    def __init__(self, code=None):
        super().__init__("boom")
        self.error = type("E", (), {"code": code})()


def _sdk(pages):
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


def _page(uris, cursor=None):
    from types import SimpleNamespace

    return SimpleNamespace(resources=[SimpleNamespace(uri=u, name=u, mimeType=None, size=None)
                                      for u in uris], nextCursor=cursor)


def test_10_resource_list_failures_raise():
    with pytest.raises(McpClientError):
        _sdk([_Err()]).list_resources()                                 # auth / transport
    with pytest.raises(McpClientError, match="page after 1"):
        _sdk([_page(["a://1"], cursor="c2"), _Err()]).list_resources()  # second page
    assert _sdk([_Err(code=-32601)]).list_resources() == []             # verified absence
    assert [r.uri for r in _sdk([_page(["a://1"])]).list_resources()] == ["a://1"]


# === #11 strict MCPJungle resource URIs =============================================================
def test_11_malformed_jungle_uris_are_refused_and_recorded():
    good = jg.encode_resource_uri("srv", "file:///a.md")
    assert jg.decode_resource_uri(good) == ("srv", "file:///a.md")
    for bad in ("mcpj://res/srv/@@@", "mcpj://res/srv/", "mcpj://res/sr__v/YQ",
                "mcpj://res/srv/" + base64.b64encode(b"a+/?").decode(),
                jg.encode_resource_uri("srv", good)):                   # nested
        with pytest.raises(jg.JungleError):
            jg.decode_resource_uri(bad)

    class _Api:
        def resources(self):
            return [{"name": "srv__ok", "uri": good}, {"name": "srv__bad", "uri": "mcpj://res/srv/@@"}]

        def read_resource(self, uri):
            return []

        def tools(self):
            return []

    conn = SourceConnection.model_validate({"id": "srv", "via": "mcpjungle",
                                            "transport": {"type": "stdio", "command": "x"}})
    jc = mc.JungleClient(conn, backend=_Api())
    assert [r.uri for r in jc.list_resources()] == ["file:///a.md"]
    assert [f["name"] for f in jc.failed_resources] == ["bad"]


# === #13 strict base64 ==============================================================================
def test_13_corrupted_base64_fails_the_item():
    good = base64.b64encode(b"hello").decode()
    assert mc._b64(good) == b"hello"
    assert mc._b64(good[:4] + "\n" + good[4:]) == b"hello"               # MIME line breaks
    # "aGVs!bG8=" decodes to b"hello" when invalid characters are silently discarded
    for bad in ("aGVs!bG8=", good[:-1] + "!", "aGVsbG8", "a*b="):
        with pytest.raises(McpClientError):
            mc._b64(bad)


# === #14 list_changed is durable and retried ========================================================
@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("WALNUT_LIST_CHANGED_QUEUE", str(tmp_path / "lc.jsonl"))
    monkeypatch.setenv("WALNUT_LIST_CHANGED_DEBOUNCE_S", "30")
    monkeypatch.setenv("WALNUT_LIST_CHANGED_RETRY_S", "0.05")
    mc.clear_list_changed_listeners()
    yield tmp_path / "lc.jsonl"
    mc.clear_list_changed_listeners()


def _events(path):
    return [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_14_a_failing_listener_is_retried_and_the_event_is_delivered(journal):
    calls: list = []
    done = threading.Event()

    def flaky(cid, kind):
        calls.append((cid, kind))
        if len(calls) == 1:
            raise RuntimeError("transient")
        done.set()

    mc.add_list_changed_listener(flaky)
    assert mc.notify_list_changed("s", "tools") == 1
    assert mc.notify_list_changed("s", "tools") == 0                     # coalesced, in flight
    assert done.wait(5)
    time.sleep(0.1)
    assert calls == [("s", "tools")] * 2
    assert _events(journal) == ["received", "listener_failed", "delivered"]
    assert mc.pending_list_changed() == []
    assert mc.notify_list_changed("s", "tools") == 0                     # debounced AFTER delivery


def test_14_an_undelivered_event_stays_pending_and_is_replayed(journal, monkeypatch):
    monkeypatch.setenv("WALNUT_LIST_CHANGED_RETRIES", "0")

    def broken(cid, kind):
        raise RuntimeError("down")

    mc.add_list_changed_listener(broken)
    mc.notify_list_changed("s", "resources")
    assert _events(journal)[-1] == "gave_up"
    assert [e["connection_id"] for e in mc.pending_list_changed()] == ["s"]
    # a restart: the listener is fixed, the journal is replayed
    mc.clear_list_changed_listeners()
    got: list = []
    mc.add_list_changed_listener(lambda cid, kind: got.append((cid, kind)))
    assert mc.replay_list_changed() == 1
    assert got == [("s", "resources")] and mc.pending_list_changed() == []


# === D4 (R-019 round 2, via the coordinator): SSRF re-checked at CONNECT time ======================
def _resolver(table):
    def resolve(host, port):
        if host not in table:
            raise OSError("unresolvable")
        return table[host]
    return resolve


def test_d4_a_host_that_rebinds_to_a_private_address_is_refused_at_connect(monkeypatch):
    import asyncio

    from databridge_mcp_ingest import connections as cn

    monkeypatch.delenv("WALNUT_SOURCE_ALLOWED_HOSTS", raising=False)
    table = {"mcp.example.test": ["93.184.216.34"]}                  # public at registration
    monkeypatch.setattr(cn, "_resolve_host", _resolver(table))
    assert registration_refusal({"id": "h", "transport": {"type": "http",
                                                          "url": "http://mcp.example.test/mcp"}},
                                origin="remote") is None
    table["mcp.example.test"] = ["169.254.169.254"]                  # ...rebinds afterwards
    conn = SourceConnection.model_validate({"id": "h", "transport": {
        "type": "http", "url": "http://mcp.example.test/mcp"}})

    async def enter():
        async with mc._sdk_streams(conn, 5):
            pass

    with pytest.raises(McpClientError, match="non-public"):
        asyncio.run(enter())
    monkeypatch.setenv("WALNUT_SOURCE_ALLOWED_HOSTS", "mcp.example.test")   # the same allow-list
    assert mc.connect_destination_refusal("http://mcp.example.test/mcp") is None


def test_d4_a_redirect_to_an_internal_address_is_never_followed(monkeypatch):
    import asyncio

    import httpx

    from databridge_mcp_ingest import connections as cn

    monkeypatch.delenv("WALNUT_SOURCE_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(cn, "_resolve_host", _resolver({"mcp.example.test": ["93.184.216.34"],
                                                        "127.0.0.1": ["127.0.0.1"]}))
    sent: list[str] = []

    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"})

    monkeypatch.setattr(mc, "_HTTP_TRANSPORT", httpx.MockTransport(handler))

    async def go():
        async with mc.guarded_http_client() as client:
            await client.get("http://mcp.example.test/mcp")

    with pytest.raises(McpClientError, match="127.0.0.1"):
        asyncio.run(go())
    assert sent == ["http://mcp.example.test/mcp"]                    # the hop was never sent


# === FXI1 request: redact() masks key=value credentials too (the ledger scrubber uses it) =========
@pytest.mark.parametrize("text", [
    f"connect failed: password={SECRET} host=db",
    f"GET /x?api_key={SECRET}&page=2",
    f"env TOKEN={SECRET}",
    f'{{"client_secret": "{SECRET}"}}',
    f"--api-key={SECRET}",
])
def test_redact_masks_key_value_credentials(text):
    from databridge_mcp_ingest.secret_scan import redact

    out = redact(text)
    assert SECRET not in out and "[REDACTED]" in out


def test_redact_keeps_references_and_non_credentials():
    from databridge_mcp_ingest.secret_scan import redact

    for text in ("password=${PG_PASSWORD}", "page=2 host=db", "token_file=/run/secrets/t",
                 "token_count=5", '{"tokens_in": 12}'):          # ledger counters stay
        assert redact(text) == text
