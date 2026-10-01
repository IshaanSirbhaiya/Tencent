"""W1 — real-time drift hooks in the MCP client (BUILD_PLAN §5.6; AA review, wiring items 2 and 3).

A REAL stdio server (FastMCP, written to a temp dir below) that, when its `poke` tool is called,
sends `notifications/tools/list_changed` or `notifications/resources/list_changed`, and that
declares a resource TEMPLATE. Proves:

* a list_changed from the server reaches a registered listener EXACTLY ONCE — over a pooled
  session too (an idle pooled session keeps listening);
* `install_list_changed_hook()` (the gateway's boot call) routes it to the adapter agents'
  `on_list_changed(connection_id)` exactly once, and installing twice never doubles it;
* `SdkClient.list_resource_templates()` pages the server's templates, the read-only wrapper passes
  it through, and the drift probe observes the `resource_templates` facet; through MCPJungle (no
  template route) the facet stays UNOBSERVED (None), never an empty "all removed" set.

Everything synthetic; no network, no model.
"""
from __future__ import annotations

import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from databridge_mcp_ingest import client as mc
from databridge_mcp_ingest.connections import SourceConnection

SERVER = textwrap.dedent('''
    from mcp.server.fastmcp import Context, FastMCP

    mcp = FastMCP("w1-list-changed")

    @mcp.tool()
    async def poke(kind: str, ctx: Context) -> str:
        """Tell the client its tool or resource list changed."""
        if kind == "tools":
            await ctx.session.send_tool_list_changed()
        else:
            await ctx.session.send_resource_list_changed()
        return "poked " + kind

    @mcp.tool()
    def read_note(name: str) -> str:
        """Read a note."""
        return "note " + name

    @mcp.resource("notes://index", mime_type="text/plain")
    def index() -> str:
        return "alpha\\nbeta"

    @mcp.resource("notes://item/{name}", mime_type="text/markdown")
    def item(name: str) -> str:
        return "# " + name

    if __name__ == "__main__":
        mcp.run()
''')


@pytest.fixture
def server_conn(tmp_path, monkeypatch) -> SourceConnection:
    path = tmp_path / "w1_server.py"
    path.write_text(SERVER, encoding="utf-8")
    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(tmp_path / "sources"))
    return SourceConnection.model_validate({
        "id": "w1src", "title": "w1src",
        "transport": {"type": "stdio", "command": sys.executable, "args": [str(path)]}})


@pytest.fixture(autouse=True)
def clean_listeners(monkeypatch):
    mc.clear_list_changed_listeners()
    yield
    mc.clear_list_changed_listeners()


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.event = threading.Event()

    def __call__(self, connection_id: str, kind: str) -> None:
        self.calls.append((connection_id, kind))
        self.event.set()


def _settle(rec: Recorder, seconds: float = 0.6) -> None:
    """Wait for the first delivery, then long enough that a duplicate would have arrived."""
    assert rec.event.wait(15), "the list_changed notification never reached the listener"
    time.sleep(seconds)


@pytest.mark.parametrize("kind", ["tools", "resources"])
def test_a_server_list_changed_reaches_the_listener_exactly_once(server_conn, kind):
    rec = Recorder()
    mc.add_list_changed_listener(rec)
    mc.add_list_changed_listener(rec)               # registering twice is a no-op
    with mc.open_client(server_conn, pooled=False) as c:
        assert "poked" in c.call_tool("poke", {"kind": kind}).text
        _settle(rec)
    assert rec.calls == [(server_conn.id, kind)]


def test_a_pooled_session_listens_and_duplicates_are_debounced(server_conn, monkeypatch):
    monkeypatch.setenv("WALNUT_LIST_CHANGED_DEBOUNCE_S", "30")
    pool = mc.SessionPool(max_idle=2, idle_timeout=60)
    rec = Recorder()
    mc.add_list_changed_listener(rec)
    try:
        with pool.lease(server_conn) as c:
            c.call_tool("poke", {"kind": "tools"})
            c.call_tool("poke", {"kind": "tools"})  # a chatty server: the same change twice
            _settle(rec)
        assert pool.snapshot()["idle"] == 1           # returned to the pool, still open
        assert rec.calls == [(server_conn.id, "tools")]
        # the session is reused warm and still delivers (a different kind is not debounced)
        with pool.lease(server_conn) as c:
            rec.event.clear()
            c.call_tool("poke", {"kind": "resources"})
            _settle(rec)
        assert pool.stats["hits"] == 1
        assert rec.calls == [(server_conn.id, "tools"), (server_conn.id, "resources")]
    finally:
        pool.shutdown()


