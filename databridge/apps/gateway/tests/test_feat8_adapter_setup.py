"""FEAT8 (founder design, 2026-10-02): a registered read source is set up THROUGH ITS ADAPTER AGENT.

Driven end to end on TWO estates (workspace, finance) through the real gateway routes, the real
ingest facade, the real mapper and a real adapter-agent manager. Only the model is fake: it answers
the explanation from the evidence it was SENT, plus two claims the agent must drop.

1. every registered read source is listed as "ready to set up"; every route refuses an anonymous
   caller (401);
2. Set up creates the agent's draft as the signed-in person and the agent proposes the plan;
   the agent's own principal cannot approve the plan; a person does;
3. the first run discovers the schema and proposes a mapping, and builds NOTHING;
4. the agent explains the mapping through the harness (a models.yaml task class): facts equal the
   mapper's proposal, every kept sentence cites evidence, every pointer resolves into the captured
   bytes, ungrounded claims are dropped, and the mapping is unchanged (advisory);
5. the agent cannot approve its own mapping, and cannot hand off before a person approves;
6. a person approves, hands off: the agent is activated by that person (sealed delegation) and its
   build run builds the approved tables;
7. Activity shows what the agent proposed, what the person approved and what it built.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import TEST_TOKENS, auth_headers
from databridge_gateway.public_errors import STANDARD_FOR  # noqa: E402  (R-028 #11)
from test_platform_routes import _mount, env  # noqa: F401  (fixture)

MI_TESTS = Path(__file__).resolve().parents[3] / "packages" / "mcp_ingest" / "tests"
if str(MI_TESTS) not in sys.path:
    sys.path.insert(0, str(MI_TESTS))

import estates  # noqa: E402

SID = "feat8-src"
ESTATE_NAMES = ("workspace", "finance")
PERSON = "analyst-7"
MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"


def _profile(tmp_path: Path, name: str) -> Path:
    from databridge_mcp_ingest.plans import sql_dialect

    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    dialect = dict(sql_dialect("sqlite"))
    dialect["name"] = f"estate_{name}"
    dialect["list_tables"] = " UNION ALL ".join(
        f"SELECT '{s}' AS table_schema, name AS table_name FROM {s}.sqlite_master "
        f"WHERE type = 'table'" for s in estates.ESTATES[name]["schemas"]) + " ORDER BY 1, 2"
    (d / f"estate_{name}.sql.yaml").write_text(yaml.safe_dump(dialect), encoding="utf-8")
    (d / f"estate_{name}.profile.yaml").write_text(yaml.safe_dump({
        "id": f"estate-{name}", "title": "test estate", "priority": 100,
        "verified": "test fixture", "match": {"all_tools": ["query"]},
        "step": {"strategy": "tabular", "sql": {"tool": "query", "arg": "sql",
                                                "dialect": f"estate_{name}",
                                                "parse": {"mode": "json_rows"}}}}),
        encoding="utf-8")
    return d


def _fk_pair(db: sqlite3.Connection, name: str) -> list[str]:
    """Two tables of the estate joined by a DECLARED foreign key (read from the estate itself):
    the first table, in name order, whose foreign key points at another table of its schema."""
    for schema, table in estates.tables_of(db, name):
        for fk in db.execute(f"PRAGMA {schema}.foreign_key_list('{table}')").fetchall():
            ref = fk[2]
            if ref != table and (schema, ref) in estates.tables_of(db, name):
                return [f"{schema}.{table}", f"{schema}.{ref}"]
    raise AssertionError(f"the {name} estate declares no foreign key")


class FakeHarness:
    """Answers `data_to_text` from the payload it is SENT (never from a table of answers): per
    table it CHOOSES the offered facts other than the kind, in reverse order, plus one id that
    was never offered (which the agent must drop). It writes no words. Any other task is
    refused."""

    def __init__(self):
        self.calls: list[dict] = []

    def complete_structured(self, task_class, messages, response_model, **kw):
        if task_class != "data_to_text":
            raise RuntimeError(f"fake harness: no answer for {task_class}")
        sent = json.loads(messages[-1]["content"])
        self.calls.append({"task_class": task_class, "payload": sent})
        tables = []
        for t in sent["tables"]:
            chosen = [f["id"] for f in t["facts"] if f["type"] != "kind"][::-1]
            tables.append({"table": t["table"], "reasons": [*chosen, "f999"]})

        class R:
            parsed = response_model.model_validate({"tables": tables})
            event = {"model": "fake/model"}
        return R()


@pytest.fixture(params=ESTATE_NAMES)
def estate(request, env, tmp_path, monkeypatch):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents.ingest_service import McpIngestService
    from databridge_graph.packs import clear_caches, deployment
    from databridge_graph.store import GraphStore

    import databridge_mcp_ingest as mi
    from databridge_mcp_ingest import register_connection

    from databridge_gateway.activity_routes import register_activity_routes
    from databridge_gateway.platform_runtime import IngestFacade

    name = request.param
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")
    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_profile(tmp_path, name)))
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    db = estates.build_estate(name, tmp_path / "e.sqlite")
    scope = _fk_pair(db, name)
    clear_caches()
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        client = estates.engine_client(db)
        # the ONE seam: the estate's engine stands in for the MCP server's transport
        for fn in ("propose_plan", "approve_plan", "run_ingest"):
            real = getattr(mi, fn)

            def wrapped(*a, _real=real, _fn=fn, **kw):
                kw["client"] = client
                if _fn == "run_ingest":
                    kw["vocab"] = vocab
                return _real(*a, **kw)
            monkeypatch.setattr(mi, fn, wrapped)
        from databridge_mcp_ingest import drift
        real_probe = drift.probe_source
        monkeypatch.setattr(drift, "probe_source",
                            lambda sid, **kw: real_probe(sid, **{**kw, "client": client}))
        register_connection({"id": SID, "transport": {"type": "stdio", "command": "uvx"},
                             "scope": {"tables_include": scope}})
        store = GraphStore(str(tmp_path / "g.db"))
        app, rt, _ = _mount(env)
        app.state.graph_store = store
        register_activity_routes(app)
        rt.ingest = IngestFacade()
        rt.harness = FakeHarness()
        rt.manager = AdapterAgentManager(service=McpIngestService(),
                                         registry=AdapterAgentRegistry(), store=store)
        yield {"name": name, "app": app, "rt": rt, "db": db, "scope": scope, "vocab": vocab,
               "store": store}
    clear_caches()
    db.close()


@pytest.fixture(autouse=True)
def _fresh_auth():
    from databridge_gateway.auth import reset_auth_state_for_tests

    yield
    reset_auth_state_for_tests()


def _agent_token(monkeypatch, agent_id: str) -> dict:
    """A credential whose principal IS the agent (`adapter_agent:<id>`)."""
    from databridge_gateway.auth import reset_auth_state_for_tests

    tokens = {**TEST_TOKENS, "test-token-agent": f"adapter_agent:{agent_id}"}
    monkeypatch.setenv("WALNUT_API_TOKENS", json.dumps(tokens))
    reset_auth_state_for_tests()
    return {"Authorization": "Bearer test-token-agent"}


ROUTES = [("get", "/adapter-setup/sources"), ("get", f"/adapter-setup/{SID}"),
          ("post", f"/adapter-setup/{SID}/start"), ("post", f"/adapter-setup/{SID}/plan/approve"),
          ("post", f"/adapter-setup/{SID}/discover"), ("post", f"/adapter-setup/{SID}/explain"),
          ("post", f"/adapter-setup/{SID}/mapping/approve"),
          ("post", f"/adapter-setup/{SID}/handoff")]


def test_every_setup_route_refuses_an_unauthenticated_caller(estate):
    with TestClient(estate["app"]) as anon:
        for method, path in ROUTES:
            assert getattr(anon, method)(path).status_code == 401, path
    from databridge_mcp_ingest.plans import plan_path
    assert not plan_path(SID).is_file()                       # nothing was proposed
    assert estate["rt"].manager.registry.list() == []         # no agent was created


def _nodes(store) -> int:
    return int(store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])


def _row_id(row: dict, pk: list[str], ordinal: int) -> str:
    """The test's own statement of a row's id: the primary key's values joined, else its ordinal."""
    vals = [row.get(c) for c in pk]
    return ",".join(str(v) for v in vals) if pk and None not in vals else f"#{ordinal}"


