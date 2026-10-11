"""Finance dry run 2026-10-02 (wiki/40-reviews/FIN-live-dry-run-2026-10-02.md), fixed by ANS6.

* FIN-12: "How many facilities have status watchlist, and what is their total limit by currency?"
  (a) The plan filtered ``status = 'watchlist'``: 3 rows. The column stores the value in three
      spellings (WATCHLIST 25, Watchlist 5, watchlist 3), so 30 of the 33 were never read.
  (b) The count was DROPPED: the pushdown statement computed ``COUNT(*)`` per group and threw it
      away, and the planner had discarded its separate count read.
  Now a text ``eq`` filter reads every stored spelling that differs only in letter case or
  spacing (stated as an assumption, naming each spelling), the count is a cited column beside the
  total, and groups that are one value spelled differently (``SGD``/``sgd``) are said, not merged.
* FIN-15: /brain could not count anything until someone created an adapter agent: the federated
  catalogue was built from adapter agents only. A declared connection no adapter agent keeps is
  now offered through its own read-only facts store, still under the caller's grant.

Two estates, each loaded from this repo's seed SQL into a REAL facts store (SQLite + manifest,
read through `count_query`): the finance core-banking ``facility`` table and the Larkspur
workspace ``invoices`` table. Every expectation is computed from the same rows with plain Python,
never written down.
"""
from __future__ import annotations

import json
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from databridge_federated import CatalogueAdmission, DelegationPlan, build_service
from databridge_federated.pointers import rows_digest

REPO = Path(__file__).resolve().parents[3]
FIN = REPO / "fixtures" / "packs" / "finance"
LARKSPUR = REPO / "fixtures" / "workspace_demo"
ADMIN = CatalogueAdmission("dana.ops")


def _rows(paths, table: str) -> tuple[list[str], list[dict]]:
    db = sqlite3.connect(":memory:")
    for f in paths:
        sql = Path(f).read_text("utf-8")
        for m in re.finditer(r"INSERT INTO (?:\w+\.)?(\w+) \(([^)]*)\) VALUES(.*?);\s*$", sql,
                             re.S | re.M):
            t, cols, vals = m.groups()
            if t != table:
                continue
            cols = [c.strip() for c in cols.split(",")]
            db.execute(f'CREATE TABLE IF NOT EXISTS "{t}" ('
                       + ", ".join(f'"{c}"' for c in cols) + ")")
            db.execute(f'INSERT INTO "{t}" ({", ".join(cols)}) VALUES {vals}')
    cur = db.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    return cols, [dict(zip(cols, r)) for r in cur.fetchall()]


#: estate -> (connection, table, rows-source, key, filter column, amount column, group column)
ESTATES = {
    "finance": ("finance_core_banking", "facility",
                sorted((FIN / "core_banking" / "seed").glob("05_facility*.sql")),
                "facility_id", "status", "limit_amount", "currency"),
    "larkspur": ("larkspur_db", "invoices", [LARKSPUR / "db" / "01_schema_and_data.sql"],
                 "invoice_no", "status", "total_gbp", "customer_id"),
}


