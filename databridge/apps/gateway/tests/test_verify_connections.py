"""`/evidence/verify` for pointers that did NOT come from the fixture estate.

Two kinds, each verified by the thing that can actually answer for it:

* a **capture-bound** pointer (anything ingestion minted with a `capture_digest`) — re-hash the
  captured bytes and read the cited span back (`source_capture.snippet_and_verify`, reused);
* a **`table_cell`** from a **registered connection** — re-read the cell LIVE through the
  connection's session (a fake gateway here: tests never start an MCP server) and compare.

Plus impact over a temp shared store's reverse index. Every expected value is derived from the
fixtures this file writes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
for _p in ("packages/adapter", "packages/core", "packages/graph", "packages/mcp_ingest"):
    _path = str(DATABRIDGE_ROOT / _p)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from databridge_gateway.verify_fleet import dispose_fleet  # noqa: E402
from databridge_gateway.verify_routes import register_verify_routes  # noqa: E402

from conftest import auth_headers  # noqa: E402

CONNECTION = "gamma-db"
DOC_TEXT = "Quillfeather Logistics pays every invoice on net-30 terms."


def _put(capture: Path, data: bytes, text: str | None = None,
         source: str | None = CONNECTION, *, authenticated: bool = True) -> str:
    """Write a capture the way ingestion does: bytes, optional text, and metadata carrying the
    `meta_digest` that binds it to the content address (`capture.meta_digest`, W1). With
    `authenticated=False` the meta is a legacy, undigested file."""
    from databridge_mcp_ingest.capture import meta_digest

    sha = hashlib.sha256(data).hexdigest()
    d = capture / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    (d / sha).write_bytes(data)
    if text is not None:
        (d / f"{sha}.txt").write_text(text, encoding="utf-8")
    meta = {"uri": "x://1"} | ({"source": source} if source else {})
    if authenticated:
        meta["meta_digest"] = meta_digest(sha, meta)
    (d / f"{sha}.json").write_text(json.dumps(meta), encoding="utf-8")
    return sha


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from databridge_mcp_ingest import register_connection

    capture, sources = tmp_path / "capture", tmp_path / "sources"
    monkeypatch.setenv("WALNUT_CAPTURE_DIR", str(capture))
    monkeypatch.setenv("WALNUT_SOURCES_DIR", str(sources))
    # R-019 round 2, finding 10: reading a connection needs the operator's grant. The default
    # test identity is granted every connection; tests of the refusal narrow it themselves.
    monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps({"staff": "*"}))
    register_connection({"id": CONNECTION, "transport": {"type": "stdio", "command": "never-run"}},
                        root=sources)
    return capture


class _Result:
    def __init__(self, text: str, is_error: bool = False):
        self.text, self.is_error, self.structured = text, is_error, None


class FakeGateway:
    """Stands in for the connection's MCP session. Records every statement it was sent."""

    def __init__(self, rows):
        self.rows, self.statements = rows, []

    def query(self, connection, sql):
        self.statements.append((connection, sql))
        return _Result(json.dumps(self.rows))


@pytest.fixture()
def app(env):
    a = FastAPI()
    register_verify_routes(a)
    yield a
    dispose_fleet(a)


@pytest.fixture()
def client(app):
    with TestClient(app, headers=auth_headers("staff")) as c:
        yield c


# ---------------------------------------------------------------------------------------------
# capture-bound pointers
# ---------------------------------------------------------------------------------------------
def _span_pointer(sha: str, needle: str) -> dict:
    start = DOC_TEXT.index(needle)
    return {"source_id": CONNECTION, "kind": "doc_span", "document_id": "item-1",
            "char_start": start, "char_end": start + len(needle),
            "capture_digest": sha, "trust": "untrusted"}


def test_a_capture_bound_pointer_verifies_as_of_capture(client, env):
    sha = _put(env, DOC_TEXT.encode(), DOC_TEXT)
    body = client.post("/evidence/verify",
                       json={"pointer": _span_pointer(sha, "net-30"), "value": "net-30"}).json()
    assert body["verdict"] == "verified_as_of_capture", body
    assert body["method"] == "capture"
    assert body["capture_check"]["verify"]["status"] == "verified"
    assert body["cited_value"] == "net-30"


