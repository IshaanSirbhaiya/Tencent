"""A synthetic, Larkspur-shaped platform workspace for the materialiser's tests.

Everything here is fictional and written into a directory the caller owns (a pytest ``tmp_path``):

* a **capture store** (§5.2) — one text per invoice / person / document, so every pointer below is
  a ``doc_span`` that re-slices out of real captured bytes;
* a **shared graph** (§5.1) over the ``workspace`` pack's kinds — organisations, invoices (with a
  functional ``billed_to`` edge), people (a SUBJECT kind: email and employee id are identifiers,
  with a functional ``works_for`` edge), documents — each attribute carrying its own pointer, with
  staggered ``valid_from`` / ``system_from`` so the temporal contract has something to cut;
* a **facts store** (S2 layout: ``<source>_facts.sqlite`` + manifest) with a ``payments`` table,
  including an ``email`` column that names a declared identifier;
* a **passage store** (§5.5), built by the real `databridge_retrieval.BuildSession` over two
  captured documents.

It is a builder, not a recording: every count a test asserts is derived from what this wrote.
The caller binds the ``workspace`` pack first (``databridge_graph.packs.deployment``).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from databridge_core import SourcePointer

FILES_SOURCE = "larkspur-files"
DB_SOURCE = "larkspur-db"

#: Everything is relative to this instant, so a test that passes today passes next year.
EPOCH = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
BUILD_TIME = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
#: When the workspace's passages were ingested, on the workspace's own clock (before BUILD_TIME),
#: in the indexer's own `%Y-%m-%dT%H:%M:%SZ` form.
PASSAGES_INGESTED_AT = (BUILD_TIME - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

ORGS = ("Acme Holdings", "Birchwood Ltd", "Cobalt Partners")


@dataclass
class Workspace:
    root: Path
    capture: Path
    facts: Path
    passages: Path
    graph_path: Path
    store: object
    invoices: list[dict] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)


def write_capture(root: Path, text: str, meta: dict) -> str:
    """One capture in the §5.2 layout, written through the capture store's own writers, so its
    `.json` carries the `meta_digest` the reader verifies (R-021 round 2, Finding 4). The text is
    plain UTF-8 (`mime: text/plain` unless given), so the reader can re-derive it from the bytes."""
    from databridge_mcp_ingest import capture

    data = text.encode("utf-8")
    sha = capture.put_bytes(data, root)
    d = root / sha[:2]
    (d / f"{sha}.txt").write_text(text, encoding="utf-8", newline="")
    capture.put_meta(sha, {"mime": "text/plain", **meta}, root)
    return sha


def pinned(plan, materialiser):
    """``plan`` carrying the live pins of exactly the sources it reads (R-021 round 2, F3)."""
    return plan.model_copy(update={"temporal": plan.temporal.model_copy(
        update={"source_snapshots": materialiser.pins_for(plan)})})


def span(sha: str, text: str, needle: str, *, source: str = FILES_SOURCE) -> SourcePointer:
    start = text.index(needle)
    return SourcePointer(source_id=source, kind="doc_span", document_id=sha, char_start=start,
                         char_end=start + len(needle), capture_digest=sha)


def whole(sha: str, text: str, *, source: str = FILES_SOURCE) -> SourcePointer:
    return SourcePointer(source_id=source, kind="doc_span", document_id=sha, char_start=0,
                         char_end=len(text), capture_digest=sha)


def build_workspace(root: Path, *, with_passages: bool = True) -> Workspace:
    from databridge_graph import GraphStore
    from databridge_graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

    root = Path(root)
    cap, facts, pas = root / "capture", root / "facts", root / "passages"
    for d in (cap, facts, pas):
        d.mkdir(parents=True, exist_ok=True)
    graph_path = root / "workspace.db"
    store = GraphStore(str(graph_path))
    ws = Workspace(root=root, capture=cap, facts=facts, passages=pas, graph_path=graph_path,
                   store=store)

    # --- organisations ---------------------------------------------------------------------
    org_ids = []
    for i, name in enumerate(ORGS):
        text = f"Customer record. Organisation: {name}. Segment: enterprise."
        sha = write_capture(cap, text, {"source": FILES_SOURCE, "title": f"{name} record"})
        oid = f"organisation:{name.lower().split()[0]}"
        org_ids.append(oid)
        store.add_node(GraphNode(
            id=oid, kind=NodeKind("organisation"), label=name,
            attr_sources={"label": span(sha, text, name)}, sources=(whole(sha, text),),
            valid_from=EPOCH - timedelta(days=400), system_from=EPOCH - timedelta(days=30)))

    # --- invoices: four per organisation, monthly; every third one overdue ------------------
    n = 0
    for o, oid in enumerate(org_ids):
        for k in range(4):
            n += 1
            number = f"INV-{n:03d}"
            issued = EPOCH + timedelta(days=30 * k + 7 * o)
            recorded = issued + timedelta(days=2 + (n % 3))
            amount = 1000 + 150 * n
            overdue = (n % 3 == 0)
            text = (f"Invoice {number} issued to {ORGS[o]} on {issued.date().isoformat()} "
                    f"for amount {amount}. Overdue: {overdue}. Terms: net 30.")
            sha = write_capture(cap, text, {"source": FILES_SOURCE, "title": number})
            iid = f"invoice:{number.lower()}"
            store.add_node(GraphNode(
                id=iid, kind=NodeKind("invoice"), label=number,
                attrs={"amount": amount, "overdue": overdue, "number": number},
                attr_sources={"label": span(sha, text, number), "number": span(sha, text, number),
                              "amount": span(sha, text, str(amount)),
                              "overdue": span(sha, text, f"Overdue: {overdue}")},
                sources=(whole(sha, text),), valid_from=issued, system_from=recorded))
            store.add_edge(GraphEdge(
                id=f"billed_to:{iid}->{oid}", src_id=iid, dst_id=oid, kind=EdgeKind("billed_to"),
                sources=(span(sha, text, ORGS[o]),), valid_from=issued, system_from=recorded))
            ws.invoices.append({"id": iid, "number": number, "org": oid, "issued": issued,
                                "recorded": recorded, "amount": amount, "overdue": overdue})

    # --- people: a SUBJECT kind; email + employee_id are declared identifiers ---------------
    for name, email, emp, title, o, joined, remote in (
            ("Ana Tan", "ana.tan@larkspur.example", "E-017", "Finance lead", 0, 0, False),
            ("Ravi Menon", "ravi.menon@larkspur.example", "E-042", "Account manager", 1, 0,
             True),
            ("Mei Lim", "mei.lim@larkspur.example", "E-058", "Analyst", 0, 20, True)):
        text = (f"Staff card. Name: {name}. Email: {email}. Employee id: {emp}. "
                f"Title: {title}. Remote: {remote}. Works for {ORGS[o]}.")
        sha = write_capture(cap, text, {"source": FILES_SOURCE, "title": f"staff {emp}"})
        pid = f"person:{emp.lower()}"
        start = EPOCH + timedelta(days=joined)
        store.add_node(GraphNode(
            id=pid, kind=NodeKind("person"), label=name,
            attrs={"email": email, "employee_id": emp, "title": title, "remote": remote},
            attr_sources={"label": span(sha, text, name), "email": span(sha, text, email),
                          "employee_id": span(sha, text, emp), "title": span(sha, text, title),
                          "remote": span(sha, text, f"Remote: {remote}")},
            sources=(whole(sha, text),), valid_from=start, system_from=start))
        store.add_edge(GraphEdge(
            id=f"works_for:{pid}->{org_ids[o]}", src_id=pid, dst_id=org_ids[o],
            kind=EdgeKind("works_for"), sources=(span(sha, text, ORGS[o]),),
            valid_from=start, system_from=start))
        ws.people.append({"id": pid, "name": name, "email": email, "employee_id": emp,
                          "org": org_ids[o], "joined": start})

    # --- facts store ---------------------------------------------------------------------
    _build_facts(ws)

    # --- passage store -------------------------------------------------------------------
    if with_passages:
        _build_passages(ws)
    return ws


def _build_facts(ws: Workspace) -> None:
    path = ws.facts / f"{DB_SOURCE}_facts.sqlite"
    extracted = "2026-09-01T00:00:00+00:00"
    rows = [(f"PAY-{i:03d}", inv["number"], inv["amount"] - (0 if i % 2 else 100),
             int(not inv["overdue"]), f"ap{i}@{ORGS[0].split()[0].lower()}.example")
            for i, inv in enumerate(ws.invoices[:6], start=1)]
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE _facts_tables (facts_table TEXT PRIMARY KEY, source_table TEXT, mode TEXT,
            rows_source INTEGER, rows_in_facts INTEGER, group_by TEXT, reason TEXT,
            extracted_at TEXT, source_sql TEXT);
        CREATE TABLE _facts_columns (facts_table TEXT, column_name TEXT, source_type TEXT,
            class TEXT, prose INTEGER, distinct_n INTEGER, non_null INTEGER, lo TEXT, hi TEXT);
        CREATE TABLE _facts_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE payments (payment_id TEXT, invoice_number TEXT, paid_amount NUMERIC,
            settled INTEGER, email TEXT);
    """)
    db.executemany("INSERT INTO payments VALUES (?,?,?,?,?)", rows)
    n = len(rows)
    cols = [("payment_id", "text", "text", n), ("invoice_number", "text", "text", n),
            ("paid_amount", "numeric", "numeric", len({r[2] for r in rows})),
            ("settled", "boolean", "boolean", 2), ("email", "text", "text", n)]
    db.executemany("INSERT INTO _facts_columns VALUES (?,?,?,?,?,?,?,?,?)",
                   [("payments", c, t, cls, 0, d, n, None, None) for c, t, cls, d in cols])
    db.execute("INSERT INTO _facts_tables VALUES (?,?,?,?,?,?,?,?,?)",
               ("payments", "public.payments", "rows", n, n, "[]", "all rows copied", extracted,
                "SELECT * FROM public.payments"))
    db.executemany("INSERT INTO _facts_meta VALUES (?,?)",
                   [("source_id", DB_SOURCE), ("extracted_at", extracted)])
    db.commit()
    db.close()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    (ws.facts / f"{DB_SOURCE}_facts.json").write_text(json.dumps({
        "source_id": DB_SOURCE, "extracted_at": extracted, "facts_file": str(path),
        "sha256": sha, "status": "ok", "rows_total": n,
        "tables": [{"source_table": "public.payments", "facts_table": "payments", "mode": "rows",
                    "rows_source": n, "rows_in_facts": n}]}), encoding="utf-8")
    ws.payments = [dict(zip(("payment_id", "invoice_number", "paid_amount", "settled", "email"),
                            r)) for r in rows]


