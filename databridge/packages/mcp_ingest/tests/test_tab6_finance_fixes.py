"""TAB6 — the finance dry run's tabular findings (2026-10-02), on BOTH fixture estates.

C-1 / FIN-06  every declared foreign key becomes an edge, whatever order the tables were mapped in
              and however the referenced table is keyed; the FK value stays on the node.
C-2 / FIN-18  tables are read WHOLE by default (paged); a cap is explicit and reported "N of M".
C-3           a column no FK declares, whose values are another table's keys, is PROPOSED as a link
              (never to a person kind, never a merge) and becomes an edge only when a person
              approves it.
FIN-05        a key named only after what it references never becomes a ROLE edge on policy order.
FIN-14        the tabular recipe and the core run share ONE run directory; run history labels a
              run in flight `running` and never lists a recipe side directory as a run.
Completeness  each run's report carries, per table, source rows vs nodes, declared FKs vs edges
              and unresolved references (`counts.completeness`).

Every expectation is DERIVED from the estates' own data (the SQL fixtures, loaded into sqlite and
read through the real `crawl.fetch_table`) or from the rule the code uses — never written down.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

import estates
from conftest import test_principal

ESTATE_NAMES = ("workspace", "finance")


# --- fixtures ----------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def estate_dbs(tmp_path_factory):
    out = {}
    for name in ESTATE_NAMES:
        d = tmp_path_factory.mktemp(f"estate_{name}")
        out[name] = (estates.build_estate(name, d / "e.sqlite"), d / "e.sqlite")
    yield out
    for db, _ in out.values():
        db.close()


@pytest.fixture(scope="module")
def estate_snaps(estate_dbs):
    return {name: estates.snapshots(db, name) for name, (db, _) in estate_dbs.items()}


@pytest.fixture(params=ESTATE_NAMES)
def estate(request, estate_dbs, estate_snaps):
    from databridge_graph.packs import deployment

    name = request.param
    with deployment(estates.ESTATES[name]["pack"]) as vocab:
        yield {"name": name, "db": estate_dbs[name][0], "snaps": estate_snaps[name],
               "vocab": vocab}


def _count(db, schema, table, where=""):
    return db.execute(f"SELECT COUNT(*) FROM {schema}.{table} {where}").fetchone()[0]


def _fk_list(snap):
    return [f for f in snap.get("foreign_keys") or [] if f.get("column") and f.get("ref_table")]


def _ref(fk):
    return f"{fk['ref_schema']}.{fk['ref_table']}" if fk.get("ref_schema") else fk["ref_table"]


class _Model:
    """A fake model answering the mapping prompt from a table → kind dict."""

    def __init__(self, kinds: dict[str, str]):
        self.kinds, self.asked = kinds, []

    def complete_structured(self, task, messages, schema, seed=None):
        tables = json.loads(messages[-1]["content"])["tables"]
        self.asked += [t["table"] for t in tables]
        answer = {"tables": [{"table": t["table"], "node_kind": self.kinds.get(t["table"])}
                             for t in tables]}

        class R:
            parsed = schema.model_validate(answer)
        return R()


def _model_case(estate):
    """(snapshots, the model's answers): the referenced tables the RULES cannot map, which the
    model maps — the order of the dry run. Finance: the estate as it is (rules leave the CRM
    organisation table unmapped; the model gave it the kind the dry run's model gave it, read
    from that run's mapping file). Workspace: every rule maps its referenced tables, so the most
    referenced one is RENAMED to a name no rule maps, and the model answers the kind the rules
    give it under its real name."""
    from databridge_mcp_ingest.mapping import propose_rules

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    referenced = {}
    for t, s in snaps.items():
        for fk in _fk_list(s):
            referenced[_ref(fk)] = referenced.get(_ref(fk), 0) + 1
    unmapped_refs = [t for t in referenced if rules[t].status == "unmapped"]
    if unmapped_refs:
        evidence = Path(estates.DATABRIDGE / "var" / "finance" / "sources")
        answers = {}
        for f in sorted(evidence.glob("*.mapping.yaml")) if evidence.is_dir() else []:
            for t, tm in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get(
                    "tables", {}).items():
                if t in unmapped_refs and (tm.get("node") or {}).get("kind"):
                    answers[t] = tm["node"]["kind"]
        if not answers:
            # no dry-run evidence on this machine: the kind the referencing tables' declarations
            # admit as the far end of a party_to/role edge is not derivable without a model —
            # fall back to renaming, as for an estate whose rules map everything
            unmapped_refs = []
        else:
            return snaps, answers
    def renamed_at(target):
        schema = snaps[target].get("schema")
        alias = f"{schema}.zzq_rows" if schema else "zzq_rows"
        out = {}
        for t, s in snaps.items():
            s = copy.deepcopy(s)
            if t == target:
                s["table"] = "zzq_rows"
                t = alias
            for fk in s.get("foreign_keys") or []:
                if fk.get("ref_table") and _ref(fk) == target:
                    fk["ref_table"] = "zzq_rows"
            out[t] = s
        return alias, out

    # the most referenced table that no rule maps once its name says nothing (a table mapped by
    # its columns, e.g. a declared subject identifier, is mapped whatever it is called)
    for target in sorted((t for t in referenced if rules[t].status == "proposed"),
                         key=lambda t: (-referenced[t], t)):
        alias, renamed = renamed_at(target)
        if propose_rules({alias: renamed[alias]}, vocab)[alias].status == "unmapped":
            kind = rules[target].node["kind"]
            break
    else:
        raise AssertionError("no referenced table the rules map only by its name")
    return renamed, {alias: kind}


# === FIN-05: a key named after its target is never a role on policy order ============================
def test_fin05_a_key_named_only_for_its_target_is_never_a_role_on_order_alone(estate):
    from databridge_graph.meta import EdgeMetaKind

    from databridge_mcp_ingest.mapping import (_explicit, _hint_score, _policy, fk_hint_roots,
                                               propose_rules)
    from databridge_mcp_ingest.build import _admits

    vocab, snaps = estate["vocab"], estate["snaps"]
    metas = list(_policy()["fk_edge_metas"])
    rules = propose_rules(snaps, vocab)
    kinds = {t: (tm.node or {}).get("kind") for t, tm in rules.items()}
    unhinted = hinted = 0
    for t, s in snaps.items():
        tm = rules[t]
        if not tm.node:
            continue
        for fk in _fk_list(s):
            ref_kind = kinds.get(_ref(fk))
            edge = next((e for e in tm.edges if e["evidence"] == [fk["column"]]), None)
            if ref_kind is None or edge is None:
                continue
            kind = tm.node["kind"]
            roots = fk_hint_roots(fk["column"], kind, ref_kind, fk["ref_table"])
            # every declaration naming both kinds, either direction, with its meta-kind
            cands = []
            for meta in metas:
                for name in vocab.edges_binding_to(EdgeMetaKind(meta)):
                    d = vocab.edge(name)
                    for direction, (a, b) in enumerate(((kind, ref_kind), (ref_kind, kind))):
                        if _explicit(d, "src", a) and _explicit(d, "dst", b):
                            cands.append((name, meta, _hint_score(roots, name, d), direction))
            if len({m for _, m, _, _ in cands}) < 2:
                continue
            chosen_meta = vocab.edge(edge["kind"]).binds_to.value
            if max(h for _, _, h, _ in cands) == 0:
                unhinted += 1
                forward = [c for c in cands if c[3] == 0]
                if forward:
                    # the key's own direction: its earliest meta-kind
                    assert metas.index(chosen_meta) == min(metas.index(c[1]) for c in forward)
                else:
                    # read in reverse, a role the key does not name is never preferred to a
                    # participation: nothing earlier in the policy was passed over
                    assert metas.index(chosen_meta) <= min(metas.index(c[1]) for c in cands), \
                        (t, fk["column"], edge["kind"], cands)
                    # independent of the policy's order: a ROLE (the Layer-0 `role_of`
                    # meta-kind) read in reverse is never chosen over a non-role declaration
                    # when the key does not name it — the dry run's borrower-as-guarantor
                    if any(c[1] != "role_of" for c in cands):
                        assert chosen_meta != "role_of", (t, fk["column"], edge["kind"], cands)
            else:
                hinted += 1
                chosen = next(c for c in cands if c[0] == edge["kind"])
                assert chosen[2] == max(h for _, _, h, _ in cands), (t, fk["column"], cands)
    assert unhinted + hinted > 0                     # non-vacuous: a choice was actually made
    if estate["name"] == "finance":
        assert unhinted > 0          # the dry run's case: a role and a participation both fit


# === C-1 / FIN-06: every declared FK becomes an edge, whatever the mapping order ===================
@pytest.mark.real_approval
def test_c1_declared_fks_get_edges_when_the_model_maps_the_target_after_the_rules(
        walnut_env, estate):
    from databridge_mcp_ingest.mapping import _policy, ensure_mapping, fk_edges

    snaps, answers = _model_case(estate)
    model = _Model(answers)
    m = ensure_mapping("c1src", snaps, estate["vocab"], harness=model)
    assert set(answers) <= set(model.asked)                    # the model DID map them, later
    kinds = {t: (tm.node or {}).get("kind") for t, tm in m.tables.items()
             if tm.status != "unmapped"}
    into_model_tables = 0
    for t, s in snaps.items():
        tm = m.tables[t]
        if tm.status == "unmapped" or not tm.node:
            continue
        want, _ = fk_edges(s, tm.node["kind"], kinds, estate["vocab"], _policy())
        got = {e["evidence"][0]: e["kind"] for e in tm.edges}
        for e in want:
            assert got.get(e["evidence"][0]) == e["kind"], (t, e, tm.rationale)
        for fk in _fk_list(s):
            # the FK value is on the node as an attribute, edge or not (never dropped)
            assert tm.node["attrs"].get(fk["column"]) == fk["column"]
            if _ref(fk) in answers:
                into_model_tables += 1
                assert fk["column"] in got, (t, fk, tm.rationale)
    assert into_model_tables > 0                                 # non-vacuous


@pytest.mark.real_approval
def test_c1_an_approved_table_missing_its_fk_edge_is_reproposed_never_silently_kept(
        walnut_env, estate):
    """The dry run's state: tables approved while the table they reference had no kind. The next
    sync proposes the edge again and drops the stale approval — a person approves the change."""
    from databridge_mcp_ingest.mapping import (SourceMapping, approve_mapping, ensure_mapping,
                                               propose_model, propose_rules, save_mapping)

    snaps, answers = _model_case(estate)
    vocab = estate["vocab"]
    rules = propose_rules(snaps, vocab)                       # the referenced table: no kind yet
    model = propose_model(snaps, list(answers), rules, vocab, _Model(answers))
    tables = {**rules, **{t: tm for t, tm in model.items() if tm.status == "proposed"}}
    # the OLD mapping: no edge into the model's tables (strip what the fixed proposer adds)
    stale = {}
    for t, tm in tables.items():
        if tm.node:
            edges = [e for e in tm.edges if not any(
                e.get("fk", "").endswith(f"-> {a}") for a in answers)]
            stale[t] = tm.model_copy(update={"edges": edges})
        else:
            stale[t] = tm
    save_mapping(SourceMapping(source_id="c1b", tables=stale))
    approve_mapping("c1b", principal=test_principal("analyst"), vocab=vocab)
    m = ensure_mapping("c1b", snaps, vocab)
    affected = [t for t, s in snaps.items() if stale[t].node and any(
        _ref(fk) in answers for fk in _fk_list(s))]
    assert affected                                                       # non-vacuous
    for t in affected:
        tm = m.tables[t]
        assert tm.status == "proposed" and tm.approval is None, t
        assert any("re-proposed" in r for r in tm.rationale)
        cols = {fk["column"] for fk in _fk_list(snaps[t]) if _ref(fk) in answers}
        assert cols <= {c for e in tm.edges for c in e["evidence"]}
    # a table whose edges did not change keeps its approval
    kept = [t for t, tm in m.tables.items() if t not in affected and stale[t].node]
    assert kept and all(m.tables[t].status == "approved" for t in kept)


def _build_store(tmp_path, name):
    from databridge_graph.store import GraphStore

    return GraphStore(str(tmp_path / f"{name}.db"))


def _approved_tables(estate, snaps):
    """The tables the rules map, as approved mapping dicts (a test human approved them)."""
    from databridge_mcp_ingest.mapping import _policy, fk_edges, propose_rules

    rules = propose_rules(snaps, estate["vocab"])
    kinds = {t: (tm.node or {}).get("kind") for t, tm in rules.items() if tm.node}
    out = {}
    for t, tm in rules.items():
        if tm.node:
            edges, _ = fk_edges(snaps[t], tm.node["kind"], kinds, estate["vocab"], _policy())
            out[t] = {**tm.model_dump(mode="json"), "edges": edges, "status": "approved"}
        elif tm.association and tm.status == "proposed":
            out[t] = {**tm.model_dump(mode="json"), "status": "approved"}
    return out


def test_c1_build_counts_every_reference_and_every_edge(walnut_env, estate, tmp_path):
    import datetime as dt

    from databridge_mcp_ingest.build import build_tables

    snaps = estate["snaps"]
    maps = _approved_tables(estate, snaps)
    small = {t: s for t, s in snaps.items() if t in maps and len(s["rows"]) <= 5000}
    store = _build_store(tmp_path, estate["name"])
    out = build_tables(store, estate["vocab"], source_id="c1", retrieved_at=dt.datetime.now(
        dt.timezone.utc), mask_cfg=None,
        tables=[(s, f"sha-{t}", maps[t]) for t, s in small.items()])
    recs = {r["table"]: r for r in out["completeness"]}
    checked = 0

    def keys_of(fk):
        target = small.get(_ref(fk))
        return None if target is None else {str(x[fk["ref_column"]]) for x in target["rows"]}

    for t, s in small.items():
        rec = recs[t]
        assert rec["captured_rows"] == len(s["rows"]) and rec["rows_built"] == len(s["rows"])
        assert rec["source_rows"] == s["row_count_total"] and rec["capped"] is False
        assert rec["declared_fks"] == len(_fk_list(s))
        if maps[t].get("association"):
            cols = [fk for fk in _fk_list(s) if fk["column"] in maps[t]["edges"][0]["evidence"]]
            want = sum(1 for row in s["rows"] if all(
                keys_of(fk) is not None and str(row[fk["column"]]) in keys_of(fk)
                for fk in cols))
            assert rec["nodes"] == 0 and rec["edges_expected"] == want
            assert rec["edges_built"] == rec["graph_rows"] == want
            checked += want > 0
            continue
        assert rec["nodes"] == rec["graph_rows"] == len(
            {tuple(r[c] for c in s["primary_key"]) for r in s["rows"]})
        for fk in _fk_list(s):
            r = next(x for x in rec["foreign_keys"] if x["column"] == fk["column"])
            present = [row[fk["column"]] for row in s["rows"] if row[fk["column"]] is not None]
            keys = keys_of(fk)
            expected = sum(1 for v in present if keys is not None and str(v) in keys)
            assert (r["rows_with_value"], r["edges_expected"]) == (len(present), expected)
            assert r["unresolved_references"] == len(present) - expected
            assert r["target_captured"] is (keys is not None)
            # an expected edge is built — unless the mapping has no edge for the key, which is
            # then COUNTED as missing, never silent
            assert r["edges_built"] == (expected if r["edge_kind"] else 0), (t, fk, r)
            assert r["edges_missing"] == (0 if r["edge_kind"] else expected)
            checked += expected > 0
    assert checked > 0
    assert out["edges"] == sum(rec["edges_built"] for rec in recs.values())


def test_c1_a_reference_to_a_non_key_column_meets_the_row_however_it_is_keyed(
        walnut_env, estate, tmp_path):
    """Rewrite one declared FK (data AND declaration) to reference a UNIQUE NON-KEY column of its
    target: the edges built must be exactly those of the original key reference."""
    import datetime as dt

    from databridge_mcp_ingest.build import build_tables

    snaps = estate["snaps"]
    maps = _approved_tables(estate, snaps)
    case = None
    for t, s in sorted(snaps.items()):
        if t not in maps or len(s["rows"]) > 5000:
            continue
        for fk in _fk_list(s):
            target = snaps.get(_ref(fk))
            if target is None or _ref(fk) not in maps or len(target["primary_key"]) != 1:
                continue
            for col in [c["name"] for c in target["columns"]]:
                vals = [r[col] for r in target["rows"]]
                if col in target["primary_key"] or None in vals or len(set(vals)) != len(vals):
                    continue
                case = (t, fk, col)
                break
            if case:
                break
        if case:
            break
    assert case, "no FK whose target has a unique non-key column"
    t, fk, col = case
    target = copy.deepcopy(snaps[_ref(fk)])
    by_key = {str(r[fk["ref_column"]]): r[col] for r in target["rows"]}
    src = copy.deepcopy(snaps[t])
    for r in src["rows"]:
        if r[fk["column"]] is not None:
            r[fk["column"]] = by_key.get(str(r[fk["column"]]), r[fk["column"]])
    for f in src["foreign_keys"]:
        if f["column"] == fk["column"]:
            f["ref_column"] = col
    now = dt.datetime.now(dt.timezone.utc)

    def edges_for(snapshot, name):
        store = _build_store(tmp_path, name)
        out = build_tables(store, estate["vocab"], source_id="c1n", retrieved_at=now,
                           mask_cfg=None, tables=[(snapshot, "sha-a", maps[t]),
                                                  (target, "sha-b", maps[_ref(fk)])])
        rec = next(r for r in out["completeness"] if r["table"] == t)
        return next(r for r in rec["foreign_keys"] if r["column"] == fk["column"]), store

    original, _ = edges_for(snaps[t], "orig")
    rewritten, _ = edges_for(src, "nonkey")
    assert original["edges_built"] > 0
    assert rewritten["edges_built"] == original["edges_built"]
    assert rewritten["unresolved_references"] == original["unresolved_references"]
    assert rewritten["edges_expected"] == original["edges_expected"]


# === C-2 / FIN-18: whole tables by default, an explicit cap reported ================================
def test_c2_tables_are_read_whole_by_default_and_paged(estate):
    from databridge_mcp_ingest.connections import Scope

    db, snaps = estate["db"], estate["snaps"]
    assert Scope().max_rows_per_table is None                  # no implicit cap
    for t, s in snaps.items():
        schema, table = t.split(".", 1)
        assert len(s["rows"]) == s["row_count_total"] == _count(db, schema, table)
        assert s["limit"] is None
    # paging, forced with a small page: every row once, in key order
    client = estates.engine_client(db)
    t = max(snaps, key=lambda k: (len(snaps[k]["rows"]), k))
    schema, table = t.split(".", 1)
    page = max(2, len(snaps[t]["rows"]) // 3)
    paged = estates.snapshot(client, schema, table, page_rows=page)
    assert paged["rows"] == snaps[t]["rows"]
    pages = [q for q in client.sql if "OFFSET" in q]
    assert len(pages) >= 3                                     # it really paged


def test_c2_an_explicit_cap_is_recorded_and_reported_n_of_m(estate, walnut_env):
    from databridge_mcp_ingest import capture, runner
    from databridge_mcp_ingest.connections import Scope, SourceConnection
    from databridge_mcp_ingest.crawl import Item

    db, snaps = estate["db"], estate["snaps"]
    t = max(snaps, key=lambda k: (len(snaps[k]["rows"]), k))
    schema, table = t.split(".", 1)
    total = _count(db, schema, table)
    cap = max(1, total // 2)
    conn = SourceConnection.model_validate({
        "id": "c2", "transport": {"type": "stdio", "command": "uvx"},
        "scope": {"max_rows_per_table": cap, "table_page_rows": max(1, cap // 2)}})
    lines: dict[str, list] = {"coverage": [], "inventory": [], "captured": [], "failed": []}

    class L:
        def __init__(self, name):
            self.name = name

        def write(self, rec):
            lines[self.name].append(rec)

    item = Item(source_id="c2", step_id="sql", kind="table", uri=t, name=table, rel=t,
                mime="application/json", ctx={"schema": schema, "table": table})
    counts = {"captured": 0}
    ok = runner._fetch_one(estates.engine_client(db), estates.sql_step(), item, conn,
                           {k: L(k) for k in lines}, lambda *a: None, counts)
    assert ok
    snap = json.loads(capture.get_bytes(lines["captured"][0]["sha256"]))
    assert len(snap["rows"]) == cap and snap["limit"] == cap and snap["row_count_total"] == total
    [cov] = lines["coverage"]
    assert (cov["rows_read"], cov["rows_total"], cov["rule"]) == (cap, total, "row_cap")
    assert f"{cap} of {total}" in cov["detail"]
    assert Scope(max_rows_per_table=None).max_rows_per_table is None


def test_c2_a_declared_cap_comes_only_from_the_operator_manifest(tmp_path, walnut_env):
    from databridge_mcp_ingest.connections import import_declarations

    decl = {"version": 1, "servers": [
        {"id": f"{n}_db", "source_class": "database", "scope": {"roots": [n], **extra},
         "direct": {"transport": "stdio", "command": "uvx", "args": ["x"]}}
        for n, extra in (("workspace", {}), ("finance", {"max_rows_per_table": 7}))]}
    p = tmp_path / "servers.yaml"
    p.write_text(yaml.safe_dump(decl), encoding="utf-8")
    got = {c.id: c.scope.max_rows_per_table for c in import_declarations(p)}
    assert got == {"workspace_db": None, "finance_db": 7}


# === C-3: proposed links where no FK is declared ====================================================
def _strip_fk(snaps, t, col):
    out = copy.deepcopy(snaps)
    out[t]["foreign_keys"] = [f for f in out[t]["foreign_keys"] if f["column"] != col]
    return out


def test_c3_an_undeclared_reference_is_proposed_like_the_fk_it_should_have_been(estate):
    """Remove each declared FK in turn: the measured overlap proposes the same target, with the
    same edge kind the declared FK gets — and never a link INTO a person kind."""
    from databridge_mcp_ingest.mapping import (_key_values, is_person_kind, propose_links,
                                               propose_rules)

    vocab, snaps = estate["vocab"], estate["snaps"]
    rules = propose_rules(snaps, vocab)
    seen = proposed = refused = 0
    for t, s in sorted(snaps.items()):
        if not rules[t].node or len(s["rows"]) > 5000:
            continue
        for fk in _fk_list(s):
            target = _ref(fk)
            if target not in rules or not rules[target].node or not _key_values(snaps[target]):
                continue
            values = [r[fk["column"]] for r in s["rows"] if r[fk["column"]] is not None]
            if len(values) < 3:
                continue
            seen += 1
            stripped = _strip_fk(snaps, t, fk["column"])
            tables = propose_rules(stripped, vocab)
            links = propose_links("src", stripped, tables, vocab).get(t, [])
            link = next(lk for lk in links if lk["column"] == fk["column"])
            assert (link["source"], link["table"]) == ("src", target)
            assert link["matched"] == sum(1 for v in values if str(v) in {
                str(r[fk["ref_column"]]) for r in snaps[target]["rows"]})
            if is_person_kind(vocab, rules[target].node["kind"]):
                refused += 1
                assert link["status"] == "not_proposed" and link["edge"] is None
            else:
                proposed += 1
                want = next(e for e in rules[t].edges if e["evidence"] == [fk["column"]])
                assert link["status"] == "proposed"
                assert (link["edge"]["kind"], link["edge"]["src"], link["edge"]["dst"]) == \
                    (want["kind"], want["src"], want["dst"])
    assert seen and proposed                                              # non-vacuous


def test_c3_nothing_is_proposed_below_the_measured_threshold(estate):
    """Exhaustive: every (column, target key) pair of the estate is measured by the TEST; a link is
    proposed exactly where the policy's thresholds hold, and every target is a mapped table."""
    from databridge_mcp_ingest.mapping import (_NUMERIC_TYPE, _key_values, _policy,
                                               propose_links, propose_rules)

    vocab, snaps = estate["vocab"], estate["snaps"]
    lp = _policy()["link_proposals"]
    rules = propose_rules(snaps, vocab)
    links = propose_links("src", snaps, rules, vocab)
    keys = {t: _key_values(s) for t, s in snaps.items() if rules[t].node}
    for t, s in snaps.items():
        if not rules[t].node:
            assert t not in links
            continue
        declared = {f["column"] for f in s["foreign_keys"]}
        types = {c["name"]: str(c["type"] or "") for c in s["columns"]}
        got = {lk["column"]: lk for lk in links.get(t, [])}
        for col in [c["name"] for c in s["columns"]]:
            vals = [str(r[col]) for r in s["rows"] if r[col] is not None]
            eligible = (col not in s["primary_key"] and col not in declared
                        and not _NUMERIC_TYPE.search(types[col]) and len(vals) >= lp["min_matched"])
            hit = eligible and any(
                kv is not None and not (u == t and kv[0] == col)
                and sum(v in kv[1] for v in vals) >= lp["min_matched"]
                and sum(v in kv[1] for v in vals) / len(vals) >= lp["min_ratio"]
                for u, kv in keys.items())
            assert (col in got) == bool(hit), (t, col)


@pytest.mark.real_approval
def test_c3_an_approved_link_becomes_an_edge_into_the_other_source(walnut_env, estate,
                                                                   tmp_path):
    """Split the estate in two sources at a declared FK whose target is not a person kind, drop
    the declaration: the link is proposed, nothing is built from the proposal, and once a person
    approves it the build joins the two sources' rows."""
    import datetime as dt

    from databridge_mcp_ingest.build import build_tables
    from databridge_mcp_ingest.mapping import (MappingApprovalRefused, SourceMapping, approve_link,
                                               approve_mapping, is_person_kind, load_mapping,
                                               propose_rules, refresh_link_proposals,
                                               save_mapping)

    vocab, snaps = estate["vocab"], estate["snaps"]
    rules = propose_rules(snaps, vocab)
    case = next(((t, fk) for t, s in sorted(snaps.items()) if rules[t].node
                 and len(s["rows"]) <= 5000 for fk in _fk_list(s)
                 if rules.get(_ref(fk)) and rules[_ref(fk)].node
                 and not is_person_kind(vocab, rules[_ref(fk)].node["kind"])
                 and _ref(fk) != t), None)
    assert case
    t, fk = case
    target = _ref(fk)
    mine = _strip_fk({t: snaps[t]}, t, fk["column"])
    save_mapping(SourceMapping(source_id="left", tables=propose_rules(mine, vocab)))
    approve_mapping("left", principal=test_principal("analyst"), vocab=vocab)
    others = {"right": {target: (snaps[target], rules[target].node["kind"])}}
    m = refresh_link_proposals("left", mine, vocab, others=others)
    [link] = [lk for lk in m.tables[t].links if lk["column"] == fk["column"]]
    assert (link["source"], link["table"], link["status"]) == ("right", target, "proposed")
    assert m.tables[t].status == "approved"            # a proposal touches no approval
    with pytest.raises(MappingApprovalRefused):
        approve_link("left", t, fk["column"], principal="someone", vocab=vocab)
    m = approve_link("left", t, fk["column"], principal=test_principal("analyst"), vocab=vocab)
    tm = m.tables[t]
    assert tm.status == "approved" and tm.approval["principal_id"] == "analyst"
    [edge] = [e for e in tm.edges if e.get("ref")]
    assert edge["ref"] == {"source": "right", "table": target, "key": link["key"]}
    store = _build_store(tmp_path, f"link_{estate['name']}")
    now = dt.datetime.now(dt.timezone.utc)
    right_map = {**rules[target].model_dump(mode="json"), "edges": [], "status": "approved"}
    build_tables(store, vocab, source_id="right", retrieved_at=now, mask_cfg=None,
                 tables=[(snaps[target], "sha-r", right_map)])
    out = build_tables(store, vocab, source_id="left", retrieved_at=now, mask_cfg=None,
                       tables=[(mine[t], "sha-l", load_mapping("left").tables[t].model_dump(
                           mode="json"))],
                       context={("right", target): snaps[target]})
    rec = next(r for r in out["completeness"] if r["table"] == t)
    ref = next(r for r in rec["foreign_keys"] if r["column"] == fk["column"])
    keys = {str(r[fk["ref_column"]]) for r in snaps[target]["rows"]}
    want = sum(1 for r in mine[t]["rows"] if r[fk["column"]] is not None
               and str(r[fk["column"]]) in keys)
    assert ref["origin"] == "approved_link" and ref["edges_built"] == want > 0
    n = store.fetchall("SELECT COUNT(*) AS n FROM edges WHERE kind = ? AND system_to IS NULL",
                       (edge["kind"],))[0]["n"]
    assert n >= want


def test_c3_a_link_into_a_person_kind_is_refused_by_validation(estate):
    from databridge_mcp_ingest.mapping import TableMapping, validate_table_mapping

    vocab = estate["vocab"]
    person = next(k for k in sorted(vocab.node_kinds) if vocab.is_subject(k))
    other = next(k for k in sorted(vocab.node_kinds) if not vocab.is_subject(k))
    ek = sorted(vocab.edge_kinds)[0]
    tm = TableMapping(node={"kind": other, "label": "{a}", "sources": ["a"], "attrs": {}},
                      edges=[{"kind": ek, "src": f"{other}:{{walnut_row_key}}",
                              "dst": f"{person}:{{walnut_fk_c}}", "evidence": ["c"],
                              "ref": {"source": "s", "table": "t", "key": "k"}}])
    probs = validate_table_mapping("t", tm, vocab)
    assert any("link to person kind" in p for p in probs), probs
    # the same link into a kind that is not a person: no such problem (non-vacuous)
    ok = tm.model_copy(update={"edges": [{**tm.edges[0], "dst": f"{other}:{{walnut_fk_c}}"}]})
    assert not any("link to person kind" in p for p in validate_table_mapping("t", ok, vocab))


# === FIN-14: one directory per run; run history labels honestly ======================================
def test_fin14_list_runs_skips_a_recipe_side_directory_and_labels_a_run_in_flight(walnut_env):
    from databridge_mcp_ingest import runner

    for sid in ESTATE_NAMES:
        base = runner.source_dir(sid)
        side = base / "20261001T000000Z-aaaaaa"
        side.mkdir(parents=True)
        for f in ("profile.json", "facts.manifest.json", "cards.jsonl", "tabular.stages.jsonl"):
            (side / f).write_text("{}", encoding="utf-8")
        done = base / "20261001T000001Z-bbbbbb"
        done.mkdir()
        (done / "binding.json").write_text("{}", encoding="utf-8")
        (done / "report.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        live = base / "20261001T000002Z-cccccc"
        live.mkdir()
        (live / "binding.json").write_text("{}", encoding="utf-8")
        assert [(r["run_id"], r["status"]) for r in runner.list_runs(sid)] == [
            (done.name, "ok"), (live.name, "incomplete")]
        with runner._source_lock(sid):
            assert [r["status"] for r in runner.list_runs(sid)] == ["ok", "running"]
        assert [r["status"] for r in runner.list_runs(sid)] == ["ok", "incomplete"]


# === completeness in the RUN REPORT (an E2E asserts on it) ==========================================
def test_completeness_reaches_the_run_report_totals(walnut_env, estate, tmp_path):
    import datetime as dt

    from databridge_mcp_ingest import runner
    from databridge_mcp_ingest.build import build_tables, unbuilt_completeness

    snaps = estate["snaps"]
    maps = _approved_tables(estate, snaps)
    small = {t: s for t, s in snaps.items() if t in maps and len(s["rows"]) <= 5000}
    out = build_tables(_build_store(tmp_path, "rep"), estate["vocab"], source_id="rep",
                       retrieved_at=dt.datetime.now(dt.timezone.utc), mask_cfg=None,
                       tables=[(s, f"sha-{t}", maps[t]) for t, s in small.items()])
    unbuilt = [t for t in snaps if t not in maps]
    rd = runner.run_dir_for("rep", "20261002T000000Z-dddddd")
    rd.mkdir(parents=True)
    for rec in out["completeness"]:
        runner._write_completeness(rd, rec)
    for t in unbuilt:
        runner._write_completeness(rd, unbuilt_completeness(
            snaps[t], source_id="rep", snaps={("rep", u): v for u, v in small.items()},
            reason="unmapped"))
    summary = runner.verify_run("rep", rd.name)["counts"]["completeness"]
    totals = summary["totals"]
    assert totals["tables"] == len(small) + len(unbuilt)
    assert totals["source_rows"] == sum(s["row_count_total"] for t, s in snaps.items()
                                        if t in small or t in unbuilt)
    assert totals["captured_rows"] == totals["source_rows"] and totals["rows_not_read"] == 0
    assert totals["nodes"] == out["nodes"]
    assert totals["edges_built"] == out["edges"]
    assert totals["declared_fks"] == sum(len(_fk_list(snaps[t])) for t in [*small, *unbuilt])
    by = {r["table"]: r for r in summary["tables"]}
    for t in unbuilt:
        assert by[t]["built"] is False and by[t]["nodes"] == by[t]["edges_built"] == 0
        assert by[t]["edges_missing"] == by[t]["edges_expected"]
        assert by[t]["edges_expected"] + by[t]["unresolved_references"] == sum(
            sum(1 for r in snaps[t]["rows"] if r[f["column"]] is not None)
            for f in _fk_list(snaps[t]))
    assert totals["edges_missing"] == sum(by[t]["edges_missing"] for t in by)


# === join tables: their rows are EDGES between the rows they reference (MAT6 audit) ===============
def test_a_join_table_is_found_by_structure_and_becomes_edges(walnut_env, estate, tmp_path):
    import datetime as dt

    from databridge_mcp_ingest.build import build_tables
    from databridge_mcp_ingest.mapping import propose_rules

    snaps, vocab = estate["snaps"], estate["vocab"]
    rules = propose_rules(snaps, vocab)
    joins = []
    for t, s in snaps.items():
        fk_cols = {f["column"] for f in _fk_list(s)}
        structural = len(fk_cols) >= 2 and bool(s["primary_key"]) and \
            set(s["primary_key"]) <= fk_cols                     # the audit's rule, restated
        tm = rules[t]
        assert bool(tm.association) is structural, t
        if not structural:
            continue
        assert tm.node is None and tm.status == "proposed", (t, tm.rationale)
        [edge] = tm.edges
        assert edge["evidence"] == [c for c in s["primary_key"] if c in fk_cols][:2]
        joins.append(t)
    if estate["name"] == "finance":
        assert joins                                              # non-vacuous
    else:
        # the workspace estate has no join table: its two-FK table keeps its own key and is NOT
        # one (non-vacuous: such a table exists and stays a node-shaped decision)
        assert any(len({f["column"] for f in _fk_list(s)}) >= 2 for s in snaps.values())
        return
    maps = _approved_tables(estate, snaps)
    now = dt.datetime.now(dt.timezone.utc)
    for t in joins:
        refs = {_ref(f) for f in _fk_list(snaps[t])}
        store = _build_store(tmp_path, f"join_{t}")
        out = build_tables(store, vocab, source_id="j", retrieved_at=now, mask_cfg=None,
                           tables=[(snaps[u], f"sha-{u}", maps[u]) for u in [t, *sorted(refs)]
                                   if u in maps])
        rec = next(r for r in out["completeness"] if r["table"] == t)
        assert rec["nodes"] == 0 and rec["edges_built"] == rec["edges_expected"] > 0
        kind = maps[t]["edges"][0]["kind"]
        rows = store.fetchall(
            "SELECT e.id, s.source_pointer_json AS p FROM edges e JOIN sources s "
            "ON s.entity_id = e.id WHERE e.kind = ? AND e.system_to IS NULL", (kind,))
        cited = {json.loads(r["p"]).get("table") for r in rows}
        assert t in cited                       # each join edge cites the join row's own cells


# === end to end: a real sync of each estate; FIN-14 + completeness in the report =================
def _estate_profile(tmp_path, name):
    """A crawl profile and a SQL dialect for the estate's sqlite engine (the bundled sqlite dialect
    with a table listing that covers the estate's attached schemas)."""
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


def test_e2e_one_run_directory_and_completeness_in_the_report(walnut_env, estate, tmp_path,
                                                              monkeypatch):
    import databridge_mcp_ingest as mi
    from databridge_graph.store import GraphStore
    from databridge_mcp_ingest import register_connection, runner

    name, db = estate["name"], estate["db"]
    monkeypatch.setenv("WALNUT_CRAWL_PROFILES_DIR", str(_estate_profile(tmp_path, name)))
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    # the finance estate is large: the tables of one join and the CRM keep the run short
    include = (["public.*"] if name == "workspace" else
               ["crm.*", "core_banking.party", "core_banking.account", "core_banking.facility",
                "core_banking.facility_account"])
    sid = f"e2e_{name}"
    register_connection({"id": sid, "transport": {"type": "stdio", "command": "uvx"},
                         "scope": {"tables_include": include}, "auto_approve": True})
    client = estates.engine_client(db)
    mi.propose_plan(sid, client=client)
    store = GraphStore(str(tmp_path / "g.db"))
    rep = mi.run_ingest(sid, store=store, client=client, vocab=estate["vocab"])
    assert rep.status in ("ok", "unverified"), (rep.status, rep.message)
    # FIN-14: the recipe's artefacts and the core run share ONE directory; nothing else is a run
    runs = runner.list_runs(sid)
    assert [r["run_id"] for r in runs] == [rep.run_id]
    dirs = [p for p in runner.source_dir(sid).iterdir() if p.is_dir()]
    assert [p.name for p in dirs] == [rep.run_id]
    assert (Path(rep.run_dir) / "tabular.stages.jsonl").is_file()
    # completeness: in the written report, per captured table, against the engine's own counts
    body = json.loads((Path(rep.run_dir) / "report.json").read_text(encoding="utf-8"))
    comp = body["counts"]["completeness"]
    captured = {r["uri"] for r in runner.read_jsonl(Path(rep.run_dir) / "captured.jsonl")}
    assert {t["table"] for t in comp["tables"]} == captured and captured
    truth = {t: _count(db, *t.split(".", 1)) for t in captured}
    assert comp["totals"]["source_rows"] == comp["totals"]["captured_rows"] == sum(truth.values())
    assert comp["totals"]["rows_not_read"] == 0 and comp["totals"]["capped_tables"] == []
    for t in comp["tables"]:
        assert t["source_rows"] == truth[t["table"]]
        for fk in t["foreign_keys"]:
            # every expected edge is built, or the mapping's lack of one is COUNTED
            assert fk["edges_built"] + fk["edges_missing"] == fk["edges_expected"]
            if t["built"] and fk["edge_kind"]:
                # a missing edge has a counted reason: its target row is not in the graph (e.g.
                # the screen kept that table out) or the value was masked
                assert fk["edges_missing"] <= fk["dangling"] + fk["masked"], (t["table"], fk)
    assert comp["totals"]["edges_built"] > 0
