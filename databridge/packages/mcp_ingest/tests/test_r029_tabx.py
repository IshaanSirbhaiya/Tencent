"""TABX — E2E run 5 (2026-10-02) tabular findings, on BOTH fixture estates (workspace, finance).

F-03  a reference whose target table is built LATER is re-resolved: the referring table is rebuilt
      the run its target is built (order-independent, across runs), and every run's completeness
      reports every table — an unchanged one carried forward, never silently absent.
F-01  one injected cell holds its ROW, not its table: the rest of the table builds, the held rows
      are counted and listed one by one, and an owner lifts exactly one row.
F-11  link proposals are typed, normalised and order-independent; a target table with a kind split
      is measured per kind; a link from a source built FIRST is proposed when the other source is
      built (cross-source order independence).
F-02  real stage progress through the screen and the build; the batch runs with a page cache.
W-01  a manifest declaration that names a write tool must be `purpose: agent_write`.

Every expectation is DERIVED from the estates' own data or the rule the code uses.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest
import yaml

import estates
from conftest import test_principal

ESTATE_NAMES = ("workspace", "finance")


# --- fixtures ---------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def estate_snaps(tmp_path_factory):
    out = {}
    for name in ESTATE_NAMES:
        d = tmp_path_factory.mktemp(f"snap_{name}")
        db = estates.build_estate(name, d / "e.sqlite")
        out[name] = estates.snapshots(db, name)
        db.close()
    return out


@pytest.fixture(params=ESTATE_NAMES)
def estate(request, estate_snaps, tmp_path):
    from databridge_graph.packs import deployment

    name = request.param
    (tmp_path / "own").mkdir()
    db = estates.build_estate(name, tmp_path / "own" / "e.sqlite")     # this test's own engine
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        yield {"name": name, "db": db, "snaps": estate_snaps[name], "vocab": vocab}
    db.close()


def _ref(fk: dict) -> str:
    return f"{fk['ref_schema']}.{fk['ref_table']}" if fk.get("ref_schema") else fk["ref_table"]


def _fks(snap: dict) -> list[dict]:
    return [f for f in snap.get("foreign_keys") or [] if f.get("column") and f.get("ref_table")]


def _target_case(estate) -> tuple[str, list[str]]:
    """(the mapped table the most rows reference, its mapped referrers): derived from the rules'
    proposal and the captured rows, never named."""
    from databridge_mcp_ingest.mapping import propose_rules

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    mapped = {t for t, tm in rules.items() if tm.node and len(snaps[t]["rows"]) <= 5000}
    weight: dict[str, int] = {}
    referrers: dict[str, set] = {}
    for t in mapped:
        for fk in _fks(snaps[t]):
            r = _ref(fk)
            if r in mapped and r != t:
                weight[r] = weight.get(r, 0) + sum(1 for row in snaps[t]["rows"]
                                                   if row.get(fk["column"]) is not None)
                referrers.setdefault(r, set()).add(t)
    target = max(sorted(weight), key=lambda t: weight[t])
    return target, sorted(referrers[target])


def _source(tmp_path, monkeypatch, estate, sid: str, include: list[str]):
    """Register + approve the plan of one source over the estate's engine (a named person)."""
    import databridge_mcp_ingest as mi
    from databridge_mcp_ingest import plans
    from test_tab6_finance_fixes import _estate_profile

    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_estate_profile(tmp_path, estate["name"])))
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    mi.register_connection({"id": sid, "transport": {"type": "stdio", "command": "uvx"},
                            "scope": {"tables_include": include}})
    client = estates.engine_client(estate["db"])
    plan = mi.propose_plan(sid, client=client)
    unknown = [t for t, c in plan.tool_classes.items() if c.startswith("unclassified:unknown")]
    plans.approve_plan(sid, principal=test_principal(), vouch_read_tools=unknown, client=client)
    return client


def _run(sid, store, client, vocab, progress=None):
    import databridge_mcp_ingest as mi

    rep = mi.run_ingest(sid, store=store, client=client, vocab=vocab,
                        **({"progress_cb": progress.append} if progress is not None else {}))
    assert rep.status in ("ok", "unverified"), (rep.status, rep.message)
    assert rep.verify.get("ok"), rep.verify.get("problems")
    return rep


def _lines(rep, name: str) -> list[dict]:
    from databridge_mcp_ingest.runner import read_jsonl

    return read_jsonl(Path(rep.run_dir) / f"{name}.jsonl")


def _completeness(rep) -> dict[str, dict]:
    body = json.loads((Path(rep.run_dir) / "report.json").read_text(encoding="utf-8"))
    return {t["table"]: t for t in body["counts"]["completeness"]["tables"]}


def _approve(sid, vocab, tables):
    from databridge_mcp_ingest.mapping import approve_mapping

    return approve_mapping(sid, principal=test_principal(), vocab=vocab, tables=tables)


def _held_row_ids(rep, table: str) -> set[str]:
    return {str(g["row_id"]) for g in _lines(rep, "governance")
            if g.get("kind") == "table_row" and g.get("table") == table
            and g.get("decision") != "allow"}


