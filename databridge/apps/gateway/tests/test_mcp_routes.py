"""``/mcp/*`` — the Connectors surface, tested against a **fake aggregator, not Docker** (T-44).

`MCP_RUNBOOK.md` §6 states the rule this file obeys: *"a test that needs a running container is a
test that rots"*. So the whole MCPJungle HTTP API is stood up here behind ``httpx.MockTransport``
— the same paths, payload shapes and status codes observed against the live container on
2026-08-13 — and injected into :class:`MCPJungleClient`. That choice is deliberate over stubbing
the client's own methods: the request URL, the flat invoke payload and the 204-on-delete are part
of what can be wrong, and a method-level stub would pass while all three were.

:class:`FakeJungle` records **every** request it receives. That call log is what turns the central
claim into an assertion rather than a comment: connecting an ACT server must produce *no*
``POST /api/v0/servers`` at all, and invoking an ACT tool must produce *no*
``POST /api/v0/tools/invoke``. Absence of a call is the thing being tested, so the log has to be
complete.

The three non-negotiables, each with its own test:

* an ACT server never reaches the agent's aggregator   -> ``test_act_server_is_never_registered_*``
* the invoke route refuses an ACT tool                  -> ``test_invoke_refuses_an_act_tool*``
* a down aggregator refuses, never an empty list        -> ``test_*_when_the_registry_is_down``
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_gateway.mcp_routes import (
    ActRegistry,
    MCPJungleClient,
    _server_info,
    act_registry_path,
    register_mcp_routes,
    registry_url,
)

REGISTRY = "http://registry.test:8080"

# The four tools our read-only clinic server really exposes, with the real schemas
# (`GET /api/v0/tools` against the live aggregator, 2026-08-13).
CLINIC_TOOLS = [
    {"name": "walnut_clinic_read__brain_query", "enabled": True,
     "description": "Answer a question from the clinic graph, with a citation per value.",
     "input_schema": {"type": "object", "required": ["question"],
                      "properties": {"question": {"type": "string", "title": "Question"}}}},
    {"name": "walnut_clinic_read__brain_neighbors", "enabled": True,
     "description": "The subgraph around an entity, to a given depth.",
     "input_schema": {"type": "object", "required": ["entity"],
                      "properties": {"entity": {"type": "string"},
                                     "depth": {"type": "integer", "default": 1}}}},
    {"name": "walnut_clinic_read__brain_timeline", "enabled": True,
     "description": "The bi-temporal version history of an entity.",
     "input_schema": {"type": "object", "required": ["entity"],
                      "properties": {"entity": {"type": "string"}}}},
    {"name": "walnut_clinic_read__brain_path", "enabled": True,
     "description": "The provenance path between two entities, with the pointers along it.",
     "input_schema": {"type": "object", "required": ["from_entity", "to_entity"],
                      "properties": {"from_entity": {"type": "string"},
                                     "to_entity": {"type": "string"}}}},
]

CLINIC_SERVER = {
    "name": "walnut_clinic_read", "transport": "streamable_http", "enabled": True,
    "description": "Walnut clinic knowledge graph - READ ONLY.",
    "url": "http://host.docker.internal:8765/mcp", "session_mode": "stateless",
}


class FakeJungle:
    """A whole MCPJungle, in memory, behind ``httpx.MockTransport``.

    Mirrors the observed API exactly, including the parts that are easy to get wrong: register
    answers ``201 {"server": {...}}``, deregister answers ``204`` with an empty body, and invoke
    answers ``200`` with ``isError`` inside the body even when the tool failed.
    """

    def __init__(self, *, servers=None, tools=None, down: bool = False) -> None:
        self.servers = list(servers if servers is not None else [CLINIC_SERVER])
        self.tools = list(tools if tools is not None else CLINIC_TOOLS)
        self.down = down
        self.calls: list[tuple[str, str, dict]] = []

    # -- assertions helpers ----------------------------------------------------------------
    def called(self, method: str, path: str) -> list[dict]:
        return [body for m, p, body in self.calls if m == method and p == path]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> MCPJungleClient:
        return MCPJungleClient(REGISTRY, transport=self.transport)

    # -- the API ---------------------------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, path, body))
        if self.down:
            # What a stopped container actually looks like from httpx.
            raise httpx.ConnectError("All connection attempts failed", request=request)

        if method == "GET" and path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if method == "GET" and path == "/api/v0/servers":
            return httpx.Response(200, json=self.servers)
        if method == "GET" and path == "/api/v0/tools":
            return httpx.Response(200, json=self.tools)
        if method == "POST" and path == "/api/v0/servers":
            if not body.get("transport"):
                return httpx.Response(400, json={"error": "transport is required"})
            server = {"name": body["name"], "transport": body["transport"], "enabled": True,
                      "description": body.get("description", ""), "url": body["url"],
                      "session_mode": "stateless"}
            self.servers.append(server)
            self.tools.append({"name": f"{body['name']}__ping", "enabled": True,
                               "description": "ping", "input_schema": {"type": "object"}})
            return httpx.Response(201, json={"server": server})
        if method == "DELETE" and path.startswith("/api/v0/servers/"):
            name = path.rsplit("/", 1)[-1]
            if name not in {s["name"] for s in self.servers}:
                return httpx.Response(404, json={"error": f"MCP server {name} not found"})
            self.servers = [s for s in self.servers if s["name"] != name]
            self.tools = [t for t in self.tools if not t["name"].startswith(f"{name}__")]
            return httpx.Response(204)
        if method == "POST" and path == "/api/v0/tools/invoke":
            name = body.get("name")
            if name not in {t["name"] for t in self.tools}:
                return httpx.Response(200, json={
                    "isError": True,
                    "content": [{"type": "text", "text": f"Unknown tool: {name}"}]})
            args = {k: v for k, v in body.items() if k != "name"}
            return httpx.Response(200, json={
                "isError": False,
                "content": [{"type": "text", "text": json.dumps({"tool": name, "args": args})}]})
        return httpx.Response(404, json={"error": f"no route {method} {path}"})


def build(jungle: FakeJungle, *, acts: ActRegistry | None = None,
          reader_up: bool = True) -> TestClient:
    app = FastAPI()
    register_mcp_routes(
        app, client=jungle.client(), act_registry=acts if acts is not None else ActRegistry(),
        # The reader probe is injected too: a test must not depend on whether a developer happens
        # to have brain_reader_server.py running on 8765.
        reader_probe=lambda: {"url": "http://127.0.0.1:8765/mcp", "reachable": reader_up,
                              "server_info": {"name": "databridge-clinic-reader"} if reader_up
                              else None,
                              "detail": None if reader_up else "ConnectError"})
    return TestClient(app)


@pytest.fixture()
def jungle() -> FakeJungle:
    return FakeJungle()


@pytest.fixture()
def client(jungle) -> TestClient:
    return build(jungle)


def connect_act(client: TestClient, name: str = "walnut_mail_act") -> dict:
    r = client.post("/mcp/servers", json={
        "name": name, "url": "http://127.0.0.1:8790/mcp", "kind": "act",
        "description": "Mailpit SMTP sink — dispatcher only."})
    assert r.status_code == 201, r.text
    return r.json()


# =========================================================================================
# 1. A down aggregator is a refusal, never an empty list
# =========================================================================================


def test_servers_refuses_when_the_registry_is_down():
    down = FakeJungle(down=True)
    r = build(down).get("/mcp/servers")
    assert r.status_code == 503
    body = r.json()
    assert body["refusal"]["code"] == "REGISTRY_UNREACHABLE"
    assert REGISTRY in body["refusal"]["message"]
    # The whole point: the caller cannot mistake this for "nothing is connected".
    assert "servers" not in body and "counts" not in body


def test_tools_refuses_when_the_registry_is_down():
    r = build(FakeJungle(down=True)).get("/mcp/tools")
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "REGISTRY_UNREACHABLE"
    assert "tools" not in r.json()


def test_invoke_refuses_when_the_registry_is_down():
    r = build(FakeJungle(down=True)).post(
        "/mcp/tools/walnut_clinic_read__brain_timeline/invoke", json={"entity": "WELL2"})
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "REGISTRY_UNREACHABLE"


def test_connecting_refuses_when_the_registry_is_down_even_for_an_act_server():
    # The ACT claim IS "this name is not in the agent's registry". While the registry cannot be
    # read, that claim cannot be made, so recording one would be asserting something unverified.
    down = FakeJungle(down=True)
    r = build(down).post("/mcp/servers", json={
        "name": "walnut_mail_act", "url": "http://127.0.0.1:8790/mcp", "kind": "act"})
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "REGISTRY_UNREACHABLE"


def test_status_reports_a_down_registry_without_failing_the_request():
    down = FakeJungle(down=True)
    r = build(down, reader_up=False).get("/mcp/status")
    assert r.status_code == 200                      # "everything is down" IS the answer here
    body = r.json()
    assert body["registry"] == {"url": REGISTRY, "reachable": False, "status": None,
                                "detail": body["registry"]["detail"]}
    assert body["registry"]["detail"], "a down registry must say why, not just 'false'"
    assert body["reader"]["reachable"] is False


def test_status_reports_both_processes_when_up(client, jungle):
    body = client.get("/mcp/status").json()
    assert body["registry"]["reachable"] is True
    assert body["registry"]["status"] == "ok"
    assert body["registry"]["url"] == REGISTRY
    assert body["reader"]["reachable"] is True
    assert body["reader"]["server_info"]["name"] == "databridge-clinic-reader"
    assert jungle.called("GET", "/health")


# =========================================================================================
# 2. Listing: servers, their tools, their schemas
# =========================================================================================


def test_servers_lists_the_registered_reader_with_its_four_tools(client):
    body = client.get("/mcp/servers").json()
    assert body["registry"] == {"url": REGISTRY, "reachable": True}
    assert body["counts"] == {"read": 1, "act": 0, "tools": 4}
    [server] = body["servers"]
    assert server["name"] == "walnut_clinic_read"
    assert server["kind"] == "read"
    assert server["agent_reachable"] is True
    assert server["url"] == "http://host.docker.internal:8765/mcp"
    assert [t["tool"] for t in server["tools"]] == [
        "brain_query", "brain_neighbors", "brain_timeline", "brain_path"]
    assert all(t["description"] for t in server["tools"]), (
        "MCP_RUNBOOK.md §5: four tools with empty descriptions passed every test and were "
        "unusable — a tool list without descriptions is not a tool list")


def test_tools_carry_their_input_schemas(client):
    body = client.get("/mcp/tools").json()
    by_name = {t["name"]: t for t in body["tools"]}
    assert len(by_name) == 4
    query = by_name["walnut_clinic_read__brain_query"]
    assert query["input_schema"]["required"] == ["question"]
    assert query["server"] == "walnut_clinic_read"
    assert query["tool"] == "brain_query"
    assert query["invocable"] is True


def test_a_disabled_tool_is_listed_but_not_invocable():
    jungle = FakeJungle(tools=[{**CLINIC_TOOLS[0], "enabled": False}])
    body = build(jungle).get("/mcp/tools").json()
    assert body["tools"][0]["enabled"] is False
    assert body["tools"][0]["invocable"] is False


# =========================================================================================
# 3. THE NON-NEGOTIABLE: an ACT server never enters the agent's aggregator
# =========================================================================================


def test_act_server_is_never_registered_into_the_aggregator(client, jungle):
    result = connect_act(client)
    assert result["kind"] == "act"
    assert result["registered_into_aggregator"] is False
    assert result["server"]["agent_reachable"] is False
    # The assertion that matters is about a call that must NOT exist.
    assert jungle.called("POST", "/api/v0/servers") == [], (
        "an ACT server reached the aggregator the agent queries — the capability boundary is "
        "gone (AGENT_CAPABILITIES.md, R-34)")
    assert {s["name"] for s in jungle.servers} == {"walnut_clinic_read"}


def test_act_server_never_appears_in_the_agents_tool_surface(client):
    connect_act(client)
    tools = client.get("/mcp/tools").json()["tools"]
    assert all(t["server"] == "walnut_clinic_read" for t in tools)
    assert not any("mail" in t["name"] for t in tools)


def test_act_server_is_listed_for_the_operator_but_marked_unreachable(client):
    connect_act(client)
    body = client.get("/mcp/servers").json()
    act = [s for s in body["servers"] if s["kind"] == "act"]
    assert len(act) == 1
    assert act[0]["name"] == "walnut_mail_act"
    assert act[0]["agent_reachable"] is False
    assert act[0]["in_aggregator"] is False
    assert act[0]["tools_known"] is False, (
        "we never open a session with an act-capable endpoint from this process, so its tools "
        "are honestly unknown rather than listed")
    assert act[0]["tools"] == []
    assert body["counts"] == {"read": 1, "act": 1, "tools": 4}
    assert body["boundary_violations"] == []


def test_an_act_name_appearing_in_the_aggregator_is_flagged_not_hidden(jungle):
    # Simulates the boundary being broken out-of-band (someone runs the mcpjungle CLI by hand).
    acts = ActRegistry()
    acts.add(name="walnut_mail_act", url="http://127.0.0.1:8790/mcp", description="")
    jungle.servers.append({**CLINIC_SERVER, "name": "walnut_mail_act"})
    body = build(jungle, acts=acts).get("/mcp/servers").json()
    assert body["boundary_violations"] == ["walnut_mail_act"]
    act = [s for s in body["servers"] if s["kind"] == "act"][0]
    assert act["boundary_violation"] is True and act["agent_reachable"] is False


def test_read_server_is_registered_into_the_aggregator(client, jungle):
    r = client.post("/mcp/servers", json={
        "name": "weather_read", "url": "http://host.docker.internal:9100/mcp", "kind": "read",
        "description": "public weather, read-only"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["registered_into_aggregator"] is True
    assert body["server"]["kind"] == "read"
    assert body["server"]["agent_reachable"] is True
    [sent] = jungle.called("POST", "/api/v0/servers")
    assert sent == {"name": "weather_read", "url": "http://host.docker.internal:9100/mcp",
                    "description": "public weather, read-only", "transport": "streamable_http"}
    # ... and it genuinely changed what the agent can call.
    tools = client.get("/mcp/tools").json()["tools"]
    assert "weather_read__ping" in {t["name"] for t in tools}


def test_the_two_kinds_are_recorded_in_different_stores(client, jungle):
    connect_act(client, "walnut_mail_act")
    client.post("/mcp/servers", json={"name": "weather_read", "kind": "read",
                                      "url": "http://h:9100/mcp", "description": ""})
    aggregator = {s["name"] for s in jungle.servers}
    assert "weather_read" in aggregator
    assert "walnut_mail_act" not in aggregator


def test_the_consequence_of_each_kind_is_stated_to_the_ui(client):
    body = client.get("/mcp/servers").json()
    assert "immediately" in body["kind_consequence"]["read"]
    assert "approves" in body["kind_consequence"]["act"]


# =========================================================================================
# 4. THE NON-NEGOTIABLE: the invoke route refuses ACT tools
# =========================================================================================


def test_invoke_refuses_an_act_tool(client, jungle):
    connect_act(client)
    r = client.post("/mcp/tools/walnut_mail_act__send_email/invoke",
                    json={"to": "someone@example.com"})
    assert r.status_code == 403
    refusal = r.json()["refusal"]
    assert refusal["code"] == "ACT_TOOL_NOT_INVOCABLE"
    assert "approve" in refusal["message"]
    assert refusal["nearest_alternative"]
    # Refused BEFORE the aggregator was contacted at all.
    assert jungle.called("POST", "/api/v0/tools/invoke") == []


def test_invoke_refuses_an_act_tool_even_if_the_aggregator_somehow_lists_it(jungle):
    # Belt and braces: the ACT check runs on the server name, before the tool-existence lookup,
    # so a boundary broken out-of-band still cannot be exercised through this button.
    acts = ActRegistry()
    acts.add(name="walnut_mail_act", url="http://127.0.0.1:8790/mcp", description="")
    jungle.tools.append({"name": "walnut_mail_act__send_email", "enabled": True,
                         "description": "send", "input_schema": {"type": "object"}})
    r = build(jungle, acts=acts).post("/mcp/tools/walnut_mail_act__send_email/invoke", json={})
    assert r.status_code == 403
    assert r.json()["refusal"]["code"] == "ACT_TOOL_NOT_INVOCABLE"
    assert jungle.called("POST", "/api/v0/tools/invoke") == []


def test_invoke_refuses_a_writer_process_tool(jungle):
    # `brain_ingest` lives in the writer process (apps/mcp_server/capabilities.py). If it ever
    # surfaced on the read endpoint, that is R-34 recurring — refuse rather than relay.
    jungle.tools.append({"name": "walnut_clinic_read__brain_ingest", "enabled": True,
                         "description": "ingest", "input_schema": {"type": "object"}})
    c = build(jungle)
    assert [t for t in c.get("/mcp/tools").json()["tools"]
            if t["tool"] == "brain_ingest"][0]["invocable"] is False
    r = c.post("/mcp/tools/walnut_clinic_read__brain_ingest/invoke", json={})
    assert r.status_code == 403
    assert r.json()["refusal"]["code"] == "WRITE_TOOL_NOT_INVOCABLE"
    assert jungle.called("POST", "/api/v0/tools/invoke") == []


def test_invoke_runs_a_read_tool_and_relays_the_raw_result(client, jungle):
    r = client.post("/mcp/tools/walnut_clinic_read__brain_timeline/invoke",
                    json={"entity": "WELL2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool"] == "walnut_clinic_read__brain_timeline"
    assert body["server"] == "walnut_clinic_read"
    assert body["is_error"] is False
    assert body["arguments"] == {"entity": "WELL2"}
    assert json.loads(body["text"])["args"] == {"entity": "WELL2"}
    assert body["raw"]["content"], "the raw aggregator result is relayed, not summarised"
    assert body["duration_ms"] >= 0
    # The payload really is flat: `{"name": ..., **args}`. Nesting it under `input`/`arguments`
    # makes the tool fail its own schema validation, which reads like a bug in the tool.
    [sent] = jungle.called("POST", "/api/v0/tools/invoke")
    assert sent == {"entity": "WELL2", "name": "walnut_clinic_read__brain_timeline"}


def test_invoke_cannot_be_retargeted_through_a_name_argument(client, jungle):
    client.post("/mcp/tools/walnut_clinic_read__brain_timeline/invoke",
                json={"entity": "WELL2", "name": "walnut_clinic_read__brain_query"})
    [sent] = jungle.called("POST", "/api/v0/tools/invoke")
    assert sent["name"] == "walnut_clinic_read__brain_timeline"


def test_invoke_of_an_unknown_tool_is_a_404_refusal_not_a_relayed_error(client, jungle):
    r = client.post("/mcp/tools/walnut_clinic_read__nope/invoke", json={})
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_TOOL"
    assert jungle.called("POST", "/api/v0/tools/invoke") == []


def test_a_tool_error_is_relayed_as_an_error_not_flattened_into_success():
    """The aggregator answers HTTP 200 with ``isError: true`` when a tool rejects its arguments.

    Reporting that as a success would be the page lying about the call it just made.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v0/tools":
            return httpx.Response(200, json=CLINIC_TOOLS)
        return httpx.Response(200, json={"isError": True, "content": [
            {"type": "text", "text": "Error executing tool brain_query: field required"}]})

    app = FastAPI()
    register_mcp_routes(app, client=MCPJungleClient(REGISTRY, transport=httpx.MockTransport(handler)),
                        act_registry=ActRegistry(), reader_probe=lambda: {"reachable": False})
    r = TestClient(app).post("/mcp/tools/walnut_clinic_read__brain_query/invoke", json={})
    assert r.status_code == 200
    assert r.json()["is_error"] is True
    assert "field required" in r.json()["text"]


