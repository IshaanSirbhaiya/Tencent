"""FINALS tools for the walnut-internal-brain and walnut-ib-audit MCP servers.

Covers the new tools added in CodeBuddy session CB8 (written by CodeBuddy; reviewed by Claude,
who fixed the sink attachment in the tamper fixture and tightened (b) and (h)):
  (a) no new tool's input schema has an identity argument,
  (b) after ask_internal_brain, my_answer_receipt(<that request id>) returns a receipt with the
      question and at least one stage,
  (c) my_answer_receipt for an unknown id and for ANOTHER person's real request id return the
      identical fixed sentence,
  (d) "..", "", "." and a 201-character id are refused without a request,
  (e) what_can_i_open returns counts and never a document id,
  (f) org_activity_summary and run_tamper_demo with an employee token return a forbidden error,
      and with a compliance-officer token succeed,
  (g) org_activity_summary(0) and (3651) are refused without a request,
  (h) run_tamper_demo reports "real log modified: no" and the live ledger still verifies after it
      (the app is built with a ledger sink as test_tamper_demo_route.py does),
  (i) a malformed JSON 200 response gives the sanitized error for each new tool.
"""
from __future__ import annotations

import asyncio
import re

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib import mcp_server, audit_mcp_server
from databridge_ib.app import Services, create_app
from databridge_ib.audit.anchor import FileSink, publish_head
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.audit.routes import make_audit_router
from databridge_ib.config import IbConfig
from databridge_ib.index import Index
from databridge_ib.ingest import Ingestor
from databridge_ib.pipeline import Pipeline

# reuse the in-process app builder, routing harness and the _call_tool / _tool_schemas helpers
from test_mcp_server import (_call_tool, _svc, _build_app, _Routed, IDENTITY_ARGS)

NOT_FOUND_RECEIPT = "No receipt found for that request id in your own requests."


class _RoutedFresh:
    """Like _Routed but _client() returns a fresh TestClient on every call (so a tool that
    closes its client does not break the next tool), and accepts the optional ``timeout``
    argument audit_mcp_server._client now takes."""

    def __init__(self, monkeypatch, app, token, mod):
        self.app = app
        self.token = token
        monkeypatch.setattr(mod, "_token", lambda: self.token)
        monkeypatch.setattr(mod, "_base_url", lambda: "")
        monkeypatch.setattr(mod, "_client", lambda *a, **k: TestClient(app))


def _tool_schemas(server) -> dict[str, dict]:
    tools = asyncio.run(server.list_tools())
    return {t.name: t.inputSchema for t in tools}


# --------------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------------
@pytest.fixture
def routed(tmp_path, monkeypatch):
    """IB app in-process + a minted employee token, with MCP HTTP routed to it."""
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    mods = [_RoutedFresh(monkeypatch, app, tok, mcp_server),
            _RoutedFresh(monkeypatch, app, tok, audit_mcp_server)]
    return mods, svc


@pytest.fixture
def routed_co(tmp_path, monkeypatch):
    """IB app in-process + a minted compliance-officer token, with MCP HTTP routed to it."""
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "sara.sec", ["employee", "compliance_officer"])
    mods = [_RoutedFresh(monkeypatch, app, tok, mcp_server),
            _RoutedFresh(monkeypatch, app, tok, audit_mcp_server)]
    return mods, svc


def _build_app_with_sink(tmp_path):
    """Like _build_app but the audit router is built with a FileSink (for the tamper demo)."""
    svc = _svc(tmp_path)
    sink = FileSink(tmp_path / "ext" / "heads.jsonl")
    # rebuild the ledger with the external sink so publish_head + tamper have an external head.
    # The _svc ledger is empty; seed one record so publish_head has a head to publish.
    import datetime as _dt
    from databridge_ib.models import AuditQueryEvent
    svc.ledger.external = sink
    svc.ledger.append(AuditQueryEvent(sub="sara.sec", filter={"op": "seed"}, result_count=0,
                                      event_time=_dt.datetime.now(_dt.timezone.utc)))
    publish_head(svc.ledger, sink)

    def audit_factory(current, require_role):
        return make_audit_router(svc.ledger, svc.answers, svc.projection, require_role,
                                 sink=sink,
                                 clock=lambda: __import__("datetime").datetime.now(
                                     __import__("datetime").timezone.utc))

    return create_app(svc, audit_router_factory=audit_factory), svc, sink


