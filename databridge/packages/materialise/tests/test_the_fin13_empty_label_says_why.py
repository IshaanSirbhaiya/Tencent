"""Finance dry run (2026-10-02), finding FIN-13 — reproduced offline on TWO estates, then closed.

"Build a training table with one row per facility … labelled with the facility status" built a
dataset of **0 rows**: 306 unlabelled, 14 immature, 3 incomplete (receipt ``b7f8c2dc…``).

**Honest or a defect?** Honest, and the receipt failed to say why. The label is the cohort row's
own ``status`` (a STATE label, MAT5's run-4 rule). A database row has one version, stamped at the
capture instant (2026-10-01T17:08Z in the run), so the store cannot say what a facility's status
was at ``start_date + P365D`` for any facility whose horizon fell before that instant (306), and
the 14 facilities that started after 2025-10-01 have horizons still in the future. No join, label
or decision instant was wrong: the planner did what it was asked, against a store with no history.

**What was missing** was the WHY and a way forward. The receipt said only "label window closed
and no outcome was recorded inside it". The fix (`shapes/tabular.py::_diagnose_label`) adds a
``label_diagnosis`` to the receipt, the platform block and the job result, and a note: it names
the instants the label's versions came into force, the horizons of the unlabelled rows, the
re-run instant from which the immature rows mature, whether the label has any history at all,
and the whole-day window that would label the most rows against THIS store — all computed from
the store and the plan, never recalled.

Both estates are built by the REAL ingest code (``build.table_rows`` over a captured table
snapshot) at the dry run's own instants. Expectations are derived from the rows, never written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
import yaml

from databridge_graph.packs import deployment

from databridge_materialise import MaterialisationPlan
from databridge_materialise.durations import parse_duration
from databridge_materialise.platform import PlatformMaterialiser
from databridge_materialise.testing import pinned

TEMPLATE = Path(__file__).parent / "fixtures" / "e2e_run4" / "labelled_plan.yaml"


@dataclass(frozen=True)
class Estate:
    name: str
    pack: str
    source: str
    table: str
    kind: str
    pk: str
    decision: str
    label: str
    within: str
    captured: datetime
    ingested: datetime
    built: datetime
    rows: Callable[[Path], list[dict]]


def _finance_facilities(tmp: Path) -> list[dict]:
    """`core_banking.facility`, loaded from the finance fixture seed by the production loader."""
    from databridge_adapter.testing.estate import load_finance_estate
    from sqlalchemy import text

    engine = load_finance_estate(tmp / "estate", tables=("01_party_01.sql", "05_facility_01.sql"))
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT facility_id, party_id, facility_type, limit_amount, currency, start_date, "
            "status FROM core_banking.facility ORDER BY facility_id"))
        rows = [{k: (None if v is None else str(v)) for k, v in r._mapping.items()}
                for r in result]
    engine.dispose()
    return rows


def _workspace_invoices(_tmp: Path) -> list[dict]:
    """E2E run 4's `public.invoices` rows (the workspace/Larkspur estate), as the graph held them."""
    from test_the_e2e_run4_labels_are_mature_and_features_honest import RUN4_INVOICES

    return [dict(r) for r in RUN4_INVOICES]


ESTATES = [
    Estate(name="finance", pack="finance", source="finance_core_banking", table="facility",
           kind="facility", pk="facility_id", decision="start_date", label="status",
           within="P365D",
           # the dry run's own instants: graph `nodes` valid_from/system_from, plan build_time
           captured=datetime(2026, 10, 1, 17, 8, 19, tzinfo=timezone.utc),
           ingested=datetime(2026, 10, 1, 17, 8, 21, 714615, tzinfo=timezone.utc),
           built=datetime(2026, 10, 1, 17, 31, 47, tzinfo=timezone.utc),
           rows=_finance_facilities),
    Estate(name="workspace", pack="workspace", source="larkspur_db", table="invoices",
           kind="invoice", pk="invoice_no", decision="issued_on", label="status", within="P30D",
           captured=datetime(2026, 10, 1, 12, 56, 46, tzinfo=timezone.utc),
           ingested=datetime(2026, 10, 1, 12, 56, 48, 53728, tzinfo=timezone.utc),
           built=datetime(2026, 10, 1, 13, 2, 55, tzinfo=timezone.utc),
           rows=_workspace_invoices),
]