def test_set_up_a_source_through_its_adapter_agent(estate, monkeypatch):
    from databridge_adapter_agents.setup import keeper_id
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.mapping import is_person_kind, load_mapping, mapping_digest
    from databridge_mcp_ingest.runner import load_state

    app, rt, vocab, scope = estate["app"], estate["rt"], estate["vocab"], estate["scope"]
    aid = keeper_id(SID)
    person = auth_headers(PERSON)
    with TestClient(app, headers=person) as c:
        # 1. the source is offered, ready to set up
        listed = c.get("/adapter-setup/sources").json()["sources"]
        [row] = [s for s in listed if s["source_id"] == SID]
        assert row["phase"] == "ready" and row["keeper"] is None
        # 2. Set up: the draft is created as the person, the agent proposes the plan
        r = c.post(f"/adapter-setup/{SID}/start")
        assert r.status_code == 200, r.text
        assert r.json()["agent"] == aid and r.json()["plan"]["status"] == "proposed"
        spec = rt.manager.registry.get(aid)
        assert spec.status == "draft" and spec.connections == [SID]
        assert rt.manager.registry.history(aid)[0]["by"] == PERSON
        # nothing runs before a person approves the plan
        assert c.post(f"/adapter-setup/{SID}/discover").status_code == 409
        digest = c.get(f"/adapter-setup/{SID}").json()["plan_digest"]
        assert digest                                               # the plan the person sees
    agent = _agent_token(monkeypatch, aid)
    with TestClient(app, headers=agent) as c:
        r = c.post(f"/adapter-setup/{SID}/plan/approve", json={"plan_digest": digest})
        assert r.status_code == 409 and "machine actor" in r.text      # the agent is no person
    with TestClient(app, headers=person) as c:
        r = c.post(f"/adapter-setup/{SID}/plan/approve",
                   json={"vouch_read_tools": [], "plan_digest": digest})
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["approved_by"] == PERSON
        assert rt.manager.registry.get(aid).plans == {SID: "approved"}
        # 3. a capture-only discovery captures, screens and proposes; it builds nothing
        r = c.post(f"/adapter-setup/{SID}/discover")
        assert r.status_code == 202, r.text
        assert r.json()["mode"] == "discover"
        assert load_state(SID) == {}                                # state not advanced
        assert _nodes(estate["store"]) == 0                         # the graph is untouched
        from databridge_mcp_ingest.runner import DISCOVERED, latest_discovery
        disc = latest_discovery(SID)
        assert disc["run_id"] == r.json()["run_id"] and disc["status"] == DISCOVERED
        mapping = load_mapping(SID)
        assert set(mapping.tables) == set(scope)
        assert {tm.status for tm in mapping.tables.values()} <= {"proposed", "unmapped"}
        before = {t: (tm.status, mapping_digest(tm)) for t, tm in mapping.tables.items()}
        # 4. the agent explains
        r = c.post(f"/adapter-setup/{SID}/explain")
        assert r.status_code == 200, r.text
        ex = r.json()["explanation"]
    harness: FakeHarness = rt.harness
    assert harness.calls and {x["task_class"] for x in harness.calls} == {"data_to_text"}
    routing = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))
    assert "data_to_text" in json.dumps(routing)               # a task class models.yaml routes
    assert ex["advisory"] is True and ex["produced_by"]["mode"] == "model"
    assert ex["produced_by"]["agent"] == f"adapter_agent:{aid}"
    sent = {t["table"]: t for call in harness.calls for t in call["payload"]["tables"]}
    explained = {t["table"]: t for t in ex["tables"]}
    assert set(explained) == set(scope)
    with_pointers = 0
    assert ex["discovery"]["run_id"] == disc["run_id"]
    for table, t in explained.items():
        tm = mapping.tables[table]
        assert t["kind"] == (tm.node or {}).get("kind")           # facts are the mapper's
        assert t["is_person"] is is_person_kind(vocab, t["kind"])
        assert t["mapping_digest"] == mapping_digest(tm)
        # the evidence is the DISCOVERY's capture of this table
        assert t["capture_sha"] == disc["tables"][table]["sha256"]
        ev = {e["id"]: e for e in t["evidence"]}
        for e in t["evidence"]:                                     # every pointer resolves
            p = e["pointer"]
            assert p["kind"] == "table_cell" and p["table"] == table
            assert p["capture_digest"] == disc["tables"][table]["sha256"]
            snap = json.loads(capture.get_bytes(p["capture_digest"]))
            pk = list(snap.get("primary_key") or [])
            rows = {_row_id(rw, pk, i): rw for i, rw in enumerate(snap["rows"])}
            raw = rows[p["row_id"]][p["column"]]
            if e["masked"]:
                assert str(raw) not in json.dumps(ex)               # a masked value never leaks
            else:
                assert e["value"] == (None if raw is None else str(raw))
            with_pointers += 1
        facts = {f["id"]: f for f in t["facts"]}
        for claim in [t["summary"], *t["reasons"]]:
            # every sentence shown IS an offered fact; a capture fact cites cells of its own
            assert claim["text"] == facts[claim["fact"]]["text"]
            assert claim["evidence"] == facts[claim["fact"]]["evidence"]
            if claim["basis"] == "capture":
                assert claim["evidence"]
                assert all(ev[i]["fact"] == claim["fact"] for i in claim["evidence"])
            else:
                assert claim["evidence"] == []                      # a proposal cites nothing
        assert t["summary"]["type"] == "kind" and t["summary"]["evidence"] == []
        alias = t["model_alias"]                    # the model knows a table by its alias only
        if not ev:          # nothing captured to cite (e.g. held by the screen): not sent
            assert alias not in sent and t["explained_by"] == "rules"
        if alias in sent:
            assert t["explained_by"] == "model" and t["model_dropped"] == 1
            assert table not in json.dumps(sent[alias]) or not (t["is_person"] or t["is_subject"])
            if t["is_person"] or t["is_subject"]:                   # protected: no value sent
                assert all("value" not in e for e in sent[alias]["evidence"])
                assert all("text" not in f for f in sent[alias]["facts"])
    assert with_pointers > 0 and sent                               # non-vacuous
    assert any(t["explained_by"] == "model" for t in ex["tables"])
    after = load_mapping(SID)
    assert {t: (tm.status, mapping_digest(tm)) for t, tm in after.tables.items()} == before
    # 5. the agent cannot approve its own mapping, and nothing hands off yet
    agent = _agent_token(monkeypatch, aid)
    with TestClient(app, headers=agent) as c:
        r = c.post(f"/adapter-setup/{SID}/mapping/approve",
                   json={"explanation_digest": ex["mapping_digest"]})
        assert r.status_code == 409 and "machine actor" in r.text
        r = c.post(f"/sources/{SID}/mapping/approve", json={})
        assert r.status_code == 409
    after = load_mapping(SID)
    assert {t: (tm.status, mapping_digest(tm)) for t, tm in after.tables.items()} == before
    proposed = sorted(t for t, (s, _) in before.items() if s == "proposed")
    assert proposed                                                 # non-vacuous
    with TestClient(app, headers=person) as c:
        r = c.post(f"/adapter-setup/{SID}/handoff")
        assert r.status_code == 409 and "nothing has been approved to build" in r.text
        assert rt.manager.registry.get(aid).status == "draft"
        # 6. a person approves, naming the explanation they read, then hands off
        r = c.post(f"/adapter-setup/{SID}/mapping/approve", json={"tables": proposed})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["EXPLANATION_NOT_READ"], "EXPLANATION_NOT_READ")
        r = c.post(f"/adapter-setup/{SID}/mapping/approve",
                   json={"tables": proposed, "explanation_digest": ex["mapping_digest"]})
        assert r.status_code == 200, r.text
        approved = load_mapping(SID)
        assert all(approved.tables[t].approval["principal_id"] == PERSON for t in proposed)
        pending = c.get(f"/adapter-setup/{SID}").json()["state"]["pending_decisions"]
        if pending:                                 # every pending decision is deferred by name
            r = c.post(f"/adapter-setup/{SID}/handoff", json={"cadence": "daily"})
            assert r.status_code == 409 and r.json()["refusal"]["code"] == "DECISIONS_PENDING"
        r = _handoff(c, cadence="daily")
        assert r.status_code == 202, r.text
        state_row = c.get(f"/adapter-setup/{SID}").json()["state"]
    spec = rt.manager.registry.get(aid)
    assert spec.status == "active"
    assert rt.manager.delegator(spec) == PERSON                    # the sealed delegation
    syncs = rt.manager.run_records(aid)
    assert syncs and {r_["kind"] for r_ in syncs} == {"adapter_agent"}
    assert syncs[-1]["trigger"]["by"] == PERSON
    built = [e for e in load_state(SID).values() if e.get("graph") is not False]
    assert built                                                    # the approved tables built
    assert state_row["phase"] == "kept" and state_row["keeper"]["id"] == aid
    # 7. Activity: proposed by the agent, decided by the person, built
    with TestClient(app, headers=person) as c:
        # R-028 CONS #8: Set-up steps are `adapter_setup` (never `adapter_sync`); the build run
        # the hand-off started IS a sync
        feed = c.get("/activity", params={"kinds": "adapter_setup,adapter_sync",
                                          "limit": 200}).json()
    mine = [i for i in feed["items"] if i.get("run_owner") == aid]
    assert {i["kind"] for i in mine} == {"adapter_setup", "adapter_sync"}
    reasons = [i.get("reason") or "" for i in mine]
    assert any(x.startswith("Proposed by the agent: proposed a crawl plan") for x in reasons)
    assert any(x.startswith(f"Decided by {PERSON}: approved the crawl plan") for x in reasons)
    assert any(x.startswith("Proposed by the agent: proposed the mapping") for x in reasons)
    assert any(x.startswith(f"Decided by {PERSON}: approved the mapping") for x in reasons)
    assert any(x.startswith("Proposed by the agent: started the build run") for x in reasons)
    # the build is recorded started, then its TERMINAL outcome from the sync job (f1 #9)
    builds = [a for a in state_row["activity"] if a["step"] == "setup.build"]
    assert [a["status"] for a in builds] == ["started", "completed"], builds
    assert any(i.get("run_id") == syncs[-1]["run_id"] for i in mine)   # the build run itself