@pytest.fixture
def routed_co_sink(tmp_path, monkeypatch):
    """IB app with a ledger sink + compliance-officer token (for the tamper-demo test)."""
    app, svc, sink = _build_app_with_sink(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "sara.sec", ["employee", "compliance_officer"])
    mods = [_RoutedFresh(monkeypatch, app, tok, mcp_server),
            _RoutedFresh(monkeypatch, app, tok, audit_mcp_server)]
    return mods, svc, sink


# --------------------------------------------------------------------------------------------
# (a) no new tool's input schema has an identity argument
# --------------------------------------------------------------------------------------------
def test_internal_brain_new_tools_have_no_identity_argument():
    schemas = _tool_schemas(mcp_server.build_server())
    assert {"my_answer_receipt", "what_can_i_open"} <= set(schemas)
    for name in ("my_answer_receipt", "what_can_i_open"):
        props = set(schemas[name].get("properties", {}).keys())
        assert not (props & IDENTITY_ARGS), f"{name} exposes identity args: {props}"


def test_audit_new_tools_have_no_identity_argument():
    schemas = _tool_schemas(audit_mcp_server.build_server())
    assert {"org_activity_summary", "run_tamper_demo"} <= set(schemas)
    for name in ("org_activity_summary", "run_tamper_demo"):
        props = set(schemas[name].get("properties", {}).keys())
        assert not (props & IDENTITY_ARGS), f"{name} exposes identity args: {props}"
    # audit_verify takes no arguments at all
    assert set(schemas["audit_verify"].get("properties", {}).keys()) == set()


# --------------------------------------------------------------------------------------------
# (b) after ask_internal_brain, my_answer_receipt(<that request id>) returns a receipt
# --------------------------------------------------------------------------------------------
def test_my_answer_receipt_returns_receipt_for_own_request(routed):
    mods, svc = routed
    server = mcp_server.build_server()
    ask_result = _call_tool(server, "ask_internal_brain",
                            {"question": "what is the database migration status and blockers?"})
    assert isinstance(ask_result, str)
    # the ask result now prints the request id
    m = re.search(r"request id: ([0-9a-f]+)", ask_result)
    assert m, f"ask_internal_brain did not print a request id: {ask_result}"
    req_id = m.group(1)
    receipt = _call_tool(server, "my_answer_receipt", {"request_id": req_id})
    assert isinstance(receipt, str)
    assert "Question:" in receipt
    assert "migration" in receipt.lower()
    assert "Stages:" in receipt  # at least one stage
    # the MCP call is a bearer call with the workbuddy-mcp label, and the receipt shows it
    assert "Channel: workbuddy-mcp" in receipt


# --------------------------------------------------------------------------------------------
# (c) unknown id and ANOTHER person's real request id return the identical fixed sentence
# --------------------------------------------------------------------------------------------
def test_my_answer_receipt_unknown_id_returns_fixed_sentence(routed):
    mods, svc = routed
    server = mcp_server.build_server()
    receipt = _call_tool(server, "my_answer_receipt",
                         {"request_id": "no-such-request-id-12345"})
    assert receipt == NOT_FOUND_RECEIPT, (
        f"expected the fixed not-found sentence, got: {receipt}")