def _write_facts(facts_dir: Path, source: str, table: str, cols: list[str], rows: list[dict]):
    from databridge_mcp_ingest.tabular.facts import sha256_file
    facts_dir.mkdir(parents=True, exist_ok=True)
    path = facts_dir / f"{source}_facts.sqlite"
    db = sqlite3.connect(path)
    num = {c for c in cols if all(isinstance(r[c], (int, float)) or r[c] is None for r in rows)}
    db.execute(f'CREATE TABLE "{table}" (' + ", ".join(
        f'"{c}" {"NUMERIC" if c in num else "TEXT"}' for c in cols) + ")")
    db.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in cols)})',
                   [tuple(r[c] for c in cols) for r in rows])
    db.execute("CREATE TABLE _facts_meta (key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    db.close()
    (facts_dir / f"{source}_facts.json").write_text(json.dumps(
        {"source_id": source, "extracted_at": "2026-10-01T17:08:19+00:00", "status": "ok",
         "sha256": sha256_file(path), "tables": []}), encoding="utf-8")


def _fold(v) -> str:
    return " ".join(str(v).casefold().split())


@pytest.fixture
def estates(tmp_path, monkeypatch):
    """Both estates' facts stores, both connections DECLARED, and NO adapter agent."""
    for name, sub in {"WALNUT_FACTS_DIR": "facts", "WALNUT_ADAPTER_AGENTS_DIR": "adapter_agents",
                      "WALNUT_RUNS_DIR": "runs", "WALNUT_CAPTURE_DIR": "capture",
                      "WALNUT_SOURCES_DIR": "sources"}.items():
        monkeypatch.setenv(name, str(tmp_path / sub))
    (tmp_path / "adapter_agents").mkdir()
    (tmp_path / "sources").mkdir()
    out = {}
    for est, (conn, table, files, key, fcol, acol, gcol) in ESTATES.items():
        cols, rows = _rows(files, table)
        assert rows, est
        _write_facts(tmp_path / "facts", conn, table, cols, rows)
        from fed_testkit import write_mapping       # the approved mapping (Codex R-029 r1)
        write_mapping(tmp_path / "sources", conn, {table: list(cols)})
        (tmp_path / "sources" / f"{conn}.yaml").write_text(f"id: {conn}\n", encoding="utf-8")
        out[est] = {"conn": conn, "table": table, "rows": rows, "key": key, "fcol": fcol,
                    "acol": acol, "gcol": gcol}
    return tmp_path, out


def _service(root: Path):
    from databridge_agent_runtime import McpFactsStore
    from databridge_graph.packs import deployment
    with deployment("workspace") as vocab:
        return build_service(facts=McpFactsStore(), adapter_agents_root=root / "adapter_agents",
                             vocabulary=vocab, runs_dir=root / "runs")


def _value_asked(e: dict) -> str:
    """The value the question names, as a person types it: the LOWER-case spelling of the most
    frequent value of the filter column (derived from the rows)."""
    counts: dict[str, int] = {}
    for r in e["rows"]:
        counts[_fold(r[e["fcol"]])] = counts.get(_fold(r[e["fcol"]]), 0) + 1
    return max(counts, key=lambda k: (counts[k], k))


# ================================================================================================
# FIN-15: counting works with no adapter agent at all
# ================================================================================================
@pytest.mark.parametrize("est", sorted(ESTATES))
def test_fin15_a_declared_source_is_counted_with_no_adapter_agent(estates, est):
    root, es = estates
    e = es[est]
    svc = _service(root)
    assert not any((root / "adapter_agents").iterdir())          # really no adapter agent
    assert e["conn"] in svc.catalogue.agents                     # offered through its facts store
    entry = svc.catalogue.agent(e["conn"]).connection(e["conn"])
    assert entry.channel == "tabular" and entry.table(e["table"]) is not None
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "t1", "adapter_agent": e["conn"], "entity_kinds": [e["table"]],
         "aggregate": {"op": "count"}}]})
    ans = svc.answer(f"how many {e['table']} are there?", admission=ADMIN, plan=plan)
    assert ans.status == "completed", ans.answer_markdown
    assert ans.table["rows"][0]["cells"]["count"]["value"] == len(e["rows"])
    assert e["conn"] in ans.coverage["searched"]
    # the caller's grant still decides: a principal granted nothing reads nothing
    none = svc.answer("q", admission=CatalogueAdmission("eve", set()), plan=plan)
    assert not none.table.get("rows") or all(
        c is None for r in none.table["rows"] for c in r["cells"].values())
    assert e["conn"] not in none.coverage["searched"]