# === F-03 ===========================================================================================
@pytest.mark.real_approval
def test_f03_references_resolve_when_the_referenced_table_is_built_later(walnut_env, estate,
                                                                         tmp_path, monkeypatch):
    """The dry run's order: the referring tables are approved and built while the table they
    reference is not; the referenced table is approved later. Its build re-resolves every pending
    reference (the referrers are rebuilt that same run), and later runs keep reporting them."""
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.mapping import load_mapping

    target, referrers = _target_case(estate)
    sid = f"f03_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    vocab, snaps = estate["vocab"], estate["snaps"]
    _run(sid, store, client, vocab)                          # proposes the mapping only
    proposed = [t for t, tm in load_mapping(sid).tables.items() if tm.status == "proposed"]
    assert target in proposed and set(referrers) <= set(proposed)
    _approve(sid, vocab, [t for t in proposed if t != target])
    first = _run(sid, store, client, vocab)
    comp = _completeness(first)
    keys = {str(r[k]) for r in snaps[target]["rows"] for k in snaps[target]["primary_key"]}
    pending = 0
    for t in referrers:
        for fk in (x for x in comp[t]["foreign_keys"] if x["references"] == target):
            if not fk["edge_kind"]:
                continue
            assert fk["edges_built"] == 0 and fk["dangling"] == fk["edges_expected"]
            pending += fk["edges_expected"]
    assert pending > 0                                           # non-vacuous: refs are waiting
    assert comp[target]["built"] is False

    # the target is approved later: its build re-resolves the pending references, same run
    _approve(sid, vocab, [target])
    second = _run(sid, store, client, vocab)
    rebuilt = {r["uri"]: r.get("rebuild") for r in _lines(second, "extracted")}
    assert set(referrers) <= set(rebuilt), rebuilt               # NOT "unchanged"
    assert all(rebuilt[t] for t in referrers)                    # each says why
    comp = _completeness(second)
    held = _held_row_ids(second, target)
    resolved = 0
    for t in referrers:
        for fk in (x for x in comp[t]["foreign_keys"] if x["references"] == target):
            if not fk["edge_kind"]:
                continue
            col = fk["column"]
            # the referring table's OWN held rows (the screen withholds them) carry no edge
            own_held = _held_row_ids(second, t)
            tpk = snaps[t]["primary_key"]
            vals = [str(r[col]) for r in snaps[t]["rows"] if r.get(col) is not None
                    and not (len(tpk) == 1 and str(r[tpk[0]]) in own_held)]
            want = sum(1 for v in vals if v in keys and v not in held)
            assert fk["edges_built"] == want, (t, col, fk)
            assert fk["dangling"] == sum(1 for v in vals if v in held)
            resolved += want
    assert resolved > 0

    # a run with nothing new: every table is still reported (carried), with the same edges
    third = _run(sid, store, client, vocab)
    assert not [r for r in _lines(third, "extracted")]           # all unchanged
    carried = _completeness(third)
    assert set(carried) == set(comp)
    for t, rec in carried.items():
        assert rec["carried_forward"] is True and rec["from_run"] == second.run_id
        assert rec["edges_built"] == comp[t]["edges_built"]


# === F-01 ===========================================================================================
def _injection_text() -> str:
    """A cell the screen holds as text addressed to an automated reader — READ from the finance
    estate's own planted cells (the run-5 evidence), never written here."""
    from databridge_mcp_ingest.screen import load_screen, screen_text

    import tempfile

    cfg = load_screen()

    tmp = Path(tempfile.mkdtemp())
    fin = estates.build_estate("finance", tmp / "e.sqlite")
    try:
        for t, snap in estates.snapshots(fin, "finance").items():
            for row in snap["rows"]:
                for v in row.values():
                    if isinstance(v, str) and screen_text(json.dumps({"c": v}), cfg).decision \
                            == "review":
                        return v
    finally:
        fin.close()
    raise AssertionError("the finance estate holds no planted cell")


@pytest.mark.real_approval
def test_f01_one_injected_cell_holds_its_row_and_the_table_builds(walnut_env, estate, tmp_path,
                                                                  monkeypatch):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import screen
    from databridge_mcp_ingest.mapping import load_mapping

    target, referrers = _target_case(estate)
    snaps, vocab, db = estate["snaps"], estate["vocab"], estate["db"]
    snap = snaps[target]
    pk = snap["primary_key"]
    assert len(pk) == 1
    # plant ONE injected cell in a referenced row, in a text column that is no key
    fk_cols = {f["column"] for f in _fks(snap)}
    col = next(c["name"] for c in snap["columns"] if c["name"] not in (*pk, *fk_cols)
               and ("char" in str(c["type"]).lower() or "text" in str(c["type"]).lower()))
    referenced = {str(r[f["column"]]) for t in referrers for f in _fks(snaps[t])
                  if _ref(f) == target for r in snaps[t]["rows"] if r.get(f["column"])}
    row_key = next(str(r[pk[0]]) for r in snap["rows"] if str(r[pk[0]]) in referenced)
    schema, table = target.split(".", 1)
    db.execute(f"UPDATE {schema}.{table} SET {col} = ? WHERE {pk[0]} = ?",
               (_injection_text(), row_key))
    db.commit()

    sid = f"f01_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    _run(sid, store, client, vocab)
    _approve(sid, vocab, [t for t, tm in load_mapping(sid).tables.items()
                          if tm.status == "proposed"])
    rep = _run(sid, store, client, vocab)
    gov = _lines(rep, "governance")
    table_dec = next(g for g in gov if g.get("uri") == target and g.get("kind") == "table")
    assert table_dec["decision"] == "allow"                       # the table is NOT held whole
    rows = [g for g in gov if g.get("kind") == "table_row" and g.get("table") == target]
    # every held row is one the screen holds on its own; the planted one is among them
    cfg = screen.load_screen()
    by_id = {str(r[pk[0]]): r for r in estates.snapshot(client, schema, table)["rows"]}
    for g in rows:
        assert screen.screen_text(json.dumps(by_id[g["row_id"]], sort_keys=True, default=str),
                                  cfg).decision == g["decision"] != "allow"
        assert g["item_id"] == screen.row_ref(sid, target, g["row_id"])
        assert g["sha256"] == screen.row_digest(by_id[g["row_id"]])
    assert row_key in {g["row_id"] for g in rows}
    assert table_dec["rows_held"]["review"] == sum(g["decision"] == "review" for g in rows)
    comp = _completeness(rep)[target]
    assert comp["built"] and comp["rows_withheld"] == len(rows)
    assert comp["nodes"] == len(by_id) - len(rows) > 0
    assert rep.counts["table_rows_held"].get("review", 0) >= 1

    # an owner lifts exactly the planted row: the next sync rebuilds the table (and the tables
    # that reference that row), the row reaches the graph, nothing else is lifted
    lift = next(g for g in rows if g["row_id"] == row_key)
    screen.approve_override(sid, lift["item_id"], digest=lift["sha256"],
                            principal=test_principal(), reason="checked by a person")
    after = _run(sid, store, client, vocab)
    rebuilt = {r["uri"]: r.get("rebuild") for r in _lines(after, "extracted")}
    assert rebuilt.get(target), rebuilt
    comp2 = _completeness(after)
    assert comp2[target]["rows_withheld"] == len(rows) - 1
    assert comp2[target]["nodes"] == comp["nodes"] + 1
    for t in referrers:
        assert t in rebuilt                                       # its edges into the row resolve
        for fk in (x for x in comp2[t]["foreign_keys"] if x["references"] == target):
            if fk["edge_kind"]:
                still = _held_row_ids(after, target)
                vals = [str(r[fk["column"]]) for r in snaps[t]["rows"]
                        if r.get(fk["column"]) is not None]
                assert fk["dangling"] == sum(1 for v in vals if v in still)