def test_my_answer_receipt_other_person_id_returns_same_sentence(routed_co, monkeypatch):
    """Another person's real request id must be indistinguishable from an unknown id."""
    mods, svc = routed_co
    ib_server = mcp_server.build_server()
    # sara.sec (the officer) asks a question — that request belongs to sara.sec
    ask_result = _call_tool(ib_server, "ask_internal_brain",
                            {"question": "what is the database migration status and blockers?"})
    m = re.search(r"request id: ([0-9a-f]+)", ask_result)
    assert m
    sara_req_id = m.group(1)

    # now switch mcp_server to act as jdoe (employee) against the same app, and ask for sara's
    # request id — it must look indistinguishable from an unknown id
    tok_jdoe = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    app = mods[0].app
    monkeypatch.setattr(mcp_server, "_token", lambda: tok_jdoe)
    monkeypatch.setattr(mcp_server, "_base_url", lambda: "")
    monkeypatch.setattr(mcp_server, "_client", lambda *a, **k: TestClient(app))
    server = mcp_server.build_server()
    receipt = _call_tool(server, "my_answer_receipt", {"request_id": sara_req_id})
    assert receipt == NOT_FOUND_RECEIPT, (
        f"another person's id must be indistinguishable from unknown, got: {receipt}")


# --------------------------------------------------------------------------------------------
# (d) "..", "", "." and a 201-character id are refused without a request
# --------------------------------------------------------------------------------------------
def test_my_answer_receipt_dotdot_refused_without_request(routed, monkeypatch):
    mods, svc = routed
    request_made = []

    class _TrackingClient(httpx.Client):
        def __init__(self):
            super().__init__()

        def get(self, url, **kwargs):
            request_made.append(("GET", url))
            return super().get(url, **kwargs)

    monkeypatch.setattr(mcp_server, "_client", lambda *a, **k: _TrackingClient())
    server = mcp_server.build_server()
    for bad in ("..", "", ".", "x" * 201):
        request_made.clear()
        result = _call_tool(server, "my_answer_receipt", {"request_id": bad})
        assert result == NOT_FOUND_RECEIPT, f"bad id {bad!r} should give the fixed sentence"
        assert not request_made, f"bad id {bad!r} made a request: {request_made}"


# --------------------------------------------------------------------------------------------
# (e) what_can_i_open returns counts and never a document id
# --------------------------------------------------------------------------------------------
def test_what_can_i_open_returns_counts_no_doc_ids(routed):
    mods, svc = routed
    server = mcp_server.build_server()
    result = _call_tool(server, "what_can_i_open", {})
    assert isinstance(result, str)
    assert "item(s)" in result
    assert "container(s)" in result
    assert "platform(s)" in result
    # the map lists synthetic docs like slack:t1 / confluence:cf-*; none of those ids may appear
    for doc_id in ("slack:t1", "confluence:cf-", "jira:PAY-", "drive:"):
        assert doc_id not in result, f"document id leaked into the map summary: {doc_id}"


# --------------------------------------------------------------------------------------------
# (f) org_activity_summary and run_tamper_demo with an employee token -> forbidden;
#     with a compliance-officer token -> succeed
# --------------------------------------------------------------------------------------------
def test_org_activity_summary_forbidden_for_employee(routed):
    mods, svc = routed
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "org_activity_summary", {})
    assert "403" in result, f"expected 403 forbidden for employee, got: {result}"


def test_run_tamper_demo_forbidden_for_employee(routed):
    mods, svc = routed
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "run_tamper_demo", {})
    assert "403" in result, f"expected 403 forbidden for employee, got: {result}"


def test_org_activity_summary_succeeds_for_officer(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "org_activity_summary", {})
    assert isinstance(result, str)
    assert "403" not in result
    assert "Window:" in result
    assert "Requests:" in result
    assert "Documents:" in result
    assert "Ledger:" in result


def test_run_tamper_demo_succeeds_for_officer(routed_co_sink):
    mods, svc, sink = routed_co_sink
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "run_tamper_demo", {})
    assert isinstance(result, str)
    assert "403" not in result
    assert "Records copied:" in result
    assert "Real log modified:" in result


