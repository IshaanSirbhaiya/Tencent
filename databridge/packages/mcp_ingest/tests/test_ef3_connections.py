"""EF3 — connection registration: F-08 (tools_hint / writes_for reach the catalogue), aliases
(EF4: "CRM" must resolve to larkspur_db), and Codex round 3 (c): literal secrets are rejected —
and never echoed — at registration; env-var NAMES / references are fine."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MANIFEST = Path(__file__).resolve().parents[3] / "infra" / "mcp_servers.yaml"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_f08_import_carries_tools_hint_writes_for_and_aliases(walnut_env):
    from databridge_mcp_ingest.connections import import_declarations, load_connection

    decls = {d["id"]: d for d in _manifest()["servers"]}
    carried = [d for d in decls.values()
               if d.get("tools_hint") or d.get("writes_for") or d.get("aliases")]
    assert carried                                                        # non-vacuous
    conns = {c.id: c for c in import_declarations(MANIFEST, advertise=False)}
    for d in carried:
        saved = load_connection(d["id"])                                  # round-trips on disk
        for c in (conns[d["id"]], saved):
            assert c.tools_hint == (d.get("tools_hint") or {})
            assert c.writes_for == list(d.get("writes_for") or [])
            assert c.aliases == list(d.get("aliases") or [])


def test_aliases_crm_names_the_database_that_holds_the_crm(walnut_env):
    decls = {d["id"]: d for d in _manifest()["servers"]}
    writers = [d for d in decls.values() if d.get("purpose") == "agent_write"]
    assert writers
    target = writers[0]["writes_for"][0]                                  # derived, not named
    assert "crm" in [a.lower() for a in decls[target].get("aliases") or []]
    assert "crm" in (decls[target].get("title") or "").lower()            # the title says so


def test_the_catalogue_sees_write_tools_and_aliases(walnut_env):
    catalogue = pytest.importorskip("databridge_automation.catalogue")
    from databridge_mcp_ingest.connections import connection_path, import_declarations

    conns = import_declarations(MANIFEST, advertise=False)
    writer = next(c for c in conns if c.purpose == "agent_write")
    info = catalogue.load_connection_info(connection_path(writer.id))
    assert info.writes_for == tuple(writer.writes_for) and info.agent_write
    hinted = writer.tools_hint.get("write")
    assert hinted and hinted in {t.name for t in info.write_tools}
    target = catalogue.load_connection_info(connection_path(writer.writes_for[0]))
    assert "crm" in [a.lower() for a in target.aliases]


SECRET = "S3cretValue9"
BAD = [
    {"env": {"DATABASE_URI": f"postgresql://reader:{SECRET}@db:5432/x"}},
    {"env": {"API_URL": f"https://h.example/api?api_key={SECRET}"}},
    {"args": [f"--password={SECRET}"]},
    {"args": ["--api-key", SECRET]},
    {"args": [f"--token={SECRET}"]},
    {"env": {"DB_PASSWORD": SECRET}},
    {"args": [f"PASSWORD={SECRET}"]},
    {"env": {"OPENAI": "sk-proj-" + "a1B2" * 8}},
    {"args": ["AKIA" + "ABCDEFGHIJKLMNOP"]},
]
GOOD = [
    {"env": {"DATABASE_URI": "postgresql://reader:${PG_PASSWORD}@db:5432/x"}},
    {"env": {"DATABASE_URI": "postgresql://reader@db:5432/x"}},
    {"args": ["--password-env", "PG_PASSWORD"]},
    {"env_from": {"DB_PASSWORD": "HOST_DB_PASSWORD"}},
    {"env": {"DB_PASSWORD": "${HOST_DB_PASSWORD}"}},
    {"args": ["--root", "/data/files", "--access-mode=restricted"]},
]


def _spec(**transport):
    return {"id": "s", "transport": {"type": "stdio", "command": "uvx", **transport}}


@pytest.mark.parametrize("over", BAD)
def test_codex_c_a_literal_secret_is_refused_and_never_echoed(walnut_env, over):
    from databridge_mcp_ingest.connections import ConnectionError_, connection_path, \
        register_connection

    with pytest.raises(ConnectionError_) as exc:
        register_connection(_spec(**over))
    msg = str(exc.value)
    assert "secret" in msg.lower()
    assert SECRET not in msg and "a1B2a1B2" not in msg and "ABCDEFGHIJKLMNOP" not in msg
    assert not connection_path("s").exists()


@pytest.mark.parametrize("over", GOOD)
def test_codex_c_references_and_names_are_accepted(walnut_env, over):
    from databridge_mcp_ingest.connections import register_connection

    assert register_connection(_spec(**over)).id == "s"


def test_codex_c_http_urls_and_headers_are_checked(walnut_env):
    from databridge_mcp_ingest.connections import ConnectionError_, register_connection

    with pytest.raises(ConnectionError_):
        register_connection({"id": "h", "transport": {
            "type": "http", "url": f"https://u:{SECRET}@mcp.example/mcp"}})
    with pytest.raises(ConnectionError_):
        register_connection({"id": "h", "transport": {
            "type": "http", "url": "https://mcp.example/mcp",
            "headers": {"Authorization": f"Bearer {SECRET}xyz"}}})
    assert register_connection({"id": "h", "transport": {
        "type": "http", "url": "https://mcp.example/mcp",
        "headers_from": {"Authorization": "MCP_BEARER"}}}).id == "h"


# --- RTF (R-021 agent-runtime): a tool result keeps the MCP `structuredContent` ------------------
class _Api:
    STRUCT = {"rows": [{"invoice_no": "INV-1", "net": "12.50"}], "row_count": 1}

    def invoke_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": "1 row"}],
                "structuredContent": self.STRUCT, "isError": False}


def test_a_tool_result_keeps_structured_content_end_to_end():
    from databridge_mcp_ingest.client import JungleClient, ReadOnlyClient, ToolResult
    from databridge_mcp_ingest.connections import SourceConnection

    assert ToolResult(parts=()).structured is None                        # additive default
    conn = SourceConnection.model_validate({"id": "j", "via": "mcpjungle", "transport": {
        "type": "stdio", "command": "x"}})
    client = JungleClient(conn, backend=_Api())
    for c in (client, ReadOnlyClient(client, deny=[], error_prefixes=["1 row"])):
        res = c.call_tool("query", {"sql": "select 1"})
        assert res.structured == _Api.STRUCT and res.text == "1 row"


# --- F-08 (live advert): a connection's live tools/list is written where the catalogue reads it -
def test_f08_the_live_tool_list_is_advertised_for_the_catalogue(walnut_env):
    from databridge_mcp_ingest.client import ToolInfo
    from databridge_mcp_ingest.connections import connection_path, register_connection
    from databridge_mcp_ingest.plans import write_tool_advert

    register_connection({"id": "w", "purpose": "agent_write", "writes_for": ["db"],
                         "transport": {"type": "stdio", "command": "x"}}, advertise=False)
    tools = [ToolInfo("execute_sql", "Run SQL"), ToolInfo("list_schemas", read_only=True)]

    class _C:
        def list_tools(self):
            return tools

    path = write_tool_advert("w", client=_C())
    assert path == connection_path("w").with_name("w.tools.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [t["name"] for t in data["tools"]] == [t.name for t in tools]
    assert data["tools"][1]["read_only"] is True
    catalogue = pytest.importorskip("databridge_automation.catalogue")
    info = catalogue.load_connection_info(connection_path("w"))
    assert {t.name for t in info.tools} == {t.name for t in tools}
    assert "execute_sql" in {t.name for t in info.write_tools}               # purpose: write