# === F-11 ===========================================================================================
def _strip(snaps, t, col):
    out = copy.deepcopy(snaps)
    out[t]["foreign_keys"] = [f for f in out[t]["foreign_keys"] if f.get("column") != col]
    return out


def _link_cases(estate):
    """Every declared FK (rows <= 5000) whose stripped column the measurement proposes as a link."""
    from databridge_mcp_ingest.mapping import propose_links, propose_rules

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    for t, s in sorted(snaps.items()):
        if not rules[t].node or len(s["rows"]) > 5000:
            continue
        for fk in _fks(s):
            stripped = _strip(snaps, t, fk["column"])
            got = propose_links("src", stripped, propose_rules(stripped, vocab), vocab).get(t, [])
            link = next((lk for lk in got if lk["column"] == fk["column"]), None)
            if link is not None:
                yield t, fk, stripped, link


def _fullwidth(value: str) -> str:
    """The same characters in their full-width forms (NFKC folds them back)."""
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in value)


def _perturb(value: str, i: int) -> str:
    return [value.lower(), f"  {value.upper()} ", value.swapcase(), f"{value}\t",
            _fullwidth(value)][i % 5]


def test_f11_proposals_are_normalised_and_independent_of_row_order(estate):
    from databridge_mcp_ingest.mapping import _policy, propose_links, propose_rules

    vocab = estate["vocab"]
    n = 0
    for t, fk, stripped, base in _link_cases(estate):
        col = fk["column"]
        # normalised: the same values written in another case / with spaces are the same keys
        noisy = copy.deepcopy(stripped)
        for i, r in enumerate(noisy[t]["rows"]):
            if isinstance(r.get(col), str):
                r[col] = _perturb(r[col], i)
        got = next(lk for lk in propose_links("src", noisy, propose_rules(noisy, vocab),
                                              vocab)[t] if lk["column"] == col)
        assert (got["table"], got["matched"], got["status"]) == (
            base["table"], base["matched"], base["status"])
        # row order: every table's rows reversed and shuffled — the same proposal
        for k in (0, 1):
            moved = copy.deepcopy(stripped)
            for s in moved.values():
                rows = s["rows"]
                rows.reverse() if k == 0 else random.Random(7).shuffle(rows)
            again = propose_links("src", moved, propose_rules(moved, vocab), vocab)
            assert next(lk for lk in again[t] if lk["column"] == col) == base
        # a sample (more distinct values than the cap) is fixed by the values, not the order
        cap = max(3, len({r[col] for r in stripped[t]["rows"] if r.get(col)}) // 2)
        pol = {**_policy(), "link_proposals": {**_policy()["link_proposals"],
                                               "max_distinct_values": cap}}
        a = propose_links("src", stripped, propose_rules(stripped, vocab), vocab, policy=pol)
        moved = copy.deepcopy(stripped)
        for s in moved.values():
            random.Random(3).shuffle(s["rows"])
        b = propose_links("src", moved, propose_rules(moved, vocab), vocab, policy=pol)
        la = next((lk for lk in a.get(t, []) if lk["column"] == col), None)
        lb = next((lk for lk in b.get(t, []) if lk["column"] == col), None)
        assert la == lb
        assert la is None or la.get("sampled", {}).get("measured") in (None, cap)
        # typed: the same values in a temporal column are never measured
        typed = copy.deepcopy(stripped)
        for c in typed[t]["columns"]:
            if c["name"] == col:
                c["type"] = "timestamp"
        assert not [lk for lk in propose_links("src", typed, propose_rules(typed, vocab),
                                               vocab).get(t, []) if lk["column"] == col]
        n += 1
    assert n > 0                                                       # non-vacuous


def test_f11_a_split_target_is_measured_per_kind_and_never_into_its_subject_rows(estate):
    """A table whose approved kind SPLIT builds some rows as a non-subject kind is a link target
    for those rows only (the dry run's CRM `external_ref` into the core-banking party table)."""
    from databridge_mcp_ingest.mapping import (_key_values, _link_targets, is_subject_kind,
                                               propose_links, propose_rules, split_value)

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    split_tables = [t for t, tm in rules.items() if tm.split and _key_values(snaps[t])]
    if not split_tables:
        # an estate whose pack splits nothing: one target per mapped table, of its own kind
        for t, tm in rules.items():
            if tm.node and _key_values(snaps[t]):
                [tgt] = _link_targets("s", t, snaps[t], tm.node["kind"], None)
                assert tgt[4] == tm.node["kind"]
        return
    checked = 0
    for t in split_tables:
        tm, snap = rules[t], snaps[t]
        key = _key_values(snap)[0]
        targets = _link_targets("s", t, snap, tm.node["kind"], tm.split)
        kinds = tm.split["kinds"]
        for _, _, _, vals, kind in targets:
            want = {str(r[key]) for r in snap["rows"] if (kinds.get(split_value(
                r[tm.split["column"]]), tm.split["default"]) if r.get(tm.split["column"])
                is not None else tm.split["default"]) == kind}
            assert vals == want
        free = [x for x in targets if not is_subject_kind(vocab, x[4])]
        if not free:
            continue                                      # every kind of this split is a subject
        # a column holding keys of the non-subject rows only: proposed INTO that kind
        tkey, kvals, kind = free[0][2], sorted(free[0][3]), free[0][4]
        col = "zz_ref"
        other = {"schema": "x", "table": "y", "primary_key": ["id"],
                 "columns": [{"name": "id", "type": "text"}, {"name": col, "type": "text"},
                             {"name": "name", "type": "text"}],
                 "foreign_keys": [],
                 "rows": [{"id": f"Y{i}", col: v, "name": f"n{i}"}
                          for i, v in enumerate(kvals)]}
        mine = {"x.y": other}
        own = propose_rules(mine, vocab)
        if not own["x.y"].node:
            own["x.y"] = own["x.y"].model_copy(update={"status": "proposed", "node": {
                "kind": next(k for k in sorted(vocab.node_kinds) if not is_subject_kind(vocab, k)
                             and k != kind),
                "label": "{name}", "sources": ["id"], "attrs": {}}})
        got = propose_links("me", mine, own, vocab,
                            others={"them": {t: (snap, tm.node["kind"], tm.split)}})
        [lk] = [x for x in got.get("x.y", []) if x["column"] == col]
        assert (lk["source"], lk["table"], lk["target_kind"]) == ("them", t, kind)
        assert lk["matched"] == len(kvals)
        # the SAME column holding the subject rows' keys is never proposed into them
        subj = [x for x in targets if is_subject_kind(vocab, x[4])]
        if subj:
            other2 = copy.deepcopy(other)
            other2["rows"] = [{"id": f"Y{i}", col: v, "name": f"n{i}"}
                              for i, v in enumerate(sorted(subj[0][3]))]
            got2 = propose_links("me", {"x.y": other2}, own, vocab,
                                 others={"them": {t: (snap, tm.node["kind"], tm.split)}})
            for x in got2.get("x.y", []):
                assert x["status"] == "not_proposed" and x["edge"] is None
        checked += 1
    assert checked


@pytest.mark.real_approval
@pytest.mark.parametrize("first", ["schema_order", "reverse_order"])
def test_f11_a_cross_source_link_is_proposed_whichever_source_is_built_first(
        walnut_env, tmp_path, monkeypatch, first):
    """Two sources over the finance estate — its two schemas, as the run-5 deployment had them.
    Whichever is built first, once both are built every column whose values are the other's
    keys (into a non-subject kind) is a link proposal; nothing is built from a proposal; once a
    person approves it, the next sync builds exactly the edges the rows support."""
    from databridge_graph.packs import deployment
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.mapping import (approve_link, is_subject_kind, link_value,
                                               load_mapping, split_value)

    name = "finance"
    (tmp_path / "own").mkdir()
    db = estates.build_estate(name, tmp_path / "own" / "e.sqlite")
    schemas = list(estates.ESTATES[name]["schemas"])
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        est = {"name": name, "db": db, "vocab": vocab}
        snaps = estates.snapshots(db, name)
        sources = {s: f"x_{s}" for s in schemas}
        clients = {s: _source(tmp_path, monkeypatch, est, sources[s], [f"{s}.*"])
                   for s in schemas}
        store = GraphStore(str(tmp_path / "g.db"))
        order = schemas if first == "schema_order" else list(reversed(schemas))
        # the referring schema is the one whose undeclared column holds the other's keys: found
        # below from the proposals; build in the parametrised order, mapping approved each time
        for s in order:
            _run(sources[s], store, clients[s], vocab)
            _approve(sources[s], vocab, [t for t, tm in load_mapping(sources[s]).tables.items()
                                         if tm.status == "proposed"])
            _run(sources[s], store, clients[s], vocab)
        found = []
        for s in schemas:
            for t, tm in load_mapping(sources[s]).tables.items():
                for lk in tm.links or []:
                    if lk["source"] != sources[s]:
                        found.append((s, t, lk))
        assert found                                              # cross-source proposals exist
        for s, t, lk in found:
            assert lk["status"] in ("proposed", "not_proposed")
            if is_subject_kind(vocab, lk["target_kind"]):
                assert lk["status"] == "not_proposed" and lk["edge"] is None
        # the measurement the test makes itself: a column holding the other source's
        # NON-subject keys at >= the policy ratio must be among the proposals
        live = [(s, t, lk) for s, t, lk in found if lk["status"] == "proposed"]
        assert live, found
        s, t, lk = live[0]
        other_schema = next(x for x in schemas if sources[x] == lk["source"])
        target = snaps[lk["table"]]
        tm_t = load_mapping(lk["source"]).tables[lk["table"]]
        split = tm_t.split or {}
        kind_of = {}
        for r in target["rows"]:
            raw = r.get(split.get("column")) if split else None
            kind_of[link_value(r[lk["key"]])] = ((split.get("kinds") or {}).get(
                split_value(raw), split.get("default")) if split and raw is not None
                else (split.get("default") if split else tm_t.node["kind"]))
        vals = [link_value(r[lk["column"]]) for r in snaps[t]["rows"]
                if r.get(lk["column"]) is not None]
        assert lk["matched"] == sum(1 for v in vals if kind_of.get(v) == lk["target_kind"])
        assert other_schema != s
        # nothing is built from a proposal; a person approves it; the next sync builds the edges
        approve_link(sources[s], t, lk["column"], principal=test_principal(), vocab=vocab,
                     target=f"{lk['source']}:{lk['table']}")
        rep = _run(sources[s], store, clients[s], vocab)
        rec = _completeness(rep)[t]
        ref = next(r for r in rec["foreign_keys"] if r["column"] == lk["column"])
        assert ref["origin"] == "approved_link"
        assert ref["edges_built"] == lk["matched"] > 0
    db.close()


def test_f11_an_approved_link_resolves_normalised_values_to_the_rows_own_key(walnut_env, estate,
                                                                             tmp_path):
    import datetime as dt

    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.build import build_tables
    from databridge_mcp_ingest.mapping import is_subject_kind, propose_rules

    vocab, snaps = estate["vocab"], estate["snaps"]
    case = next(((t, fk, stripped, link) for t, fk, stripped, link in _link_cases(estate)
                 if link["status"] == "proposed"
                 and not is_subject_kind(vocab, link["target_kind"])), None)
    assert case
    t, fk, stripped, link = case
    col, target = fk["column"], link["table"]
    rules = propose_rules(stripped, vocab)
    built = {}
    for noisy in (False, True):
        mine = copy.deepcopy(stripped[t])
        if noisy:
            for i, r in enumerate(mine["rows"]):
                if isinstance(r.get(col), str):
                    # full width: the one perturbation a node id's own slug does not fold
                    r[col] = _fullwidth(r[col]) if i % 2 == 0 else _perturb(r[col], i)
        e = link["edge"]
        tmap = {**rules[t].model_dump(mode="json"), "status": "approved",
                "edges": [x for x in rules[t].edges if col not in (x.get("evidence") or [])]
                + [{"kind": e["kind"], "src": e["src"], "dst": e["dst"], "evidence": [col],
                    "ref": {"source": "right", "table": target, "key": link["key"]}}]}
        store = GraphStore(str(tmp_path / f"n{int(noisy)}.db"))
        now = dt.datetime.now(dt.timezone.utc)
        right = {**rules[target].model_dump(mode="json"), "edges": [], "status": "approved"}
        build_tables(store, vocab, source_id="right", retrieved_at=now, mask_cfg=None,
                     tables=[(snaps[target], "sha-r", right)])
        out = build_tables(store, vocab, source_id="left", retrieved_at=now, mask_cfg=None,
                           tables=[(mine, "sha-l", tmap)], context={("right", target): snaps[target]})
        rec = next(r for r in out["completeness"] if r["table"] == t)
        built[noisy] = next(r for r in rec["foreign_keys"] if r["column"] == col)
        store.close()
    assert built[True]["edges_built"] == built[False]["edges_built"] > 0
    assert built[True]["edges_expected"] == built[False]["edges_expected"]


# === F-02 ===========================================================================================
@pytest.mark.real_approval
def test_f02_the_screen_and_the_build_report_real_progress(walnut_env, estate, tmp_path,
                                                          monkeypatch):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.mapping import load_mapping

    target, referrers = _target_case(estate)
    sid = f"f02_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    _run(sid, store, client, estate["vocab"])
    _approve(sid, estate["vocab"], [t for t, tm in load_mapping(sid).tables.items()
                                    if tm.status == "proposed"])
    events: list = []
    rep = _run(sid, store, client, estate["vocab"], progress=events)
    build = [e for e in events if e.get("stage") == "build"]
    phases = {e.get("phase") for e in build}
    assert {"rows", "nodes", "edges"} <= phases, phases
    tables_seen = {e.get("table") for e in build if e.get("phase") == "rows"}
    built = {r["uri"] for r in _lines(rep, "build") if r.get("kind") == "table"}
    assert built and built <= tables_seen
    screened = [e for e in events if e.get("stage") == "screen" and e.get("total")]
    assert len(screened) >= len(_lines(rep, "extracted"))        # one per screened item at least
    # progress is reported in order: screen before build, nodes before edges
    stages = [e.get("stage") for e in events]
    assert stages.index("screen") < stages.index("build")
    order = [e.get("phase") for e in build if e.get("phase") in ("nodes", "edges")]
    assert order.index("nodes") < order.index("edges")


def test_f02_a_large_table_build_gives_the_planner_statistics(walnut_env, estate, tmp_path,
                                                              monkeypatch):
    """The measured cause of the 27-minute build: without statistics the per-row lookups ("the
    open node of this kind with this canonical id") scanned every open node of the kind. The
    build refreshes them as it grows; after it the lookup uses the selective index."""
    import datetime as dt

    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import build
    from test_tab6_finance_fixes import _approved_tables

    snaps = estate["snaps"]
    maps = _approved_tables(estate, snaps)
    t = max((x for x in maps if maps[x].get("node") and len(snaps[x]["rows"]) <= 5000),
            key=lambda x: len(snaps[x]["rows"]))
    n = len(snaps[t]["rows"])
    # an estate whose largest table is small still crosses the threshold: derived from its size
    monkeypatch.setattr(build, "STATS_FIRST", min(build.STATS_FIRST, max(1, n // 2)))
    store = GraphStore(str(tmp_path / "p.db"))
    q = ("EXPLAIN QUERY PLAN SELECT * FROM nodes WHERE kind = ? AND canonical_id = ? "
         "AND system_to IS NULL ORDER BY rowid_pk DESC LIMIT 1")
    kind = maps[t]["node"]["kind"]
    out = build.build_tables(store, estate["vocab"], source_id="p", mask_cfg=None,
                             retrieved_at=dt.datetime.now(dt.timezone.utc),
                             tables=[(snaps[t], "sha-p", maps[t])])
    assert out["nodes"] == n > build.STATS_FIRST
    stats = store.conn.execute("SELECT COUNT(*) FROM sqlite_stat1 WHERE tbl = 'nodes'").fetchone()
    assert stats[0] > 0
    plan = " ".join(str(r[-1]) for r in store.conn.execute(q, (kind, "x")).fetchall())
    assert "ix_nodes_kind_open" not in plan and "canon" in plan, plan
    store.close()


# === W-01 ===========================================================================================
def test_w01_every_manifest_write_tool_is_an_agent_write_connection_and_never_crawled(walnut_env,
                                                                                    tmp_path):
    import databridge_mcp_ingest as mi
    from databridge_mcp_ingest.connections import ConnectionError_, import_declarations
    from databridge_mcp_ingest.plans import PlanError

    infra = Path(estates.DATABRIDGE) / "infra"
    writers = 0
    for manifest in sorted(infra.glob("mcp_servers*.yaml")):
        decls = yaml.safe_load(manifest.read_text(encoding="utf-8"))["servers"]
        for d in decls:
            if (d.get("tools_hint") or {}).get("write"):
                writers += 1
                assert d.get("purpose") == "agent_write", (manifest.name, d["id"])
    assert writers >= 2                                         # non-vacuous: both estates
    # an operator manifest that forgets the purpose is refused, naming the declaration
    bad = yaml.safe_load((infra / "mcp_servers.yaml").read_text(encoding="utf-8"))
    victim = next(d for d in bad["servers"] if (d.get("tools_hint") or {}).get("write"))
    victim.pop("purpose", None)
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ConnectionError_, match=victim["id"]):
        import_declarations(p, register=False)
    # the shipped write root is imported as a write connection, which a crawl plan refuses
    conns = {c.id: c for c in import_declarations(infra / "mcp_servers.yaml", register=True,
                                                  advertise=False, only=[victim["id"]])}
    assert conns[victim["id"]].purpose == "agent_write"
    with pytest.raises(PlanError):
        mi.propose_plan(victim["id"])


# === IDN cross-track 2: the keyless reconciliation reaches the run report ==========================
def test_the_identity_summary_reports_the_keyless_reconciliation(tmp_path):
    from databridge_mcp_ingest.runner import IDENTITY_FILE, _identity_note, identity_summary

    rec = {"status": "no_rules", "merged": [], "refused": [], "kinds": {}, "absorbed": 0,
           "reconciled": {"status": "ran", "merged": [{"survivor": "a", "absorbed": ["b", "c"]}],
                          "refused": [{"mention": "d"}], "absorbed": 2}}
    (tmp_path / IDENTITY_FILE).write_text(json.dumps(rec), encoding="utf-8")
    got = identity_summary(tmp_path)["reconciled"]
    assert got == {"status": "ran", "merged": 1, "absorbed": 2, "refused": 1}
    assert "2 absorbed into 1 record(s), 1 refused" in _identity_note(rec)
    rec.pop("reconciled")
    (tmp_path / IDENTITY_FILE).write_text(json.dumps(rec), encoding="utf-8")
    assert "reconciled" not in identity_summary(tmp_path)


# === IDN cross-track: splits never move rows between person kinds; document kinds declared ========
def test_a_kind_split_never_moves_rows_into_a_person_kind(estate):
    from databridge_mcp_ingest.mapping import (TableMapping, is_person_kind, propose_rules,
                                               split_problems)

    vocab = estate["vocab"]
    rules = propose_rules(estate["snaps"], vocab)
    for t, tm in rules.items():
        for k in (tm.split or {}).get("kinds", {}).values():
            assert k == tm.split["default"] or not is_person_kind(vocab, k), (t, tm.split)
    persons = sorted(k for k in vocab.node_kinds if is_person_kind(vocab, k))
    assert persons                                                  # non-vacuous
    base, other = persons[0], (persons[1:] or persons)[0]
    tm = TableMapping(status="proposed", node={"kind": base, "label": "{a}", "sources": ["a"],
                                               "attrs": {}},
                      columns=["a", "b"], split={"column": "b", "default": base,
                                                 "kinds": {"x": other}})
    bad = split_problems("t", tm, vocab)
    assert (other != base) == any("person kind" in p for p in bad), bad


def test_finance_document_connections_declare_their_document_kind(walnut_env):
    from databridge_graph.packs import deployment
    from databridge_mcp_ingest.connections import import_declarations

    manifest = Path(estates.DATABRIDGE) / "infra" / "mcp_servers.finance.yaml"
    decls = yaml.safe_load(manifest.read_text(encoding="utf-8"))["servers"]
    files = [d for d in decls if d.get("source_class") == "files"
             and (d.get("purpose") or "ingest") == "ingest"]
    assert files
    with deployment("finance") as vocab:
        for d in files:
            assert vocab.is_document_kind(d.get("document_kind") or ""), d["id"]
    got = {c.id: c for c in import_declarations(manifest, register=False)}
    assert all(got[d["id"]].document_kind == d["document_kind"] for d in files)


# === F-03 on the run-5 evidence itself (read only) ===================================================
def test_f03_the_run5_finance_state_rebuilds_the_tables_whose_party_edges_dangle(monkeypatch):
    """The live state run 5 left: party built in a later run than account/facility/address/contact,
    whose edges to it dangled; run 5 called all of them "unchanged". Fed the evidence's own state
    and run directories (read only), the next run rebuilds exactly the tables whose last build
    recorded dangling references to a table that has been built since."""
    from databridge_mcp_ingest import runner

    ev = Path(estates.DATABRIDGE) / "var" / "e2e5-finance-20261002T1551" / "ingest"
    sid = "finance_core_banking"
    if not (ev / sid / "state.json").is_file():
        pytest.skip("run-5 evidence not on this machine")
    monkeypatch.setenv("WALNUT_INGEST_DIR", str(ev))
    state = runner.load_state(sid)
    captured = [{"item_id": iid, "uri": e["uri"], "sha256": e["content_hash"], "kind": "table"}
                for iid, e in state.items()]
    by_uri = {e["uri"]: e for e in state.values()}
    want = set()
    for iid, e in state.items():
        last = [r for r in runner.read_jsonl(runner.run_dir_for(sid, e["run_id"])
                                             / runner.COMPLETENESS_LEDGER)
                if r.get("table") == e["uri"]][-1:]
        for fk in (last[0]["foreign_keys"] if last else []):
            src, _, tab = fk["references"].partition(":") if ":" in fk["references"]                 else (sid, "", fk["references"])
            pool = by_uri if src == sid else {x["uri"]: x for x in runner.load_state(src)
                                             .values()}
            tgt = pool.get(tab)
            if fk.get("edge_kind") and fk.get("dangling") and tgt and tgt.get("built_at"):
                want.add(e["uri"])
    assert want                                                      # the run-5 gap is there
    got = runner._rebuild_for_references(captured, state, sid, overrides=set())
    assert {by_uri_e["uri"] for iid, by_uri_e in state.items() if iid in got} == want


# === Codex R-029 round 1 =============================================================================
class _PersonVocab:
    """The estate's vocabulary with ONE more kind marked a natural person but NOT a subject — the
    shape (person-like, undeclared) Codex #1 names. Everything else is the real vocabulary."""

    def __init__(self, vocab, person: str):
        self._v, self._p = vocab, person

    def __getattr__(self, name):
        return getattr(self._v, name)

    def is_subject(self, kind):
        return self._v.is_subject(kind)

    def is_person(self, kind):
        return str(kind) == self._p or self._v.is_person(kind)


def test_codex1_a_person_kind_never_receives_an_inferred_link(walnut_env, estate):
    """A kind that is a person but not a subject: the link INTO it is never proposed, an approved
    link edge into it fails validation, and its approval is refused — on both estates."""
    from databridge_mcp_ingest.mapping import (SourceMapping, MappingApprovalRefused,
                                               approve_link, never_linked, propose_links,
                                               propose_rules, save_mapping,
                                               validate_table_mapping)

    vocab = estate["vocab"]
    case = next(((t, fk, st, lk) for t, fk, st, lk in _link_cases(estate)
                 if lk["status"] == "proposed"), None)
    assert case                                                        # non-vacuous
    t, fk, stripped, link = case
    pv = _PersonVocab(vocab, link["target_kind"])
    assert not vocab.is_subject(link["target_kind"]) and never_linked(pv, link["target_kind"])
    got = propose_links("src", stripped, propose_rules(stripped, vocab), pv)[t]
    [again] = [lk for lk in got if lk["column"] == fk["column"]]
    assert again["status"] == "not_proposed" and again["edge"] is None
    assert "person" in again["why"]
    # validation: an approved-link edge into it is a problem; the same edge is fine without it
    rules = propose_rules(stripped, vocab)
    e = link["edge"]
    edge = {"kind": e["kind"], "src": e["src"], "dst": e["dst"], "evidence": [fk["column"]],
            "ref": {"source": "src", "table": link["table"], "key": link["key"]}}
    tm = rules[t].model_copy(update={"edges": [*rules[t].edges, edge]})
    assert any("person kind" in p for p in validate_table_mapping(t, tm, pv))
    assert not any("person kind" in p for p in validate_table_mapping(t, tm, vocab))
    # approval: the proposal made under the real vocabulary is refused once it is a person kind
    save_mapping(SourceMapping(source_id="pk", tables={
        t: rules[t].model_copy(update={"links": [link]})}))
    with pytest.raises(MappingApprovalRefused, match="person"):
        approve_link("pk", t, fk["column"], principal=test_principal(), vocab=pv,
                     target=f"{link['source']}:{link['table']}")


def _graph_ids_citing(store, table: str, row_id: str) -> set[str]:
    rows = store.fetchall("SELECT DISTINCT s.entity_id AS id FROM sources s WHERE "
                          "s.source_pointer_json LIKE ? AND s.source_pointer_json LIKE ?",
                          (f"%{table}%", f'%"row_id":{json.dumps(row_id)}%'))
    out = set()
    for r in rows:
        if store.open_node_row(r["id"]) is not None or store.fetchone(
                "SELECT 1 AS x FROM edges WHERE id = ? AND system_to IS NULL", (r["id"],)):
            out.add(r["id"])
    return out


@pytest.mark.real_approval
def test_codex2_a_row_newly_withheld_or_deleted_leaves_no_open_graph(walnut_env, estate,
                                                                    tmp_path, monkeypatch):
    """Run A builds every row. Before run B one row of the referenced table gains an injected
    cell (now held) and one row of a referring table is deleted at the source. After run B
    neither row has an open node or edge citing only it; every other row is still there."""
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest.mapping import load_mapping

    target, referrers = _target_case(estate)
    snaps, vocab, db = estate["snaps"], estate["vocab"], estate["db"]
    sid = f"c2_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    _run(sid, store, client, vocab)
    _approve(sid, vocab, [t for t, tm in load_mapping(sid).tables.items()
                          if tm.status == "proposed"])
    first = _run(sid, store, client, vocab)
    pk = snaps[target]["primary_key"][0]
    already = _held_row_ids(first, target)
    hold_id = next(str(r[pk]) for r in snaps[target]["rows"] if str(r[pk]) not in already)
    ref = referrers[0]
    rpk = snaps[ref]["primary_key"][0]
    gone_id = next(str(r[rpk]) for r in snaps[ref]["rows"]
                   if str(r[rpk]) not in _held_row_ids(first, ref))
    held_before = _graph_ids_citing(store, target, hold_id)
    gone_before = _graph_ids_citing(store, ref, gone_id)
    assert held_before and gone_before                                # both were built
    fk_cols = {f["column"] for f in _fks(snaps[target])}
    col = next(c["name"] for c in snaps[target]["columns"]
               if c["name"] not in (pk, *fk_cols)
               and ("char" in str(c["type"]).lower() or "text" in str(c["type"]).lower()))
    s1, t1 = target.split(".", 1)
    db.execute(f"UPDATE {s1}.{t1} SET {col} = ? WHERE {pk} = ?", (_injection_text(), hold_id))
    s2, t2 = ref.split(".", 1)
    db.execute(f"DELETE FROM {s2}.{t2} WHERE {rpk} = ?", (gone_id,))
    db.commit()
    second = _run(sid, store, client, vocab)
    assert hold_id in _held_row_ids(second, target)
    assert not _graph_ids_citing(store, target, hold_id) - set()     # nothing open cites only it
    assert not _graph_ids_citing(store, ref, gone_id)
    comp = _completeness(second)
    assert comp[target]["graph_rows_closed"] >= 1 and comp[ref]["graph_rows_closed"] >= 1
    # every other row of both tables is still in the graph
    other = next(str(r[pk]) for r in snaps[target]["rows"]
                 if str(r[pk]) not in already | {hold_id})
    assert _graph_ids_citing(store, target, other)


@pytest.mark.real_approval
def test_codex3_link_proposals_never_measure_withheld_rows(walnut_env, estate, tmp_path,
                                                           monkeypatch):
    """The snapshots every link-proposal path measures — this run's, an unchanged table's, and
    another source's (`captured_tables`) — hold no row the screen withheld."""
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import mapping as mp
    from databridge_mcp_ingest import runner
    from databridge_mcp_ingest.screen import row_digest

    target, referrers = _target_case(estate)
    vocab, db, snaps = estate["vocab"], estate["db"], estate["snaps"]
    pk = snaps[target]["primary_key"][0]
    fk_cols = {f["column"] for f in _fks(snaps[target])}
    col = next(c["name"] for c in snaps[target]["columns"]
               if c["name"] not in (pk, *fk_cols)
               and ("char" in str(c["type"]).lower() or "text" in str(c["type"]).lower()))
    s1, t1 = target.split(".", 1)
    db.execute(f"UPDATE {s1}.{t1} SET {col} = ? WHERE {pk} = ?",
               (_injection_text(), str(snaps[target]["rows"][0][pk])))
    db.commit()
    seen: list[dict] = []
    real = mp.refresh_link_proposals

    def spy(source_id, snapshots, v, **kw):
        seen.append(snapshots)
        return real(source_id, snapshots, v, **kw)
    monkeypatch.setattr(mp, "refresh_link_proposals", spy)
    sid = f"c3_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    _run(sid, store, client, vocab)
    _approve(sid, vocab, [t for t, tm in mp.load_mapping(sid).tables.items()
                          if tm.status == "proposed"])
    rep = _run(sid, store, client, vocab)
    _run(sid, store, client, vocab)                                  # all unchanged
    held = {g["sha256"] for g in _lines(rep, "governance") if g.get("kind") == "table_row"
            and g.get("decision") != "allow"}
    assert held and len(seen) >= 3                                   # non-vacuous
    measured = [*seen, mp.captured_tables(sid)]
    for snapshots in measured:
        for t, snap in snapshots.items():
            assert not [r for r in snap.get("rows") or [] if row_digest(r) in held], t
    assert any(target in s for s in measured)


@pytest.mark.real_approval
def test_codex4_a_changed_screen_rereads_an_unchanged_table_and_closes_what_it_now_holds(
        walnut_env, estate, tmp_path, monkeypatch):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import runner, screen
    from databridge_mcp_ingest.mapping import load_mapping

    target, referrers = _target_case(estate)
    vocab, snaps = estate["vocab"], estate["snaps"]
    sid = f"c4_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    _run(sid, store, client, vocab)
    _approve(sid, vocab, [t for t, tm in load_mapping(sid).tables.items()
                          if tm.status == "proposed"])
    first = _run(sid, store, client, vocab)
    pk = snaps[target]["primary_key"][0]
    victim = next(str(r[pk]) for r in snaps[target]["rows"]
                  if str(r[pk]) not in _held_row_ids(first, target))
    assert _graph_ids_citing(store, target, victim)
    # policy B: one more decisive rule, matching exactly that row's key
    base = screen.load_screen()
    import re as _re
    policy_b = screen.ScreenConfig(
        decisive=[*base.decisive, screen.Rule(id="test_policy_b",
                                              pattern=_re.compile(_re.escape(victim)))],
        signals=base.signals, redactions=base.redactions, quarantine=base.quarantine,
        block_threshold=base.block_threshold, review_threshold=base.review_threshold,
        min_signals_to_block=base.min_signals_to_block, source=base.source)
    monkeypatch.setattr(runner, "load_screen", lambda *a, **k: policy_b)
    second = _run(sid, store, client, vocab)
    rebuilt = {r["uri"]: r.get("rebuild") for r in _lines(second, "extracted")}
    assert "screen" in str(rebuilt.get(target)), rebuilt
    assert victim in {g["row_id"] for g in _lines(second, "governance")
                      if g.get("kind") == "table_row" and g.get("decision") == "block"}
    assert not _graph_ids_citing(store, target, victim)
    # and a third run under policy B is unchanged again
    third = _run(sid, store, client, vocab)
    assert target not in {r["uri"] for r in _lines(third, "extracted")}


def test_codex5_a_legacy_state_without_a_mapping_digest_rebuilds_once():
    from databridge_mcp_ingest import runner

    cap = [{"item_id": "i1", "uri": "s.t", "sha256": "h", "kind": "table"}]
    legacy = {"i1": {"content_hash": "h", "uri": "s.t", "run_id": "r", "built_at": "x",
                     "screen": "fp"}}
    got = runner._rebuild_for_references(cap, legacy, "src", set(), approved={"s.t": "d1"},
                                         screen="fp")
    assert "mapping" in got["i1"]
    current = {"i1": {**legacy["i1"], "mapping_digest": "d1", "pending_refs": []}}
    assert runner._rebuild_for_references(cap, current, "src", set(), approved={"s.t": "d1"},
                                          screen="fp") == {}


# === SETX Critical: a build pinned to the capture digests a person authorised ======================
@pytest.mark.real_approval
def test_a_pinned_build_builds_only_the_authorised_bytes(walnut_env, estate, tmp_path,
                                                         monkeypatch):
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import runner
    from databridge_mcp_ingest.mapping import load_mapping

    target, referrers = _target_case(estate)
    vocab = estate["vocab"]
    sid = f"pin_{estate['name']}"
    client = _source(tmp_path, monkeypatch, estate, sid, [target, *referrers])
    store = GraphStore(str(tmp_path / "g.db"))
    first = _run(sid, store, client, vocab)                          # proposes only
    _approve(sid, vocab, [t for t, tm in load_mapping(sid).tables.items()
                          if tm.status == "proposed"])
    caps = {r["uri"]: r for r in _lines(first, "captured")}
    ok, moved = caps[target], caps[referrers[0]]
    pins = {ok["item_id"]: ok["sha256"], moved["item_id"]: "0" * 64}  # one changed since
    import databridge_mcp_ingest as mi
    before = {r["id"] for r in store.fetchall("SELECT id FROM nodes WHERE system_to IS NULL")}
    rep = mi.run_ingest(sid, store=store, client=client, vocab=vocab, only_items=pins)
    assert rep.status in ("ok", "unverified") and rep.verify["ok"], rep.verify.get("problems")
    built = {r["uri"] for r in _lines(rep, "build")}
    assert built == {target}
    skipped = {r["uri"]: r["reason"] for r in _lines(rep, "extract_skipped")}
    assert skipped[referrers[0]] == runner.CHANGED_SINCE_DECISION
    others = set(caps) - {target, referrers[0]}
    assert all(skipped[u] == runner.NOT_IN_PINNED_SET for u in others)
    pc = rep.counts["pinned"]
    assert (pc["pinned"], pc["changed_since_decision"], pc["built"],
            pc["not_in_pinned_set"]) == (2, 1, 1, len(others))
    # nothing outside the set reached the graph
    kinds = {r["kind"] for r in store.fetchall("SELECT id, kind FROM nodes "
                                               "WHERE system_to IS NULL") if r["id"] not in before}
    tm = load_mapping(sid).tables[target]
    from databridge_mcp_ingest.mapping import split_kinds
    assert kinds and kinds <= set(split_kinds(tm))
