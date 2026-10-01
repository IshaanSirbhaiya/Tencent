"""E2E run 4 (2026-10-01), finding Q-07 — reproduced offline, then closed.

The labelled tabular dataset built, but would have misled a model trainer:

* INV-2026-0057 (issued 09-15) and INV-2026-0063 (issued 09-30) were labelled with their status
  "30 days after issue" — 10-15 and 10-30 — by a build on 10-01. Both label instants were in the
  future.
* INV-2026-0041 (issued 08-31), the only invoice whose 30-day window HAD closed, was counted
  ``rejected_by_maturity``.
* ``net_gbp``, ``vat_gbp``, ``billed_to_id`` and ``part_of_id`` were all-null on every row with
  the missing reason ``not_in_source``, although the graph holds every one of them.

**Root cause, from the evidence** (``var/e2e-20261001T1255``: the graph's ``nodes`` rows 29-31,
``materialise_downloads/6dcf5529…/plan.yaml`` and its receipt): `mcp_ingest` stamps every row of a
database table with ``valid_from`` = the capture instant (12:56:46) and ``system_from`` = the
ingest instant (12:56:48). The label is the invoice's OWN ``status`` joined on ``_id``. The tabular
builder read it as an *event*: "the earliest outcome whose valid time falls in (T_d, T_d+30D]".
The one version the graph holds, valid from 10-01, fell inside the still-open windows of 0057 and
0063 — so "today's status" became the status at day 30 — and outside 0041's closed window, which
found "no outcome" and was miscounted as immature. The maturity gate tested only when the version
was RECORDED, never whether the label's own instant had arrived. The features were cut correctly
(nothing was recorded by T_d) but reported as absent from the source.

**The fix** (`shapes/tabular.py`, `temporal.py`): a label that is an attribute of the cohort row
itself — declared structurally by the plan (label entity = cohort entity, joined on the cohort
key), never by what the entity is — is a STATE, read as the version in force at its business
instant ``T_d + within``. That instant must have arrived by ``T_b - lag`` or the row is immature;
a closed window with no version in force at it is ``rejected_unlabelled``, counted apart. A
feature that the T_d cut empties on every emitted row is refused by name in
``receipt.columns_refused`` and excluded; partial cuts are counted per column in
``receipt.features_unknown_at_decision``.

The workspace is built by the REAL ingest code (`build.table_rows` over a capture shaped like
`crawl.fetch_table`'s), from the run's own rows and timestamps. The plan is the run's plan.yaml,
byte for byte (``fixtures/e2e_run4/labelled_plan.yaml``), with only its source pins re-taken for
this workspace. Every expectation below is derived from the rule and the data, never written down.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from databridge_core import SourcePointer
from databridge_graph.packs import deployment

from databridge_materialise.durations import parse_duration
from databridge_materialise.platform import PlatformMaterialiser
from databridge_materialise.testing import pinned

DB = "larkspur_db"
FIXTURE = Path(__file__).parent / "fixtures" / "e2e_run4" / "labelled_plan.yaml"

#: The run's own timestamps (graph `nodes` rows 29-31): the capture and the ingest instants.
CAPTURED = datetime(2026, 10, 1, 12, 56, 46, tzinfo=timezone.utc)
INGESTED = datetime(2026, 10, 1, 12, 56, 48, 53728, tzinfo=timezone.utc)
#: The run's build instant (plan.yaml `temporal.build_time`).
RUN4_BUILT = datetime(2026, 10, 1, 13, 2, 55, tzinfo=timezone.utc)

#: The run's `public.invoices` rows, as the graph holds them (attrs_json + the two edges).
#: Synthetic, fictional data from the e2e estate.
RUN4_INVOICES = [
    {"invoice_no": "INV-2026-0041", "customer_id": "C-1001", "project_id": "PRJ-101",
     "issued_on": "2026-08-31", "due_on": "2026-09-30", "net_gbp": "18500.00",
     "vat_gbp": "3700.00", "total_gbp": "22200.00", "status": "paid"},
    {"invoice_no": "INV-2026-0057", "customer_id": "C-1002", "project_id": "PRJ-102",
     "issued_on": "2026-09-15", "due_on": "2026-10-15", "net_gbp": "12750.00",
     "vat_gbp": "2550.00", "total_gbp": "15300.00", "status": "open"},
    {"invoice_no": "INV-2026-0063", "customer_id": "C-1001", "project_id": "PRJ-101",
     "issued_on": "2026-09-30", "due_on": "2026-10-30", "net_gbp": "9250.00",
     "vat_gbp": "1850.00", "total_gbp": "11100.00", "status": "open"},
]
RUN4_CUSTOMERS = [{"customer_id": "C-1001", "name": "Pellucid Freightways Ltd"},
                  {"customer_id": "C-1002", "name": "Corriedale Grocers Co-operative"}]
RUN4_PROJECTS = [{"project_id": "PRJ-101", "name": "Route Optimisation Pilot"},
                 {"project_id": "PRJ-102", "name": "Demand Forecasting Rollout"}]


@pytest.fixture(scope="module")
def vocab():
    with deployment("workspace") as v:
        yield v


def _capture(cap: Path, table: str, rows: list[dict], pk: str) -> tuple[str, dict]:
    """Capture a table snapshot exactly as `crawl.fetch_table` + the runner do."""
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.crawl import table_text

    snap = {"schema": "public", "table": table,
            "columns": [{"name": c, "type": "character varying"} for c in rows[0]],
            "primary_key": [pk], "foreign_keys": [], "row_count_total": len(rows),
            "limit": 1000, "rows": rows}
    data = json.dumps(snap, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    parsed = json.loads(data)
    sha = capture.put_bytes(data, cap)
    (cap / sha[:2] / f"{sha}.txt").write_text(table_text(parsed), encoding="utf-8", newline="")
    capture.put_meta(sha, {"mime": "application/json", "source": DB, "title": table,
                           "uri": f"public.{table}"}, cap)
    return sha, parsed


def _rows(cap: Path, table: str, rows: list[dict], pk: str):
    from databridge_mcp_ingest.build import table_rows

    sha, snap = _capture(cap, table, rows, pk)
    return list(table_rows(snap, source_id=DB, sha=sha, mask_cfg=None, redactions=[],
                           blocked_rows=[]))


@pytest.fixture()
def run4(tmp_path, vocab):
    """Run 4's graph: invoices, customers and projects ingested from a database, every version
    stamped at the capture/ingest instants, every attribute citing its captured `table_cell`."""
    from databridge_graph import GraphStore
    from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

    root = tmp_path / "ledger"
    cap, facts, pas = root / "capture", root / "facts", root / "passages"
    for d in (cap, facts, pas):
        d.mkdir(parents=True)
    store = GraphStore(str(root / "workspace.db"))
    stamp = {"valid_from": CAPTURED, "system_from": INGESTED}

    def nid(kind: str, key: str) -> str:
        return f"{kind}:{key.lower()}"

    for table, rows, pk, kind in (("customers", RUN4_CUSTOMERS, "customer_id", "organisation"),
                                  ("projects", RUN4_PROJECTS, "project_id", "project")):
        for row in _rows(cap, table, rows, pk):
            store.add_node(GraphNode(
                id=nid(kind, row[pk]), kind=NodeKind(kind), label=row["name"],
                attrs={"name": row["name"]},
                attr_sources={"label": row.pointer_for("name"), "name": row.pointer_for("name")},
                sources=(row.pointer_for(pk),), **stamp))
    attrs_cols = ("issued_on", "due_on", "net_gbp", "vat_gbp", "total_gbp", "status")
    for row in _rows(cap, "invoices", RUN4_INVOICES, "invoice_no"):
        iid = nid("invoice", row["invoice_no"])
        store.add_node(GraphNode(
            id=iid, kind=NodeKind("invoice"), label=row["invoice_no"],
            attrs={c: row[c] for c in attrs_cols},
            attr_sources={"label": row.pointer_for("invoice_no"),
                          **{c: row.pointer_for(c) for c in attrs_cols}},
            sources=(row.pointer_for("invoice_no"),), **stamp))
        for ek, col, kind in (("billed_to", "customer_id", "organisation"),
                              ("part_of", "project_id", "project")):
            store.add_edge(GraphEdge(
                id=f"{iid}|{ek}|{nid(kind, row[col])}", src_id=iid,
                dst_id=nid(kind, row[col]), kind=EdgeKind(ek),
                sources=(row.pointer_for(col),), **stamp))
    m = PlatformMaterialiser(store_provider=lambda: store, vocabulary=vocab, facts_root=facts,
                             passages_root=pas, capture_root=cap, out_root=tmp_path / "out",
                             register_emissions=False, clock=lambda: RUN4_BUILT)
    yield m
    store.close()


def _run4_plan(m: PlatformMaterialiser, *, build_time: datetime | None = None) -> dict:
    """The run's plan.yaml, byte for byte, re-pinned to this workspace (and optionally re-built
    at a later instant). Nothing else is changed."""
    from databridge_materialise import MaterialisationPlan

    body = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    body.pop("id", None)
    body.pop("hashes", None)
    plan = MaterialisationPlan.model_validate(body)
    if build_time is not None:
        plan = plan.model_copy(update={"temporal": plan.temporal.model_copy(
            update={"build_time": build_time})})
    return pinned(plan, m).model_dump(mode="json")


def _expected(plan: dict) -> dict:
    """Each invoice's fate, DERIVED from the rule and the run's data — the answer is computed."""
    t = plan["temporal"]
    t_b = datetime.fromisoformat(t["build_time"].replace("Z", "+00:00"))
    cutoff = t_b - parse_duration(t["label_maturity_lag"])
    out = {"emitted": [], "immature": [], "unlabelled": []}
    for inv in RUN4_INVOICES:
        t_d = datetime.combine(date.fromisoformat(inv["issued_on"]), datetime.min.time(),
                               tzinfo=timezone.utc)
        horizon = t_d + parse_duration(t["label_window"]["within"])
        if horizon > cutoff:
            out["immature"].append(inv["invoice_no"])            # the label's instant is future
        elif CAPTURED > horizon:
            out["unlabelled"].append(inv["invoice_no"])          # no version in force at it
        else:
            out["emitted"].append(inv["invoice_no"])
    return out


def _published(m: PlatformMaterialiser, result: dict) -> tuple[list[dict], list[dict], dict]:
    roles = m.file_roles(result["plan_hash"])
    data = next(f for f, r in roles.items() if r == "dataset")
    side = next(f for f, r in roles.items() if r == "pointers")
    rows = [json.loads(x) for x in m.file_path(result["plan_hash"], data)
            .read_text("utf-8").splitlines() if x]
    lines = [json.loads(x) for x in m.file_path(result["plan_hash"], side)
             .read_text("utf-8").splitlines() if x]
    return rows, lines, m.receipt(result["plan_hash"])


def test_the_run4_plan_on_the_run4_graph_labels_nothing_whose_instant_has_not_arrived(run4):
    """The run's exact input. Before the fix: 2 emitted (0057, 0063, both labelled `open` for a
    day still in the future), 1 `rejected_by_maturity` (0041, whose window had closed)."""
    plan = _run4_plan(run4)
    want = _expected(plan)
    # non-vacuous: the run's data exercises both rejection reasons
    assert want["immature"] and want["unlabelled"], want
    result = run4.run({"plan": plan}, by=plan["policy"]["principal"]["actor"])
    rows = result["rows"]
    assert rows["emitted"] == len(want["emitted"])
    assert rows["rejected_by_maturity"] == len(want["immature"])
    assert rows["rejected_unlabelled"] == len(want["unlabelled"])
    assert sum(v for k, v in rows.items() if k != "emitted") + rows["emitted"] == len(RUN4_INVOICES)
    data, _, doc = _published(run4, result)
    t_b = datetime.fromisoformat(plan["temporal"]["build_time"].replace("Z", "+00:00"))
    within = parse_duration(plan["temporal"]["label_window"]["within"])
    for row in data:
        horizon = datetime.fromisoformat(row["invoice.issued_on"]).replace(
            tzinfo=timezone.utc) + within
        assert horizon <= t_b, f"{row['invoice._id']} carries a label for {horizon} > T_b {t_b}"


def test_once_the_windows_close_the_labels_are_the_state_at_the_horizon(run4):
    """The same plan re-built after every 30-day window has closed. A row whose horizon postdates
    the capture is labelled with the version in force then; the row whose window closed BEFORE
    anything was recorded is unlabelled, never "immature", never a negative."""
    plan = _run4_plan(run4, build_time=datetime(2026, 12, 1, tzinfo=timezone.utc))
    want = _expected(plan)
    assert want["emitted"] and want["unlabelled"] and not want["immature"], want
    result = run4.run({"plan": plan}, by=plan["policy"]["principal"]["actor"])
    assert result["rows"]["emitted"] == len(want["emitted"])
    assert result["rows"]["rejected_unlabelled"] == len(want["unlabelled"])
    assert result["rows"]["rejected_by_maturity"] == 0
    data, lines, doc = _published(run4, result)
    by_no = {i["invoice_no"]: i for i in RUN4_INVOICES}
    got = sorted(r["invoice._id"].rsplit(":", 1)[1].upper() for r in data)
    assert got == sorted(want["emitted"])
    for row in data:
        assert row["invoice.status"] == by_no[row["invoice._id"].rsplit(":", 1)[1].upper()]["status"]
    check = result["pointer_check"]
    assert check["all_resolved"] and check["all_match"], check["failures"]


def test_features_not_known_at_the_decision_are_refused_by_name_not_shipped_empty(run4):
    """Every feature version was first recorded at the ingest instant, after every T_d. The four
    feature columns are excluded and named in the receipt with their count — never an all-null
    column whose companion says `not_in_source`."""
    plan = _run4_plan(run4, build_time=datetime(2026, 12, 1, tzinfo=timezone.utc))
    result = run4.run({"plan": plan}, by=plan["policy"]["principal"]["actor"])
    data, lines, doc = _published(run4, result)
    receipt = doc["receipt"]
    features = [c["path"] for c in plan["shape"]["columns"]
                if c["source"]["relation"] not in ("cohort_key", "decision_time")]
    # derived: a feature is unknown at T_d iff its version was recorded after T_d — all of them
    unknown = [p for p in features
               if all(INGESTED > datetime.combine(date.fromisoformat(i["issued_on"]),
                                                  datetime.min.time(), tzinfo=timezone.utc)
                      for i in RUN4_INVOICES)]
    assert unknown and data, "non-vacuous: rows were emitted and features were cut"
    refused = {r.split(":", 1)[0] for r in receipt["columns_refused"]}
    assert refused == set(unknown)
    for path in unknown:
        assert receipt["features_unknown_at_decision"][path] == len(data)
        assert all(path not in row and f"{path}_missing_reason" not in row for row in data)
        assert all(ln["column"] != path for ln in lines)
    assert doc["platform"]["unresolved"]["columns_refused"] == receipt["columns_refused"]
    # nothing the trainer receives claims a value the source lacks
    assert not any(v == "not_in_source" for row in data for k, v in row.items()
                   if k.endswith("_missing_reason"))


def test_an_event_label_is_unchanged_an_open_window_with_no_outcome_is_immature():
    """The event pathway keeps its contract, and gains the same arrival rule: an outcome dated
    after `T_b - lag` is pending, an open window with no outcome is immature, a closed one with
    none is unlabelled."""
    from databridge_core import GroundedValue

    from databridge_materialise.shapes.tabular import _read_label

    plan_body = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    plan_body.pop("id", None)
    plan_body.pop("hashes", None)
    from databridge_materialise import MaterialisationPlan

    plan = MaterialisationPlan.model_validate(plan_body)
    t_b = plan.temporal.build_time
    within = parse_duration(plan.temporal.label_window.within)
    p = SourcePointer(source_id="s", kind="table_cell", table="t", column="c", row_id="1")

    def row(valid: datetime, value="x"):
        return {"_valid_from": GroundedValue(value=valid.isoformat(), pointer=p, confidence=1.0),
                "_system_from": GroundedValue(value=valid.isoformat(), pointer=p,
                                              confidence=1.0),
                "status": GroundedValue(value=value, pointer=p, confidence=1.0)}

    open_d = t_b - within / 2                       # window still open at T_b
    closed_d = t_b - within * 2                     # window closed before T_b
    assert _read_label(plan, [], open_d, t_b, state=False)[0] == "immature"
    assert _read_label(plan, [], closed_d, t_b, state=False)[0] == "unlabelled"
    inside = closed_d + within / 2
    assert _read_label(plan, [row(inside, "y")], closed_d, t_b, state=False) [0] == "ok"
    future = t_b + timedelta(days=1)
    assert _read_label(plan, [row(future)], t_b - within + timedelta(days=2), t_b,
                       state=False)[0] == "immature"


#: The run's request, verbatim (artefacts/p4_mat_core.json), and the answer it was given.
RUN4_REQUEST = ("One row per invoice at its issue date, with the customer, project, net and VAT "
                "amounts as features; the label is the invoice status 30 days after it was "
                "issued.")
RUN4_ANSWER = "P0D: the label is read exactly 30 days after the issue date."


def _run4_model():
    """A FakeBackend returning the draft the run's model authored (the plan.yaml's columns), with
    the lag the answer gives: the AGENT pathway the live run took (`shaped_by: agent`)."""
    from databridge_harness import FakeBackend, Harness

    body = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    features = [{"path": c["path"], "entity": c["source"]["entity"],
                 "field_name": c["source"]["field"], "relation": c["source"]["relation"],
                 "type": c["type"], "system_time_field": c["source"]["system_time_field"]}
                for c in body["shape"]["columns"]
                if c["source"]["relation"] not in ("cohort_key", "decision_time")]
    label = body["shape"]["label"]
    draft = {"shape": "tabular", "entity": body["selection"]["entity"],
             "decision_time_field": body["selection"]["cohort"]["decision_time_field"],
             "feature_axis": "system_time", "feature_as_of": "per_row", "features": features,
             "label": {"path": label["path"], "entity": label["source"]["entity"],
                       "field_name": label["source"]["field"],
                       "join_key": label["source"]["join_key"],
                       "valid_time_field": label["source"]["valid_time_field"],
                       "system_time_field": label["source"]["system_time_field"],
                       "type": label["type"]},
             "label_axis": "valid_time",
             "label_window": body["temporal"]["label_window"]["within"],
             "label_read_at_system_time": "build_time",
             "label_maturity_lag": body["temporal"]["label_maturity_lag"],
             "row_order": [body["shape"]["columns"][0]["path"]], "null_policy": "keep"}
    return Harness(FakeBackend(lambda **_: json.dumps(draft)), profile="cloud", cache=None)


@pytest.mark.parametrize("build", [None, datetime(2026, 12, 1, tzinfo=timezone.utc)])
def test_the_agent_pathway_obeys_the_same_rules(run4, build):
    """The live run went through the data agent. The same request, the same draft, at the run's
    own instant (no label may be in the future) and after the windows close (features refused)."""
    from databridge_materialise.questions import question_id

    run4._harness = _run4_model()
    if build is not None:
        run4._clock = lambda: build
    result = run4.run({"request": RUN4_REQUEST, "answers": {question_id("lag?"): RUN4_ANSWER}},
                      by="e2e4-analyst")
    doc = run4.receipt(result["plan_hash"])
    plan = yaml.safe_load(run4.file_path(result["plan_hash"], "plan.yaml").read_text("utf-8"))
    want = _expected(plan)
    rows = result["rows"]
    assert (rows["emitted"], rows["rejected_by_maturity"], rows["rejected_unlabelled"]) == (
        len(want["emitted"]), len(want["immature"]), len(want["unlabelled"]))
    data, _, _ = _published(run4, result)
    if data:
        refused = {r.split(":", 1)[0] for r in doc["receipt"]["columns_refused"]}
        assert refused and all(p not in row for p in refused for row in data)