# --------------------------------------------------------------------------------------------
# (g) org_activity_summary(0) and (3651) are refused without a request
# --------------------------------------------------------------------------------------------
def test_org_activity_summary_days_out_of_range_refused_without_request(routed_co, monkeypatch):
    mods, svc = routed_co
    request_made = []

    class _TrackingClient(httpx.Client):
        def __init__(self):
            super().__init__()

        def get(self, url, **kwargs):
            request_made.append(("GET", url))
            return super().get(url, **kwargs)

    monkeypatch.setattr(audit_mcp_server, "_client", lambda *a, **k: _TrackingClient())
    server = audit_mcp_server.build_server()
    for bad in (0, 3651):
        request_made.clear()
        result = _call_tool(server, "org_activity_summary", {"days": bad})
        assert "must be between" in result.lower(), (
            f"days={bad} should be rejected, got: {result}")
        assert not request_made, f"days={bad} made a request: {request_made}"


# --------------------------------------------------------------------------------------------
# (h) run_tamper_demo reports "real log modified: no" and the live ledger still verifies
# --------------------------------------------------------------------------------------------
def test_run_tamper_demo_real_log_not_modified_and_ledger_verifies(routed_co_sink):
    mods, svc, sink = routed_co_sink
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "run_tamper_demo", {})
    assert isinstance(result, str)
    assert "Real log modified: no" in result, (
        f"expected 'real log modified: no', got: {result}")
    # one line per scenario
    scenario_lines = [ln for ln in result.splitlines() if ln.startswith("  - ")]
    assert len(scenario_lines) >= 1, f"expected at least one scenario line, got: {result}"
    # every scenario behaved as expected (the baseline is not flagged, every tamper is)
    assert all("as_expected=yes" in ln for ln in scenario_lines), result
    assert any("expected=not flagged, detected=no" in ln for ln in scenario_lines), result
    # the live ledger still verifies after the demo
    assert svc.ledger.verify().ok, "the live ledger does not verify after the tamper demo"


# --------------------------------------------------------------------------------------------
# (i) a malformed JSON 200 response gives the sanitized error for each new tool
# --------------------------------------------------------------------------------------------
class _MalformedClient(httpx.Client):
    """An httpx.Client that returns malformed HTML 200 for all requests."""

    def __init__(self):
        super().__init__()

    def get(self, url, **kwargs):
        return httpx.Response(200, content="<html>not json</html>",
                              headers={"content-type": "text/html"})

    def post(self, url, **kwargs):
        return httpx.Response(200, content="<html>not json</html>",
                              headers={"content-type": "text/html"})


def test_malformed_json_gives_sanitized_error_for_new_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setenv("IB_URL", "http://127.0.0.1:8100")
    monkeypatch.setattr(mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_server, "_base_url", lambda: "")
    monkeypatch.setattr(mcp_server, "_client", lambda *a, **k: _MalformedClient())
    monkeypatch.setattr(audit_mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(audit_mcp_server, "_base_url", lambda: "")
    monkeypatch.setattr(audit_mcp_server, "_client", lambda *a, **k: _MalformedClient())

    ib_server = mcp_server.build_server()
    # my_answer_receipt: a valid-shaped id so it reaches the HTTP call
    r = _call_tool(ib_server, "my_answer_receipt", {"request_id": "valid-id-abc"})
    assert "malformed" in r.lower(), f"my_answer_receipt: expected sanitized error, got: {r}"
    # what_can_i_open
    r = _call_tool(ib_server, "what_can_i_open", {})
    assert "malformed" in r.lower(), f"what_can_i_open: expected sanitized error, got: {r}"

    au_server = audit_mcp_server.build_server()
    # org_activity_summary
    r = _call_tool(au_server, "org_activity_summary", {})
    assert "malformed" in r.lower(), f"org_activity_summary: expected sanitized error, got: {r}"
    # run_tamper_demo (POST)
    r = _call_tool(au_server, "run_tamper_demo", {})
    assert "malformed" in r.lower(), f"run_tamper_demo: expected sanitized error, got: {r}"
    # audit_verify (extended output path)
    r = _call_tool(au_server, "audit_verify", {})
    assert "malformed" in r.lower(), f"audit_verify: expected sanitized error, got: {r}"
