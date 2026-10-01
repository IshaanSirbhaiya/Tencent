"""`/activity` — E2E run 4, Q-11: the delegating agent's Activity tree was thin.

Each `delegate.ask` sub-task of an AGENT run showed ``request: {}`` and no row count. The run
record held both: `agent_runtime` writes the shaped sub-task it SENT under ``args.subtask``
(a federated run records it flat), and its result pointers are aggregate groups, document spans
and query cells — of which the tree counted only ``row_ordinal``.

The record below is written by the producer's own writer (`write_run`) in the shape run 4
recorded (`var/e2e-20261001T1255/runs/agent/larkspur_cross_source_qa/…`). Every expectation is
derived from the record written here — the request from the sub-task, the counts from the
pointers' own identities — never typed in.
"""
from __future__ import annotations

import copy
import json

from fastapi.testclient import TestClient

from conftest import auth_headers
from test_activity_routes import OWNER, OTHER, _agent_run, env, fx, world  # noqa: F401

from databridge_gateway.activity_routes import REQUEST_SCHEMA, delegation_tree

SENTINEL = "zz-secret-hint-7781"
AGENT = "followup"           # the fixture's agent owned by OWNER


def _sub(**kw) -> dict:
    return {"adapter_agent": "crm_sources", **kw}


def _qcell(qh: str, ordinal: int, col: str) -> dict:
    return {"source_id": "crm_db", "kind": "query_cell", "column": col, "row_ordinal": ordinal,
            "query_hash": qh, "trust": "untrusted"}


def _group(digest: str, n: int, part: str) -> dict:
    return {"source_id": "crm_db", "kind": "derived", "rows_digest": digest, "row_count": n,
            "derivation": part, "derived_from": [
                {"source_id": "crm_db", "kind": "table_cell", "table": "invoices",
                 "column": "customer_id", "row_id": f"row-{digest}"}]}


def _span(doc: str, a: int) -> dict:
    return {"source_id": "files", "kind": "doc_span", "document_id": doc, "char_start": a,
            "char_end": a + 9}


def _steps() -> list[dict]:
    """Four delegate calls in run 4's shape: a document read, an aggregate, an aggregate that
    returned nothing, and a tabular read. Each sub-task is nested under ``args.subtask``."""
    docs = [_span(d, a) for d in ("doc-a", "doc-b", "doc-c") for a in (0, 40)]
    docs.append({"source_id": "files", "kind": "derived", "derivation": "mention",
                 "derived_from": [_span("doc-a", 80)]})
    groups = [_group(dg, n, part) for dg, n in (("g1", 2), ("g2", 3))
              for part in ("GROUP KEY customer_id", "SUM(total)")]
    cells = [_qcell("qh1", i, c) for i in range(3) for c in ("customer_id", "name")]
    window = {"axis": "valid_time", "field": "due_on", "from": "2026-10-01", "to": "2026-12-31"}
    return [
        {"tool": "delegate.ask:crm_sources",
         "args": {"subtask": _sub(entity_kinds=["meeting"], fields=["text", "date"], limit=50)},
         "result_pointers": docs},
        {"tool": "delegate.ask:crm_sources",
         "args": {"subtask": _sub(entity_kinds=["invoices"], fields=["customer_id", "total"],
                                  time_window=window,
                                  filters=[{"field": "status", "op": "eq", "value": "open",
                                            "hint": SENTINEL}],
                                  aggregate={"op": "sum", "field": "total",
                                             "group_by": ["customer_id"]},
                                  mystery=SENTINEL)},
         "result_pointers": groups},
        {"tool": "delegate.ask:crm_sources",
         "args": {"subtask": _sub(entity_kinds=["invoices"],
                                  time_window={**window, "from": "2026-07-01",
                                               "to": "2026-09-30"},
                                  aggregate={"op": "sum", "field": "total",
                                             "group_by": ["customer_id"]})},
         "result_pointers": []},
        {"tool": "delegate.ask:crm_sources",
         "args": {"subtask": _sub(entity_kinds=["customers"],
                                  fields=["customer_id", "name"], limit=200)},
         "result_pointers": cells},
    ]


def _expected_rows(ptrs: list[dict]) -> int:
    """The row unit each pointer shape names (the rule, restated from the pointers)."""
    rows = {(p["query_hash"], p["row_ordinal"]) for p in ptrs if p["kind"] == "query_cell"}
    groups = {p["rows_digest"] for p in ptrs if p.get("rows_digest")}
    docs = {q["document_id"] for p in ptrs for q in [p, *p.get("derived_from", [])]
            if q.get("kind") == "doc_span"}
    return (len(rows) + len(groups)) or len(docs)


