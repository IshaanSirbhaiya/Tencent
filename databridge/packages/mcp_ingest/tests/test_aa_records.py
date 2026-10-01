"""AA — `records.py` is the typed format of what ingestion REALLY writes.

The contract test drives a real `run_ingest` over the stdio fixture server (files + SQLite dressed
as Postgres) with a fake model that labels organisations with an off-ontology kind, then validates
EVERY artefact of the run with `records.read_run(strict=True)`. If a writer renames or stops
writing a field a consumer relies on, this fails here — not silently in a consumer (R-017).
Expectations are derived from the run's own files, never typed in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import records
from databridge_mcp_ingest.runner import LEDGERS

from conftest import ENTITIES, fs_connection

OFF = "shipping_line"


def _off_ontology_harness():
    from databridge_harness import FakeBackend, Harness

    orgs = {n for n, k in ENTITIES.items() if k == "organisation"}

    def handler(*, model, messages, guided_json) -> str:
        last = messages[-1]["content"]
        if not last.startswith("DOCUMENT TEXT:\n"):
            return json.dumps({})
        text = last[len("DOCUMENT TEXT:\n"):]
        ents = []
        for name, kind in ENTITIES.items():
            i = text.find(name)
            if i >= 0:
                ents.append({"name": name, "kind": OFF if name in orgs else kind,
                             "char_start": i, "char_end": i + len(name)})
        return json.dumps({"entities": ents, "relations": []})

    return Harness(FakeBackend(handler), profile="cloud")


@pytest.fixture
def real_run(walnut_env, corpus, fixture_db, common_vocab, tmp_path, monkeypatch):
    from databridge_graph.store import GraphStore

    monkeypatch.setenv("WALNUT_FACTS_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("WALNUT_PASSAGES_DIR", str(tmp_path / "passages"))
    root, _ = corpus
    db, _ = fixture_db
    mi.register_connection(fs_connection("both", root, db=db))
    assert mi.propose_plan("both").status == "approved"
    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    report = mi.run_ingest("both", store=GraphStore(str(path)), harness=_off_ontology_harness())
    return Path(report.run_dir), report


def _raw(run_dir: Path, name: str) -> list[dict]:
    p = run_dir / f"{name}.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_every_artefact_of_a_real_run_validates(real_run):
    run_dir, report = real_run
    arts = records.read_run(run_dir, strict=True)          # raises on the first bad line
    assert arts.problems == []
    assert arts.report is not None and arts.report.status == report.status
    assert arts.report.run_id == run_dir.name
    # every typed ledger the runner writes was read, record for record
    typed = [n for n in LEDGERS if n in records.LEDGER_MODELS]
    assert typed, "the typed format must cover the runner's ledgers"
    for name in typed:
        assert (run_dir / f"{name}.jsonl").is_file(), name
        assert len(arts.ledgers[name]) == len(_raw(run_dir, name)), name
    # non-vacuous: the run exercised the models that matter to consumers
    for name in ("inventory", "captured", "governance", "governance_chunks", "refused",
                 "excluded", "blocked"):
        assert arts.ledgers[name], f"the fixture run wrote no {name} records"


def test_typed_counts_reconcile_with_the_report(real_run):
    run_dir, report = real_run
    arts = records.read_run(run_dir, strict=True)
    c = arts.report.counts
    assert len({r.item_id for r in arts.records("inventory")}) == c["inventory"]
    assert len({r.item_id for r in arts.records("captured")}) == c["captured"]
    excl = arts.exclusions_by_rule()
    assert sum(excl["excluded"].values()) == c["excluded"]
    assert sum(excl["blocked"].values()) == c["blocked"]
    assert len(arts.records("refused")) == c["refused"]


def test_refusal_sentences_parse_to_the_refused_kind(real_run):
    run_dir, _ = real_run
    arts = records.read_run(run_dir, strict=True)
    refused = arts.records("refused")
    parsed = [r.refusal() for r in refused]
    ours = [p for p in parsed if p.kind == OFF]
    assert ours, "the fake model's off-ontology kind must be refused"
    orgs = {n for n, k in ENTITIES.items() if k == "organisation"}
    assert all(p.axis == "node" and p.name in orgs and p.chunk is not None for p in ours)
    # the chunk named in the sentence is one the per-chunk screen recorded for that document
    chunks = {(r.item_id, r.chunk) for r in arts.records("governance_chunks") if r.item_id}
    assert all((r.item_id, r.refusal().chunk) in chunks for r in refused
               if r.refusal().kind == OFF)


def test_a_bad_line_is_kept_as_a_problem_and_still_counted(tmp_path):
    run = tmp_path / "r1"
    run.mkdir()
    good = {"item_id": "a" * 64, "uri": "x", "rel": "x", "rule": "file_type:.bin",
            "reverse": "add it", "at": "2026-10-01T00:00:00Z"}
    (run / "excluded.jsonl").write_text(json.dumps(good) + "\n" + '{"rule": "size_cap"}\n',
                                        encoding="utf-8")
    (run / "excluded.drift.jsonl").write_text(json.dumps(
        {**good, "rule": records.DRIFT_PENDING_APPROVAL}) + "\n", encoding="utf-8")
    arts = records.read_run(run)
    assert [p.line for p in arts.problems] == [2] and arts.problems[0].raw == {"rule": "size_cap"}
    # found BY PATTERN, and the invalid line still counted under its own rule (P3)
    assert arts.exclusions_by_rule()["excluded"] == {"file_type:.bin": 1, "size_cap": 1,
                                                     records.DRIFT_PENDING_APPROVAL: 1}
    assert not arts.complete                               # no report.json: not a finished run
    with pytest.raises(records.ArtefactError):
        records.read_run(run, strict=True)


def test_refusal_reason_parser_covers_both_axes():
    node = records.parse_refusal_reason("chunk 3: entity 'Acme': unknown kind 'vessel' — x")
    assert (node.chunk, node.axis, node.kind, node.name) == (3, "node", "vessel", "Acme")
    edge = records.parse_refusal_reason("relation 'charters': unknown type — x")
    assert (edge.chunk, edge.axis, edge.kind) == (None, "edge", "charters")
    other = records.parse_refusal_reason("chunk 0: entity 'Zed': name not found in source")
    assert other.axis is None and other.chunk == 0