def test_fin15_a_source_an_adapter_agent_keeps_is_not_offered_twice_and_a_paused_one_not_at_all(
        estates):
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    root, es = estates
    kept, paused = es["finance"]["conn"], es["larkspur"]["conn"]
    for aid, conn, status in (("keeper", kept, "active"), ("sleeper", paused, "paused")):
        spec = AdapterAgentSpec.model_validate({
            "id": aid, "title": aid, "created_from": f"keep {conn} right", "connections": [conn],
            "extract": {"pack": "workspace"}, "status": status})
        (root / "adapter_agents" / f"{aid}.yaml").write_text(dump_spec(spec), encoding="utf-8")
    svc = _service(root)
    assert set(svc.catalogue.agents) == {"keeper"}               # no direct twin, no paused source
    assert svc.catalogue.agent("keeper").connection(kept) is not None


# ================================================================================================
# FIN-12: every spelling of the value is read, and the count is stated beside the total
# ================================================================================================
@pytest.mark.parametrize("est", sorted(ESTATES))
def test_fin12_how_many_and_what_total_reads_every_spelling_and_keeps_the_count(estates, est):
    root, es = estates
    e = es[est]
    asked = _value_asked(e)
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "t1", "adapter_agent": e["conn"], "entity_kinds": [e["table"]],
         "filters": [{"field": e["fcol"], "op": "eq", "value": asked}],
         "fields": [e["key"], e["fcol"], e["acol"], e["gcol"]],
         "aggregate": {"op": "sum", "field": e["acol"], "group_by": [e["gcol"]]}}]})
    ans = _service(root).answer("how many, and what total?", admission=ADMIN, plan=plan)
    assert ans.status == "completed", ans.answer_markdown

    # the truth, by hand: every row whose value is the asked one once case and spacing are out
    match = [r for r in e["rows"] if _fold(r[e["fcol"]]) == _fold(asked)]
    spellings = sorted({r[e["fcol"]] for r in match})
    want: dict[str, list[dict]] = {}
    for r in match:
        want.setdefault(str(r[e["gcol"]]), []).append(r)
    got = {str(row["cells"][e["gcol"]]["value"]): row["cells"] for row in ans.table["rows"]}
    assert set(got) == set(want)
    label = f"sum_{e['acol']}"
    for g, rows in want.items():
        cells = got[g]
        assert Decimal(str(cells[label]["value"])) == sum(Decimal(str(r[e["acol"]])) for r in rows)
        # (b) the count is THERE, and cited to the key cell of exactly the rows it counted
        assert cells["count"]["value"] == len(rows)
        p = cells["count"]["pointer"]
        keys = [str(r[e["key"]]) for r in rows]
        assert p["row_count"] == len(rows) and p["rows_digest"] == rows_digest(keys)
        assert {d["row_id"] for d in p["derived_from"]} == set(keys)
        assert {d["column"] for d in p["derived_from"]} == {e["key"]}

    # (a) the reading is said, naming every spelling, exactly when there is more than one
    said = [a for a in (ans.plan or {}).get("assumptions", []) if "letter case" in a]
    if len(spellings) > 1 or spellings != [asked]:
        assert len(said) == 1 and all(repr(s) in said[0] for s in spellings)
    else:
        assert said == []
    # groups that are one value spelled differently are said, never merged
    folded: dict[str, set] = {}
    for g in want:
        folded.setdefault(_fold(g), set()).add(g)
    split = [v for v in folded.values() if len(v) > 1]
    reasons = " ".join(ans.coverage.get("reasons") or [])
    assert all(all(repr(s) in reasons for s in v) for v in split)
    if not split:
        assert "several spellings" not in reasons


def test_fin12_the_estates_cover_both_branches():
    """Non-vacuous: the finance estate stores its status in several spellings AND has groups that
    differ only by case; Larkspur stores one spelling. Measured from the rows, not asserted."""
    seen = {}
    for est, (conn, table, files, key, fcol, acol, gcol) in ESTATES.items():
        _cols, rows = _rows(files, table)
        by = {}
        for r in rows:
            by.setdefault(_fold(r[fcol]), set()).add(r[fcol])
        seen[est] = max(len(v) for v in by.values())
    assert seen["finance"] > 1 and seen["larkspur"] == 1


