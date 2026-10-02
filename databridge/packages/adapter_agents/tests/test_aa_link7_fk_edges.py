"""LINK7 (finance dry run, 2026-10-02): an adapter agent's schema adaptation gives a foreign key
the SAME edge an ingest sync gives it.

`adapt.py` had its own FK-edge code: it re-ran the mapping rules on the one changed table, so the
referenced table's kind came only from its NAME. A reference into a table the model (or a person)
mapped got no edge (C-1 / FIN-06), and a new JOIN table was "noted unmapped". It now uses
`mapping.fk_edges` / `mapping.resolve_pending_fks` against the kinds the source's mapping already
gives every table.

Both fixture estates (workspace/Larkspur and finance), read through the real `crawl.fetch_table`;
the expected edges are what the ingest-sync rule produces for the same tables (the oracle), and
each test checks that the oracle is not empty.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

MI_TESTS = Path(__file__).resolve().parents[2] / "mcp_ingest" / "tests"
if str(MI_TESTS) not in sys.path:
    sys.path.insert(0, str(MI_TESTS))

import estates  # noqa: E402

ESTATE_NAMES = ("workspace", "finance")
ALIAS = "zzq_rows"                      # a table name no rule maps: only the mapping knows its kind


@pytest.fixture(scope="module")
def estate_snaps(tmp_path_factory):
    out = {}
    for name in ESTATE_NAMES:
        d = tmp_path_factory.mktemp(f"aa_link7_{name}")
        db = estates.build_estate(name, d / "e.sqlite")
        try:
            out[name] = estates.snapshots(db, name)
        finally:
            db.close()
    return out


@pytest.fixture(params=ESTATE_NAMES)
def estate(request, estate_snaps):
    from databridge_graph.packs import deployment

    with deployment(estates.ESTATES[request.param]["pack"]) as vocab:
        yield {"name": request.param, "snaps": estate_snaps[request.param], "vocab": vocab}


class _Service:
    """The two calls the adaptation makes for a table: the vocabulary, and the proposal for a new
    table — the rules on that table ALONE, as `McpIngestService.propose_table_mappings` does
    (no model here)."""

    def __init__(self, vocab, snaps):
        self.vocab, self.snaps = vocab, snaps

    def vocabulary(self):
        return self.vocab

    def propose_table_mappings(self, conn, refs, *, vocab, harness=None):
        from databridge_mcp_ingest.mapping import propose_rules

        out = propose_rules({r: self.snaps[r] for r in refs}, vocab)
        return {t: tm.model_dump(mode="json") for t, tm in out.items()}


def _engine(tmp_path, svc):
    from databridge_adapter_agents.adapt import AdaptationEngine

    return AdaptationEngine(root_fn=lambda: tmp_path, service=svc)


def _ref(fk):
    return f"{fk['ref_schema']}.{fk['ref_table']}" if fk.get("ref_schema") else fk["ref_table"]


def _alias(ref: str) -> str:
    schema = ref.rpartition(".")[0]
    return f"{schema}.{ALIAS}" if schema else ALIAS


def _renamed(snaps: dict, target: str) -> dict:
    """The estate with `target` renamed to a name no rule maps (every reference follows it)."""
    out = {}
    for t, s in snaps.items():
        s = copy.deepcopy(s)
        if t == target:
            s["table"] = ALIAS
            t = _alias(target)
        for fk in s.get("foreign_keys") or []:
            if fk.get("ref_table") and _ref(fk) == target:
                fk["ref_table"] = ALIAS
        out[t] = s
    return out


def _shape(snaps: dict, refs: list[str]):
    from databridge_mcp_ingest.drift import ColumnShape, SourceShape, TableShape

    return SourceShape(source_id="src", tables={r: TableShape(
        ref=r, columns={c["name"]: ColumnShape(name=c["name"], type=str(c.get("type") or ""))
                        for c in snaps[r]["columns"]},
        primary_key=list(snaps[r].get("primary_key") or []),
        foreign_keys=[dict(f) for f in snaps[r].get("foreign_keys") or []]) for r in refs})


# --- a new foreign-key column on an approved table ---------------------------------------------
def test_a_new_fk_into_a_table_only_the_mapping_types_gets_the_sync_edge(estate, tmp_path):
    from databridge_mcp_ingest.drift import Drift
    from databridge_mcp_ingest.mapping import propose_rules, resolve_pending_fks

    vocab, snaps = estate["vocab"], estate["snaps"]
    rules = propose_rules(snaps, vocab)
    checked = 0
    for t, tm in sorted(rules.items()):
        if not tm.node:
            continue
        for edge in [e for e in tm.edges if e.get("fk")]:
            col = edge["evidence"][0]
            fk = next(f for f in snaps[t]["foreign_keys"] if f["column"] == col)
            target = _ref(fk)
            if target == t or not (rules.get(target) and rules[target].node):
                continue
            renamed = _renamed(snaps, target)
            alias = _alias(target)
            approved = tm.model_dump(mode="json")
            approved.update(status="approved", edges=[e for e in approved["edges"]
                                                       if e["evidence"] != [col]])
            approved["node"]["attrs"].pop(col, None)
            mapping = {"tables": {t: approved,
                                  alias: {**rules[target].model_dump(mode="json"),
                                          "status": "approved"}}}
            # the oracle: what an ingest sync gives the same table (FIN-06's re-resolution)
            from databridge_mcp_ingest.mapping import TableMapping
            got = resolve_pending_fks({u: TableMapping.model_validate(e)
                                       for u, e in mapping["tables"].items()},
                                      {t: renamed[t]}, vocab)
            want = [e for e in got[t].edges if e["evidence"] == [col]]
            assert want, (t, col)                                       # non-vacuous oracle
            eng = _engine(tmp_path, _Service(vocab, renamed))
            ctx = {"mapping": mapping, "current": _shape(renamed, [t, alias]), "plan_tools": {}}
            d = Drift(kind="added_column", unit=f"table:{t}", table=t, column=col, detail=f"+{col}",
                      new={"type": "", "foreign_key": next(
                          f for f in renamed[t]["foreign_keys"] if f["column"] == col)})
            p = eng._propose_table("src", t, [d], ctx, None)
            entry = p.content["entry"]
            assert [e for e in entry["edges"] if e["evidence"] == [col]] == want, (t, col)
            assert entry["node"]["attrs"].get(col) == col               # the value is kept too
            assert entry["node"]["kind"] == tm.node["kind"]             # the person's kind
            checked += 1
            break
    assert checked                                                      # non-vacuous


def test_a_new_fk_whose_target_no_kind_admits_gets_no_edge_and_says_so(estate, tmp_path):
    """The rule is the sync's, including its refusals: a reference into a table the mapping does
    not type, under a name no rule maps, gets no edge (and the note says why) — never a guess."""
    from databridge_mcp_ingest.drift import Drift
    from databridge_mcp_ingest.mapping import propose_rules

    vocab, snaps = estate["vocab"], estate["snaps"]
    rules = propose_rules(snaps, vocab)
    t, col, target = next((t, e["evidence"][0], _ref(next(
        f for f in snaps[t]["foreign_keys"] if f["column"] == e["evidence"][0])))
        for t, tm in sorted(rules.items()) if tm.node for e in tm.edges if e.get("fk"))
    renamed = _renamed(snaps, target)
    approved = {**rules[t].model_dump(mode="json"), "status": "approved"}
    approved["edges"] = [e for e in approved["edges"] if e["evidence"] != [col]]
    ctx = {"mapping": {"tables": {t: approved}},                         # the target is untyped
           "current": _shape(renamed, [t]), "plan_tools": {}}
    d = Drift(kind="added_column", unit=f"table:{t}", table=t, column=col, detail=f"+{col}",
              new={"type": "", "foreign_key": next(
                  f for f in renamed[t]["foreign_keys"] if f["column"] == col)})
    p = _engine(tmp_path, _Service(vocab, renamed))._propose_table("src", t, [d], ctx, None)
    assert not [e for e in p.content["entry"]["edges"] if e["evidence"] == [col]]
    assert any("no declared edge kind admits it" in n for n in p.notes)


# --- a new JOIN table -------------------------------------------------------------------------
def _join_case(snaps: dict) -> tuple[str, dict]:
    """The estate's join table (by structure: >=2 FKs, key made of FK columns only). An estate
    without one gets one DERIVED from its first table with two foreign keys: those two columns,
    keyed by both."""
    from databridge_mcp_ingest.mapping import is_association

    for t, s in sorted(snaps.items()):
        if is_association(s):
            return t, s
    t, s = next((t, s) for t, s in sorted(snaps.items())
                if len({f["column"] for f in s["foreign_keys"] if f.get("ref_table")}) >= 2)
    fks = [f for f in s["foreign_keys"] if f.get("ref_table")][:2]
    cols = [f["column"] for f in fks]
    schema = t.rpartition(".")[0]
    ref = f"{schema}.zzq_join" if schema else "zzq_join"
    j = {**copy.deepcopy(s), "table": "zzq_join", "primary_key": cols,
         "columns": [c for c in s["columns"] if c["name"] in cols], "foreign_keys": fks,
         "rows": [{c: r[c] for c in cols} for r in s["rows"]]}
    assert is_association(j)
    return ref, j


def test_a_new_join_table_becomes_the_sync_edges_not_an_unmapped_note(estate, tmp_path):
    from databridge_mcp_ingest.drift import Drift
    from databridge_mcp_ingest.mapping import association_mapping, propose_rules

    vocab, snaps = estate["vocab"], estate["snaps"]
    jref, jsnap = _join_case(snaps)
    rules = propose_rules(snaps, vocab)
    ends = [_ref(f) for f in jsnap["foreign_keys"]][:2]
    # the referenced tables are typed ONLY by the mapping: renamed so no rule maps their names
    world = {**snaps, jref: jsnap}
    tables: dict[str, Any] = {}
    for i, end in enumerate(ends):
        if end in tables or not rules[end].node:
            continue
        new = f"zzq_end{i}"
        schema = end.rpartition(".")[0]
        alias = f"{schema}.{new}" if schema else new
        world = {(alias if u == end else u): ({**s, "table": new} if u == end else s)
                 for u, s in world.items()}
        for f in world[jref]["foreign_keys"]:
            if _ref(f) == end:
                f["ref_table"] = new
        tables[alias] = {**rules[end].model_dump(mode="json"), "status": "approved"}
    kinds = {u: e["node"]["kind"] for u, e in tables.items()}
    want = association_mapping(jref, world[jref], kinds, vocab)
    assert want.status == "proposed" and want.edges                       # non-vacuous oracle
    alone = propose_rules({jref: world[jref]}, vocab)[jref]
    assert alone.status == "unmapped"                    # the rules alone cannot type its ends
    eng = _engine(tmp_path, _Service(vocab, world))
    ctx = {"mapping": {"tables": tables}, "current": _shape(world, [jref, *tables]),
           "plan_tools": {}}
    d = Drift(kind="new_table", unit=f"table:{jref}", table=jref, detail="new")
    p = eng._propose_table("src", jref, [d], ctx, None)
    assert p.noted is None and p.artifact == "mapping", p.noted
    entry = p.content["entry"]
    assert entry["association"] is True and entry["node"] is None
    assert entry["edges"] == want.edges
    end_kinds = {str(x).split(":", 1)[0] for e in want.edges for x in (e["src"], e["dst"])}
    assert d.subject_touching == any(vocab.is_subject(k) for k in end_kinds)
    assert p.hold == {"level": "table", "table": jref, "new": True}      # a person decides


def test_a_new_table_referencing_a_mapping_typed_table_gets_its_edge(estate, tmp_path):
    """A new NODE table whose declared FK points into a table the mapping types (its name maps to
    nothing) gets the edge the sync gives it, not "referenced table has no kind"."""
    from databridge_mcp_ingest.drift import Drift
    from databridge_mcp_ingest.mapping import TableMapping, propose_rules, resolve_pending_fks

    vocab, snaps = estate["vocab"], estate["snaps"]
    rules = propose_rules(snaps, vocab)
    t, col, target = next((t, e["evidence"][0], _ref(next(
        f for f in snaps[t]["foreign_keys"] if f["column"] == e["evidence"][0])))
        for t, tm in sorted(rules.items()) if tm.node and not tm.association
        for e in tm.edges if e.get("fk") and _ref(next(
            f for f in snaps[t]["foreign_keys"] if f["column"] == e["evidence"][0])) != t)
    renamed = _renamed(snaps, target)
    alias = _alias(target)
    tables = {alias: {**rules[target].model_dump(mode="json"), "status": "approved"}}
    alone = TableMapping.model_validate(_Service(vocab, renamed).propose_table_mappings(
        "src", [t], vocab=vocab)[t])
    assert not [e for e in alone.edges if e["evidence"] == [col]]       # the old path: no edge
    got = resolve_pending_fks({alias: TableMapping.model_validate(tables[alias]), t: alone},
                              {t: renamed[t]}, vocab)
    want = [e for e in got[t].edges if e["evidence"] == [col]]
    assert want                                                         # non-vacuous oracle
    eng = _engine(tmp_path, _Service(vocab, renamed))
    ctx = {"mapping": {"tables": tables}, "current": _shape(renamed, [t, alias]),
           "plan_tools": {}}
    d = Drift(kind="new_table", unit=f"table:{t}", table=t, detail="new")
    p = eng._propose_table("src", t, [d],
                           ctx, None)
    assert [e for e in p.content["entry"]["edges"] if e["evidence"] == [col]] == want