def test_without_debounce_every_notification_is_delivered(server_conn, monkeypatch):
    monkeypatch.setenv("WALNUT_LIST_CHANGED_DEBOUNCE_S", "0")
    rec = Recorder()
    mc.add_list_changed_listener(rec)
    with mc.open_client(server_conn, pooled=False) as c:
        c.call_tool("poke", {"kind": "tools"})
        c.call_tool("poke", {"kind": "tools"})
        deadline = time.monotonic() + 15
        while len(rec.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    assert rec.calls == [(server_conn.id, "tools")] * 2


def test_a_failing_listener_never_stops_the_others(server_conn):
    def broken(connection_id, kind):
        raise RuntimeError("listener bug")

    rec = Recorder()
    mc.add_list_changed_listener(broken)
    mc.add_list_changed_listener(rec)
    with mc.open_client(server_conn, pooled=False) as c:
        c.call_tool("poke", {"kind": "tools"})
        _settle(rec)
        # and the session is still usable
        assert c.call_tool("read_note", {"name": "a"}).text == "note a"
    assert rec.calls == [(server_conn.id, "tools")]


def test_remove_listener():
    rec = Recorder()
    remove = mc.add_list_changed_listener(rec)
    remove()
    assert mc.notify_list_changed("x", "tools") == 0 and rec.calls == []


def test_the_boot_hook_routes_to_adapter_agents_on_list_changed_exactly_once(server_conn):
    pytest.importorskip("databridge_adapter_agents")

    class FakeManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.event = threading.Event()

        def on_list_changed(self, connection_id: str, *, kind: str = "tools"):
            self.calls.append((connection_id, kind))
            self.event.set()
            return []

    m = FakeManager()
    assert mc.install_list_changed_hook(m) is True
    assert mc.install_list_changed_hook(m) is True           # idempotent: never doubled
    assert len(mc.list_changed_listeners()) == 1
    with mc.open_client(server_conn, pooled=False) as c:
        c.call_tool("poke", {"kind": "tools"})
        assert m.event.wait(15), "on_list_changed was never called"
        time.sleep(0.6)
    assert m.calls == [(server_conn.id, "tools")]


def test_non_list_changed_messages_are_ignored():
    class Msg:
        def __init__(self, method):
            self.root = type("R", (), {"method": method})()

    assert mc.list_changed_kind(Msg("notifications/tools/list_changed")) == "tools"
    assert mc.list_changed_kind(Msg("notifications/resources/list_changed")) == "resources"
    assert mc.list_changed_kind(Msg("notifications/progress")) is None
    assert mc.list_changed_kind(RuntimeError("transport error")) is None


# --- resource templates ------------------------------------------------------------------------
def test_sdk_client_lists_resource_templates(server_conn):
    with mc.open_client(server_conn, pooled=False) as c:
        templates = c.list_resource_templates()
        ro = mc.ReadOnlyClient(c)
        assert ro.supports_resource_templates
        assert ro.list_resource_templates() == templates
        declared = [r.uri for r in c.list_resources()]
    assert [t["uriTemplate"] for t in templates] == ["notes://item/{name}"]
    assert templates[0]["mimeType"] == "text/markdown"
    # a template is not a listed resource (and vice versa): the two facets are distinct
    assert "notes://item/{name}" not in declared and declared


def test_the_drift_probe_observes_the_template_facet(server_conn):
    from databridge_mcp_ingest import drift
    from databridge_mcp_ingest.plans import CrawlPlan

    plan = CrawlPlan.model_validate({"source_id": server_conn.id, "status": "approved",
                                     "steps": []})
    with mc.open_client(server_conn, pooled=False) as c:
        expected = {t["uriTemplate"]: t["mimeType"] for t in c.list_resource_templates()}
        shape = drift.probe_shape(server_conn, plan, c)
    assert expected and shape.resource_templates == expected
    assert not [p for p in shape.problems if "template" in p]


def test_through_mcpjungle_templates_are_unobserved_not_empty():
    from databridge_mcp_ingest import drift
    from databridge_mcp_ingest.plans import CrawlPlan

    class Api:
        def tools(self):
            return [{"name": "srv__read_note", "description": "Read a note.",
                     "input_schema": {"type": "object"}}]

        def invoke_tool(self, name, arguments):
            return {"content": [], "isError": False}

    conn = SourceConnection.model_validate({"id": "jsrc", "via": "mcpjungle",
                                            "jungle_server": "srv",
                                            "transport": {"type": "stdio", "command": "npx"}})
    jc = mc.JungleClient(conn, backend=Api())
    assert jc.supports_resource_templates is False
    with pytest.raises(mc.McpClientError, match="not exposed by MCPJungle"):
        jc.list_resource_templates()
    assert mc.ReadOnlyClient(jc).supports_resource_templates is False
    shape = drift.probe_shape(conn, CrawlPlan.model_validate(
        {"source_id": "jsrc", "status": "approved", "steps": []}), jc)
    assert shape.tools is not None and shape.resource_templates is None