POLICY_TEXT = (
    "Larkspur accounts receivable policy. Invoices are issued on net 30 terms. An invoice that "
    "remains unpaid thirty days after its due date is escalated to the account manager, who "
    "contacts the customer's finance team and records the outcome in the CRM. Disputed invoices "
    "are paused from dunning until the dispute is resolved. Credit notes supersede the invoice "
    "they correct and are issued within five working days of approval. ") * 3

KICKOFF_TEXT = (
    "Project kickoff notes for the Cobalt Partners forecasting engagement. Attendees agreed the "
    "demand model will be retrained monthly, that the data retention review happens each "
    "quarter, and that the steering committee meets on the first Tuesday of the month. Actions "
    "were assigned for the data inventory and the access review.") * 2


def _build_passages(ws: Workspace, *, harness: Optional[object] = None) -> None:
    from databridge_harness import FakeBackend, Harness
    from databridge_retrieval import BuildSession
    from databridge_retrieval.screen import load_chunk_screen

    h = harness or Harness(FakeBackend(), profile="cloud", cache=None)
    shas = [write_capture(ws.capture, POLICY_TEXT, {"source": FILES_SOURCE,
                                                     "uri": "file:///larkspur/policy/ar.md",
                                                     "title": "AR policy"}),
            write_capture(ws.capture, KICKOFF_TEXT, {"source": FILES_SOURCE,
                                                      "uri": "file:///larkspur/meetings/kick.md",
                                                      "title": "Kickoff"})]
    with BuildSession(FILES_SOURCE, harness=h, screen=load_chunk_screen("workspace"),
                      passages_dir=ws.passages, capture_dir=ws.capture) as b:
        for sha in shas:
            b.index_capture(sha)
    # The workspace has ONE timeline: the graph and the facts store are stamped on this module's
    # clock (BUILD_TIME), and the indexer stamps `ingested_at` with the wall clock. Since the
    # reader enforces the as-of cut on passages (R-021 Finding 10), a wall-clock stamp would put
    # the passages AFTER the workspace's own build instant. Stamp them on the workspace clock.
    for path in ws.passages.glob("*.sqlite"):
        conn = sqlite3.connect(path)
        try:
            conn.execute("UPDATE documents SET ingested_at = ?", (PASSAGES_INGESTED_AT,))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------------------