def test_altered_capture_bytes_are_a_hash_mismatch_and_escalate(client, env):
    sha = _put(env, DOC_TEXT.encode(), DOC_TEXT)
    (env / sha[:2] / sha).write_bytes(b"something else entirely")
    body = client.post("/evidence/verify",
                       json={"pointer": _span_pointer(sha, "net-30")}).json()
    assert body["capture_check"]["verify"]["status"] == "hash_mismatch"
    assert body["verdict"] == "drift_material" and body["must_escalate"] is True


def test_a_span_that_no_longer_holds_the_value_is_value_not_in_span(client, env):
    sha = _put(env, DOC_TEXT.encode(), DOC_TEXT)
    body = client.post("/evidence/verify",
                       json={"pointer": _span_pointer(sha, "net-30"), "value": "net-60"}).json()
    assert body["capture_check"]["verify"]["status"] == "value_not_in_span"
    assert body["verdict"] == "drift_material"


# ---------------------------------------------------------------------------------------------
# table_cell pointers, re-read live through the connection
# ---------------------------------------------------------------------------------------------
SNAPSHOT = {"schema": "public", "table": "invoices", "primary_key": ["invoice_id"],
            "rows": [{"invoice_id": 7, "amount": "120.00", "status": "open"}]}


def _cell_pointer(env, snapshot: dict | None = None, column: str = "amount",
                  source: str | None = CONNECTION) -> tuple[dict, str, Path]:
    snapshot = snapshot or SNAPSHOT
    data = json.dumps(snapshot, sort_keys=True).encode()
    sha = _put(env, data, source=source)
    row = snapshot["rows"][0]
    pk = ",".join(str(row[c]) for c in snapshot["primary_key"])     # ingestion's own row-id rule
    ptr = {"source_id": CONNECTION, "kind": "table_cell",
           "table": f"{snapshot['schema']}.{snapshot['table']}", "column": column,
           "row_id": pk, "capture_digest": sha, "trust": "untrusted"}
    return ptr, sha, env / sha[:2] / sha


def test_a_table_cell_that_still_holds_its_value_is_verified_live(client, app, env):
    ptr, _, _ = _cell_pointer(env)
    cited = SNAPSHOT["rows"][0]["amount"]
    app.state.source_gateway = FakeGateway([{"amount": str(float(cited))}])  # 120.0 == 120.00
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "verified", body
    assert body["method"] == "live_source"
    assert body["cited_value"] == cited and body["current_value_known"] is True
    # the statement names only identifiers taken from the CAPTURED snapshot
    (conn, sql), = app.state.source_gateway.statements
    assert conn == CONNECTION
    assert '"invoices"' in sql and '"amount"' in sql and "'7'" in sql


def test_a_changed_cell_is_drift_with_old_and_new_and_nothing_is_updated(client, app, env):
    ptr, sha, raw = _cell_pointer(env)
    before = raw.read_bytes()
    cited = SNAPSHOT["rows"][0]["amount"]
    changed = "150.00"
    assert changed != cited
    app.state.source_gateway = FakeGateway([{"amount": changed}])
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "drift_material" and body["must_escalate"] is True
    assert body["cited_value"] == cited and body["current_value"] == changed
    assert raw.read_bytes() == before, "a drift must never rewrite the cited evidence"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == sha


def test_a_row_the_source_no_longer_returns_has_vanished(client, app, env):
    ptr, _, _ = _cell_pointer(env)
    app.state.source_gateway = FakeGateway([])
    assert client.post("/evidence/verify", json={"pointer": ptr}).json()["verdict"] == "vanished"


def test_an_unreachable_source_falls_back_to_the_capture_and_says_so(client, app, env):
    ptr, _, _ = _cell_pointer(env)

    class Down:
        def query(self, connection, sql):
            raise ConnectionError("server not running")

    app.state.source_gateway = Down()
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "verified_as_of_capture"
    assert body["live_check"]["attempted"] is True and body["live_check"]["error"]


