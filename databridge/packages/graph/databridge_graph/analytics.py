"""Graph-derived operational analytics (`PLAN.md` §6.7, `PHASE_7` T-42/T-43).

Phase 5's `metrics.py` measures **per-intake** minutes saved. These are the aggregates only a
traversal can produce — computed by walking edges, not by a second code path running beside the
metrics module. If a number here could be obtained by counting rows in `app.state.intakes`, it does
not belong here.

## The constraint that shapes every function below

**These are counts of structural facts, never claims about outcomes.** "11 documents, 2 of them
ambiguous" is a fact about the graph. "We saved N hours" is a claim about the world, and the only
version of that claim we are entitled to make is Phase 5's per-intake, stage-completed model. So:

- no derived rates presented as performance;
- no projection, extrapolation or annualisation;
- every figure carries the denominator it was computed over, because "2 conflicts" means nothing
  without "out of 11 documents";
- **and every figure carries the ids it was counted over** (`Count.members`), so a reader can walk
  to the subgraph that produced it instead of trusting the number. A count whose members cannot be
  named is indistinguishable from a literal.

`patients` is the figure that proves why the second rule is not decoration. The clinic's registry
holds 300 rows and `clinic_seed` seeds all of them, so "how many patients?" has two true answers —
**seen here** (a document names them) and **on file** (the CSV has a row) — that differ by a factor
of twenty-five. As a bare `int` it silently meant whichever one the seed happened to produce. As a
`Count` it means `n` of `of`, and cannot be read as the other.

## Why this reads the graph rather than the pipeline

`ambiguity_rate` is the clearest case. A document with two `FOR_PATIENT` edges is one the resolver
could not narrow to a single registry row — the *structure* records the ambiguity. Counting it from
intake state would mean re-deriving what the graph already asserts, and the two would drift.

## What this deliberately does NOT do

It never ranks, scores or narrows a `FOR_PATIENT` claim. `ambiguity` reports *how many* candidates
a document named; it never says which one is right, and there is no field here that could be read
as a determination (CLAUDE.md §Hard constraints 1). `conflicts` likewise reports that two documents
on file name different issuers for the same patient — deciding which applies is the counter's job,
and the window policy that turns that structure into a front-desk flag lives in
`eligibility.py`/`coverage_rules.yaml`, not here.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from .schema import EdgeKind, NodeKind
from .store import GraphStore


def _parse(value: str | None) -> dt.datetime | None:
    """Store text → datetime. Python 3.11+ parses the store's trailing `Z`."""
    return dt.datetime.fromisoformat(value) if value else None


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """Normalise to UTC-aware.

    The store round-trips datetimes through SQLite text and a caller may hand us a naive
    `as_of` (`datetime(2026, 8, 1)` is the obvious thing to type). Comparing naive and aware
    datetimes raises `TypeError`, so `compute(store, as_of=naive)` used to blow up — untested,
    because until T-43 nothing called this module at all. Same fix, same reason, as
    `clinic_history._aware`.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Count:
    """A count, the population it was counted over, and the ids behind it. Never one without
    the others — `members` is what makes the figure traceable to a traversal."""

    label: str
    n: int
    of: int
    members: tuple[str, ...] = ()

    @property
    def pct(self) -> float:
        return (self.n / self.of * 100.0) if self.of else 0.0

    def to_dict(self) -> dict:
        return {"label": self.label, "n": self.n, "of": self.of,
                "pct": round(self.pct, 1), "members": list(self.members)}


@dataclass(frozen=True)
class Entity:
    """The minimum needed to render an id as something a person can read and click."""

    id: str
    kind: str
    label: str

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label}


@dataclass(frozen=True)
class IssuerDocuments:
    """One row of the issuer mix: who sent us paperwork, and exactly which documents."""

    issuer_id: str
    issuer: str
    documents: tuple[str, ...] = ()

    @property
    def n(self) -> int:
        return len(self.documents)

    def to_dict(self) -> dict:
        return {"issuer_id": self.issuer_id, "issuer": self.issuer,
                "documents": self.n, "members": list(self.documents)}


@dataclass
class GraphAnalytics:
    documents: int = 0
    # A `Count`, not an int, and that is the point — see `compute`. `n` is the patients this
    # clinic has actually seen; `of` is how many it has on file.
    patients: Count | None = None
    issuer_mix: list[IssuerDocuments] = field(default_factory=list)
    ambiguity: Count | None = None
    unlinked_documents: Count | None = None
    conflicts: Count | None = None
    tests_outside_any_package: list[Entity] = field(default_factory=list)
    tests_in_catalogue: int = 0
    packages: int = 0
    nodes: int = 0
    edges: int = 0
    as_of: str | None = None
    # id → Entity for every id named anywhere above, so the caller never has to guess a label
    # or issue a second query per member.
    entities: dict[str, Entity] = field(default_factory=dict)

    def to_dict(self) -> dict:
        def c(x: Count | None) -> dict | None:
            return None if x is None else x.to_dict()
        return {
            "documents": self.documents,
            "patients": c(self.patients),
            "packages": self.packages,
            "tests_in_catalogue": self.tests_in_catalogue,
            "nodes": self.nodes,
            "edges": self.edges,
            "issuer_mix": [i.to_dict() for i in self.issuer_mix],
            "ambiguity": c(self.ambiguity),
            "unlinked_documents": c(self.unlinked_documents),
            "conflicts": c(self.conflicts),
            "tests_outside_any_package": [e.to_dict() for e in self.tests_outside_any_package],
            "as_of": self.as_of,
            "entities": {k: v.to_dict() for k, v in sorted(self.entities.items())},
        }


def compute(store: GraphStore, *, as_of: dt.datetime | None = None) -> GraphAnalytics:
    """Walk the open graph once and produce the aggregates.

    `as_of` filters to facts valid at that instant, so the same function backs the trend view — the
    bi-temporal axis is what makes "how has the conflict rate changed?" answerable at all, rather
    than needing a separate time-series table. (T-30 made those dates defensible: before it, 63 of
    111 seed edges claimed to predate one of their own endpoints, so every `as_of` answer here was
    reading a date nobody could defend.)

    Labels are read from the SAME filtered snapshot as the counts, never from a fresh lookup of the
    currently-open row: a rewound view must not borrow a label the graph only learned later.
    """
    as_of = _aware(as_of)
    node_rows = store.open_nodes()
    edge_rows = store.open_edges()

    def valid(row) -> bool:
        if as_of is None:
            return True
        vf, vt = _aware(_parse(row["valid_from"])), _aware(_parse(row["valid_to"]))
        if vf and vf > as_of:
            return False
        if vt and vt <= as_of:
            return False
        return True

    node_rows = [r for r in node_rows if valid(r)]
    edges = [r for r in edge_rows if valid(r)]
    entities = {r["id"]: Entity(id=r["id"], kind=r["kind"], label=r["label"]) for r in node_rows}
    live = set(entities)
    # An edge whose endpoint is not valid at `as_of` is not a fact yet either. Without this the
    # rewound view counts relationships to nodes that do not exist in it.
    edges = [e for e in edges if e["src_id"] in live and e["dst_id"] in live]

    by_kind = Counter(r["kind"] for r in node_rows)
    doc_ids = {r["id"] for r in node_rows if r["kind"] == NodeKind.DOCUMENT.value}

    # --- issuer mix: who sent us today's paperwork -------------------------------------
    issued_docs: dict[str, list[str]] = {}          # issuer node id → document node ids
    issuer_of: dict[str, str] = {}                  # document node id → issuer node id
    for e in edges:
        if e["kind"] == EdgeKind.ISSUED.value and e["dst_id"] in doc_ids:
            issued_docs.setdefault(e["src_id"], []).append(e["dst_id"])
            issuer_of[e["dst_id"]] = e["src_id"]
    mix = [IssuerDocuments(issuer_id=oid, issuer=entities[oid].label,
                           documents=tuple(sorted(set(docs))))
           for oid, docs in issued_docs.items()]
    mix.sort(key=lambda i: (-i.n, i.issuer))

    # --- ambiguity: documents the resolver could not narrow to one registry row ---------
    # TWO `FOR_PATIENT` edges from one document means two candidates, neither of them a
    # determination (see the warning in schema.py). This is the honest denominator for "how often
    # does the front desk actually have to choose?".
    claims: dict[str, set[str]] = {}                # document node id → claimed patient node ids
    for e in edges:
        if e["kind"] == EdgeKind.FOR_PATIENT.value and e["src_id"] in doc_ids:
            claims.setdefault(e["src_id"], set()).add(e["dst_id"])
    ambiguous = sorted(d for d, pats in claims.items() if len(pats) > 1)
    linked = sorted(claims)

    # --- documents that reached no registry row at all ----------------------------------
    unlinked = sorted(doc_ids - set(linked))

    # --- patients whose documents on file do not agree on who is paying ------------------
    # STRUCTURE ONLY: two documents naming the same patient, issued by different organisations.
    # Whether that is a live conflict for a given visit is `eligibility.py`'s call — it owns the
    # conflict window, and re-deriving that policy here is exactly the drift this module exists to
    # avoid. What the graph can say without policy is that the paperwork disagrees.
    docs_by_patient: dict[str, set[str]] = {}
    for doc, pats in claims.items():
        for p in pats:
            docs_by_patient.setdefault(p, set()).add(doc)
    disagreeing = sorted(
        p for p, docs in docs_by_patient.items()
        if len({issuer_of[d] for d in docs if d in issuer_of}) > 1)

    # --- tests a document asked for that no package includes ----------------------------
    # Already surfaced as an insight; §6.7 asks for it as a number. It is the coverage-gap
    # signal — what the patient may end up paying for.
    in_packages = {e["dst_id"] for e in edges if e["kind"] == EdgeKind.INCLUDES_TEST.value}
    requested = {e["dst_id"] for e in edges if e["kind"] == EdgeKind.REQUESTS_TEST.value}
    orphans = [entities[t] for t in sorted(requested - in_packages) if t in entities]

    # --- patients: SEEN HERE, out of ON FILE --------------------------------------------
    # This was a bare `int` — the count of PATIENT nodes — and it was the one figure in this
    # module that broke the module's own rule three paragraphs into the docstring: no denominator,
    # no members, nothing to walk to. That went unnoticed while the seed held only patients some
    # document named, because then the two readings happened to be the same number.
    #
    # They are not the same number any more. `clinic_seed` now seeds the whole registry, so a
    # count of PATIENT nodes is "rows in the clinic's file", while the figure a dashboard means by
    # "patients" is "people who came through the door". Same key, same type, a completely
    # different claim — which is exactly the failure `Count` exists to make impossible: the
    # denominator travels with the number, so the reading cannot drift.
    #
    # `docs_by_patient` is already the honest numerator, built above and already used as the
    # `conflicts` denominator. A claim is still a CLAIM (constraint 1) — an ambiguous document
    # contributes two rows and neither is settled — so this is an upper bound on distinct people,
    # and the label says "registry rows", never "people".
    seen_patients = Count("registry rows a document has claimed",
                          len(docs_by_patient), by_kind.get(NodeKind.PATIENT.value, 0),
                          tuple(sorted(docs_by_patient)))
    ambiguity = Count("documents naming more than one candidate",
                      len(ambiguous), len(linked), tuple(ambiguous))
    unlinked_count = Count("documents matching no registry row",
                           len(unlinked), len(doc_ids), tuple(unlinked))
    # "registry row", not "patient": a FOR_PATIENT edge is a CLAIM, so the honest reading is
    # "this row is claimed by documents that disagree", never "this person has two insurers".
    conflicts = Count("registry rows claimed by documents from more than one issuer",
                      len(disagreeing), len(docs_by_patient), tuple(disagreeing))
    # Only the ids actually named above travel with the payload. Shipping the whole node table
    # would make the response grow with the graph rather than with the figures on it.
    named: set[str] = {*(i.issuer_id for i in mix), *(e.id for e in orphans)}
    for cnt in (seen_patients, ambiguity, unlinked_count, conflicts):
        named.update(cnt.members)
    for issuer in mix:
        named.update(issuer.documents)

    return GraphAnalytics(
        documents=len(doc_ids),
        patients=seen_patients,
        packages=by_kind.get(NodeKind.PACKAGE.value, 0),
        tests_in_catalogue=by_kind.get(NodeKind.TEST.value, 0),
        nodes=len(node_rows),
        edges=len(edges),
        issuer_mix=mix,
        ambiguity=ambiguity,
        unlinked_documents=unlinked_count,
        conflicts=conflicts,
        tests_outside_any_package=orphans,
        as_of=as_of.isoformat() if as_of else None,
        entities={k: v for k, v in entities.items() if k in named},
    )
