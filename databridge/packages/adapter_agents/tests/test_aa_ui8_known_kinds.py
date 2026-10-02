"""UI8 (R-026 LINK7 cross-track 2): `McpIngestService.propose_table_mappings` — the call an adapter
agent makes for a NEW table — tells the mapping rules the kinds the source's mapping already gives
its other tables (`known_kinds`), exactly as an ingest sync (`mapping.ensure_mapping`) does.

Without them, a foreign key from the new table into a table that only the MAPPING types (a person
or the model chose its kind; no rule maps its name) got no edge, so an adapter-agent sync mapped
the table differently from an ingest sync of the same estate.

Both fixture estates (workspace/Larkspur and finance), read through the real `crawl.fetch_table`.
The oracle is the rules' edge for the same column on the UN-renamed estate — what a sync gives it
when the target's kind is known — and each test checks it is not empty.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

MI_TESTS = Path(__file__).resolve().parents[2] / "mcp_ingest" / "tests"
if str(MI_TESTS) not in sys.path:
    sys.path.insert(0, str(MI_TESTS))

from test_aa_link7_fk_edges import (_alias, _ref, _renamed, estate,  # noqa: E402,F401
                                    estate_snaps)

SRC = "ui8_src"


def _cases(snaps: dict, vocab):
    """(table, fk column, target): a table the rules map with an FK edge into ANOTHER mapped,
    non-join table."""
    from databridge_mcp_ingest.mapping import propose_rules

    rules = propose_rules(snaps, vocab)
    for t, tm in sorted(rules.items()):
        if not tm.node:
            continue
        for e in tm.edges:
            if not e.get("fk"):
                continue
            col = e["evidence"][0]
            fk = next(f for f in snaps[t]["foreign_keys"] if f["column"] == col)
            target = _ref(fk)
            if target == t or not (rules.get(target) and rules[target].node):
                continue
            yield t, col, target, e, rules


def _shape(e: dict) -> tuple:
    return (e["kind"], tuple(e["evidence"]), e["src"], e["dst"], bool(e.get("reversed")))


def test_a_new_table_s_fk_into_a_table_only_the_mapping_types_gets_the_sync_edge(
        estate, tmp_path, monkeypatch):
    from databridge_adapter_agents.ingest_service import McpIngestService
    from databridge_mcp_ingest import drift
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping, save_mapping

    vocab, snaps = estate["vocab"], estate["snaps"]
    checked = 0
    for t, col, target, want, rules in _cases(snaps, vocab):
        renamed = _renamed(snaps, target)
        alias = _alias(target)
        # the source's mapping types the renamed target (a person approved its kind); `t` is new
        save_mapping(SourceMapping(source_id=SRC, tables={alias: TableMapping.model_validate(
            {**rules[target].model_dump(mode="json"), "status": "approved"})}), tmp_path)
        monkeypatch.setattr(drift, "profile_tables",
                            lambda sid, refs, root=None, client=None, _r=renamed:
                            {r: _r[r] for r in refs})
        got = McpIngestService(sources_root=tmp_path).propose_table_mappings(
            SRC, [t], vocab=vocab)
        edges = [e for e in got[t]["edges"] if e["evidence"] == [col]]
        assert [_shape(e) for e in edges] == [_shape(want)], (t, col, edges)
        assert got[t]["node"]["kind"] == rules[t].node["kind"]
        checked += 1
        if checked >= 2:
            break
    assert checked                                                      # non-vacuous


def test_an_unmapped_entry_lends_no_kind(estate, tmp_path, monkeypatch):
    """Only a MAPPED entry is a known kind: a target the mapping holds as `unmapped` still gives
    the new table's reference no edge (the rule is the sync's, including its refusals)."""
    from databridge_adapter_agents.ingest_service import McpIngestService
    from databridge_mcp_ingest import drift
    from databridge_mcp_ingest.mapping import SourceMapping, TableMapping, save_mapping

    vocab, snaps = estate["vocab"], estate["snaps"]
    t, col, target, _, rules = next(_cases(snaps, vocab))
    renamed = _renamed(snaps, target)
    alias = _alias(target)
    entry = {**rules[target].model_dump(mode="json"), "status": "unmapped"}
    save_mapping(SourceMapping(source_id=SRC, tables={
        alias: TableMapping.model_validate(entry)}), tmp_path)
    monkeypatch.setattr(drift, "profile_tables",
                        lambda sid, refs, root=None, client=None: {r: renamed[r] for r in refs})
    got = McpIngestService(sources_root=tmp_path).propose_table_mappings(SRC, [t], vocab=vocab)
    assert not [e for e in got[t]["edges"] if e["evidence"] == [col]]
