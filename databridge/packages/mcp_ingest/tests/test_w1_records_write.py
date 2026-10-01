"""W1 — writers build artefacts THROUGH `records.py` (AA review item 4), and `run_ingest(
event_attrs=)` lets `run_completed` carry an adapter agent's `shape_changed` (item 5).

* every ledger the runner writes that has a typed format is opened typed, so a record that does
  not match its model is refused AT WRITE TIME (never a silent reader-side drift, R-017);
* the aggregate `outside_roots` exclusion (no item, a count) is a valid typed line;
* `report.json` goes through `RunReportRecord`;
* a real run's artefacts validate strictly, the build stage's per-chunk ledger included;
* `event_attrs` reaches the run_completed event without changing any §5.3 event type, and the
  run's own attributes win over a caller's key of the same name.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import databridge_mcp_ingest as mi
from databridge_mcp_ingest import records, runner
from databridge_mcp_ingest.ledger import EVENT_TYPES, Ledger, typed_ledger

from conftest import fs_connection


def test_every_typed_ledger_the_runner_writes_is_opened_typed(tmp_path):
    typed = [n for n in runner.LEDGERS if n in records.LEDGER_MODELS]
    assert len(typed) >= 10, "the typed format must cover the runner's ledgers"
    for name in typed:
        led = typed_ledger(tmp_path / f"{name}.jsonl")
        try:
            assert led.model is records.LEDGER_MODELS[name], name
        finally:
            led.close()
    # a tagged variant is typed by its base name
    led = typed_ledger(tmp_path / f"{runner.HELD_LEDGER}.jsonl")
    assert led.model is records.ExclusionRecord
    led.close()


@pytest.mark.parametrize("name,bad", [
    ("excluded", {"item_id": "i", "uri": "u", "rel": "r", "reverse": "x", "at": "t"}),  # no rule
    ("excluded", {"item_id": None, "rule": "outside_roots", "reverse": "x", "at": "t"}),  # no count
    ("captured", {"item_id": "i", "uri": "u", "name": "n", "kind": "document", "sha256": "s",
                  "bytes": "many", "mime": None, "step": "s", "at": "t"}),
    ("governance", {"item_id": "i", "uri": "u", "sha256": "s", "rules_source": "r", "at": "t",
                    "decision": "maybe", "fired": [], "rationale": "", "reverse": "",
                    "score": None}),
    ("build_skipped", {"item_id": "i", "uri": "u"}),                                    # no reason
])
def test_a_record_that_breaks_its_format_is_refused_at_write_time(tmp_path, name, bad):
    led = typed_ledger(tmp_path / f"{name}.jsonl", truncate=True)
    try:
        with pytest.raises(records.ArtefactError):
            led.write(bad)
    finally:
        led.close()
    assert (tmp_path / f"{name}.jsonl").read_text(encoding="utf-8") == "", "nothing half-written"


def test_valid_lines_round_trip_exactly(tmp_path):
    good = {"item_id": None, "step": "s1", "rule": "outside_roots", "count": 3, "roots": ["a"],
            "reverse": "declare a root", "at": "2026-10-01T00:00:00Z"}
    led = typed_ledger(tmp_path / "excluded.jsonl", truncate=True)
    led.write(good)
    led.close()
    [line] = (tmp_path / "excluded.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == good                      # no field added, none dropped
    assert records.read_run(tmp_path, strict=True).ledgers["excluded"][0].count == 3
    # an untyped ledger is written as is
    plain = Ledger(tmp_path / "build.jsonl", truncate=True)
    plain.write({"anything": 1})
    plain.close()


def test_a_bridge_window_is_a_valid_chunk_and_its_refusals_parse(tmp_path):
    """Found by writing through the models: the build stage labels a cross-chunk bridge window
    `bridge:<k>` in `governance_chunks` and `bridge <k>/<k+1>: ` in refusals — the typed format
    had assumed an integer chunk, so every bridge line was a reader-side 'problem'."""
    from databridge_mcp_ingest.build import ProseBuilder  # noqa: F401  (the writer in question)

    led = typed_ledger(tmp_path / "governance_chunks.jsonl", truncate=True)
    led.write({"item_id": "i", "chunk": "bridge:0", "char_start": 10, "char_end": 90,
               "bridge": True, "decision": "allow", "fired": [], "rationale": "", "reverse": "",
               "score": 0.0})
    led.close()
    assert records.read_run(tmp_path, strict=True).ledgers["governance_chunks"][0].chunk == \
        "bridge:0"
    r = records.parse_refusal_reason("bridge 2/3: relation 'ships_via': unknown type in pack")
    assert (r.axis, r.kind, r.chunk, r.bridge) == ("edge", "ships_via", 2, True)
    r = records.parse_refusal_reason("chunk 4: entity 'Quillon': unknown kind 'shipping_line'")
    assert (r.axis, r.kind, r.name, r.chunk, r.bridge) == \
        ("node", "shipping_line", "Quillon", 4, False)


@pytest.fixture
def real_run(walnut_env, corpus, common_vocab, fake_harness):
    from databridge_graph.store import GraphStore

    root, _ = corpus
    mi.register_connection(fs_connection("docs", root))
    mi.propose_plan("docs")
    path = walnut_env["WALNUT_GRAPH_DB_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = {"drifts": 2, "drift_kinds": ["added_column", "renamed"], "pending": ["ad_x"]}
    report = mi.run_ingest("docs", store=GraphStore(str(path)), harness=fake_harness,
                           event_attrs={"shape_changed": shape, "status": "caller-cannot-set",
                                        "adapter_agent": "a1"})
    return report, shape, walnut_env


def test_a_real_run_writes_only_valid_typed_lines(real_run):
    report, _, _ = real_run
    rd = Path(report.run_dir)
    arts = records.read_run(rd, strict=True)
    assert arts.problems == [] and arts.report.status == report.status == "ok"
    # non-vacuous: the chunk ledger (written by build.py) and the report were exercised
    assert arts.ledgers["governance_chunks"] and arts.ledgers["captured"]
    raw = json.loads((rd / "report.json").read_text(encoding="utf-8"))
    assert records.RunReportRecord.model_validate(raw).run_id == rd.name
    assert set(raw) >= {"source_id", "run_id", "status", "counts", "verify"}


def test_event_attrs_ride_on_run_completed_without_new_event_types(real_run):
    report, shape, env = real_run
    events = [json.loads(x) for x in
              Path(env["WALNUT_EVENTS_PATH"]).read_text(encoding="utf-8").splitlines()]
    assert {e["type"] for e in events} <= EVENT_TYPES
    [done] = [e for e in events if e["type"] == "run_completed" and e["run_id"] == report.run_id]
    assert done["attrs"]["shape_changed"] == shape and done["attrs"]["adapter_agent"] == "a1"
    assert done["attrs"]["status"] == report.status             # the run's own value wins
    # item events are untouched by the caller's attrs
    assert not [e for e in events if e["type"].startswith("item_") and "shape_changed" in
                e["attrs"]]