def test_an_unregistered_source_is_not_reread_live(client, app, env):
    ptr, _, _ = _cell_pointer(env, source="not-registered")
    ptr["source_id"] = "not-registered"
    gw = FakeGateway([{"amount": "999"}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == [], "only a registered connection is ever asked"
    assert body["verdict"] == "verified_as_of_capture"


def test_a_capture_from_another_source_vouches_for_nothing(client, app, env):
    """R-019 finding 9 (capture path): this test used to re-label a `gamma-db` capture as
    `not-registered` and expect VERIFIED_AS_OF_CAPTURE — bytes source A produced, vouching for a
    citation of source B. The capture's own meta names who produced it."""
    ptr, _, _ = _cell_pointer(env)                       # captured by CONNECTION
    ptr["source_id"] = "not-registered"
    gw = FakeGateway([{"amount": "999"}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == []
    assert body["verdict"] == "unverifiable", body
    assert body["capture_check"]["status"] == "source_mismatch"


# ---------------------------------------------------------------------------------------------
# impact over the shared store's reverse index
# ---------------------------------------------------------------------------------------------
def test_impact_is_read_from_the_shared_stores_reverse_index(client, app, env):
    from databridge_core import SourcePointer
    from databridge_graph import GraphNode, GraphStore
    from databridge_graph.kinds import NodeKind

    ptr, _, _ = _cell_pointer(env)
    pointer = SourcePointer.model_validate(ptr)
    store = GraphStore()
    t0 = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    kind = next(iter(NodeKind))
    store.add_node(GraphNode(id="n:cites", kind=kind, label="cites the cell",
                             sources=[pointer], valid_from=t0, system_from=t0))
    app.state.graph_store = store
    body = client.post("/evidence/impact", json={"pointer": ptr}).json()
    impact = body["impact"]
    assert impact["answer"] in ("known", "unknown")
    assert "n:cites" in {r["entity_id"] for r in impact["records"]}


# ---------------------------------------------------------------------------------------------
# R-019 (Codex batch 2): what can no longer produce a false verdict
# ---------------------------------------------------------------------------------------------
def test_a_registered_connection_is_never_verified_against_the_fixture_estate(
        client, app, env, tmp_path):
    """Finding 1: a registered connection that shares a source id with a fixture adapter
    (`core_banking`), cited by a pointer with no capture binding, fell through to the synthetic
    finance adapter and was verdicted against FIXTURE rows."""
    from databridge_mcp_ingest import register_connection

    from databridge_gateway.verify_fleet import READS

    fixture_source = READS[0].source_id                  # a source id the fixture fleet serves
    register_connection({"id": fixture_source,
                         "transport": {"type": "stdio", "command": "never-run"}},
                        root=tmp_path / "sources")
    ptr = {"source_id": fixture_source, "kind": "table_cell", "table": "x.facility",
           "column": "limit_amount", "row_id": "1", "trust": "trusted"}
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body.get("method") != "adapter", body
    assert body["verdict"] == "unverifiable"
    assert "registered connection" in body["note"]


def test_a_text_identifier_losing_its_leading_zeros_is_drift(client, app, env):
    """Finding 8: `_same_value` compared every decimal-looking string as a number, so `00123` →
    `123` in a text column verified as unchanged."""
    snap = {"schema": "public", "table": "accounts", "primary_key": ["id"],
            "columns": [{"name": "id", "type": "integer"}, {"name": "code", "type": "text"}],
            "rows": [{"id": 1, "code": "00123"}]}
    ptr, _, _ = _cell_pointer(env, snap, column="code")
    app.state.source_gateway = FakeGateway([{"code": "123"}])
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "drift_material", body


def test_leading_zeros_are_significant_even_with_no_declared_type(client, app, env):
    snap = {"schema": "public", "table": "accounts", "primary_key": ["id"],
            "rows": [{"id": 1, "code": "00123"}]}
    ptr, _, _ = _cell_pointer(env, snap, column="code")
    app.state.source_gateway = FakeGateway([{"code": "123"}])
    assert client.post("/evidence/verify", json={"pointer": ptr}).json()["verdict"] == \
        "drift_material"


def test_a_numeric_column_still_compares_as_numbers(client, app, env):
    """The other direction: a declared numeric column re-rendered is not a drift."""
    snap = {"schema": "public", "table": "invoices", "primary_key": ["invoice_id"],
            "columns": [{"name": "amount", "type": "numeric(12,2)"}],
            "rows": [{"invoice_id": 7, "amount": "0120.00"}]}
    ptr, _, _ = _cell_pointer(env, snap)
    app.state.source_gateway = FakeGateway([{"amount": 120}])
    assert client.post("/evidence/verify", json={"pointer": ptr}).json()["verdict"] == "verified"


def test_a_live_row_without_the_cited_column_is_unverifiable(client, app, env):
    """Finding 8, second half: a one-field row under ANOTHER name was taken as the current
    value."""
    ptr, _, _ = _cell_pointer(env)
    cited = SNAPSHOT["rows"][0]["amount"]
    app.state.source_gateway = FakeGateway([{"something_else": cited}])
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "unverifiable", body
    assert body["current_value_known"] is False


def test_a_capture_from_another_connection_is_never_used_to_query_this_one(
        client, app, env, tmp_path):
    """Finding 9: a digest captured from registered source A, cited under registered source B,
    took A's table/key/column and ran them against B."""
    from databridge_mcp_ingest import register_connection

    other = "delta-db"
    register_connection({"id": other, "transport": {"type": "stdio", "command": "never-run"}},
                        root=tmp_path / "sources")
    ptr, _, _ = _cell_pointer(env, source=other)          # captured BY delta-db
    assert ptr["source_id"] == CONNECTION                  # ...cited as gamma-db
    gw = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == [], "no statement may be sent on another source's capture"
    assert body["verdict"] == "unverifiable", body


def test_a_capture_that_records_no_source_is_not_used_to_query(client, app, env):
    """Round 2, finding 8: an unbound capture used to still earn VERIFIED_AS_OF_CAPTURE. It
    vouches for nothing about THIS source, so it is UNVERIFIABLE — and no statement is sent."""
    ptr, _, _ = _cell_pointer(env, source=None)
    gw = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == []
    assert body["verdict"] == "unverifiable", body
    assert body["capture_check"]["status"] == "capture_unbound"


@pytest.mark.parametrize("key", ["7\' OR 1=1 --", "7' OR '1'='1", "7\x00"])
def test_a_key_that_cannot_be_written_dialect_safely_is_never_sent(client, app, env, key):
    """Finding 10: `'` doubling is not an escape in a backslash-escaping dialect, so a captured
    key like `7\' OR 1=1 --` broke out of its literal."""
    snap = {"schema": "public", "table": "invoices", "primary_key": ["invoice_id"],
            "rows": [{"invoice_id": key, "amount": "120.00"}]}
    ptr, _, _ = _cell_pointer(env, snap)
    gw = FakeGateway([{"amount": "120.00"}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == [], gw.statements
    assert body["verdict"] == "verified_as_of_capture"
    assert body["live_check"]["declined"] == "unsafe_key_literal"


def test_every_capture_check_status_has_a_named_verdict():
    """F6 added `text_unbound`; it reached the lattice only through the unknown-status fallback.
    The statuses are READ from `snippet_and_verify`'s own source, so a status added there later
    fails here until it is given a verdict on purpose."""
    import inspect
    import re

    from databridge_gateway import source_capture
    from databridge_gateway.verify_fleet import _CAPTURE_STATUS_VERDICT

    code = inspect.getsource(source_capture.snippet_and_verify)
    statuses = set(re.findall(r'"status"\]?\s*[=:]\s*"([a-z_]+)"', code))
    statuses |= set(re.findall(r'\belse "([a-z_]+)"', code))     # `... if cond else "no_text"`
    assert {"verified", "text_unbound", "capture_missing", "no_text",
            "span_out_of_range"} <= statuses, statuses
    missing = sorted(statuses - set(_CAPTURE_STATUS_VERDICT))
    assert missing == [], f"capture statuses with no named verdict: {missing}"
    assert _CAPTURE_STATUS_VERDICT["text_unbound"] == "UNVERIFIABLE"


def test_an_unbound_text_span_is_unverifiable_end_to_end(client, env):
    data = DOC_TEXT.encode()
    sha = _put(env, data, text=DOC_TEXT.replace("Quillfeather", "Quillfeathex"))
    body = client.post("/evidence/verify",
                       json={"pointer": _span_pointer(sha, "net-30")}).json()
    assert body["capture_check"]["verify"]["status"] == "text_unbound", body["capture_check"]
    assert body["verdict"] == "unverifiable"
    assert body["is_positive"] is False and body["must_escalate"] is False


# ---------------------------------------------------------------------------------------------
# R-019 ROUND 2 (Codex gpt-5.6-sol): the findings that were still reachable after round 1
# ---------------------------------------------------------------------------------------------
def _fixture_source() -> str:
    from databridge_gateway.verify_fleet import READS

    assert READS, "no evidence reads are declared — the tests below would be vacuous"
    return READS[0].source_id


def test_an_invalid_declaration_still_reserves_its_connection_id(client, app, env, tmp_path):
    """Round 2 finding 2: only a declaration that PARSED counted as registered, so a broken
    `<id>.yaml` let that id's pointers fall through to the fixture estate."""
    from databridge_gateway.verify_fleet import registered_connection_ids

    fixture_source = _fixture_source()
    (tmp_path / "sources" / f"{fixture_source}.yaml").write_text(
        "this: [is not, a valid connection", encoding="utf-8")
    assert fixture_source in registered_connection_ids()
    ptr = {"source_id": fixture_source, "kind": "table_cell", "table": "x.y",
           "column": "c", "row_id": "1", "trust": "trusted"}
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body.get("method") != "adapter", body
    assert body["verdict"] == "unverifiable"


@pytest.mark.parametrize("a,b,col_type,same", [
    (" ABC ", "ABC", "text", False),          # nothing is stripped
    (" ABC ", "ABC", None, False),
    ("00123", 123, None, False),              # one side a number is not enough
    ("00123", "123", None, False),
    ("120.00", "120.0", None, True),          # two canonical numbers
    ("120.00", 120, None, True),
    ("0120.00", 120, "numeric(12,2)", True),  # a declared numeric column
    ("7", 7, "text", True),                   # same text, whatever the Python type
    (True, "True", None, False),
])
def test_same_value_is_never_more_generous_than_the_source(a, b, col_type, same):
    """Round 2 finding 6."""
    from databridge_gateway.verify_fleet import _same_value

    assert _same_value(a, b, col_type) is same


def test_trailing_whitespace_lost_on_the_live_source_is_drift(client, app, env):
    snap = {"schema": "public", "table": "accounts", "primary_key": ["id"],
            "columns": [{"name": "id", "type": "integer"}, {"name": "code", "type": "text"}],
            "rows": [{"id": 1, "code": " ABC "}]}
    ptr, _, _ = _cell_pointer(env, snap, column="code")
    app.state.source_gateway = FakeGateway([{"code": "ABC"}])
    assert client.post("/evidence/verify", json={"pointer": ptr}).json()["verdict"] == \
        "drift_material"


def test_a_padded_text_value_against_a_live_integer_is_drift(client, app, env):
    snap = {"schema": "public", "table": "accounts", "primary_key": ["id"],
            "rows": [{"id": 1, "code": "00123"}]}
    ptr, _, _ = _cell_pointer(env, snap, column="code")
    app.state.source_gateway = FakeGateway([{"code": 123}])
    assert client.post("/evidence/verify", json={"pointer": ptr}).json()["verdict"] == \
        "drift_material"


def test_the_offered_value_is_checked_on_the_live_path_before_any_read(client, app, env):
    """Round 2 finding 7: capture and live both held the cited value and a request offering
    another one got VERIFIED."""
    ptr, _, _ = _cell_pointer(env)
    cited = SNAPSHOT["rows"][0]["amount"]
    offered = "999"
    assert offered != cited
    gw = FakeGateway([{"amount": cited}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr, "value": offered}).json()
    assert body["verdict"] == "drift_material", body
    assert gw.statements == [], "a mismatch is decided from the capture; nothing is sent"
    ok = client.post("/evidence/verify", json={"pointer": ptr, "value": cited}).json()
    assert ok["verdict"] == "verified", ok


def test_an_explicit_null_is_a_value_and_omitting_one_is_not(client, app, env):
    ptr, _, _ = _cell_pointer(env)
    cited = SNAPSHOT["rows"][0]["amount"]
    assert cited is not None
    app.state.source_gateway = FakeGateway([{"amount": cited}])
    null = client.post("/evidence/verify", json={"pointer": ptr, "value": None}).json()
    assert null["verdict"] == "drift_material", null
    omitted = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert omitted["verdict"] == "verified", omitted


def test_an_unauthenticated_legacy_meta_binds_no_source(client, app, env):
    """Round 2 finding 8: the meta sidecar is only evidence of a source when its `meta_digest`
    binds it to the content address. A legacy (undigested) file naming the right source is
    exactly what somebody writing the sidecar by hand would produce."""
    data = json.dumps(SNAPSHOT, sort_keys=True).encode()
    ptr, sha, _ = _cell_pointer(env)
    assert hashlib.sha256(data).hexdigest() == sha
    _put(env, data, source=CONNECTION, authenticated=False)        # overwrite the meta, undigested
    gw = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == []
    assert body["verdict"] == "unverifiable", body
    assert body["capture_check"]["status"] == "capture_unbound"


def test_a_sighting_is_not_a_source_binding(client, app, env):
    """`sightings` are outside the meta digest, so appending one relabels nothing."""
    from databridge_mcp_ingest.capture import meta_digest

    ptr, sha, _ = _cell_pointer(env, source="someone-else")
    meta_file = env / sha[:2] / f"{sha}.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["sightings"] = [{"source": CONNECTION, "uri": "x://2"}]
    assert meta["meta_digest"] == meta_digest(sha, meta), "premise: sightings are undigested"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    gw = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == [] and body["verdict"] == "unverifiable", body


def test_a_relabelled_meta_file_is_corrupt_not_bound(client, app, env):
    ptr, sha, _ = _cell_pointer(env, source="someone-else")
    meta_file = env / sha[:2] / f"{sha}.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["source"] = CONNECTION                                # edited after the digest
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    gw = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert gw.statements == []
    assert body["verdict"] == "unverifiable", body
    assert body["capture_check"]["status"] == "capture_meta_corrupt"


def test_a_table_with_no_primary_key_still_needs_a_bound_capture(client, app, env):
    """The no-primary-key branch returned VERIFIED_AS_OF_CAPTURE before the binding check."""
    snap = {"schema": "public", "table": "log", "primary_key": [],
            "rows": [{"msg": "hello"}]}
    data = json.dumps(snap, sort_keys=True).encode()
    sha = _put(env, data, source=None)
    ptr = {"source_id": CONNECTION, "kind": "table_cell", "table": "public.log",
           "column": "msg", "row_id": "#0", "capture_digest": sha, "trust": "untrusted"}
    app.state.source_gateway = FakeGateway([])
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "unverifiable", body


def test_an_unregistered_sources_unbound_table_capture_is_unverifiable(client, app, env):
    ptr, _, _ = _cell_pointer(env, source=None)
    ptr["source_id"] = "not-registered"
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "unverifiable", body


@pytest.mark.parametrize("capture_source", ["someone-else", None])
def test_a_document_span_needs_a_capture_bound_to_its_source(client, env, capture_source):
    """Finding 8: non-table captures never compared the capture's source with the pointer's."""
    sha = _put(env, DOC_TEXT.encode(), DOC_TEXT, source=capture_source)
    body = client.post("/evidence/verify",
                       json={"pointer": _span_pointer(sha, "net-30"), "value": "net-30"}).json()
    assert body["verdict"] == "unverifiable", body
    assert body["cited_value"] is None, "nothing from an unbound capture is shown as cited"


def test_an_ambiguous_composite_key_is_unverifiable_and_sends_nothing(client, app, env):
    """Round 2 finding 12: keys ("x,y","z") and ("x","y,z") both encode as "x,y,z"."""
    snap = {"schema": "public", "table": "pairs", "primary_key": ["a", "b"],
            "rows": [{"a": "x,y", "b": "z", "v": "first"},
                     {"a": "x", "b": "y,z", "v": "second"}]}
    data = json.dumps(snap, sort_keys=True).encode()
    sha = _put(env, data)
    encoded = {",".join(str(r[c]) for c in snap["primary_key"]) for r in snap["rows"]}
    assert len(encoded) == 1, "premise: the two keys collide under the ingestion rule"
    ptr = {"source_id": CONNECTION, "kind": "table_cell", "table": "public.pairs",
           "column": "v", "row_id": encoded.pop(), "capture_digest": sha, "trust": "untrusted"}
    gw = FakeGateway([{"v": "first"}])
    app.state.source_gateway = gw
    body = client.post("/evidence/verify", json={"pointer": ptr}).json()
    assert body["verdict"] == "unverifiable", body
    assert gw.statements == []


def test_live_errors_and_the_statement_never_reach_the_caller(client, app, env):
    """Round 2 finding 13."""
    secret = "db.internal.example:5432 password=hunter2"
    ptr, _, _ = _cell_pointer(env)

    class Down:
        def query(self, connection, sql):
            raise ConnectionError(secret)

    app.state.source_gateway = Down()
    r = client.post("/evidence/verify", json={"pointer": ptr})
    body = r.json()
    assert "hunter2" not in r.text and "SELECT" not in r.text
    assert body["live_check"]["error"] == "SOURCE_READ_FAILED"
    assert body["live_check"]["ref"].startswith("ERR-")
    assert "statement" not in body["live_check"]

    class Refuses:
        def query(self, connection, sql):
            return _Result(f"permission denied: {secret}", is_error=True)

    app.state.source_gateway = Refuses()
    r = client.post("/evidence/verify", json={"pointer": ptr})
    assert "hunter2" not in r.text and "SELECT" not in r.text
    assert r.json()["live_check"]["error"] == "SOURCE_RETURNED_ERROR"


@pytest.mark.parametrize("grants", [None, {"somebody-else": "*"}, {"staff": ["other-db"]}])
def test_an_authenticated_identity_without_a_grant_reads_nothing(client, app, env, monkeypatch,
                                                                grants):
    """Round 2 finding 10: `principal_id` was only recorded; any signed-in caller could have a
    connection's cell re-read live and its captured values returned."""
    if grants is None:
        monkeypatch.delenv("WALNUT_FEDERATED_GRANTS", raising=False)
    else:
        monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps(grants))
    ptr, _, _ = _cell_pointer(env)
    gw = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    app.state.source_gateway = gw
    r = client.post("/evidence/verify", json={"pointer": ptr})
    assert r.status_code == 403, r.text
    assert r.json()["refusal"]["code"] == "NOT_GRANTED"
    assert gw.statements == [] and SNAPSHOT["rows"][0]["amount"] not in r.text
    sha = _put(env, DOC_TEXT.encode(), DOC_TEXT)
    span = client.post("/evidence/verify", json={"pointer": _span_pointer(sha, "net-30")})
    assert span.status_code == 403 and "net-30" not in span.text


def test_a_grant_naming_the_connection_admits_it(client, app, env, monkeypatch):
    monkeypatch.setenv("WALNUT_FEDERATED_GRANTS", json.dumps({"staff": [CONNECTION]}))
    ptr, _, _ = _cell_pointer(env)
    app.state.source_gateway = FakeGateway([{"amount": SNAPSHOT["rows"][0]["amount"]}])
    assert client.post("/evidence/verify", json={"pointer": ptr}).json()["verdict"] == "verified"
