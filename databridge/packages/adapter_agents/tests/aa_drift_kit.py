"""Shared set-up for the adaptation tests (a uniquely named module, never a conftest).

A REAL stdio MCP server (`packages/mcp_ingest/tests/fixture_server.py`: filesystem tools over a
folder and/or SQLite dressed as Postgres), the real `McpIngestService`, the real pipeline, a real
`GraphStore` and the `workspace` pack. No network and no model. Session pooling is OFF so every
sync sees the server as it is NOW (the fixture builds its information_schema at start-up).
Everything synthetic.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

MI_TESTS = Path(__file__).resolve().parents[2] / "mcp_ingest" / "tests"
FIXTURE_SERVER = MI_TESTS / "fixture_server.py"


@dataclasses.dataclass(frozen=True)
class Principal:
    """An authenticated principal as the application boundary asserts it (cf. agent_runtime's
    `Approver`)."""
    principal_id: str
    authenticated_by: str = "test_boundary"


# The tests' authentication boundary registers its principal type, as the gateway registers its
# `AuthenticatedPrincipal` at boot: only a registered type is a principal (R-019 #2).
from databridge_adapter_agents.adapt import trust_principal_type  # noqa: E402

trust_principal_type(Principal, authenticated_by="test_boundary")

OWNER = Principal("owner@larkspur.example")


def write_db(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.executescript("""
            CREATE TABLE organisations (id INTEGER PRIMARY KEY, name TEXT, domain TEXT);
            CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT, budget INTEGER,
                                   organisation_id INTEGER REFERENCES organisations(id));
            INSERT INTO organisations VALUES (1, 'Aldermoor Logistics', 'aldermoor.example'),
                                             (2, 'Quillon Freight', 'quillon.example'),
                                             (3, 'Brackwater Mills', 'brackwater.example');
            INSERT INTO projects VALUES (10, 'Route planner', 1200, 1),
                                        (11, 'Depot summaries', 800, 2),
                                        (12, 'Mill audit', 450, 3);
        """)


def db_exec(path: Path, script: str) -> None:
    with sqlite3.connect(path) as c:
        c.executescript(script)


def db_rows(path: Path, table: str) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(f'SELECT * FROM "{table}"')]


def connection(conn_id: str, *, db: Optional[Path] = None, root: Optional[Path] = None,
               **scope: Any) -> dict[str, Any]:
    args = [str(FIXTURE_SERVER)]
    if root is not None:
        args += ["--root", str(root)]
    if db is not None:
        args += ["--db", str(db)]
    return {"id": conn_id, "title": conn_id,
            "transport": {"type": "stdio", "command": sys.executable, "args": args},
            "scope": dict(scope), "auto_approve": True}


def agent_spec(conn: str, *, agent_id: str = "larkspur_sources",
               adapt: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    spec = {"spec": "adapter_agent/v1", "id": agent_id, "title": "Larkspur sources",
            "created_from": "Keep the Larkspur sources in the graph and adapt to format changes",
            "connections": [conn], "plans": {conn: "approved"},
            "sync": {"type": "manual"}, "extract": {"pack": "workspace"},
            "freshness": {"max_age_hours": 24}, "status": "active"}
    if adapt is not None:
        spec["adapt"] = adapt
    return spec


class DriftEnv:
    def __init__(self, tmp: Path, vocab: Any) -> None:
        from databridge_graph.store import GraphStore

        from databridge_adapter_agents import (AdapterAgentManager, AdapterAgentRegistry,
                                               McpIngestService)

        self.tmp = tmp
        self.vocab = vocab
        (tmp / "graph").mkdir(parents=True, exist_ok=True)
        self.store = GraphStore(str(tmp / "graph" / "g.db"))
        self.service = McpIngestService()
        self.manager = AdapterAgentManager(
            service=self.service, registry=AdapterAgentRegistry(tmp / "agents"),
            store=self.store, runs_root=tmp / "runs",
            events_path=tmp / "events" / "events.jsonl")

    def use_service(self, service: Any) -> None:
        """Swap the ingest service (e.g. one whose probe reports a scripted change)."""
        from databridge_adapter_agents import AdapterAgentManager, AdaptationEngine

        self.service = service
        m = self.manager
        self.manager = AdapterAgentManager(service=service, registry=m.registry, store=self.store,
                                           runs_root=self.tmp / "runs",
                                           events_path=self.tmp / "events" / "events.jsonl")
        assert isinstance(self.manager.adaptation, AdaptationEngine)

    def setup(self, conn: str, *, adapt: Optional[dict[str, Any]] = None, **conn_kw: Any):
        import databridge_mcp_ingest as mi

        mi.register_connection(connection(conn, **conn_kw))
        plan = mi.propose_plan(conn)
        assert not plan.problems, plan.problems
        # a connection flag never approves a plan: a named, authenticated person does
        plan = mi.approve_plan(conn, approved_by=OWNER.principal_id, principal=OWNER)
        assert plan.status == "approved"
        if conn_kw.get("db") is not None:
            self.approve_first_mapping(conn)
        # Created ACTIVE by an AUTHENTICATED person (the principal object, not a typed name):
        # under `auto_additive` that activation seals the delegated approval of additive changes
        # (INT3; sealed record AA3). Nobody activating, or a bare name, delegates nothing.
        return self.manager.create(agent_spec(conn, adapt=adapt), by=OWNER)

    def approve_first_mapping(self, conn: str) -> None:
        """A table source's first run PROPOSES its mapping; only a named person approves it
        (mcp_ingest ignores `auto_approve`). Done once, before the agent exists, as a person
        would after reading the proposal — so the agent's first sync builds approved tables."""
        import databridge_mcp_ingest as mi
        from databridge_adapter_agents import McpIngestService

        McpIngestService().run_ingest(conn, store=self.store, harness=None)
        m = mi.approve_mapping(conn, principal=OWNER)       # R-022 f1 #1: authenticated
        assert any(t.status == "approved" for t in m.tables.values()), "nothing was proposed"

    def sync(self, agent_id: str):
        s = self.manager.sync(agent_id)
        assert s.record_error is None, s.record_error
        return s

    def mapping(self, conn: str) -> dict[str, Any]:
        from databridge_mcp_ingest.mapping import load_mapping

        return load_mapping(conn).model_dump(mode="json")

    def holds_file(self) -> bool:
        """Does the runner honour the holds file (P's hook)? Then a table is held THERE; before
        it, by moving its mapping entry back to `proposed`. Tests derive which from this."""
        return bool(self.service.holds_honoured())

    def is_held(self, conn: str, table: str) -> bool:
        """Is `table` held by an open adaptation, by whichever mechanism is in force?"""
        if self.holds_file():
            from databridge_mcp_ingest.records import load_holds

            h = load_holds(conn)
            return h.is_active(h.tables.get(table))
        return (self.mapping(conn)["tables"].get(table) or {}).get("status") == "proposed"

    def run_dir(self, summary, conn: str) -> Path:
        c = next(x for x in summary.connections if x.connection == conn)
        return self.tmp / "ingest" / conn / c.ingest_run_id

    def nodes(self, kind: str) -> list[Any]:
        return [r for r in self.store.open_nodes() if r["kind"] == kind]

    @staticmethod
    def attrs(row: Any) -> dict[str, Any]:
        return json.loads(row["attrs_json"] or "{}")

    def jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
                if x.strip()]


ENV_DIRS = (("WALNUT_SOURCES_DIR", "sources"), ("WALNUT_INGEST_DIR", "ingest"),
            ("WALNUT_CAPTURE_DIR", "capture"), ("WALNUT_EVENTS_PATH", "events/events.jsonl"),
            ("WALNUT_GRAPH_DB_PATH", "graph/g.db"), ("WALNUT_ADAPTER_AGENTS_DIR", "agents"),
            ("WALNUT_RUNS_DIR", "runs"), ("WALNUT_FACTS_DIR", "facts"),
            ("WALNUT_PASSAGES_DIR", "passages"), ("WALNUT_CACHE_DIR", "cache"))


def drift_env(tmp_path: Path, monkeypatch):
    """Generator body for a pytest fixture (see the test modules)."""
    import pytest

    pytest.importorskip("mcp")
    pytest.importorskip("databridge_mcp_ingest")
    from databridge_graph.packs import deployment

    for name, sub in ENV_DIRS:
        monkeypatch.setenv(name, str(tmp_path / sub))
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    monkeypatch.delenv("WALNUT_CRAWL_PROFILES_DIR", raising=False)
    with deployment("workspace") as vocab:
        yield DriftEnv(tmp_path, vocab)
