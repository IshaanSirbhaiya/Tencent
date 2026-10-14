"""Tests for the walnut-internal-brain and walnut-ib-audit MCP servers.

Builds the IB app in-process (the same way tests/test_app_routes.py does) and routes the MCP
tools' HTTP calls to it through a FastAPI TestClient. Asserts:
  (1) ask_internal_brain returns an answer with citations for a permitted question,
  (2) neither server's end-user tools expose an identity argument (user/persona/sub/principal),
  (3) a missing IB_TOKEN gives a clear error.
"""
from __future__ import annotations

import asyncio

import pytest
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
# --------------------------------------------------------------------------------------------
class _Routed:
    """Patches the MCP server modules so their HTTP helpers delegate to a TestClient with a token."""

    def __init__(self, monkeypatch, app, token):
        self.client = TestClient(app)
        self.token = token

        def _patch(mod):
            monkeypatch.setattr(mod, "_token", lambda: self.token)
            monkeypatch.setattr(mod, "_base_url", lambda: "")
            monkeypatch.setattr(mod, "_http_get", self._http_get)
            if hasattr(mod, "_http_post"):
                monkeypatch.setattr(mod, "_http_post", self._http_post)

        _patch(mcp_server)
        _patch(audit_mcp_server)

    def _headers(self, extra=None):
        h = {"Authorization": f"Bearer {self.token}"}
        if extra:
            h.update(extra)
        return h

    def _http_get(self, path, params=None):
        r = self.client.get(path, params=params or None, headers=self._headers())
        return self._wrap(r)

    def _http_post(self, path, json_body):
        r = self.client.post(path, json=json_body,
                             headers=self._headers({"x-ib-channel": mcp_server.CHANNEL}))
        return self._wrap(r)

    @staticmethod
    def _wrap(r):
        if r.status_code >= 400:
            return r, f"Internal Brain API error: HTTP {r.status_code}."
        return r, None


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """IB app in-process + a minted employee token, with MCP HTTP routed to it."""
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "jdoe", ["employee"])
    return _Routed(monkeypatch, app, tok), svc


@pytest.fixture
def routed_co(tmp_path, monkeypatch):
    """IB app in-process + a minted compliance-officer token, with MCP HTTP routed to it."""
    app, svc = _build_app(tmp_path)
    tok = authmod.mint(svc.cfg.token_key, "sara.sec", ["employee", "compliance_officer"])
    return _Routed(monkeypatch, app, tok), svc


# --------------------------------------------------------------------------------------------
# (1) ask_internal_brain returns an answer with citations for a permitted question
# --------------------------------------------------------------------------------------------
def test_ask_internal_brain_returns_answer_and_citations(routed):
    routed_env, svc = routed
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
    # audit_events.user is a log FILTER, not caller identity — the task allows it. The other two
    # tools must carry no identity arg.
    for name in ("audit_doc_accessors", "audit_verify"):
        props = set(schemas[name].get("properties", {}).keys())
        assert not (props & IDENTITY_ARGS), f"{name} exposes identity args: {props}"
    # audit_events exposes only the documented filter args (user here is a filter, not identity)
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
# helper: invoke a FastMCP tool by name (without a real MCP transport)
# --------------------------------------------------------------------------------------------
def _call_tool(server, name: str, arguments: dict) -> str:
    """Call a registered FastMCP tool directly and return its text result."""
    tool = server._tool_manager._tools[name]
    return asyncio.run(tool.run(arguments))