# Hand-written plans over the workspace (inputs, never expected outputs)
# ---------------------------------------------------------------------------------------
AUTHORED_AT = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


def _plan(principal: str, *, selection, temporal, shape, row_order, pii: str = "suppress",
          label: str = "", output_metadata: str = "croissant/1.1"):
    from . import (EXECUTOR_VERSION, AuthoredBy, Determinism, Executor, MaterialisationPlan,
                   MaterialisationPolicy, Output, PiiPolicy, PolicyPrincipal, Provenance, Split)

    return MaterialisationPlan(
        label=label,
        authored_by=AuthoredBy(agent="test", on_behalf_of=principal, purpose="analytics",
                               at=AUTHORED_AT),
        selection=selection, temporal=temporal, shape=shape,
        policy=MaterialisationPolicy(purpose="analytics",
                                     principal=PolicyPrincipal(actor=principal),
                                     lawful_basis="legitimate_interests",
                                     pii=PiiPolicy(direct_identifiers=pii)),
        determinism=Determinism(seed=7, row_order=row_order, split=Split(strategy="none"),
                                executor=Executor(backend="walnut-platform/sqlite",
                                                  materialiser_version=EXECUTOR_VERSION)),
        provenance=Provenance(mode="compressed", require_pointer_for_every_cell=True),
        output=Output(format="jsonl", metadata=output_metadata),
        packs=("workspace",))