def test_a_source_kept_by_an_existing_agent_is_not_duplicated(estate):
    """An agent described earlier that already manages the source is its keeper; Set up does not
    create a second one."""
    from databridge_adapter_agents.setup import draft_spec

    rt = estate["rt"]
    d = draft_spec(SID, estate["vocab"].packs[0])
    d["id"] = "described-earlier"
    rt.manager.create(d, by="someone")
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        r = c.post(f"/adapter-setup/{SID}/start")
        assert r.status_code == 200, r.text
        assert r.json()["agent"] == "described-earlier"
    assert [s.id for s in rt.manager.registry.list()] == ["described-earlier"]


def test_only_read_sources_are_offered_and_a_write_connection_is_refused(estate):
    """A write connection (purpose agent_write) is never crawled: it is not listed, and Set up
    refuses it. The read source is listed (non-vacuous)."""
    from databridge_mcp_ingest import register_connection

    register_connection({"id": "feat8-write", "purpose": "agent_write",
                         "transport": {"type": "stdio", "command": "uvx"}}, advertise=False)
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        ids = {s["source_id"] for s in c.get("/adapter-setup/sources").json()["sources"]}
        assert SID in ids and "feat8-write" not in ids
        r = c.post("/adapter-setup/feat8-write/start")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NOT_A_READ_SOURCE"
    assert estate["rt"].manager.registry.list() == []