def _capture_rows(cap: Path, est: Estate, rows: list[dict]):
    from databridge_mcp_ingest import capture
    from databridge_mcp_ingest.build import table_rows
    from databridge_mcp_ingest.crawl import table_text

    snap = {"schema": "public", "table": est.table,
            "columns": [{"name": c, "type": "character varying"} for c in rows[0]],
            "primary_key": [est.pk], "foreign_keys": [], "row_count_total": len(rows),
            "limit": len(rows), "rows": rows}
    data = json.dumps(snap, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    parsed = json.loads(data)
    sha = capture.put_bytes(data, cap)
    (cap / sha[:2] / f"{sha}.txt").write_text(table_text(parsed), encoding="utf-8", newline="")
    capture.put_meta(sha, {"mime": "application/json", "source": est.source, "title": est.table,
                           "uri": f"public.{est.table}"}, cap)
    return list(table_rows(parsed, source_id=est.source, sha=sha, mask_cfg=None, redactions=[],
                           blocked_rows=[]))


@pytest.fixture(params=ESTATES, ids=lambda e: e.name)
def estate(request, tmp_path):
    """The estate's table ingested as a graph: one version per row, stamped at the run's
    capture/ingest instants, every attribute citing its captured `table_cell`."""
    from databridge_graph import GraphStore
    from databridge_graph.schema import GraphNode, NodeKind

    est: Estate = request.param
    source_rows = est.rows(tmp_path)
    root = tmp_path / "ledger"
    cap, facts, pas = root / "capture", root / "facts", root / "passages"
    for d in (cap, facts, pas):
        d.mkdir(parents=True)
    with deployment(est.pack) as vocab:
        store = GraphStore(str(root / "graph.db"))
        attrs = [c for c in source_rows[0] if c != est.pk]
        for row in _capture_rows(cap, est, source_rows):
            store.add_node(GraphNode(
                id=f"{est.kind}:{row[est.pk].lower()}", kind=NodeKind(est.kind),
                label=row[est.pk], attrs={c: row[c] for c in attrs if row[c] is not None},
                attr_sources={"label": row.pointer_for(est.pk),
                              **{c: row.pointer_for(c) for c in attrs if row[c] is not None}},
                sources=(row.pointer_for(est.pk),), valid_from=est.captured,
                system_from=est.ingested))
        m = PlatformMaterialiser(store_provider=lambda: store, vocabulary=vocab,
                                 facts_root=facts, passages_root=pas, capture_root=cap,
                                 out_root=tmp_path / "out", register_emissions=False,
                                 clock=lambda: est.built)
        yield est, m, source_rows
        store.close()


def _plan(est: Estate, m: PlatformMaterialiser, *, within: str | None = None,
          built: datetime | None = None) -> dict:
    """The dry run's plan SHAPE (cohort key, decision instant, the row's own status as a state
    label), generated for the estate from the run-4 template — only names and instants change."""
    body = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    body.pop("id", None)
    body.pop("hashes", None)
    key_col, decision_col = (dict(c) for c in body["shape"]["columns"][:2])
    key_col.update(path=f"{est.kind}._id", source={**key_col["source"], "entity": est.kind})
    decision_col.update(path=f"{est.kind}.{est.decision}",
                        source={**decision_col["source"], "entity": est.kind,
                                "field": est.decision})
    body["selection"]["entity"] = est.kind
    body["selection"]["cohort"]["decision_time_field"] = est.decision
    body["shape"]["columns"] = [key_col, decision_col]
    body["shape"]["label"]["path"] = f"{est.kind}.{est.label}"
    body["shape"]["label"]["source"].update(entity=est.kind, field=est.label, join_key="_id")
    body["temporal"]["label_window"]["within"] = within or est.within
    body["temporal"]["build_time"] = (built or est.built).isoformat().replace("+00:00", "Z")
    body["determinism"]["row_order"] = [f"{est.kind}._id"]
    body["determinism"]["split"] = {"strategy": "none", "train_before": None, "val": None,
                                    "test": None}
    body["packs"] = [est.pack]
    plan = MaterialisationPlan.model_validate(body)
    return pinned(plan, m).model_dump(mode="json")


def _fates(est: Estate, rows: list[dict], within: str, built: datetime) -> dict[str, list[str]]:
    """Each row's fate, DERIVED from the state-label rule and the rows: the one version the store
    holds is in force from the capture instant."""
    cutoff = built                                            # lag P0D, as in the run
    out: dict[str, list[str]] = {"emitted": [], "immature": [], "unlabelled": [], "incomplete": []}
    for r in rows:
        if not r.get(est.decision):
            out["incomplete"].append(r[est.pk])
            continue
        t_d = datetime.combine(date.fromisoformat(r[est.decision][:10]), datetime.min.time(),
                               tzinfo=timezone.utc)
        horizon = t_d + parse_duration(within)
        if horizon > cutoff:
            out["immature"].append(r[est.pk])
        elif horizon < est.captured:
            out["unlabelled"].append(r[est.pk])
        else:
            out["emitted"].append(r[est.pk])
    return out


def test_the_empty_labelled_dataset_says_exactly_why(estate):
    """The dry run's input. The counts are the rule's; the receipt now names the instants."""
    est, m, rows = estate
    plan = _plan(est, m)
    want = _fates(est, rows, est.within, est.built)
    assert want["unlabelled"] and want["immature"], want          # non-vacuous: both reasons
    result = m.run({"plan": plan}, by=plan["policy"]["principal"]["actor"])
    got = result["rows"]
    assert (got["emitted"], got["rejected_by_maturity"], got["rejected_unlabelled"]) == (
        len(want["emitted"]), len(want["immature"]), len(want["unlabelled"]))

    d = result["label_diagnosis"]
    doc = m.receipt(result["plan_hash"])
    assert d and doc["receipt"]["label_diagnosis"] == d == doc["platform"]["label_diagnosis"]
    assert d["reading"] == "state" and d["label"] == f"{est.kind}.{est.label}"
    assert (d["unlabelled"], d["immature"]) == (len(want["unlabelled"]), len(want["immature"]))
    # the versions came into force at the capture instant, and the receipt says so
    assert datetime.fromisoformat(d["label_versions_in_force_from"]) == est.captured
    assert d["max_versions_per_row"] == 1
    # the unlabelled rows' horizons, derived
    by_pk = {r[est.pk]: r for r in rows}
    horizons = sorted(datetime.combine(date.fromisoformat(by_pk[k][est.decision][:10]),
                                       datetime.min.time(), tzinfo=timezone.utc)
                      + parse_duration(est.within) for k in want["unlabelled"])
    assert [datetime.fromisoformat(h) for h in d["unlabelled_horizons"]] == [horizons[0],
                                                                             horizons[-1]]
    # the instant from which a re-run labels the first immature row, derived
    due = min(datetime.combine(date.fromisoformat(by_pk[k][est.decision][:10]),
                               datetime.min.time(), tzinfo=timezone.utc)
              + parse_duration(est.within) for k in want["immature"])
    assert datetime.fromisoformat(d["immature_mature_from"]) == due
    note = next(n for n in result["notes"] if n.startswith("label diagnosis"))
    assert est.captured.isoformat() in note and "no history" in note
    if got["emitted"] == 0:
        assert "NO row emitted" in result["honest_summary"]


@pytest.mark.parametrize("later_days", [0, 10], ids=["at_the_run", "ten_days_on"])
def test_the_suggested_window_is_the_one_that_labels_the_most_rows(estate, later_days):
    """The suggestion is checked by DOING it: a brute-force sweep over whole-day windows against
    the rule finds no window labelling more rows than the one suggested, and re-running with the
    suggested window labels exactly the number promised. At the run's own instant no window
    labels anything (the store's only version is 23 minutes old); ten days on, one does."""
    est, m, rows = estate
    built = est.built + timedelta(days=later_days)
    at = m if not later_days else PlatformMaterialiser(
        store_provider=m._store_provider, vocabulary=m._vocabulary, facts_root=m._facts_root,
        passages_root=m._passages_root, capture_root=m._capture_root,
        out_root=m.out_root.parent / f"out-{later_days}", register_emissions=False,
        clock=lambda: built)
    plan = _plan(est, at, built=built)
    actor = plan["policy"]["principal"]["actor"]
    result = at.run({"plan": plan}, by=actor)
    best = result["label_diagnosis"]["best_window"]
    max_days = (built - min(
        datetime.combine(date.fromisoformat(r[est.decision][:10]), datetime.min.time(),
                         tzinfo=timezone.utc) for r in rows if r.get(est.decision))).days
    sweep = {d: len(_fates(est, rows, f"P{d}D", built)["emitted"])
             for d in range(1, max_days + 1)}
    top = max(sweep.values())
    assert (top > 0) == bool(later_days), "non-vacuous: both branches are exercised"
    if top == 0:
        assert best is None
        assert "no whole-day label window labels any" in result["label_diagnosis"]["explanation"]
        return
    assert best["rows"] == top and sweep[int(best["within"][1:-1])] == top
    again = at.run({"plan": _plan(est, at, within=best["within"], built=built)}, by=actor)
    assert again["rows"]["emitted"] == best["rows"]


def test_after_the_promised_instant_the_first_immature_row_is_labelled(estate):
    """`immature_mature_from` is a promise; keep it. Re-built at that instant, the row whose
    horizon arrives first is emitted with the status the source holds."""
    est, m, rows = estate
    plan = _plan(est, m)
    result = m.run({"plan": plan}, by=plan["policy"]["principal"]["actor"])
    due = datetime.fromisoformat(result["label_diagnosis"]["immature_mature_from"])
    later = PlatformMaterialiser(store_provider=m._store_provider, vocabulary=m._vocabulary,
                                 facts_root=m._facts_root, passages_root=m._passages_root,
                                 capture_root=m._capture_root,
                                 out_root=m.out_root.parent / "out2", register_emissions=False,
                                 clock=lambda: due)
    plan2 = _plan(est, later, built=due)
    res2 = later.run({"plan": plan2}, by=plan2["policy"]["principal"]["actor"])
    want = _fates(est, rows, est.within, due)
    assert want["emitted"] and res2["rows"]["emitted"] == len(want["emitted"])


def test_a_fully_labelled_build_carries_no_diagnosis(estate):
    """Non-vacuity in the other direction: a window long enough that every horizon postdates the
    capture, built once every horizon has arrived, rejects nothing for a label reason, and the
    receipt does not invent a diagnosis."""
    est, m, rows = estate
    starts = [datetime.combine(date.fromisoformat(r[est.decision][:10]), datetime.min.time(),
                               tzinfo=timezone.utc) for r in rows if r.get(est.decision)]
    days = (est.captured - min(starts)).days + 1
    built = max(starts) + timedelta(days=days)
    want = _fates(est, rows, f"P{days}D", built)
    assert want["emitted"] and not want["unlabelled"] and not want["immature"], want
    later = PlatformMaterialiser(store_provider=m._store_provider, vocabulary=m._vocabulary,
                                 facts_root=m._facts_root, passages_root=m._passages_root,
                                 capture_root=m._capture_root,
                                 out_root=m.out_root.parent / "out3", register_emissions=False,
                                 clock=lambda: built)
    plan = _plan(est, later, within=f"P{days}D", built=built)
    result = later.run({"plan": plan}, by=plan["policy"]["principal"]["actor"])
    assert result["rows"]["emitted"] == len(want["emitted"])
    assert result["label_diagnosis"] is None
    assert not any(n.startswith("label diagnosis") for n in result["notes"])
