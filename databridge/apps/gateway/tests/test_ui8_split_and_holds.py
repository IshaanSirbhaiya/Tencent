"""UI8 (finance dry run, 2026-10-02): the Sources page shows a table's per-row KIND SPLIT before it
is approved, and the screen's HOLDS with an owner action to lift one.

Driven end to end on TWO estates, through real syncs (`run_ingest` on the estate's own rows) and
the real gateway routes (`rt.ingest` is the real `IngestFacade`):

1. The first sync's screen HOLDS the ROW of the mixed people/organisation table that carries the
   planted note (E2E run 5 F-01: rows are held one by one, never the whole table; finance: the note in
   `core_banking.party`; workspace: the same note, copied from the finance row that carries it,
   in a table assembled from Larkspur's own employees and customers).
   `GET /sources/{id}/holds` lists exactly the run ledger's review decisions, with the rules that
   fired and the screen's reason. An owner lifts that ROW's hold with `POST /sources/{id}/overrides`
   (refused without a credential); the hold then reads "lifted, by whom".
2. The next sync captures the table and PROPOSES its kind split. `GET /sources/{id}/mapping/review`
   shows it before approval: the column, every value -> kind with its row count, and the rows
   that stay the subject (unbound / bound to the subject / NULL / masked). Every count is the
   test's own count of the estate's rows under the test's own normalisation.
3. Approving the table (`POST /sources/{id}/mapping/approve`, the existing authenticated route)
   approves the split: the approval's digest covers the mapping WITH the split.

The workspace pack declares no person/organisation alternative, so (as in ID7) its estate binds a
copy of the workspace pack with one sentence cross-referencing `organisation` from `person`.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import auth_headers
from test_platform_routes import _mount, env  # noqa: F401  (fixture)

MI_TESTS = Path(__file__).resolve().parents[3] / "packages" / "mcp_ingest" / "tests"
if str(MI_TESTS) not in sys.path:
    sys.path.insert(0, str(MI_TESTS))

import estates  # noqa: E402

SID = "ui8_src"
ESTATE_NAMES = ("workspace", "finance")


# --- the test's OWN statement of the value rule (never imported from the code under test) --------
def norm_value(v) -> str:
    t = unicodedata.normalize("NFKC", str(v)).casefold()
    return " ".join(re.findall(r"[^\W_]+", t))


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


def _planted_note(tmp_path: Path) -> str:
    """The text the finance estate plants for an automated reader: the one `core_banking.party`
    cell the screen's QUARANTINE rules fire on (found by the screen, not written down here)."""
    from databridge_mcp_ingest.screen import load_screen, screen_text

    (tmp_path / "fin_note").mkdir(exist_ok=True)
    db = estates.build_estate("finance", tmp_path / "fin_note" / "e.sqlite")
    cfg = load_screen()
    try:
        for row in db.execute("SELECT * FROM core_banking.party"):
            for v in dict(row).values():
                if isinstance(v, str) and len(v) > 20:
                    res = screen_text(v, cfg)
                    if res.decision == "review" and any(
                            f.get("kind") == "quarantine" for f in res.fired):
                        return v
    finally:
        db.close()
    raise AssertionError("the finance estate plants no note for an automated reader")


def _workspace_mixed(db: sqlite3.Connection, note: str) -> None:
    """Larkspur's own employees (people) and customers (organisations) in ONE table, the entry
    type spelled several ways, one row with no type, and one row carrying the planted note."""
    db.execute("CREATE TABLE public.persons (entry_id TEXT PRIMARY KEY, entry_type TEXT, "
               "name TEXT, remarks TEXT)")
    emp = [dict(r) for r in db.execute("SELECT employee_id, full_name FROM public.employees")]
    cust = [dict(r) for r in db.execute("SELECT customer_id, name FROM public.customers")]
    spell_p, spell_o = ("Person", "PERSON", "person"), ("ORGANISATION", "organisation", "Org")
    rows = [(str(r["employee_id"]), spell_p[i % 3], r["full_name"], None)
            for i, r in enumerate(emp)]
    rows += [(str(r["customer_id"]), spell_o[i % 3], r["name"], None)
             for i, r in enumerate(cust)]
    rows[0] = (rows[0][0], None, rows[0][2], None)                      # no type: stays subject
    rows[1] = (rows[1][0], rows[1][1], rows[1][2], note)                # the planted note
    db.executemany("INSERT INTO public.persons VALUES (?, ?, ?, ?)", rows)
    db.commit()