# =========================================================================================
# 5. Validation on connect
# =========================================================================================


@pytest.mark.parametrize("kind", [None, "", "READ-ONLY", "write", "both"])
def test_connect_refuses_an_unrecognised_kind(client, jungle, kind):
    payload = {"name": "x_srv", "url": "http://h:1/mcp"}
    if kind is not None:
        payload["kind"] = kind
    r = client.post("/mcp/servers", json=payload)
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "INVALID_KIND"
    assert jungle.called("POST", "/api/v0/servers") == []


@pytest.mark.parametrize("name", ["", "has space", "double__underscore", "-leading", "trailing_",
                                  "a" * 65, "semi;colon"])
def test_connect_refuses_a_bad_server_name(client, name):
    r = client.post("/mcp/servers", json={"name": name, "url": "http://h:1/mcp", "kind": "read"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "INVALID_SERVER_NAME"


@pytest.mark.parametrize("url", ["", "ftp://h/mcp", "file:///etc/passwd", "not a url",
                                 "http:///mcp"])
def test_connect_refuses_a_non_http_url(client, url):
    r = client.post("/mcp/servers", json={"name": "x_srv", "url": url, "kind": "read"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "INVALID_SERVER_URL"


def test_connect_refuses_an_unknown_transport(client):
    r = client.post("/mcp/servers", json={"name": "x_srv", "url": "http://h:1/mcp",
                                          "kind": "read", "transport": "carrier_pigeon"})
    assert r.status_code == 422
    assert r.json()["refusal"]["code"] == "INVALID_TRANSPORT"


def test_connect_refuses_a_duplicate_name(client):
    r = client.post("/mcp/servers", json={"name": "walnut_clinic_read", "kind": "read",
                                          "url": "http://h:1/mcp"})
    assert r.status_code == 409
    assert r.json()["refusal"]["code"] == "DUPLICATE_SERVER"


def test_a_name_cannot_change_sides_of_the_boundary_in_place(client):
    connect_act(client, "walnut_mail_act")
    r = client.post("/mcp/servers", json={"name": "walnut_mail_act", "kind": "read",
                                          "url": "http://127.0.0.1:8790/mcp"})
    assert r.status_code == 409
    assert r.json()["refusal"]["code"] == "DUPLICATE_SERVER"


def test_a_registry_rejection_is_relayed_with_its_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(500, json={"error": "failed to initialize connection with MCP "
                                                  "server: connection refused"})

    app = FastAPI()
    register_mcp_routes(app, client=MCPJungleClient(REGISTRY,
                                                    transport=httpx.MockTransport(handler)),
                        act_registry=ActRegistry(), reader_probe=lambda: {"reachable": False})
    r = TestClient(app).post("/mcp/servers", json={"name": "dead_srv", "kind": "read",
                                                   "url": "http://h:9999/mcp"})
    assert r.status_code == 502
    assert r.json()["refusal"]["code"] == "REGISTRY_REJECTED"
    assert "connection refused" in r.json()["refusal"]["message"]


# =========================================================================================
# 6. Disconnect
# =========================================================================================


def test_disconnect_a_read_server_deregisters_it_from_the_aggregator(client, jungle):
    r = client.delete("/mcp/servers/walnut_clinic_read")
    assert r.status_code == 200
    assert r.json() == {"disconnected": "walnut_clinic_read", "kind": "read",
                        "was_in_aggregator": True}
    assert jungle.servers == []
    assert client.get("/mcp/tools").json()["tools"] == []


def test_disconnect_an_act_server_removes_only_our_record(client, jungle):
    connect_act(client)
    r = client.delete("/mcp/servers/walnut_mail_act")
    assert r.status_code == 200
    assert r.json()["kind"] == "act"
    assert jungle.called("DELETE", "/api/v0/servers/walnut_mail_act") == [], (
        "an ACT server was never in the aggregator, so there is nothing to deregister there")
    assert client.get("/mcp/servers").json()["counts"]["act"] == 0


def test_disconnect_an_unknown_server_is_a_404_refusal(client):
    r = client.delete("/mcp/servers/never_existed")
    assert r.status_code == 404
    assert r.json()["refusal"]["code"] == "UNKNOWN_SERVER"


def test_disconnect_refuses_when_the_registry_is_down():
    r = build(FakeJungle(down=True)).delete("/mcp/servers/walnut_clinic_read")
    assert r.status_code == 503
    assert r.json()["refusal"]["code"] == "REGISTRY_UNREACHABLE"


# =========================================================================================
# 7. The ACT registry itself
# =========================================================================================


def test_act_registry_round_trips_through_a_file(tmp_path: pathlib.Path):
    path = tmp_path / "nested" / "act.json"
    first = ActRegistry(path)
    first.add(name="walnut_mail_act", url="http://127.0.0.1:8790/mcp", description="mail")
    assert path.exists()
    second = ActRegistry(path)
    assert second.names() == {"walnut_mail_act"}
    assert second.get("walnut_mail_act").url == "http://127.0.0.1:8790/mcp"
    assert second.get("walnut_mail_act").recorded_at
    assert second.remove("walnut_mail_act") is True
    assert ActRegistry(path).names() == frozenset()
    assert second.remove("walnut_mail_act") is False


def test_an_in_memory_act_registry_writes_nothing():
    registry = ActRegistry()
    registry.add(name="x_act", url="http://h/mcp", description="")
    assert registry.path is None and registry.names() == {"x_act"}


def test_a_corrupt_act_registry_file_starts_empty_rather_than_crashing(tmp_path):
    path = tmp_path / "act.json"
    path.write_text("{ not json", encoding="utf-8")
    assert ActRegistry(path).names() == frozenset()


# =========================================================================================
# 8. Configuration: env with a local default, and no secrets in the repo
# =========================================================================================


def test_registry_url_comes_from_env_with_a_local_default(monkeypatch):
    monkeypatch.delenv("MCPJUNGLE_URL", raising=False)
    assert registry_url() == "http://127.0.0.1:8080"
    monkeypatch.setenv("MCPJUNGLE_URL", "http://elsewhere:9090/")
    assert registry_url() == "http://elsewhere:9090"


def test_act_registry_path_comes_from_env_and_defaults_outside_version_control(monkeypatch,
                                                                               tmp_path):
    monkeypatch.delenv("WALNUT_ACT_REGISTRY_PATH", raising=False)
    default = act_registry_path()
    # `.databridge/` is git-ignored, so operator state never becomes repo content.
    assert ".databridge" in default.parts
    monkeypatch.setenv("WALNUT_ACT_REGISTRY_PATH", str(tmp_path / "a.json"))
    assert act_registry_path() == tmp_path / "a.json"


def test_no_credential_is_baked_into_this_module():
    source = (pathlib.Path(__file__).parent.parent / "databridge_gateway"
              / "mcp_routes.py").read_text(encoding="utf-8")
    for smell in ("api_key", "apikey", "Authorization", "Bearer ", "password", "secret"):
        assert smell.lower() not in source.lower(), f"possible credential in mcp_routes.py: {smell}"


# =========================================================================================
# 9. The reader probe's parsing (the transport itself is exercised by hand, not in CI)
# =========================================================================================


def test_server_info_is_read_out_of_an_sse_framed_initialize_reply():
    sse = ('event: message\n'
           'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18",'
           '"serverInfo":{"name":"databridge-clinic-reader","version":"1.27.1"}}}\n\n')
    assert _server_info(sse) == {"name": "databridge-clinic-reader", "version": "1.27.1"}


def test_server_info_is_none_when_the_reply_is_not_an_initialize_result():
    assert _server_info("event: ping\ndata: {\"jsonrpc\":\"2.0\"}\n") is None
    assert _server_info("<html>406 Not Acceptable</html>") is None