# =================================================================================================
# Codex R-027 AAX round 1 — the gates, each proven on both estates
# =================================================================================================
def _plan_approved_and_discovered(c) -> dict:
    """Set up, a person approves the plan, a capture-only discovery runs. Returns the discovery."""
    from databridge_mcp_ingest.runner import latest_discovery

    assert c.post(f"/adapter-setup/{SID}/start").status_code == 200
    digest = c.get(f"/adapter-setup/{SID}").json()["plan_digest"]
    r = c.post(f"/adapter-setup/{SID}/plan/approve", json={"plan_digest": digest})
    assert r.status_code == 200, r.text
    r = c.post(f"/adapter-setup/{SID}/discover")
    assert r.status_code == 202, r.text
    return latest_discovery(SID)


def _explain(c, *, first: bool = True) -> tuple[dict, list[str]]:
    r = c.post(f"/adapter-setup/{SID}/explain")
    assert r.status_code == 200, r.text
    ex = r.json()["explanation"]
    proposed = sorted(t["table"] for t in ex["tables"] if t["status"] == "proposed")
    assert proposed or not first                                    # non-vacuous
    return ex, proposed


def _approve(c, ex: dict, tables: list[str]):
    return c.post(f"/adapter-setup/{SID}/mapping/approve",
                  json={"tables": tables, "explanation_digest": ex["mapping_digest"]})


