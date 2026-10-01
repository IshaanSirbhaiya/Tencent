"""S4 — MCPJungle registry management against a STATEFUL fake aggregator.

The fake speaks the exact endpoints verified live on 2026-10-01 (see `jungle.py`) behind
`httpx.MockTransport`, so these tests exercise the real request paths and payload shapes. A fake
server's tools/resources can be backed by a live `SdkClient` (`proxy=`), which is how the
route-equivalence test runs one real fixture server "through MCPJungle" without a container.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import jungle as jg
from databridge_mcp_ingest.client import JungleClient, McpClientError
from databridge_mcp_ingest.connections import http_connection, stdio_connection, with_route


class FakeJungle:
    """{name: {"server": body, "tools": [...], "resources": [...], "proxy": SdkClient|None}}."""

    def __init__(self, catalogue: dict | None = None) -> None:
        self.servers: dict[str, dict] = {}
        self.catalogue = catalogue or {}           # command -> {"tools": [...], "resources": [...]}
        self.proxies: dict[str, object] = {}        # server name -> a live SdkClient
        self.requests: list[tuple[str, str, dict | None]] = []

    # -- helpers -----------------------------------------------------------------------------
    def _tools_of(self, name: str) -> list[dict]:
        proxy = self.proxies.get(name)
        if proxy is not None:
            return [{"name": t.name, "description": t.description,
                     "input_schema": t.input_schema, "enabled": True,
                     "annotations": {"readOnlyHint": t.read_only,
                                     "destructiveHint": t.destructive}}
                    for t in proxy.list_tools()]
        return self.catalogue.get(self.servers[name]["server"].get("command"), {}).get("tools",
                                                                                       [])

    def _resources_of(self, name: str) -> list[dict]:
        proxy = self.proxies.get(name)
        if proxy is not None:
            return [{"uri": r.uri, "name": r.name, "mime_type": r.mime} for r in
                    proxy.list_resources()]
        return self.catalogue.get(self.servers[name]["server"].get("command"), {}).get(
            "resources", [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/v0/servers" and request.method == "GET":
            return httpx.Response(200, json=[s["server"] for s in self.servers.values()])
        if path == "/api/v0/servers" and request.method == "POST":
            if body["name"] in self.servers:
                return httpx.Response(500, json={"error": "duplicate key value violates unique "
                                                          "constraint"})
            self.servers[body["name"]] = {"server": {**body, "enabled": True}}
            return httpx.Response(201, json={"server": body})
        if path.startswith("/api/v0/servers/") and request.method == "DELETE":
            name = path.rsplit("/", 1)[-1]
            if name not in self.servers:
                return httpx.Response(404, json={"error": "not found"})
            del self.servers[name]
            self.proxies.pop(name, None)
            return httpx.Response(204)
        if path == "/api/v0/tools":
            return httpx.Response(200, json=[{**t, "name": f"{n}__{t['name']}"}
                                             for n in self.servers for t in self._tools_of(n)])
        if path == "/api/v0/tools/invoke":
            server, _, tool = body["name"].partition("__")
            args = {k: v for k, v in body.items() if k != "name"}
            proxy = self.proxies.get(server)
            if proxy is not None:
                raw = proxy.call_tool_raw(tool, args)
                return httpx.Response(200, json={"content": raw["content"],
                                                 "isError": raw["is_error"]})
            return httpx.Response(200, json={"content": [{"type": "text",
                                                          "text": json.dumps(args)}]})
        if path == "/api/v0/resources":
            return httpx.Response(200, json=[
                {"uri": jg.encode_resource_uri(n, r["uri"]), "name": f"{n}__{r['name']}",
                 "mime_type": r.get("mime_type"), "enabled": True}
                for n in self.servers for r in self._resources_of(n)])
        if path == "/api/v0/resources/read":
            server, original = jg.decode_resource_uri(body["uri"])
            proxy = self.proxies.get(server)
            if proxy is not None:
                contents = proxy.read_resource_raw(original)
                return httpx.Response(200, json={"contents": [
                    {k: v for k, v in c.items() if k != "uri"} for c in contents]})
            return httpx.Response(200, json={"contents": [{"mimeType": "text/plain",
                                                           "text": f"content of {original}"}]})
        return httpx.Response(404, text="404 page not found")

    def api(self) -> jg.JungleAPI:
        return jg.JungleAPI("http://jungle.test", transport=httpx.MockTransport(self.handler))


CATALOGUE = {"npx": {"tools": [{"name": "read_graph", "input_schema": {}},
                               {"name": "open_nodes", "input_schema": {}}],
                     "resources": [{"uri": "demo://doc/a.md", "name": "a.md",
                                    "mime_type": "text/markdown"}]}}


@pytest.fixture
def fake():
    return FakeJungle(CATALOGUE)


def _body(name: str, *args: str) -> dict:
    return {"name": name, "transport": "stdio", "command": "npx", "args": ["-y", *args],
            "session_mode": "stateful"}


def test_register_is_idempotent_and_collisions_are_explicit(fake):
    reg = jg.JungleRegistry(fake.api())
    first = reg.register(_body("mem", "pkg-a"))
    assert first["status"] == "registered" and first["tools"] == len(CATALOGUE["npx"]["tools"])
    assert reg.register(_body("mem", "pkg-a"))["status"] == "exists"
    with pytest.raises(jg.JungleConflict):
        reg.register(_body("mem", "pkg-b"))
    assert reg.register(_body("mem", "pkg-b"), on_conflict="skip")["status"] == "skipped"
    renamed = reg.register(_body("mem", "pkg-b"), on_conflict="rename")
    assert renamed["status"] == "renamed" and renamed["name"] == "mem_2"
    again = reg.register(_body("mem", "pkg-c"), on_conflict="rename")
    assert again["name"] == "mem_3"
    replaced = reg.register(_body("mem", "pkg-b"), on_conflict="replace")
    assert replaced["status"] == "replaced"
    assert fake.servers["mem"]["server"]["args"] == ["-y", "pkg-b"]
    assert reg.server_names() == {"mem", "mem_2", "mem_3"}


def test_names_that_break_the_tool_namespace_are_refused(fake):
    reg = jg.JungleRegistry(fake.api())
    for bad in ("a__b", "", "has space", "x" * 80):
        with pytest.raises(jg.JungleError):
            reg.register(_body(bad))
    assert fake.servers == {}


def test_bulk_register_list_tools_health_and_deregister(fake):
    reg = jg.JungleRegistry(fake.api())
    names = ["s4a", "s4b", "s4c"]
    results = reg.register_many([_body(n, n) for n in names] + [_body("bad__name")])
    assert [r["status"] for r in results] == ["registered"] * len(names) + ["failed"]
    by_server = reg.tools_by_server()
    assert set(by_server) == set(names)
    assert all(v == sorted(t["name"] for t in CATALOGUE["npx"]["tools"])
               for v in by_server.values())
    one = reg.server_tools("s4b")
    assert {t["qualified_name"] for t in one} == {f"s4b__{t['name']}"
                                                  for t in CATALOGUE["npx"]["tools"]}
    health = reg.health()
    assert health["ok"] and {s["name"] for s in health["servers"]} == set(names)
    assert all(s["ok"] and s["resources"] == len(CATALOGUE["npx"]["resources"])
               for s in health["servers"])
    gone = reg.deregister_many(names + ["never_registered"])
    assert gone == {**{n: True for n in names}, "never_registered": False}
    assert reg.server_names() == set()


def test_register_declarations_from_infra_yaml(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("S4_TEST_DB_PASSWORD", "synthetic")
    decl = {"version": 1, "servers": [
        {"id": "files_x", "description": "d", "mcpjungle": {
            "transport": "stdio", "command": "npx", "args": ["-y", "pkg", "/data/x"]}},
        {"id": "db_x", "mcpjungle": {
            "transport": "stdio", "command": "uvx", "args": ["pg"],
            "env": {"DATABASE_URI": "postgresql://r:${S4_TEST_DB_PASSWORD}@h/db"}}},
        {"id": "unset_x", "mcpjungle": {"transport": "stdio", "command": "npx",
                                        "args": ["${S4_SURELY_UNSET_VAR}"]}},
        {"id": "direct_only", "direct": {"command": "npx"}}]}
    path = tmp_path / "mcp_servers.yaml"
    path.write_text(yaml.safe_dump(decl), encoding="utf-8")
    reg = jg.JungleRegistry(fake.api())
    out = {r["name"]: r for r in reg.register_declarations(path)}
    assert out["files_x"]["status"] == "registered" and out["db_x"]["status"] == "registered"
    assert out["unset_x"]["status"] == "failed" and "S4_SURELY_UNSET_VAR" in out["unset_x"]["error"]
    assert out["direct_only"]["status"] == "skipped"
    sent = fake.servers["db_x"]["server"]
    # FXI3 (Codex f1 #7): MCPJungle stores the body, so a credential is sent as a REFERENCE
    assert sent["env"]["DATABASE_URI"] == "postgresql://r:${S4_TEST_DB_PASSWORD}@h/db"
    assert "synthetic" not in json.dumps(sent)
    assert out["db_x"]["secret_references"] == ["S4_TEST_DB_PASSWORD"]
    assert sent["session_mode"] == "stateful"
    only = reg.register_declarations(path, only=["files_x"])
    assert [r["status"] for r in only] == ["exists"]


def test_the_real_infra_declarations_all_build_bodies():
    """Every server declared in infra/mcp_servers.yaml yields a valid registration body (or a
    plain-words reason it cannot, e.g. an unset variable)."""
    from databridge_mcp_ingest import paths

    data = yaml.safe_load((paths.DATABRIDGE_ROOT / "infra" / "mcp_servers.yaml")
                          .read_text(encoding="utf-8"))
    decls = [d for d in data["servers"] if d.get("mcpjungle")]
    assert decls
    for d in decls:
        try:
            body = jg.body_from_declaration(d)
        except Exception as exc:                                     # noqa: BLE001
            # an unset variable, or (FXI3, Codex f1 #7) a literal credential MCPJungle would
            # store — refused by the scanner's own rule name, never sent
            assert "${" in json.dumps(d["mcpjungle"]) or "not set" in str(exc)                 or "literal secret" in str(exc), exc
            continue
        assert body["name"] == d["id"] and (body.get("command") or body.get("url"))


def test_connection_bodies_rewrite_loopback_and_keep_secrets_out_of_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("S4_TEST_TOKEN", "tok-synthetic")
    conn = http_connection("remote", "http://127.0.0.1:9000/mcp",
                           headers_from={"Authorization": "S4_TEST_TOKEN"}, via="mcpjungle")
    mi.register_connection(conn, root=tmp_path)
    assert "tok-synthetic" not in (tmp_path / "remote.yaml").read_text(encoding="utf-8")
    monkeypatch.setenv("S4_TEST_TOKEN", "Bearer tok-synthetic")
    # FXI3 (Codex f1 #7/#12): a bearer token would be STORED by the aggregator -> refused
    with pytest.raises(jg.JungleError, match="via: direct"):
        jg.body_from_connection(conn)
    plain = http_connection("remote2", "http://127.0.0.1:9000/mcp", via="mcpjungle")
    body = jg.body_from_connection(plain)
    assert body["url"] == "http://host.docker.internal:9000/mcp"
    assert "bearer_token" not in body and body["transport"] == "streamable_http"
    assert jg.body_from_connection(plain, container_host=None)["url"].startswith(
        "http://127.0.0.1")
    stdio = stdio_connection("mem", "npx", ["-y", "@modelcontextprotocol/server-memory"],
                             env={"MEMORY_FILE_PATH": "/tmp/m.jsonl"})
    b = jg.body_from_connection(stdio)
    assert (b["command"], b["args"], b["env"]) == ("npx", stdio.transport.args,
                                                   {"MEMORY_FILE_PATH": "/tmp/m.jsonl"})


def test_resource_uris_round_trip_to_the_servers_own_uri():
    for uri in ("demo://resource/static/document/architecture.md", "file:///C:/x y/#q.md",
                "postgres://fixture/accounts/schema", "a"):
        juri = jg.encode_resource_uri("srv", uri)
        assert juri.startswith("mcpj://res/srv/")
        assert jg.decode_resource_uri(juri) == ("srv", uri)
    # the exact form MCPJungle produced live on 2026-10-01
    live = "mcpj://res/s4probe_everything/ZGVtbzovL3Jlc291cmNlL3N0YXRpYy9kb2N1bWVudC9hcmNoaXRlY3R1cmUubWQ"
    assert jg.decode_resource_uri(live) == ("s4probe_everything",
                                            "demo://resource/static/document/architecture.md")


def test_jungle_client_lists_and_reads_resources_by_their_original_uri(fake):
    api = fake.api()
    jg.JungleRegistry(api).register(_body("docs", "pkg"))
    conn = mi.SourceConnection.model_validate({"id": "docs", "via": "mcpjungle",
                                               "transport": {"type": "stdio", "command": "npx"}})
    c = JungleClient(conn, backend=api)
    assert c.supports_resources
    res = c.list_resources()
    assert [r.uri for r in res] == [r["uri"] for r in CATALOGUE["npx"]["resources"]]
    assert [r.name for r in res] == [r["name"] for r in CATALOGUE["npx"]["resources"]]
    parts = c.read_resource(res[0].uri)
    assert parts[0].text == f"content of {res[0].uri}" and parts[0].uri == res[0].uri
    raw = c.call_tool_raw("read_graph", {"x": 1, "name": "hijack"})
    sent = fake.requests[-1][2]
    assert sent == {"name": "docs__read_graph", "x": 1}               # flat, name not overridable
    assert set(raw) == {"content", "structured", "is_error"}


def test_with_route_keeps_the_id_and_the_minimal_client_still_refuses_resources(fake):
    from databridge_mcp_ingest.client import _MinimalJungle

    direct = stdio_connection("same", "npx", ["-y", "pkg"])
    via = with_route(direct, "mcpjungle", jungle_server="same_srv")
    assert (via.id, via.via, via.jungle_name) == ("same", "mcpjungle", "same_srv")
    assert with_route(via, "direct").jungle_server is None
    minimal = JungleClient(via, backend=_MinimalJungle("http://jungle.test",
                                                       transport=httpx.MockTransport(fake.handler)))
    assert not minimal.supports_resources
    with pytest.raises(McpClientError):
        minimal.list_resources()


def test_registry_reports_an_unreachable_gateway_in_plain_words():
    def down(request):
        raise httpx.ConnectError("refused")

    reg = jg.JungleRegistry(jg.JungleAPI("http://nowhere", transport=httpx.MockTransport(down)))
    h = reg.health()
    assert h["ok"] is False and "not reachable" in h["error"]
    with pytest.raises(jg.JungleError, match="not reachable"):
        reg.list_servers()