@pytest.fixture(params=ESTATE_NAMES)
def synced(request, env, tmp_path, monkeypatch):
    """A registered estate source whose plan a person approved, synced once, with the gateway
    mounted on the REAL ingest facade."""
    from databridge_graph.packs import DEFAULT_PACKS_ROOT, clear_caches, deployment
    from databridge_graph.store import GraphStore

    import databridge_mcp_ingest as mi
    from databridge_agent_runtime import register_authenticator
    from databridge_mcp_ingest import plans, register_connection

    from databridge_gateway.platform_runtime import IngestFacade

    name = request.param
    monkeypatch.setenv("WALNUT_SOURCE_REGISTRATION", "operator")
    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_profile(tmp_path, name)))
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.delenv("DATABRIDGE_PACKS_ROOT", raising=False)
    db = estates.build_estate(name, tmp_path / "e.sqlite")
    if name == "workspace":
        note = _planted_note(tmp_path)
        _workspace_mixed(db, note)
        root = tmp_path / "packs_copy"
        (root / "workspace").mkdir(parents=True)
        text = (DEFAULT_PACKS_ROOT / "workspace" / "ontology.yaml").read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines()
                    if ln.strip().startswith("description: A natural"))
        (root / "workspace" / "ontology.yaml").write_text(
            text.replace(line, line + " Use `organisation` for a company."), encoding="utf-8")
        monkeypatch.setenv("DATABRIDGE_PACKS_ROOT", str(root))
        mixed = "public.persons"
    else:
        mixed = "core_banking.party"
    clear_caches()
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        store = GraphStore(str(tmp_path / "g.db"))
        client = estates.engine_client(db)
        register_connection({"id": SID, "transport": {"type": "stdio", "command": "uvx"},
                             "scope": {"tables_include": [mixed]}})
        mi.propose_plan(SID, client=client)
        plans.approve_plan(SID, principal=register_authenticator("ui8-tests").issue("owner-1"),
                           client=client)
        rep = mi.run_ingest(SID, store=store, client=client, vocab=vocab)
        assert rep.status in ("ok", "unverified", "empty"), (rep.status, rep.message)
        app, rt, _ = _mount(env)
        rt.ingest = IngestFacade()

        def sync():
            r = mi.run_ingest(SID, store=store, client=client, vocab=vocab)
            assert r.status in ("ok", "unverified", "empty"), (r.status, r.message)
            return r

        rows = [dict(r) for r in db.execute(f"SELECT * FROM {mixed}")]
        yield {"name": name, "app": app, "vocab": vocab, "mixed": mixed, "rows": rows,
               "run_dir": Path(rep.run_dir), "sync": sync}
    clear_caches()
    db.close()


def _ledger(run_dir: Path) -> list[dict]:
    p = run_dir / "governance.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# --- 1. the screen's holds, and the owner's action ------------------------------------------------
def _row_holds(ledger: list[dict], table: str) -> list[dict]:
    """The run ledger's REVIEW decisions on ROWS of `table` (E2E run 5 F-01: a planted cell holds
    its ROW, never the table). Read by the ledger's own `kind`/`table` fields."""
    from databridge_mcp_ingest.runner import TABLE_ROW

    return [r for r in ledger if r.get("decision") == "review"
            and r.get("kind") == TABLE_ROW and r.get("table") == table]


def test_a_held_row_is_listed_with_its_reason_and_an_owner_lifts_it(synced):
    app, mixed = synced["app"], synced["mixed"]
    ledger = _ledger(synced["run_dir"])
    review = [r for r in ledger if r.get("decision") == "review"]
    rows_held = _row_holds(ledger, mixed)
    assert rows_held                                                      # non-vacuous
    # the table itself is ALLOWED (its metadata), and it counts the rows it holds
    [table_dec] = [r for r in ledger if r.get("uri") == mixed and r.get("kind") != "table_row"]
    assert table_dec["decision"] == "allow"
    assert table_dec["rows_held"]["review"] == len(rows_held)
    assert table_dec["rows_screened"] == len(synced["rows"])
    # only the rows carrying the planted note are held: the rest of the table is not
    assert len(rows_held) < len(synced["rows"])
    with TestClient(app) as anon:
        assert anon.get(f"/sources/{SID}/holds").status_code == 401
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        got = c.get(f"/sources/{SID}/holds")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["run_id"] == synced["run_dir"].name
        assert {(h["item_id"], h["digest"]) for h in body["holds"]} ==             {(r["item_id"], r["sha256"]) for r in review}
        want = rows_held[0]
        [h] = [h for h in body["holds"] if h["item_id"] == want["item_id"]]
        assert h["item_kind"] == "table_row" and h["uri"] == want["uri"]
        assert h["uri"].startswith(mixed + "#")
        assert [x["rule"] for x in h["rules"]] == [f["rule"] for f in want["fired"]]
        assert h["rationale"] == want["rationale"] and h["reverse"] == want["reverse"]
        assert h["rationale"] and h["lifted"] is False and h["lifted_by"] is None
        lift = {"item_id": h["item_id"], "digest": h["digest"], "reason": "note read; not data"}
        with TestClient(app) as anon:
            assert anon.post(f"/sources/{SID}/overrides", json=lift).status_code == 401
        r = c.post(f"/sources/{SID}/overrides", json={**lift, "principal_id": "mallory"})
        assert r.status_code == 200, r.text
        [h] = [x for x in c.get(f"/sources/{SID}/holds").json()["holds"]
               if x["item_id"] == want["item_id"]]
        assert h["lifted"] is True and h["lifted_by"] == "analyst-7"           # never a body name
        assert h["lifted_reason"] == "note read; not data" and h["lifted_dev_only"] is False
        # lifting one row lifts THAT row only
        others = [x for x in c.get(f"/sources/{SID}/holds").json()["holds"]
                  if x["item_id"] != want["item_id"]]
        assert all(x["lifted"] is False for x in others)
    # the next sync builds past the hold: the lifted row is no longer held
    rep = synced["sync"]()
    after = _ledger(Path(rep.run_dir))
    assert not [r for r in _row_holds(after, mixed) if r["item_id"] == want["item_id"]]
    assert len(_row_holds(after, mixed)) == len(rows_held) - 1
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        assert not [x for x in c.get(f"/sources/{SID}/holds").json()["holds"]
                    if x["item_id"] == want["item_id"]]