def _handoff(c, **body):
    """Hand off, deferring BY NAME every decision the page shows as pending."""
    pending = c.get(f"/adapter-setup/{SID}").json()["state"]["pending_decisions"]
    return c.post(f"/adapter-setup/{SID}/handoff",
                  json={**body, "deferred": [d["key"] for d in pending]})


def _build_records(c) -> list[str]:
    act = c.get(f"/adapter-setup/{SID}").json()["state"]["activity"]
    return [a["status"] for a in act if a["step"] == "setup.build"]


def test_f1_4_no_approval_and_no_handoff_without_a_current_explanation(estate):
    """#4: mapping approval and the hand-off need a SAVED explanation whose per-table digests are
    the current proposal's - even when the tables were approved elsewhere (the Sources route)."""
    from databridge_mcp_ingest.mapping import load_mapping, set_table_mapping

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        proposed = sorted(t for t, tm in load_mapping(SID).tables.items()
                          if tm.status == "proposed")
        assert proposed
        r = c.post(f"/adapter-setup/{SID}/mapping/approve",
                   json={"tables": proposed, "explanation_digest": "sha256:none"})
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "EXPLANATION_REQUIRED"
        # approved on the Sources page instead: the hand-off still needs the explanation
        assert c.post(f"/sources/{SID}/mapping/approve",
                      json={"tables": proposed}).status_code == 200
        r = c.post(f"/adapter-setup/{SID}/handoff")
        assert r.status_code == 409 and "has not explained the mapping" in r.text
        ex, _ = _explain(c, first=False)
        # a person edits one table's mapping after it was explained: the explanation is stale
        table = proposed[0]
        tm = load_mapping(SID).tables[table]
        label = next("{" + col + "}" for col in tm.columns
                     if "{" + col + "}" != tm.node.get("label"))
        set_table_mapping(SID, table, edited_by=PERSON, node={**tm.node, "label": label})
        r = c.post(f"/adapter-setup/{SID}/handoff")
        assert r.status_code == 409 and "changed since it was explained" in r.text
        r = _approve(c, ex, [table])
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "EXPLANATION_REQUIRED"
    assert rt.manager.registry.get(keeper_of()).status == "draft"


def _person():
    from databridge_agent_runtime import register_authenticator

    return register_authenticator("feat8-gw").issue(PERSON)


def keeper_of() -> str:
    from databridge_adapter_agents.setup import keeper_id

    return keeper_id(SID)


def test_f1_5_the_explanation_is_bound_to_the_discovery_that_captured_the_tables(estate):
    """#5: the evidence is the discovery's capture; a NEWER discovery makes the explanation stale
    (hand-off refused) until the agent explains again from it."""
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        first = _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert ex["discovery"]["run_id"] == first["run_id"]
        for t in ex["tables"]:
            assert t["capture_sha"] == first["tables"][t["table"]]["sha256"]
        assert _approve(c, ex, proposed).status_code == 200
        r = c.post(f"/adapter-setup/{SID}/discover")
        assert r.status_code == 202
        r = c.post(f"/adapter-setup/{SID}/handoff")
        assert r.status_code == 409 and "not the latest one" in r.text
        ex2, _ = _explain(c, first=False)
        assert ex2["discovery"]["run_id"] != first["run_id"]
        r = _handoff(c)
        assert r.status_code == 202, r.text