def invoice_plan(principal: str = "staff", *, feature_axis: str = "system_time",
                 maturity_lag: Optional[str] = "P14D", kind: str = "tabular"):
    """One row per invoice at its issue instant: its own amount as known then, the prior amount
    billed to the same customer, a count of prior invoices, and — the label — whether the NEXT
    invoice to that customer within 90 days was overdue, matured 14 days before the build."""
    from . import (Cohort, Column, ColumnSource, Consumer, Label, LabelSource, LabelWindow,
                   Selection, Shape, TemporalContract)

    e = "invoice"
    feat = dict(system_time_field="_system_from", valid_time_field="_valid_from")
    return _plan(
        principal,
        selection=Selection(entity=e, cohort=Cohort(decision_time_field="_valid_from")),
        temporal=TemporalContract(
            feature_axis=feature_axis, feature_as_of="per_row", label_axis="valid_time",
            label_window=LabelWindow(within="P90D"), label_read_at_system_time="build_time",
            label_maturity_lag=maturity_lag, build_time=BUILD_TIME),
        shape=Shape(
            kind=kind, consumer=Consumer(framework="xgboost", version="3.4.0"),
            columns=(
                Column(path="invoice._id", type="string",
                       source=ColumnSource(entity=e, field="_id", relation="cohort_key")),
                Column(path="invoice._valid_from", type="timestamp",
                       source=ColumnSource(entity=e, field="_valid_from",
                                           relation="decision_time")),
                Column(path="invoice.amount", type="float64",
                       source=ColumnSource(entity=e, field="amount", relation="cohort_row",
                                           **feat)),
                Column(path="invoice.prior_amount_same_customer", type="float64",
                       source=ColumnSource(entity=e, field="amount", relation="as_of_prior",
                                           join_key="billed_to_id", **feat)),
                Column(path="invoice.prior_invoice_count", type="int32",
                       pointer_mode="derived_compressed",
                       source=ColumnSource(entity=e, field="_id", relation="aggregate_count",
                                           join_key="billed_to_id", **feat)),
            ),
            label=Label(path="invoice.next_overdue", type="bool",
                        source=LabelSource(entity=e, field="overdue", join_key="billed_to_id",
                                           valid_time_field="_valid_from",
                                           system_time_field="_system_from"))),
        row_order=("invoice._valid_from", "invoice._id"),
        label="invoice overdue 90d, synthetic workspace")


def person_plan(principal: str = "staff", *, pii: str = "suppress"):
    """A SUBJECT kind: its identifiers are what the suppression policy is about. The label (is
    the next colleague to join the same organisation within 90 days remote?) exists only so the
    supervised shape compiles; the plan is about what the rows disclose."""
    from . import (Cohort, Column, ColumnSource, Consumer, Label, LabelSource, LabelWindow,
                   Selection, Shape, TemporalContract)

    e = "person"
    feat = dict(system_time_field="_system_from", valid_time_field="_valid_from")
    cols = [Column(path="person._id", type="string",
                   source=ColumnSource(entity=e, field="_id", relation="cohort_key")),
            Column(path="person._valid_from", type="timestamp",
                   source=ColumnSource(entity=e, field="_valid_from", relation="decision_time"))]
    for f in ("label", "email", "employee_id", "title", "works_for_id"):
        cols.append(Column(path=f"person.{f}", type="string",
                           source=ColumnSource(entity=e, field=f, relation="cohort_row", **feat)))
    return _plan(
        principal,
        selection=Selection(entity=e, cohort=Cohort(decision_time_field="_valid_from")),
        temporal=TemporalContract(feature_axis="system_time", feature_as_of="per_row",
                                  label_axis="valid_time",
                                  label_window=LabelWindow(within="P90D"),
                                  label_read_at_system_time="build_time",
                                  label_maturity_lag="P14D", build_time=BUILD_TIME),
        shape=Shape(kind="tabular", consumer=Consumer(framework="xgboost", version="3.4.0"),
                    columns=tuple(cols),
                    label=Label(path="person.next_colleague_remote", type="bool",
                                source=LabelSource(entity=e, field="remote",
                                                   join_key="works_for_id",
                                                   valid_time_field="_valid_from",
                                                   system_time_field="_system_from"))),
        row_order=("person._id",), pii=pii, label="people, identifiers under policy")