# --- 2 + 3. the kind split, shown before approval and approved with the table ---------------------
def _lift_and_resync(synced):
    from databridge_mcp_ingest.screen import approve_override
    from databridge_agent_runtime import register_authenticator

    for r in _ledger(synced["run_dir"]):
        if r.get("decision") == "review":
            approve_override(SID, r["item_id"], digest=r["sha256"],
                             principal=register_authenticator("ui8-tests").issue("owner-1"),
                             reason="planted note reviewed")
    return synced["sync"]()


def test_the_split_is_shown_with_measured_counts_and_approved_with_the_table(synced):
    from databridge_mcp_ingest.mapping import (approval_problem, is_person_kind, load_mapping,
                                               mapping_digest)

    _lift_and_resync(synced)
    app, mixed, vocab, rows = synced["app"], synced["mixed"], synced["vocab"], synced["rows"]
    tm = load_mapping(SID).tables[mixed]
    assert tm.status == "proposed" and tm.split                           # non-vacuous
    split = tm.split
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        sp = c.get(f"/sources/{SID}/mapping/review").json()["splits"][mixed]
    col, default, bound = split["column"], split["default"], split["kinds"]
    assert (sp["column"], sp["default"], sp["status"]) == (col, default, "proposed")
    assert sp["default_is_subject"] is True and sp["default_is_person"] is True
    assert vocab.is_subject(default) and is_person_kind(vocab, default)
    # the test's own count of the estate's rows
    counts: dict[str, int] = {}
    nulls = 0
    for r in rows:
        v = r[col]
        if v is None or not str(v).strip():
            nulls += 1
            continue
        counts[norm_value(v)] = counts.get(norm_value(v), 0) + 1
    assert sp["rows_total"] == len(rows)
    assert {x["value"]: x["rows"] for x in sp["values"]} == counts
    for x in sp["values"]:
        assert x["kind"] == bound.get(x["value"], default)
        assert x["stays_subject"] is (x["kind"] == default)
        assert x["bound"] is (x["value"] in bound)
    moved = sum(n for v, n in counts.items() if bound.get(v, default) != default)
    assert sp["rows_moved"] == moved > 0
    stay = sp["rows_stay"]
    assert stay["masked"] == 0                         # no discriminator value is a masked shape
    assert stay["null_or_blank"] == nulls
    assert stay["unrecognised"] + stay["bound_to_subject"] + nulls == len(rows) - moved
    moved_kinds = {k["kind"] for k in sp["kinds"] if not k["is_default"]}
    assert moved_kinds == {k for k in bound.values() if k != default}
    assert all(not k["person"] for k in sp["kinds"] if not k["is_default"])
    if synced["name"] == "workspace":
        assert nulls == 1                               # the row the test left without a type
    # approving the table approves the split (the existing authenticated route)
    with TestClient(app) as anon:
        assert anon.post(f"/sources/{SID}/mapping/approve",
                         json={"tables": [mixed]}).status_code == 401
    with TestClient(app, headers=auth_headers("analyst-7")) as c:
        ok = c.post(f"/sources/{SID}/mapping/approve", json={"tables": [mixed]})
        assert ok.status_code == 200, ok.text
        sp2 = c.get(f"/sources/{SID}/mapping/review").json()["splits"][mixed]
    tm = load_mapping(SID).tables[mixed]
    assert tm.status == "approved" and tm.split == split
    assert tm.approval["principal_id"] == "analyst-7"
    assert tm.approval["digest"] == mapping_digest(tm)
    assert mapping_digest(tm) != mapping_digest(tm.model_copy(update={"split": None}))
    assert approval_problem(SID, mixed, tm, vocab) is None
    assert sp2["status"] == "approved" and sp2["values"] == sp["values"]


def test_a_table_without_a_split_shows_none(synced):
    """Non-vacuous the other way: a mapping with no split is not listed in `splits`."""
    from databridge_mcp_ingest.mapping import load_mapping, save_mapping

    _lift_and_resync(synced)
    m = load_mapping(SID)
    mixed = synced["mixed"]
    save_mapping(m.model_copy(update={"tables": {**m.tables, mixed: m.tables[mixed].model_copy(
        update={"split": None})}}))
    with TestClient(synced["app"], headers=auth_headers("analyst-7")) as c:
        assert c.get(f"/sources/{SID}/mapping/review").json()["splits"] == {}