def test_f1_8_a_failed_discovery_satisfies_nothing(estate, monkeypatch):
    """#8: only a SUCCESSFUL discovery under the current plan counts. A failed one - even after a
    mapping was proposed and approved - blocks explanation and hand-off."""
    from databridge_mcp_ingest import runner

    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed).status_code == 200

        def broken(*a, **k):
            raise RuntimeError("the source went away mid-discovery")
        monkeypatch.setattr(runner, "_discover", broken)
        r = c.post(f"/adapter-setup/{SID}/discover")
        assert r.status_code == 202
        latest = runner.latest_discovery(SID)
        assert latest["run_id"] == r.json()["run_id"] and latest["status"] == "failed"
        state = c.get(f"/adapter-setup/{SID}").json()["state"]
        assert {s["id"]: s["status"] for s in state["steps"]}["discover"] == "blocked"
        r = c.post(f"/adapter-setup/{SID}/explain")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "DISCOVERY_REQUIRED"
        r = c.post(f"/adapter-setup/{SID}/handoff")
        assert r.status_code == 409 and "ended failed" in r.text
        assert _build_records(c) == []


def test_f1_6_a_stale_plan_never_activates_or_syncs(estate):
    """#6: the connection changes after the plan was approved. The hand-off, the manager's
    activation and every sync of an active agent refuse it (the shared stale check)."""
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.connections import load_connection

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed).status_code == 200
        assert c.get(f"/adapter-setup/{SID}").json()["state"]["handoff_problems"] == []

        def change_connection():
            spec = load_connection(SID).model_dump(mode="json", exclude_none=True)
            spec["scope"] = {**spec["scope"], "tables_include": list(estate["scope"])[::-1]
                             + [estate["scope"][0]]}
            register_connection(spec, overwrite=True)
        change_connection()
        r = c.post(f"/adapter-setup/{SID}/handoff")
        assert r.status_code == 409 and "stale" in r.text
    with pytest.raises(Exception, match="revision"):
        rt.manager.activate(keeper_of(), by=_person())
    assert rt.manager.registry.get(keeper_of()).status == "draft"


def test_f1_6_an_active_agent_whose_plan_went_stale_does_not_sync(estate):
    from databridge_mcp_ingest import register_connection
    from databridge_mcp_ingest.connections import load_connection

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed).status_code == 200
        assert _handoff(c).status_code == 202
    assert rt.manager.registry.get(keeper_of()).status == "active"
    spec = load_connection(SID).model_dump(mode="json", exclude_none=True)
    spec["scope"] = {**spec["scope"], "tables_include": list(estate["scope"])[::-1]
                     + [estate["scope"][0]]}
    register_connection(spec, overwrite=True)
    summary = rt.manager.sync(keeper_of(), trigger={"type": "manual", "by": PERSON})
    assert summary.status == "refused" and "revision" in (summary.reason or "")
    assert summary.connections == []                                # nothing ran


def test_f1_7_an_active_keeper_without_a_seal_is_not_handed_off(estate):
    """#7: an agent that is already ACTIVE was not activated by this person here. Without a sealed
    delegation in force the hand-off refuses and schedules nothing."""
    from databridge_adapter_agents.setup import draft_spec

    rt = estate["rt"]
    d = draft_spec(SID, estate["vocab"].packs[0])
    d.update({"id": "described-earlier", "status": "active", "plans": {SID: "approved"}})
    rt.manager.create(d, by="someone")                       # a NAME: active, but no seal
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed).status_code == 200
        spec = rt.manager.registry.get("described-earlier")
        assert spec.status == "active" and rt.manager.delegator(spec) is None
        r = _handoff(c)
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "KEEPER_NOT_SEALED"
        assert rt.jobs.busy("adapter_sync", "described-earlier") is None
    assert rt.manager.run_records("described-earlier") == []


def test_f1_9_a_build_that_fails_is_recorded_failed_never_completed(estate, monkeypatch):
    """#9: the hand-off records the build `started`, then the sync job's TERMINAL outcome."""
    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed).status_code == 200

        def broken_sync(*a, **k):
            raise RuntimeError("the store is unavailable")
        monkeypatch.setattr(rt.manager, "sync", broken_sync)
        assert _handoff(c).status_code == 202
        assert _build_records(c) == ["started", "failed"]


# =================================================================================================
# Codex R-027 AAX round 2 — every fix end to end, on both estates (and a document-only source)
# =================================================================================================
def _to_approved(c) -> tuple[dict, list[str]]:
    _plan_approved_and_discovered(c)
    ex, proposed = _explain(c)
    assert _approve(c, ex, proposed).status_code == 200
    return ex, proposed


