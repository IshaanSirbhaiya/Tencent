"""S4 — warm session pooling, health checks, the §5.4 async surface, and the read-only gate.

The real-subprocess tests use the synthetic stdio fixture server (no network, no model). Pool
bounds and eviction use an in-memory fake client, so they measure the pool, not a server.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
import time

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import client as cl
from databridge_mcp_ingest.connections import stdio_connection

from conftest import FIXTURE_SERVER, fs_connection


# --- a fake client for pool-mechanics tests ------------------------------------------------------
class _Fake:
    made = 0

    def __init__(self, conn, timeout) -> None:
        type(self).made += 1
        self.conn, self._timeout = conn, timeout
        self.alive, self.suspect, self.closed = True, False, False

    def close(self) -> None:
        self.closed, self.alive = True, False


def _conn(i: int = 0, **kw):
    return stdio_connection(f"src{i}", "python", [f"server{i}.py"], **kw)


@pytest.fixture
def pool():
    _Fake.made = 0
    p = cl.SessionPool(max_idle=2, idle_timeout=60, factory=_Fake)
    yield p
    p.shutdown()


def test_a_released_session_is_reused_for_the_same_route(pool):
    with pool.lease(_conn()) as a:
        pass
    with pool.lease(_conn()) as b:
        pass
    assert a is b and _Fake.made == 1
    assert pool.stats["misses"] == 1 and pool.stats["hits"] == 1


def test_concurrent_leases_of_one_source_get_distinct_sessions(pool):
    with pool.lease(_conn()) as a, pool.lease(_conn()) as b:
        assert a is not b
    assert _Fake.made == 2


def test_a_changed_declaration_never_reuses_a_session(pool, monkeypatch):
    base = _conn()
    with pool.lease(base) as a:
        pass
    changed_args = stdio_connection("src0", "python", ["server0.py", "--other"])
    changed_env = stdio_connection("src0", "python", ["server0.py"], env={"MODE": "b"})
    other_id = stdio_connection("src9", "python", ["server0.py"])
    keys = {cl.route_key(c) for c in (base, changed_args, changed_env, other_id)}
    assert len(keys) == 4                        # every difference is a different route
    with pool.lease(changed_args) as b:
        assert b is not a


def test_a_dead_session_is_discarded_and_replaced(pool):
    with pool.lease(_conn()) as a:
        pass
    a.alive = False                              # the server exited while idle
    with pool.lease(_conn()) as b:
        pass
    assert b is not a and a.closed and pool.stats["discarded_dead"] == 1


def test_a_suspect_session_is_not_returned_to_the_pool(pool):
    with pool.lease(_conn()) as a:
        a.suspect = True                         # a call timed out: a late reply may be pending
    assert a.closed and pool.idle_count() == 0 and pool.stats["discarded_suspect"] == 1


def test_an_exception_that_killed_the_session_discards_it(pool):
    with pytest.raises(RuntimeError):
        with pool.lease(_conn()) as a:
            a.alive = False
            raise RuntimeError("boom")
    assert a.closed and pool.idle_count() == 0


def test_the_pool_is_bounded_lru(pool):
    leased = []
    for i in range(pool.max_idle + 2):
        key, c = pool.acquire(_conn(i))
        leased.append((key, c))
    for key, c in leased:
        pool.release(key, c)
    assert pool.idle_count() == pool.max_idle
    evicted = [c for _, c in leased if c.closed]
    assert len(evicted) == len(leased) - pool.max_idle
    assert evicted == [c for _, c in leased[:len(evicted)]]        # oldest first


def test_idle_sessions_expire():
    p = cl.SessionPool(max_idle=4, idle_timeout=0.05, factory=_Fake)
    try:
        with p.lease(_conn()) as a:
            pass
        time.sleep(0.1)
        assert p.sweep() == 1 and a.closed and p.idle_count() == 0
    finally:
        p.shutdown()


# --- real subprocess: the fixture server ---------------------------------------------------------
def _fixture_conn(root, cid="pooled"):
    return mi.SourceConnection.model_validate(fs_connection(cid, root))


def test_open_client_leases_a_warm_session_and_warm_is_faster(walnut_env, corpus, monkeypatch):
    monkeypatch.setenv("WALNUT_MCP_POOL", "1")
    root, _ = corpus
    conn = _fixture_conn(root)
    cl.close_pool()
    t0 = time.perf_counter()
    with cl.open_client(conn) as a:
        a.list_tools()
    cold = time.perf_counter() - t0
    t1 = time.perf_counter()
    with cl.open_client(conn) as b:
        b.list_tools()
    warm = time.perf_counter() - t1
    assert a is b and b.alive
    assert warm < cold                           # measured, not assumed: no spawn, no initialize
    # pooled=False really closes
    with cl.open_client(conn, pooled=False) as c:
        assert c is not a
    assert not c.alive
    cl.close_pool()
    assert not a.alive


def test_a_server_that_died_is_reconnected_on_next_lease(walnut_env, corpus, monkeypatch):
    monkeypatch.setenv("WALNUT_MCP_POOL", "1")
    root, _ = corpus
    conn = _fixture_conn(root, "dies")
    with cl.open_client(conn) as a:
        pass
    a.close()                                    # the process is gone; the pool still holds it
    with cl.open_client(conn) as b:
        assert b is not a and b.alive
        assert b.list_tools()
    cl.close_pool()


def test_check_connection_reports_cold_and_warm_latency(walnut_env, corpus):
    root, _ = corpus
    conn = _fixture_conn(root, "health")
    cold = cl.check_connection(conn, fresh=True)
    assert cold["ok"], cold["error"]
    assert cold["tools"] == len(cold["tool_names"]) > 0
    assert cold["read_only_tools"] < cold["tools"]           # write_file is counted as a writer
    warm1 = cl.check_connection(conn, fresh=False)
    warm2 = cl.check_connection(conn, fresh=False)
    assert warm1["ok"] and warm2["ok"]
    assert warm2["connect_ms"] < cold["connect_ms"]
    bad = stdio_connection("nope", sys.executable, ["-c", "import sys; sys.exit(3)"])
    many = cl.check_connections([conn, bad], fresh=True, max_workers=2)
    assert [r["id"] for r in many] == [conn.id, bad.id]
    assert many[0]["ok"] and not many[1]["ok"] and many[1]["error"]
    cl.close_pool()


def test_open_session_is_pooled_and_speaks_the_contract(walnut_env, corpus):
    root, _ = corpus
    conn = _fixture_conn(root, "asess")

    async def go():
        async with mi.open_session(conn) as s:
            tools = await s.list_tools()
            listing = await s.call_tool("list_directory", {"path": str(root)})
            return s.route, tools, listing

    route, tools, listing = asyncio.run(go())
    assert route == "direct"
    assert all(set(t) == {"name", "description", "input_schema", "annotations"} for t in tools)
    assert set(listing) == {"content", "structured", "is_error"} and not listing["is_error"]
    before = cl.get_pool().stats["hits"]
    asyncio.run(go())
    assert cl.get_pool().stats["hits"] == before + 1
    cl.close_pool()


# --- the read-only gate --------------------------------------------------------------------------
class _Recorder:
    def __init__(self, conn=None) -> None:
        self._conn = conn
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append(name)
        return cl.ToolResult(parts=())


def test_every_declared_deny_tool_is_refused_even_without_a_write_verb():
    from databridge_mcp_ingest.plans import WRITE_VERB, load_profiles

    declared = sorted({t for p in load_profiles() for t in p.deny_tools})
    assert declared, "no profile declares deny_tools — the check below would be vacuous"
    ro = cl.ReadOnlyClient(_Recorder())                 # no plan: every profile's list applies
    for name in declared:
        with pytest.raises(cl.McpClientError):
            ro.call_tool(name, {})
    # a name that no verb pattern catches is refused purely because it is declared
    quiet = "fetch_everything_quietly"
    assert not WRITE_VERB.search(quiet)
    ro2 = cl.ReadOnlyClient(_Recorder(), deny=[quiet])
    with pytest.raises(cl.McpClientError):
        ro2.call_tool(quiet, {})
    scoped = mi.SourceConnection.model_validate(
        {"id": "s", "transport": {"type": "stdio", "command": "x"},
         "scope": {"deny_tools": [quiet]}})
    inner = _Recorder(scoped)
    with pytest.raises(cl.McpClientError):
        cl.ReadOnlyClient(inner).call_tool(quiet, {})
    assert inner.calls == []                            # refused BEFORE the call
    cl.ReadOnlyClient(inner).call_tool("read_text_file", {})
    assert inner.calls == ["read_text_file"]


def test_a_blob_whose_declared_sha256_does_not_match_fails():
    data = b"%PDF-1.7 synthetic"
    good = {"type": "resource", "resource": {
        "uri": "file:///x.pdf", "mimeType": "application/pdf",
        "blob": base64.b64encode(data).decode(),
        "_meta": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}}}
    (part,) = cl.parts_from_dicts([good])
    assert part.data == data
    tampered = {**good, "resource": {**good["resource"],
                                     "_meta": {"sha256": hashlib.sha256(b"other").hexdigest()}}}
    with pytest.raises(cl.McpClientError, match="integrity"):
        cl.parts_from_dicts([tampered])
    short = {**good, "resource": {**good["resource"], "_meta": {"size": len(data) + 1}}}
    with pytest.raises(cl.McpClientError, match="integrity"):
        cl.parts_from_dicts([short])