@pytest.mark.parametrize("est", sorted(ESTATES))
def test_fin12_spelling_is_not_matching_and_the_key_is_never_folded(estates, est):
    root, es = estates
    e = es[est]
    asked = _value_asked(e)
    # a PREFIX of the value is a different value: nothing is widened to it
    prefix = asked[: max(1, len(asked) - 2)]
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "t1", "adapter_agent": e["conn"], "entity_kinds": [e["table"]],
         "filters": [{"field": e["fcol"], "op": "eq", "value": prefix}],
         "aggregate": {"op": "count"}}]})
    ans = _service(root).answer("q", admission=ADMIN, plan=plan)
    want = sum(1 for r in e["rows"] if _fold(r[e["fcol"]]) == _fold(prefix))
    rows = ans.table.get("rows") or []
    assert (rows[0]["cells"]["count"]["value"] if rows else 0) == want
    # the row key names one row spelled exactly: an upper-cased key matches nothing
    k = str(e["rows"][0][e["key"]])
    plan = DelegationPlan.model_validate({"subtasks": [
        {"id": "t1", "adapter_agent": e["conn"], "entity_kinds": [e["table"]],
         "filters": [{"field": e["key"], "op": "eq", "value": k.lower()}],
         "aggregate": {"op": "count"}}]})
    ans = _service(root).answer("q", admission=ADMIN, plan=plan)
    rows = ans.table.get("rows") or []
    assert (rows[0]["cells"]["count"]["value"] if rows else 0) == (1 if k.lower() == k else 0)


def test_fin12_the_dry_run_plan_verbatim(estates):
    """The sub-task the dry run's planner wrote (var/finance/dryrun/http/0064_fed_ask.json), through
    an adapter agent as it was then: 33 facilities, not 3, and the count stated. (Its combine only
    ordered the rows; this validator refuses that order on a single pushed-down read, so the
    sub-task is asked alone.)"""
    from databridge_workflow_spec import AdapterAgentSpec, dump_spec

    root, es = estates
    e = es["finance"]
    spec = AdapterAgentSpec.model_validate({
        "id": "finance_db_graph_sync", "title": "finance db graph sync",
        "created_from": "keep core banking right", "connections": [e["conn"]],
        "extract": {"pack": "workspace"}, "status": "active"})
    (root / "adapter_agents" / "finance_db_graph_sync.yaml").write_text(dump_spec(spec),
                                                                         encoding="utf-8")
    plan = DelegationPlan.model_validate({
        "subtasks": [{"id": "t1", "adapter_agent": "finance_db_graph_sync",
                      "connection": e["conn"], "entity_kinds": ["facility"],
                      "filters": [{"field": "status", "op": "eq", "value": "watchlist"}],
                      "fields": ["facility_id", "status", "limit_amount", "currency"],
                      "aggregate": {"op": "sum", "field": "limit_amount",
                                    "group_by": ["currency"]},
                      "limit": 5000, "freshness": "stored"}]})
    ans = _service(root).answer("How many facilities in core banking have status watchlist, and "
                                "what is their total limit amount by currency?",
                                admission=ADMIN, plan=plan)
    assert ans.status == "completed", ans.answer_markdown
    want = [r for r in e["rows"] if _fold(r["status"]) == "watchlist"]
    exact = [r for r in want if r["status"] == "watchlist"]
    assert len(want) > len(exact) > 0                         # the planted inconsistency is there
    counted = sum(row["cells"]["count"]["value"] for row in ans.table["rows"])
    assert counted == len(want)
    total = sum(Decimal(str(row["cells"]["sum_limit_amount"]["value"]))
                for row in ans.table["rows"])
    assert total == sum(Decimal(str(r["limit_amount"])) for r in want)