def test_f2_1_the_generic_routes_cannot_activate_or_sync_a_setup_draft(estate):
    """#1: after Set up, /adapter-agents/{keeper}/activate and /sync refuse until the person's
    hand-off - even with the plan and the mapping approved - and nothing is built."""
    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _to_approved(c)
        r = c.post(f"/adapter-agents/{keeper_of()}/activate")
        assert r.status_code == 409 and "no person has authorised the hand-off" in r.text
        r = c.post(f"/adapter-agents/{keeper_of()}/sync")
        assert r.status_code == 409
        assert rt.manager.registry.get(keeper_of()).status == "draft"
        assert _nodes(estate["store"]) == 0
        # the hand-off authorises it; then the generic sync runs (sealed, authorised)
        assert _handoff(c).status_code == 202
        assert c.post(f"/adapter-agents/{keeper_of()}/sync").status_code == 202
        # once its sealed delegation is no longer in force, the generic sync refuses up front
        rt.manager.delegation_status = lambda spec: (None, "revoked by a scope change")
        try:
            r = c.post(f"/adapter-agents/{keeper_of()}/sync")
        finally:
            del rt.manager.delegation_status
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "ADAPTER_AGENT_GATED"
        assert "sealed delegation" in r.text


def test_f2_1_the_manager_refuses_a_setup_agent_whose_setup_changed_after_authorisation(estate):
    """#1: the authorisation covers the setup as it was; a newer discovery makes activation
    refuse (the manager re-verifies it), even for a person."""
    from databridge_adapter_agents.setup import gate_binding, load_gate

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _to_approved(c)
        g = load_gate(SID, rt.manager.registry.root, plan_stale=None)
        rt.manager.authorize_setup_handoff(keeper_of(), by=_person(), binding=gate_binding(
            plan_digest=g["plan_digest"], discovery=g["discovery"], mapping=g["mapping"],
            explanation=g["explanation"], documents=g["documents_decision"],
            deferred=[d["key"] for d in g["pending"]]))
        assert c.post(f"/adapter-setup/{SID}/discover").status_code == 202
    with pytest.raises(Exception, match="changed since|not the latest"):
        rt.manager.activate(keeper_of(), by=_person())
    assert rt.manager.registry.get(keeper_of()).status == "draft"


def test_f2_2_a_machine_never_activates_or_hands_off(estate, monkeypatch):
    """#2: a machine principal is refused by the hand-off AND by the manager's activation; a
    setup agent is never activated by a typed name; no seal, no build."""
    from databridge_agent_runtime import register_authenticator

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _to_approved(c)
    agent = _agent_token(monkeypatch, keeper_of())
    with TestClient(estate["app"], headers=agent) as c:
        r = _handoff(c)
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "MACHINE_ACTOR"
        r = c.post(f"/adapter-agents/{keeper_of()}/activate")
        assert r.status_code >= 400 and "machine actor" in r.text
    machine = register_authenticator("feat8-gw").issue(f"adapter_agent:{keeper_of()}")
    for by in (machine, "alice", "system"):
        with pytest.raises(Exception, match="machine actor|authenticated person"):
            rt.manager.activate(keeper_of(), by=by)
    with pytest.raises(Exception, match="machine actor"):
        rt.manager.authorize_setup_handoff(keeper_of(), by=machine, binding={})
    assert rt.manager.registry.get(keeper_of()).status == "draft"
    assert rt.manager.run_records(keeper_of()) == []


def test_f2_2_no_seal_no_build(estate, monkeypatch):
    """#2: if the sealed delegation is not in force after activation, nothing is scheduled and
    the agent does not stay active."""
    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _to_approved(c)
        monkeypatch.setattr(rt.manager, "delegation_status",
                            lambda spec: (None, "the seal could not be minted"))
        r = _handoff(c)
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NO_SEAL"
        assert _build_records(c) == []
    assert rt.manager.registry.get(keeper_of()).status == "paused"
    assert rt.manager.run_records(keeper_of()) == [] and _nodes(estate["store"]) == 0


def test_f2_6_mapping_approval_never_races_a_run(estate):
    """#6: validation and approval hold the source's run lock; while a run (or discovery) holds
    it, the approval is refused - nothing is approved that was not explained."""
    from databridge_mcp_ingest.mapping import load_mapping
    from databridge_mcp_ingest.runner import source_run_lock

    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        with source_run_lock(SID):
            r = _approve(c, ex, proposed)
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "RUN_IN_PROGRESS"
        assert all(load_mapping(SID).tables[t].status == "proposed" for t in proposed)
        r = _approve(c, ex, proposed)
        assert r.status_code == 200
        explained = {t["table"]: t["mapping_digest"] for t in ex["tables"]}
        m = load_mapping(SID)
        assert all(m.tables[t].approval["digest"] == explained[t] for t in proposed)