def _expected_request(step: dict) -> dict:
    sub = step["args"]["subtask"]
    out = {k: copy.deepcopy(sub[k]) for k in REQUEST_SCHEMA if k in sub}
    for f in out.get("filters", []):
        f.pop("hint", None)                          # not in the allow-list: never served
    return out


def test_the_old_reading_was_empty_so_this_test_is_not_vacuous():
    """Run 4 as it was: the request keys sit under ``subtask``, not at the top of ``args``, and
    no pointer carries a ``row_ordinal`` except the tabular read's."""
    steps = _steps()
    for s in steps:
        assert not any(k in s["args"] for k in REQUEST_SCHEMA)
        assert any(k in s["args"]["subtask"] for k in REQUEST_SCHEMA)
    assert sum(1 for s in steps if any(p.get("row_ordinal") is not None
                                       for p in s["result_pointers"])) == 1


def test_each_subtask_shows_the_request_it_sent_and_its_row_count():
    steps = _steps()
    tree = delegation_tree({"run_id": "r", "trigger": {"question": "q", "by": OWNER},
                            "coverage": {"searched": ["crm_db"], "skipped": [], "reasons": []},
                            "steps": steps})
    assert tree is not None and len(tree["subtasks"]) == len(steps)
    for t, s in zip(tree["subtasks"], steps):
        assert t["request"] == _expected_request(s) and t["request"], t
        assert t["rows_returned"] == _expected_rows(s["result_pointers"]), t
        assert t["rows_basis"] and "not recorded" not in t["rows_basis"]
        assert t["adapter_agent"] == s["tool"].partition(":")[2]
    # the empty call is a recorded zero, not "not recorded"
    empty = [t for t, s in zip(tree["subtasks"], steps) if not s["result_pointers"]]
    assert empty and all(t["rows_returned"] == 0 for t in empty)
    # the window and the aggregate the call sent are visible
    agg = tree["subtasks"][1]["request"]
    assert agg["time_window"]["from"] == steps[1]["args"]["subtask"]["time_window"]["from"]
    assert agg["filters"][0]["value"] == "open"
    # the same allow-list redaction: keys outside REQUEST_SCHEMA are not served, and are listed
    assert SENTINEL not in json.dumps(tree)
    assert "subtasks[1].request.filters[].hint" in tree["masked_fields"]
    # a run without a federated plan says so instead of serving a bare null
    assert tree["plan"] is None and tree["plan_note"]


def test_a_flat_federated_subtask_is_still_read_the_same_way():
    flat = {"id": "inv", **_steps()[1]["args"]["subtask"]}
    tree = delegation_tree({"run_id": "r", "steps": [
        {"tool": "federated.plan"},
        {"tool": "delegate.ask:crm_sources", "args": flat, "result_pointers": []}]})
    assert tree["subtasks"][0]["id"] == "inv"
    assert tree["subtasks"][0]["request"]["aggregate"] == flat["aggregate"]


def test_unrecorded_pointers_stay_unrecorded():
    tree = delegation_tree({"run_id": "r", "steps": [
        {"tool": "delegate.ask:crm_sources", "args": {"subtask": _sub(entity_kinds=["x"])}}]})
    assert tree["subtasks"][0]["rows_returned"] is None
    assert tree["subtasks"][0]["rows_basis"].startswith("not recorded")


def test_the_route_serves_the_agent_runs_subtasks_to_its_owner_only(fx):
    from databridge_agent_runtime import RunRecord, run_path
    from databridge_agent_runtime.runs import write_run

    app, _w, _r = fx
    runs = app.state.activity_sources.runs_dir
    path = _agent_run(runs, AGENT, "run-deleg", started=50, ended=51, by=OWNER,
                      trig_type="manual", answer_by={"agent": AGENT, "model": "fake/agent"})
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["steps"] = _steps()
    rec["coverage"] = {"searched": ["crm_db"], "skipped": [], "reasons": []}
    write_run(RunRecord.model_validate(rec), run_path("run-deleg", agent=AGENT, runs_dir=runs))
    with TestClient(app, headers=auth_headers(OWNER)) as c:
        body = c.get("/activity/runs/run-deleg").json()
    tree = body["delegation_tree"]
    for t, s in zip(tree["subtasks"], _steps()):
        assert t["request"] == _expected_request(s) and t["request"]
        assert t["rows_returned"] == _expected_rows(s["result_pointers"])
    assert SENTINEL not in json.dumps(body)
    with TestClient(app, headers=auth_headers(OTHER)) as c:
        assert c.get("/activity/runs/run-deleg").status_code == 404
