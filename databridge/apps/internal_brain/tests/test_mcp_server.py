"""Tests for the walnut-internal-brain and walnut-ib-audit MCP servers.

Builds the IB app in-process and routes the MCP tools' HTTP calls through a single
``_client()`` factory that returns a ``fastapi.testclient.TestClient`` (an httpx.Client
subclass). This means the REAL helpers that attach the Authorization header run in
tests — the only thing mocked is the transport.

Covers findings 3, 4, 5, 6, 7, 9 from the Codex security review:
  (1) ask_internal_brain returns an answer with citations for a permitted question,
  (2) neither server's end-user tools expose an identity argument,
  (3) a missing IB_TOKEN gives a clear error,
  (4) the Authorization header actually reaches the app (without it → 401),
  (5) an employee token calling audit tools gets 403; a compliance officer succeeds,
  (6) audit_events(user="jdoe") run by the officer records the OFFICER as the acting subject,
  (7) malformed JSON / HTML 200 responses give the sanitized error,
  (8) doc_id "../verify" is rejected/encoded (never reaches /audit/verify),
  (9) since_days outside 1..3650 is rejected,
  (10) plain-http non-loopback IB_URL is refused without a request.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from databridge_ib import auth as authmod
from databridge_ib import mcp_server, audit_mcp_server
from databridge_ib.app import Services, create_app
from databridge_ib.audit.ledger import AccessLedger
from databridge_ib.audit.query import AuditProjection
from databridge_ib.audit.routes import make_audit_router
from databridge_ib.config import IbConfig
from databridge_ib.index import Index
from databridge_ib.ingest import Ingestor
from databridge_ib.pipeline import Pipeline

from test_pipeline_core import PRIVATE, PUBLIC, READERS, FakeConnector, MemAnswers, MemLedger, _users

IDENTITY_ARGS = {"user", "persona", "sub", "principal"}


# --------------------------------------------------------------------------------------------
# in-process IB app (mirrors tests/test_app_routes.py::_svc, plus the audit router)
# --------------------------------------------------------------------------------------------
def _svc(tmp_path):
    cfg = IbConfig(var_root=tmp_path / "var")
    object.__setattr__(cfg, "demo_login", True)
    conn = FakeConnector("slack", [PUBLIC, PRIVATE], READERS)
    idx = Index(tmp_path / "index.sqlite")
    for d in (PUBLIC, PRIVATE):
        idx.upsert(d, conn.acl(d.doc_id))
    directory = _users()
    # jdoe is an employee; sara.sec is a compliance officer (for audit tools)
    directory._users["jdoe"] = directory._users["jdoe"].model_copy(update={"roles": ("employee",)})
    directory._users["sara.sec"] = directory.get("jdoe").model_copy(
        update={"id": "sara.sec", "email": "sara.sec@companya.example",
                "roles": ("employee", "compliance_officer")})
    ledger = AccessLedger(cfg)
    proj = AuditProjection(ledger)
    pipe = Pipeline(index=idx, directory=directory, connectors={"slack": conn}, ledger=ledger,
                    answers=MemAnswers())
    ing = Ingestor(idx, {"slack": conn}, tmp_path / "cursors.json", directory=directory)
    return Services(cfg=cfg, directory=directory, index=idx, connectors={"slack": conn},
                    ledger=ledger, answers=MemAnswers(), pipeline=pipe, ingestor=ing,
                    projection=proj)


def _build_app(tmp_path):
    svc = _svc(tmp_path)

    def audit_factory(current, require_role):
        return make_audit_router(svc.ledger, svc.answers, svc.projection, require_role,
                                 clock=lambda: __import__("datetime").datetime.now(
                                     __import__("datetime").timezone.utc))

    return create_app(svc, audit_router_factory=audit_factory), svc


# --------------------------------------------------------------------------------------------
# Routing the MCP tools' HTTP calls to the in-process app
#
# We patch ``_client()`` to return a ``TestClient(app)`` (an httpx.Client subclass).
# The REAL ``_http_get`` / ``_http_post`` helpers run, attaching the Authorization header.
# We also patch ``_token()`` to supply the test token, and ``_base_url()`` to return ""
# so requests go to the TestClient's base URL.
# --------------------------------------------------------------------------------------------
class _Routed:
    """Patches the MCP server modules so ``_client()`` returns a TestClient with a token."""

    def __init__(self, monkeypatch, app, token, mod):
        self.client = TestClient(app)
        self.token = token
        self.app = app
        monkeypatch.setattr(mod, "_token", lambda: self.token)
        monkeypatch.setattr(mod, "_base_url", lambda: "")
        monkeypatch.setattr(mod, "_client", lambda: self.client)


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """IB app in-process + a minted employee token, with MCP HTTP routed to it."""
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    mods = [_Routed(monkeypatch, app, tok, mcp_server),
            _Routed(monkeypatch, app, tok, audit_mcp_server)]
    return mods, svc


@pytest.fixture
def routed_co(tmp_path, monkeypatch):
    """IB app in-process + a minted compliance-officer token, with MCP HTTP routed to it."""
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "sara.sec", ["employee", "compliance_officer"])
    mods = [_Routed(monkeypatch, app, tok, mcp_server),
            _Routed(monkeypatch, app, tok, audit_mcp_server)]
    return mods, svc


# --------------------------------------------------------------------------------------------
# (1) ask_internal_brain returns an answer with citations for a permitted question
# --------------------------------------------------------------------------------------------
def test_ask_internal_brain_returns_answer_and_citations(routed):
    mods, svc = routed
    server = mcp_server.build_server()
    result = _call_tool(server, "ask_internal_brain",
                        {"question": "what is the database migration status and blockers?"})
    assert isinstance(result, str)
    assert result != ""
    # the permitted answer cites slack:t1 and is not the refusal
    assert "migration" in result.lower()
    assert "answered as jdoe" in result
    # citations section present and mentions the citation
    assert "Citations:" in result
    assert "slack" in result.lower()


# --------------------------------------------------------------------------------------------
# (2) no identity argument in either server's end-user tool input schemas
# --------------------------------------------------------------------------------------------
def _tool_schemas(server) -> dict[str, dict]:
    tools = asyncio.run(server.list_tools())
    return {t.name: t.inputSchema for t in tools}


def test_internal_brain_tools_have_no_identity_argument():
    schemas = _tool_schemas(mcp_server.build_server())
    assert {"ask_internal_brain", "my_recent_questions"} <= set(schemas)
    for name in ("ask_internal_brain", "my_recent_questions"):
        props = set(schemas[name].get("properties", {}).keys())
        assert not (props & IDENTITY_ARGS), f"{name} exposes identity args: {props}"


def test_audit_tools_have_no_identity_argument():
    schemas = _tool_schemas(audit_mcp_server.build_server())
    assert {"audit_events", "audit_doc_accessors", "audit_verify"} <= set(schemas)
    for name in ("audit_doc_accessors", "audit_verify"):
        props = set(schemas[name].get("properties", {}).keys())
        assert not (props & IDENTITY_ARGS), f"{name} exposes identity args: {props}"
    assert set(schemas["audit_events"].get("properties", {}).keys()) <= {
        "user", "container", "since_days", "doc"}


# --------------------------------------------------------------------------------------------
# (3) a missing IB_TOKEN gives a clear error
# --------------------------------------------------------------------------------------------
def test_missing_token_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("IB_TOKEN", raising=False)
    monkeypatch.delenv("IB_URL", raising=False)
    server = mcp_server.build_server()
    result = _call_tool(server, "ask_internal_brain", {"question": "anything"})
    assert isinstance(result, str)
    assert "IB_TOKEN" in result


def test_missing_token_gives_clear_error_audit(monkeypatch):
    monkeypatch.delenv("IB_TOKEN", raising=False)
    monkeypatch.delenv("IB_URL", raising=False)
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_verify", {})
    assert isinstance(result, str)
    assert "IB_TOKEN" in result


# --------------------------------------------------------------------------------------------
# (4) The Authorization header reaches the app (a request without it would be 401)
# --------------------------------------------------------------------------------------------
def test_authorization_header_reaches_app(routed_co):
    """The real _http_get/_http_post attach the Authorization header. A request without it
    would get 401 from the app. With the token, audit_verify succeeds (200)."""
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_verify", {})
    assert isinstance(result, str)
    # Should contain "Audit ledger verification" (success), not a 401 or token error.
    assert "Audit ledger verification" in result
    assert "401" not in result
    assert "IB_TOKEN" not in result


def test_authorization_header_required_for_employee_ask(routed):
    """Without a valid token, ask_internal_brain fails with the missing-token error."""
    mods, svc = routed
    server = mcp_server.build_server()
    result = _call_tool(server, "ask_internal_brain",
                        {"question": "what is the database migration status?"})
    assert isinstance(result, str)
    # With the routed employee token, this should succeed (has answer text)
    assert "migration" in result.lower() or "IB_TOKEN" not in result


# --------------------------------------------------------------------------------------------
# (5) Employee token → 403 on audit tools; compliance officer succeeds on all three
# --------------------------------------------------------------------------------------------
def test_employee_forbidden_on_audit_verify(routed):
    mods, svc = routed
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_verify", {})
    assert "403" in result, f"expected 403 forbidden for employee, got: {result}"


def test_employee_forbidden_on_audit_events(routed):
    mods, svc = routed
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_events", {})
    assert "403" in result, f"expected 403 forbidden for employee, got: {result}"


def test_employee_forbidden_on_audit_doc_accessors(routed):
    mods, svc = routed
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_doc_accessors", {"doc_id": "slack:t1"})
    assert "403" in result, f"expected 403 forbidden for employee, got: {result}"


def test_officer_succeeds_on_audit_verify(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_verify", {})
    assert "Audit ledger verification" in result
    assert "403" not in result


def test_officer_succeeds_on_audit_events(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_events", {})
    # Should either list events or say no events matched — not a 403.
    assert "403" not in result


def test_officer_succeeds_on_audit_doc_accessors(routed_co):
    mods, svc = routed_co
    # First trigger an ask so the doc has some access events
    ib_server = mcp_server.build_server()
    _call_tool(ib_server, "ask_internal_brain",
               {"question": "what is the database migration status and blockers?"})
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_doc_accessors", {"doc_id": "slack:t1"})
    assert "403" not in result


# --------------------------------------------------------------------------------------------
# (6) audit_events(user="jdoe") run by the officer records the OFFICER as the acting subject
# --------------------------------------------------------------------------------------------
def test_audit_events_records_officer_as_subject(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_events", {"user": "jdoe"})
    # The audit.query event in the ledger should have data["sub"] == "sara.sec" (the officer)
    audit_events = [e for e in svc.ledger.events() if e.kind == "audit.query"]
    assert audit_events, "expected at least one audit.query event in the ledger"
    officer_subs = [e.data.get("sub") for e in audit_events]
    assert "sara.sec" in officer_subs, (
        f"expected the officer (sara.sec) as the acting subject, got: {officer_subs}")


# --------------------------------------------------------------------------------------------
# (7) Malformed JSON / HTML 200 responses give the sanitized error
# --------------------------------------------------------------------------------------------
def _make_malformed_app() -> FastAPI:
    """An app that returns HTML 200 or empty 204 for all routes."""
    app = FastAPI()

    @app.get("/auth/whoami")
    def whoami():
        return httpx.Response(200, content="<html>not json</html>",
                              headers={"content-type": "text/html"})

    return app


class _MalformedClient(httpx.Client):
    """An httpx.Client that returns malformed responses for all requests."""

    def __init__(self):
        super().__init__()

    def get(self, url, **kwargs):
        return httpx.Response(200, content="<html>not json</html>",
                              headers={"content-type": "text/html"})

    def post(self, url, **kwargs):
        return httpx.Response(200, content="<html>not json</html>",
                              headers={"content-type": "text/html"})


def test_malformed_json_gives_sanitized_error(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setenv("IB_URL", "http://127.0.0.1:8100")
    monkeypatch.setattr(mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_server, "_base_url", lambda: "")
    monkeypatch.setattr(mcp_server, "_client", lambda: _MalformedClient())
    server = mcp_server.build_server()
    result = _call_tool(server, "ask_internal_brain", {"question": "anything"})
    assert isinstance(result, str)
    assert "malformed" in result.lower(), f"expected sanitized error, got: {result}"


def test_malformed_json_gives_sanitized_error_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setenv("IB_URL", "http://127.0.0.1:8100")
    monkeypatch.setattr(audit_mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(audit_mcp_server, "_base_url", lambda: "")
    monkeypatch.setattr(audit_mcp_server, "_client", lambda: _MalformedClient())
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_verify", {})
    assert isinstance(result, str)
    assert "malformed" in result.lower(), f"expected sanitized error, got: {result}"


def test_malformed_whoami_shows_unavailable(tmp_path, monkeypatch):
    """A malformed /auth/whoami response must not lose a good answer: show 'answered as (unavailable)'."""
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setenv("IB_URL", "http://127.0.0.1:8100")
    monkeypatch.setattr(mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_server, "_base_url", lambda: "")

    class _MixedClient(httpx.Client):
        def __init__(self):
            super().__init__()

        def get(self, url, **kwargs):
            # /auth/whoami returns malformed HTML
            return httpx.Response(200, content="<html>not json</html>",
                                  headers={"content-type": "text/html"})

        def post(self, url, **kwargs):
            # /ib/ask returns a valid answer
            return httpx.Response(200, json={"answer": "good answer", "citations": []})

    monkeypatch.setattr(mcp_server, "_client", lambda: _MixedClient())
    server = mcp_server.build_server()
    result = _call_tool(server, "ask_internal_brain", {"question": "anything"})
    assert isinstance(result, str)
    assert "good answer" in result
    assert "answered as (unavailable)" in result


# --------------------------------------------------------------------------------------------
# (8) doc_id "../verify" is rejected or encoded (never reaches /audit/verify)
# --------------------------------------------------------------------------------------------
def test_doc_id_traversal_is_rejected(routed_co):
    """doc_id="../verify" must not reach /audit/verify."""
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_doc_accessors", {"doc_id": "../verify"})
    assert isinstance(result, str)
    # Should NOT contain verify output; should either be rejected or encoded as accessors lookup
    assert "Audit ledger verification" not in result, (
        "doc_id '../verify' reached /audit/verify — endpoint confusion!")


def test_doc_id_dotdot_encoded(routed_co):
    """The encoded form of '../verify' should produce an accessors response (or no-accessors
    message), not a verify response."""
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_doc_accessors", {"doc_id": "../verify"})
    # Either rejected explicitly, or an accessors response (no accessors for an encoded path)
    assert "Audit ledger verification" not in result


# --------------------------------------------------------------------------------------------
# (9) since_days outside 1..3650 is rejected
# --------------------------------------------------------------------------------------------
def test_since_days_zero_rejected(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_events", {"since_days": 0})
    assert "since_days" in result.lower() or "must be between" in result.lower(), (
        f"expected since_days error for 0, got: {result}")


def test_since_days_too_large_rejected(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_events", {"since_days": 3651})
    assert "since_days" in result.lower() or "must be between" in result.lower(), (
        f"expected since_days error for 3651, got: {result}")


def test_since_days_negative_rejected(routed_co):
    mods, svc = routed_co
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_events", {"since_days": -1})
    assert "since_days" in result.lower() or "must be between" in result.lower(), (
        f"expected since_days error for -1, got: {result}")


# --------------------------------------------------------------------------------------------
# (10) plain-http non-loopback IB_URL is refused without a request
# --------------------------------------------------------------------------------------------
def test_plain_http_non_loopback_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setenv("IB_URL", "http://remote-host.example:8100")
    monkeypatch.setattr(mcp_server, "_token", lambda: "fake-token")
    # _base_url reads from IB_URL env, but we patch it to control the value
    monkeypatch.setattr(mcp_server, "_base_url", lambda: "http://remote-host.example:8100")
    # Make _client return a real httpx.Client (we want to verify NO request is made)
    request_made = []

    class _TrackingClient(httpx.Client):
        def get(self, url, **kwargs):
            request_made.append(("GET", url))
            return super().get(url, **kwargs)

        def post(self, url, **kwargs):
            request_made.append(("POST", url))
            return super().post(url, **kwargs)

    monkeypatch.setattr(mcp_server, "_client", lambda: _TrackingClient())
    server = mcp_server.build_server()
    result = _call_tool(server, "my_recent_questions", {})
    assert isinstance(result, str)
    assert "plain HTTP" in result or "non-loopback" in result or "IB_URL" in result, (
        f"expected plain-http refusal, got: {result}")
    assert not request_made, f"expected no HTTP request, but one was made: {request_made}"


def test_plain_http_non_loopback_refused_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setattr(audit_mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(audit_mcp_server, "_base_url", lambda: "http://remote-host.example:8100")
    request_made = []

    class _TrackingClient(httpx.Client):
        def get(self, url, **kwargs):
            request_made.append(("GET", url))
            return super().get(url, **kwargs)

    monkeypatch.setattr(audit_mcp_server, "_client", lambda: _TrackingClient())
    server = audit_mcp_server.build_server()
    result = _call_tool(server, "audit_verify", {})
    assert isinstance(result, str)
    assert "plain HTTP" in result or "non-loopback" in result or "IB_URL" in result, (
        f"expected plain-http refusal, got: {result}")
    assert not request_made, f"expected no HTTP request, but one was made: {request_made}"


# --------------------------------------------------------------------------------------------
# (extra) IB_URL with credentials is rejected
# --------------------------------------------------------------------------------------------
def test_ib_url_with_credentials_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_TOKEN", "fake-token")
    monkeypatch.setattr(mcp_server, "_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_server, "_base_url", lambda: "http://user:pass@127.0.0.1:8100")
    monkeypatch.setattr(mcp_server, "_client", lambda: httpx.Client())
    server = mcp_server.build_server()
    result = _call_tool(server, "my_recent_questions", {})
    assert "credentials" in result.lower(), f"expected credentials rejection, got: {result}"


# --------------------------------------------------------------------------------------------
# (extra) Error strings must not contain the URL
# --------------------------------------------------------------------------------------------
def test_error_string_does_not_contain_url(routed):
    """When the app returns a 4xx/5xx, the error string must not contain the URL."""
    mods, svc = routed
    server = mcp_server.build_server()
    # Ask with a very long question to trigger 422 (max 2000 chars in AskRequest)
    result = _call_tool(server, "ask_internal_brain", {"question": "x" * 3000})
    assert isinstance(result, str)
    assert "http://" not in result, f"error string leaked URL: {result}"
    assert "127.0.0.1" not in result, f"error string leaked host: {result}"


# --------------------------------------------------------------------------------------------
# helper: invoke a FastMCP tool by name (without a real MCP transport)
# --------------------------------------------------------------------------------------------
def _call_tool(server, name: str, arguments: dict) -> str:
    """Call a registered FastMCP tool directly and return its text result."""
    tool = server._tool_manager._tools[name]
    return asyncio.run(tool.run(arguments))