def test_f2_8_every_pending_decision_is_deferred_by_name(estate):
    """#8: tables left proposed (with any split), held material, proposed links and undecided
    identity rules are pending; the hand-off refuses until each is named as deferred, and the
    authorisation records exactly which."""
    from databridge_adapter_agents.setup import latest_authorization

    rt = estate["rt"]
    with TestClient(estate["app"], headers=auth_headers(PERSON)) as c:
        _plan_approved_and_discovered(c)
        ex, proposed = _explain(c)
        assert _approve(c, ex, proposed[:1]).status_code == 200
        pending = c.get(f"/adapter-setup/{SID}").json()["state"]["pending_decisions"]
        keys = [d["key"] for d in pending]
        assert {f"table:{t}" for t in proposed[1:]} <= set(keys)
        assert all(d["what"] and d["deferral"] in ("defer", "keep held") for d in pending)
        if keys:
            for partial in ([], keys[:-1], [*keys, "link:not-pending"]):
                r = c.post(f"/adapter-setup/{SID}/handoff", json={"deferred": partial})
                assert r.status_code == 409 and r.json()["refusal"]["code"] == \
                    "DECISIONS_PENDING", partial
        assert rt.manager.registry.get(keeper_of()).status == "draft"
        assert _handoff(c).status_code == 202
    auth = latest_authorization(rt.manager.registry.root, SID, keeper_of())
    assert auth["principal_id"] == PERSON and auth["binding"]["deferred"] == sorted(keys)


# --- a document-only source -------------------------------------------------------------------
def _mi_conftest():
    import importlib.util

    spec = importlib.util.spec_from_file_location("mi_conftest_feat8", MI_TESTS / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def docs_source(env, tmp_path, monkeypatch):
    from databridge_adapter_agents import AdapterAgentManager, AdapterAgentRegistry
    from databridge_adapter_agents.ingest_service import McpIngestService
    from databridge_graph.store import GraphStore

    from databridge_mcp_ingest import register_connection

    from databridge_gateway.activity_routes import register_activity_routes
    from databridge_gateway.platform_runtime import IngestFacade

    mic = _mi_conftest()
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")
    monkeypatch.setenv("WALNUT_MCP_POOL", "0")
    root = tmp_path / "corpus"
    root.mkdir()
    mic.write_corpus(root)
    register_connection({**mic.fs_connection(SID, root), "auto_approve": False})
    store = GraphStore(str(tmp_path / "g.db"))
    app, rt, _ = _mount(env)
    app.state.graph_store = store
    register_activity_routes(app)
    rt.ingest = IngestFacade()
    rt.harness = None
    rt.manager = AdapterAgentManager(service=McpIngestService(), registry=AdapterAgentRegistry(),
                                     store=store)
    return {"app": app, "rt": rt, "store": store}


def test_f2_7_a_document_only_source_finishes_setup_with_a_persons_documents_decision(
        docs_source):
    """#7: capture-only discovery first (no document built); no table-mapping gate; a person
    decides to build exactly the discovered documents; then the hand-off builds them."""
    from databridge_mcp_ingest.runner import load_state

    app, rt = docs_source["app"], docs_source["rt"]
    with TestClient(app, headers=auth_headers(PERSON)) as c:
        assert c.post(f"/adapter-setup/{SID}/start").status_code == 200, "start"
        d = c.get(f"/adapter-setup/{SID}").json()
        vouch = [t for t, k in (d["plan"].get("tool_classes") or {}).items()
                 if str(k).startswith("unclassified")]
        r = c.post(f"/adapter-setup/{SID}/plan/approve",
                   json={"plan_digest": d["plan_digest"], "vouch_read_tools": vouch})
        assert r.status_code == 200, r.text
        assert c.post(f"/adapter-setup/{SID}/discover").status_code == 202
        assert load_state(SID) == {} and _nodes(docs_source["store"]) == 0   # nothing built
        state = c.get(f"/adapter-setup/{SID}").json()["state"]
        docs = state["documents"]
        assert docs["allowed"] > 0 and docs["digest"] and not docs["decided"]
        steps = {s["id"]: s["status"] for s in state["steps"]}
        assert steps["discover"] == "done" and steps["approve_mapping"] == "done"
        assert steps["approve_documents"] == "waiting_for_person"
        r = c.post(f"/adapter-setup/{SID}/explain")
        assert r.status_code == 409 and r.json()["refusal"]["code"] == "NO_TABLES"
        r = _handoff(c)
        assert r.status_code == 409 and "decided to build" in r.text
        r = c.post(f"/adapter-setup/{SID}/documents/approve",
                   json={"discovery_run_id": docs["discovery_run_id"],
                         "documents_digest": "sha256:not-what-was-shown"})
        assert r.status_code == 409 and (r.json()["refusal"]["code"], r.json()["refusal"].get("reason_code")) == (STANDARD_FOR["DOCUMENTS_CHANGED"], "DOCUMENTS_CHANGED")
        r = c.post(f"/adapter-setup/{SID}/documents/approve",
                   json={"discovery_run_id": docs["discovery_run_id"],
                         "documents_digest": docs["digest"]})
        assert r.status_code == 200, r.text
        assert r.json()["state"]["documents"]["decided"] is True
        r = _handoff(c)
        assert r.status_code == 202, r.text
    built = [e for e in load_state(SID).values() if e.get("graph") is not False]
    assert len(built) >= docs["allowed"] - docs["held"] and _nodes(docs_source["store"]) > 0