def passages_corpus(principal: str = "staff", *, corpus_as_of: datetime,
                    entity: str = f"passages.{FILES_SOURCE}.default", max_chars: int = 200):
    """A RAG corpus over the passage store, membership on `ingested_at` (system time)."""
    from . import (Chunking, Cohort, ColumnSource, Consumer, Selection, Shape, TemporalContract)

    return _plan(
        principal,
        selection=Selection(entity=entity, cohort=Cohort()),
        temporal=TemporalContract(feature_axis="system_time", feature_as_of="fixed",
                                  feature_as_of_fixed=corpus_as_of, label_axis="none",
                                  corpus_as_of=corpus_as_of),
        shape=Shape(kind="chunk_corpus", consumer=Consumer(framework="retriever", version="1.0"),
                    text_field=ColumnSource(entity=entity, field="content",
                                            relation="cohort_row",
                                            system_time_field="ingested_at"),
                    chunking=Chunking(max_chars=max_chars, overlap_chars=0)),
        row_order=("chunk_id",), label="workspace passages corpus")


# ---------------------------------------------------------------------------------------
# Exercising the download gate (E2E run 2, N-09)
# ---------------------------------------------------------------------------------------
def withhold_published_run(out_root: Path, plan_hash: str, *, check: str = "pointer") -> Path:
    """TEST / E2E ONLY. Make a PUBLISHED run's recorded check read as failed, on disk.

    Why this exists: publication refuses a failed pointer or leak check (R-021 F4), so no API
    request can produce a published run whose check failed — which is the point, and which is
    also why `GET .../files/<dataset>` answering 409 `DATASET_WITHHELD` could not be exercised
    end to end (N-09). That gate defends runs ON DISK whose record does not clear them: written by
    a build before the gate, or modified after publication. This helper produces exactly that
    state for a real run, without any switch in the gateway or the materialiser — production code
    has no test mode to leave on. ``check``: ``"pointer"`` (the full pointer check fails),
    ``"leak"`` (the missingness leak check fails) or ``"missing"`` (no pointer check on record).

    From a shell (the E2E): ``python -m databridge_materialise.testing withhold <out_root>
    <plan_hash> [pointer|leak|missing]``. Returns the receipt path.
    """
    path = Path(out_root) / plan_hash / "receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    platform = doc.setdefault("platform", {})
    if check == "pointer":
        pc = dict(platform.get("pointer_check") or {})
        grounded = int(pc.get("grounded_cells") or 1)
        pc.update(grounded_cells=grounded, values_match=grounded - 1, all_match=False,
                  failures=[*(pc.get("failures") or []),
                            {"row": 0, "column": "(withheld by testing.withhold_published_run)",
                             "pointer_kind": "n/a", "resolves": True, "matches": False}])
        platform["pointer_check"] = pc
    elif check == "leak":
        leak = dict((doc.get("receipt") or {}).get("missingness_leak_check") or {})
        leak.update(passed=False, max_class_delta_observed=1.0,
                    threshold=leak.get("threshold", 0.02))
        doc["receipt"]["missingness_leak_check"] = leak
    elif check == "missing":
        platform.pop("pointer_check", None)
    else:
        raise ValueError(f"check must be pointer, leak or missing, not {check!r}")
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


__all__ = ["withhold_published_run", "invoice_plan", "person_plan", "passages_corpus", "Workspace", "build_workspace", "write_capture", "pinned", "span", "whole", "BUILD_TIME",
           "EPOCH", "FILES_SOURCE", "DB_SOURCE", "ORGS"]


if __name__ == "__main__":                                       # pragma: no cover - CLI
    import sys

    if len(sys.argv) >= 4 and sys.argv[1] == "withhold":
        print(withhold_published_run(Path(sys.argv[2]), sys.argv[3],
                                     check=sys.argv[4] if len(sys.argv) > 4 else "pointer"))
    else:
        print("usage: python -m databridge_materialise.testing withhold <out_root> <plan_hash> "
              "[pointer|leak|missing]")
        sys.exit(2)
