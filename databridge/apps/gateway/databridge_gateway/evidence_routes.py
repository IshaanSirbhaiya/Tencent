"""``/evidence/*`` — the evidence chain for ANY node or edge in the shared graph, end to end.

The provenance model already exists and is enforced everywhere (``GroundedValue`` /
``SourcePointer``, the anti-fabrication wall in ``databridge_graph.schema``, the append-only
ledger). This route is the single place a reader can point at any fact — a graph node, a graph
edge, or a committed ledger row — and read the whole chain:

    value -> pointer -> captured snippet (highlighted) -> source (uri, retrieved_at, content hash)
          -> re-verify (the bytes still hash to the digest; the span still contains the value)
          -> ingestion run that produced it, and what that source EXCLUDED

**Read-only.** Nothing here writes to the graph store, the ledger, the capture store or the run
ledgers. It reads the shared store (``app.state.graph_store``, falling back to
``app.state.brain.store`` — they are the same object once the store is shared), the capture store
and ``var/ingest`` (``source_capture``), and ``EvidenceLedger.entries()``.

**Domain-free.** Which entities need a human confirmation before their evidence is released is
decided by ``subject_exposure.ExposurePolicy`` from the bound vocabulary — the pack's
``subject_kinds`` and its claim edges — never by naming a kind. See that module for the rule.

## Two kinds of release, and no third

* **Pointer provenance.** An entity that is not a subject, cannot claim one, and none of whose
  pointers reaches into material that names one is ordinary knowledge. It is shown by its
  pointers: every attribute with its own pointer, every pointer with its captured snippet when one
  exists, and a re-verification of that snippet against the captured bytes.
* **A committed ledger row (R-28).** A subject-bearing entity is served only when a completed
  confirmation stands behind it: a committed row whose own pointer names a document this entity
  cites, or — for a subject node — a committed row whose ``selected_patient_id`` is this subject.
  That is precisely "a named human checked identity in person, selected this row, and confirmed".
  The refusal says which fact it is: ``AWAITING_HUMAN_CONFIRMATION`` (403) means *the entity exists
  and its evidence is not yet releasable*; ``UNKNOWN_ENTITY`` (404) means *no such thing*.

The authorisation root is read from the LEDGER, not from ``app.state.intakes``: the intakes map is
process memory and empties on restart, the ledger does not, and a committed row carries its own
``SourcePointer``.

**A claim edge is a CLAIM until a human settles it (R-26).** An edge binding to the ``ABOUT``
meta-kind (``node_sheet.claimed_identity_edges``) is reported as a **relationship** only when a
committed ledger row names the same document AND the same selected subject; otherwise it is an
``identity_claim`` carrying the graph's own ``identity_claim_caveat`` for the subject kind.

**R-015.** Every route here requires ``auth.require_principal``; the release rule above is an
additional question ("has a human confirmed this yet"), not a substitute for "may the caller ask".
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from databridge_core import SourcePointer
from databridge_graph import GraphEdge, row_to_edge, row_to_node
# The identity vocabulary is imported, never restated — see the note in `node_sheet`. If
# `_candidate_phrase` is renamed this import fails loudly, which is the correct failure.
from databridge_graph.node_sheet import (  # noqa: PLC2701
    _candidate_phrase,
    claimed_identity_edges,
    identity_claim_caveat,
)

from .auth import AuthenticatedPrincipal, install_auth_handler, require_principal
from .source_capture import (
    list_runs,
    normalise_digest,
    snippet_and_verify,
    source_summaries,
)
from .subject_exposure import AMBIGUOUS, ExposurePolicy, entity_key, walk_pointers

LIMITATION = (
    "sources_for() is read for the bi-temporal VERSION shown here, so a superseded value's "
    "citations no longer appear beside its replacement. What this route still does not do is "
    "rewind: it answers about the version open now, not about what was believed at some earlier "
    "moment, so a pointer learned after that moment can still appear."
)

#: The intake channels a document can arrive by. Any other route is one of the three below.
INTAKE_CHANNELS = frozenset({"upload", "email_forward", "walk_in"})
#: `ingestion_route` vocabulary beyond the intake channels — never widened by a guess.
ROUTE_INGEST = "ingest"          # bytes in the capture store, or named by an ingestion run
ROUTE_TABLE = "table"            # a cell of a structured source (a table, a config file)
ROUTE_UNKNOWN = "unknown"

#: Upper bound on the browse list. A browse list, not an export.
MAX_RECENT = 100
#: How many of the newest entities the browse list examines at most. Each needs its pointers read
#: to decide whether it may be named, so the scan is bounded rather than walking an estate.
MAX_RECENT_SCAN = 2_000
#: Captured levels below one pointer that are re-verified. A trace, not an audit export.
MAX_NESTED_CAPTURES = 25


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _pointer_dict(pointer: SourcePointer) -> dict:
    # The literal pointer, not a summary of it ("showcase the pointer" is literal per spec).
    return pointer.model_dump(mode="json", exclude_none=True)


def graph_store_of(app: FastAPI) -> Any:
    """The shared store: ``app.state.graph_store``, else ``app.state.brain.store``."""
    store = getattr(app.state, "graph_store", None)
    if store is None:
        store = getattr(getattr(app.state, "brain", None), "store", None)
    return store


def _document_ids_of(pointer: SourcePointer) -> list[str]:
    """Every document id this pointer's evidence reaches, over the ONE provenance walk
    (``subject_exposure.walk_pointers``: ``derived_from``, ``facts`` and ``child``) — R-019 t08 #4.
    """
    out: list[str] = []
    for p in walk_pointers([pointer]):
        if p.document_id and p.document_id not in out:
            out.append(p.document_id)
    return out


def _document_id_of(pointer: SourcePointer) -> Optional[str]:
    """The first document this pointer's evidence lives in (display only), ``None`` if none."""
    ids = _document_ids_of(pointer)
    return ids[0] if ids else None


def _capture_digests_of(pointer: SourcePointer) -> set[str]:
    return {d for d in (normalise_digest(p.capture_digest) for p in walk_pointers([pointer])) if d}


def _intake_of(app: FastAPI, document_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``(channel, intake_id)`` for a document this process read through ``/intake/upload``."""
    if document_id is None:
        return None, None
    intakes = getattr(app.state, "intakes", None) or {}
    for intake_id, held in intakes.items():
        draft = held.get("draft") if isinstance(held, dict) else None
        ref = getattr(getattr(draft, "record", None), "document", None)
        if ref is not None and ref.document_id == document_id:
            return (ref.channel or ROUTE_UNKNOWN), intake_id
    return None, None


class _RunIndex:
    """Ingestion runs on disk, read once per request, indexed by content address and by source."""

    def __init__(self) -> None:
        self._runs = None

    @property
    def runs(self):
        if self._runs is None:
            try:
                self._runs = list_runs()
            except OSError:
                self._runs = []
        return self._runs

    def runs_naming(self, digests: set[str], sources: set[str]) -> list:
        return [r for r in self.runs
                if (digests and r.digests() & digests) or r.source in sources]


def _pointer_evidence(pointer: SourcePointer, *, value: Any = None,
                      with_capture: bool = True) -> dict:
    """The pointer, and what the disk says about it: a captured snippet + re-verify, or a cell."""
    out: dict = {"pointer": _pointer_dict(pointer), "capture": None, "table_cell": None}
    if pointer.kind == "table_cell":
        out["table_cell"] = {"source": pointer.source_id, "table": pointer.table,
                             "column": pointer.column, "row": pointer.row_id}
    target = pointer if pointer.capture_digest else pointer.child
    if with_capture and target is not None and target.capture_digest:
        out["capture"] = snippet_and_verify(target, value=value)
    # Every OTHER captured level the pointer reaches — a derived pointer's inputs, its facts, a
    # deeper child — is re-verified too (R-019 t08 #4): a derivation over tampered bytes is not
    # "shown as recorded" merely because its top level names no capture.
    nested: list[dict] = []
    if with_capture:
        for p in walk_pointers([pointer]):
            if p is target or p is pointer or not p.capture_digest:
                continue
            if len(nested) >= MAX_NESTED_CAPTURES:
                break
            found = snippet_and_verify(p)
            if found is not None:
                nested.append(found)
    out["derived_captures"] = nested
    if out["capture"] is None and not nested:
        out["verify"] = {"status": "no_capture_binding",
                         "detail": "this pointer names no captured bytes, so there is nothing on "
                                   "disk to re-read — it is shown as recorded"}
    else:
        checks = ([out["capture"]] if out["capture"] is not None else []) + nested
        verdicts = [c.get("verify") or {} for c in checks]
        # The worst level speaks for the pointer: one failed input fails the derivation.
        out["verify"] = next((v for v in verdicts if v.get("status") != "verified"), verdicts[0])
    return out


def _document_summary(store, policy: ExposurePolicy, pointer: SourcePointer) -> Optional[dict]:
    """The document-shaped node this pointer's document keys name, if the graph holds one."""
    doc_node_id = policy.document_node_for(pointer)
    if doc_node_id is None:
        return None
    row = store.open_node_row(doc_node_id)
    if row is None:
        return None
    return {"id": row["id"], "kind": row["kind"], "label": row["label"],
            "valid_from": row["valid_from"], "valid_to": row["valid_to"]}


def _trace_sources(app: FastAPI, store, policy: ExposurePolicy,
                   pointers: list[SourcePointer]) -> list[dict]:
    out = []
    for pointer in pointers:
        document_id = _document_id_of(pointer)
        channel, intake_id = _intake_of(app, document_id)
        evidence = _pointer_evidence(pointer)
        capture = evidence.get("capture") or {}
        if channel is not None:
            route = channel
        elif capture:
            route = ROUTE_INGEST
        elif pointer.kind == "table_cell":
            route = ROUTE_TABLE
        else:
            route = ROUTE_UNKNOWN
        evidence.update({
            "document_id": document_id,
            "document": _document_summary(store, policy, pointer),
            "ingestion_route": route,
            "ingest_source": (capture.get("source") or {}).get("source"),
            "intake_id": intake_id,
        })
        out.append(evidence)
    return out


def _ingestion_block(runs: _RunIndex, pointers: list[SourcePointer],
                     sources: list[dict]) -> dict:
    """Which ingestion runs produced this entity, and what those sources excluded (P3/P7)."""
    digests: set[str] = set()
    for p in pointers:
        digests |= _capture_digests_of(p)
    named_sources = {s["ingest_source"] for s in sources if s.get("ingest_source")}
    matched = runs.runs_naming(digests, named_sources)
    by_source: dict[str, dict] = {}
    for run in runs.runs:
        if run.source in {m.source for m in matched}:
            by_source[run.source] = run.summary()          # the source's LATEST run on disk
    return {
        "runs": [{"source": r.source, "run_id": r.run_id} for r in matched],
        "sources": [{"source": s, "latest_run": summary["run_id"],
                     "excluded": summary["excluded"],
                     "exclusion_reasons": summary["exclusion_reasons"],
                     "counts": summary["counts"]}
                    for s, summary in sorted(by_source.items())],
        "note": (None if matched else
                 "no ingestion run on disk names this entity's captured content — it reached the "
                 "graph some other way (a seed, an intake, a table), or the runs were removed"),
    }


def _ledger_row_to_dict(row: dict) -> dict:
    pointer = SourcePointer.model_validate_json(row["source_pointer_json"])
    return {
        "rowid": row["rowid_pk"],
        "intake_id": row["intake_id"],
        "field_path": row["field_path"],
        "value": json.loads(row["value_json"]),
        "pointer": _pointer_dict(pointer),
        "extracted_at": row["extracted_at"],
        "confirmed_by": row["confirmed_by"],
        "confirmed_at": row["confirmed_at"],
        "selected_patient_id": row["selected_patient_id"],
        "supersedes": row["supersedes"],
    }


class _Commits:
    """The committed ledger rows this request needs, read once and indexed.

    **The ledger is the only authorisation root** (R-019 t08 #1). A row is indexed under exactly
    the documents its OWN pointer names — over the full provenance walk — and under the ONE
    subject node its ``selected_patient_id`` names (``ExposurePolicy.subject_node_for``: a typed
    node id, or a bare key exactly one subject node answers to). Nothing is read from
    ``app.state.intakes``: process memory can be re-pointed at another document, and it empties on
    restart. A row naming no document authorises no document; a bare key two subjects answer to
    authorises neither. With no ledger at all there are no commits, so a subject-bearing entity is
    never released.
    """

    def __init__(self, ledger: Any, policy: ExposurePolicy) -> None:
        self._rows = ledger.entries() if ledger is not None else []
        self._by_document: dict[str, list[dict]] = {}
        self._by_subject: dict[str, list[dict]] = {}
        self._subject_of: dict[int, Optional[str]] = {}
        for row in self._rows:
            try:
                pointer = ledger.pointer_of(row)
            except Exception:                                        # noqa: BLE001
                pointer = None
            for document_id in (_document_ids_of(pointer) if pointer is not None else []):
                self._by_document.setdefault(document_id, []).append(row)
            node_id = policy.subject_node_for(row.get("selected_patient_id"))
            self._subject_of[row["rowid_pk"]] = node_id
            if node_id:
                self._by_subject.setdefault(node_id, []).append(row)

    def for_document(self, document_id: str) -> list[dict]:
        return sorted(self._by_document.get(document_id, ()), key=lambda r: r["rowid_pk"])

    def for_names(self, names) -> list[dict]:
        """Rows for ANY of the names one document answers to (its key, its canonical id)."""
        seen: dict[int, dict] = {}
        for name in names:
            for row in self.for_document(name):
                seen[row["rowid_pk"]] = row
        return [seen[k] for k in sorted(seen)]

    def for_subject(self, subject_node_id: str) -> list[dict]:
        return self._by_subject.get(subject_node_id, [])

    def subject_of(self, row: dict) -> Optional[str]:
        return self._subject_of.get(row["rowid_pk"])


# --- R-26: a claim is not a relationship -------------------------------------------------


def _claim_endpoints(policy: ExposurePolicy, edge: GraphEdge) -> tuple[Optional[str],
                                                                         Optional[str]]:
    """``(document key, subject key)`` of a claim edge — the subject is whichever endpoint's kind
    is a pack subject kind, the document the other. Keys are the part after ``kind:`` (node ids
    are minted ``f"{kind}:{key}"``) — for display; authorisation uses the node ids."""
    document_node, subject_node = _claim_nodes(policy, edge.src_id, edge.dst_id)
    if document_node is None:
        return None, None
    return entity_key(document_node), entity_key(subject_node)


def _claim_nodes(policy: ExposurePolicy, src_id: str,
                 dst_id: str) -> tuple[Optional[str], Optional[str]]:
    """``(document node id, subject node id)`` of a claim edge, or ``(None, None)``."""
    if policy.is_subject(dst_id) and not policy.is_subject(src_id):
        return src_id, dst_id
    if policy.is_subject(src_id) and not policy.is_subject(dst_id):
        return dst_id, src_id
    return None, None


def _document_rows(policy: ExposurePolicy, commits: _Commits, document_node: str) -> list[dict]:
    """Committed rows for exactly this document node — by the names it answers to, and only when
    none of those names is shared with another document (R-019 t08 #3)."""
    names = policy.document_names(document_node)
    if {f"id:{n}" for n in names} & policy.collided_keys():
        return []
    return commits.for_names(names)


def _identity_backing(policy: ExposurePolicy, commits: _Commits,
                      edge: GraphEdge) -> tuple[bool, list[dict]]:
    """``(this edge asserts an identity, the committed rows that settle it)``.

    A claim edge is settled only by a row committed for **this** document whose selection names
    **this** subject node; a row for the same document naming a DIFFERENT subject is the rejected
    candidate, and settles nothing about this edge.
    """
    if edge.kind not in claimed_identity_edges():
        return False, []
    document_node, subject_node = _claim_nodes(policy, edge.src_id, edge.dst_id)
    if not (document_node and subject_node):
        return True, []            # an identity edge whose endpoints cannot be named is not vouched
    return True, [r for r in _document_rows(policy, commits, document_node)
                  if commits.subject_of(r) == subject_node]


#: Attribute keys under which a model- or agent-produced node/edge records WHO produced it
#: (BUILD_PLAN §5.8: "carry it in their attribute provenance"). Read, never required.
PRODUCED_BY_KEYS = ("produced_by", "emitted_by")


def _produced_by(attrs: Optional[dict]) -> Optional[Any]:
    """§5.8 runtime traceability, surfaced when the entity carries it and ``None`` when it does not
    — additive: an entity written before §5.8, or by deterministic rules that recorded nothing, is
    shown exactly as before."""
    for key in PRODUCED_BY_KEYS:
        value = (attrs or {}).get(key)
        if isinstance(value, (dict, str)) and value:
            return value
    return None


def _edge_payload(edge: GraphEdge) -> dict:
    return {
        "id": edge.id, "kind": edge.kind.value, "src_id": edge.src_id, "dst_id": edge.dst_id,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "attrs": edge.attrs,
        "attribute_sources": {k: _pointer_dict(v) for k, v in edge.attr_sources.items()},
        "unresolved": edge.unresolved,
        "produced_by": _produced_by(edge.attrs),
    }


def _incident_entry(store, policy: ExposurePolicy, edge: GraphEdge, subject_id: str, *,
                    established: bool, established_by: list[dict],
                    neighbour_releasable) -> dict:
    outgoing = edge.src_id == subject_id
    other_id = edge.dst_id if outgoing else edge.src_id
    other_row = store.open_node_row(other_id)
    payload = _edge_payload(edge)
    # The other end's LABEL is its own evidence, released by its OWN authorisation — never by the
    # page's. A confirmed document's rejected candidate is a subject no human selected: that the
    # document is releasable says nothing about them (R-017 t08 #2). Such a neighbour is named by
    # id only — the id is what a caller would type to ask for it, and that request meets the gate.
    other_label = other_row["label"] if other_row is not None else None
    if policy.entity_exposed(other_id) and not neighbour_releasable(other_id):
        other_label = None
    payload.update({
        "direction": "outgoing" if outgoing else "incoming",
        "other_id": other_id,
        "other_kind": other_row["kind"] if other_row is not None else None,
        "other_label": other_label,
        "established": established,
        "established_by": [r["rowid_pk"] for r in established_by],
        "sources": [_pointer_dict(p) for p in edge.sources],
        "candidate_phrase": None if established else _candidate_phrase(edge),
    })
    return payload


def _split_incident_edges(store, policy: ExposurePolicy, commits: _Commits,
                          subject_id: str) -> tuple[list[dict], list[dict]]:
    """``(relationships, identity_claims)`` for the node being traced, belief as of now."""
    relationships: list[dict] = []
    claims: list[dict] = []
    memo: dict[str, bool] = {}

    def neighbour_releasable(entity_id: str) -> bool:
        if entity_id not in memo:
            memo[entity_id] = _entity_authorised(store, policy, commits, entity_id)
        return memo[entity_id]

    for row in store.edges_touching(subject_id):
        if row["system_to"] is not None:
            continue
        try:
            edge = row_to_edge(store, row)
        except (KeyError, ValueError):
            continue          # an edge kind the bound vocabulary does not declare is not shown
        is_identity, backing = _identity_backing(policy, commits, edge)
        if is_identity and not backing:
            claims.append(_incident_entry(store, policy, edge, subject_id, established=False,
                                          established_by=[],
                                          neighbour_releasable=neighbour_releasable))
        else:
            relationships.append(_incident_entry(store, policy, edge, subject_id,
                                                 established=True, established_by=backing,
                                                 neighbour_releasable=neighbour_releasable))
    return relationships, claims


# --- R-28: the authorisation root --------------------------------------------------------


def _authorise(policy: ExposurePolicy, commits: _Commits, *, exposed: bool,
               pointers: list[SourcePointer], subject_id: Optional[str] = None,
               subject_ids: tuple[str, ...] = (),
               claim_document: Optional[str] = None) -> Optional[dict]:
    """What authorises serving this entity's evidence, or ``None`` if nothing does.

    **A subject is authorised by a confirmation naming IT, and by nothing else** (R-017 t08 #1).
    It used to be enough that a committed row cited a document this subject's pointers name — but
    the human who committed that document may have selected a DIFFERENT candidate, and the subject
    being traced is then exactly the person nobody confirmed. ``subject_id`` (a subject NODE id)
    and ``subject_ids`` (the subject endpoint node ids of a non-claim edge) each need a committed
    row whose selection names that node (R-019 t08 #2); a document commit is never a substitute.
    ``claim_document`` (a claim edge's document node) needs a confirmation of that document.
    """
    if not exposed:
        return {"basis": "pointer_provenance", "committed_rows": 0,
                "why": ("this entity is not a subject, cannot claim one, and none of its pointers "
                        "reaches material that names one — it is shown by its pointers"),
                "policy": policy.describe()}
    subjects = tuple(x for x in ((subject_id,) if subject_id else ()) + tuple(subject_ids) if x)
    if subjects:
        rows_per_subject = [commits.for_subject(x) for x in subjects]
        if not all(rows_per_subject):
            return None
        return {"basis": "committed_ledger_row",
                "committed_rows": sum(len(r) for r in rows_per_subject),
                "why": ("a human checked this subject's identity in person, selected this "
                        "registry row and confirmed" if len(subjects) == 1 else
                        "a human checked each subject's identity in person, selected that "
                        "registry row and confirmed"),
                "policy": policy.describe()}
    if claim_document is not None:
        return _authorise_claim(policy, commits, claim_document, pointers)
    return _authorise_documents(policy, commits, pointers)


def _authorise_documents(policy: ExposurePolicy, commits: _Commits,
                         pointers: list[SourcePointer]) -> Optional[dict]:
    """Every subject-bearing document BRANCH the pointers reach must be confirmed (R-019 t08 #4).

    One walk over every level (``derived_from``, ``facts``, ``child``). A level that reaches
    subject material must name exactly one document (``document_identity``) with a committed row;
    a level that reaches such material and names no provable document — an ambiguous key, a
    subject's table row — cannot be settled by any document confirmation, so nothing is released.
    When no level is subject-bearing (the entity is exposed by its kind alone), one confirmed
    document it cites is the authorisation, as before. One unrelated committed pointer never
    stands in for another branch.
    """
    required: list[frozenset] = []
    cited: list[frozenset] = []
    for level in walk_pointers(pointers):
        exposed_here = policy.level_exposed(level)
        identity = policy.document_identity(level)
        if identity is AMBIGUOUS:
            if exposed_here:
                return None
            continue
        if identity is None:
            if exposed_here:
                return None                 # e.g. a subject's own table row: no document settles it
            continue
        cited.append(identity)
        if exposed_here:
            required.append(identity)
    if required:
        rows_per_branch = [commits.for_names(names) for names in required]
        if not all(rows_per_branch):
            return None
        rows = {r["rowid_pk"] for branch in rows_per_branch for r in branch}
        names = sorted({min(n) for n in required})
    else:
        committed = [(names, commits.for_names(names)) for names in cited]
        committed = [(names, rows) for names, rows in committed if rows]
        if not committed:
            return None
        rows = {r["rowid_pk"] for _names, branch in committed for r in branch}
        names = sorted({min(n) for n, _rows in committed})
    return {"basis": "committed_ledger_row", "committed_rows": len(rows),
            "why": (f"a human confirmed an intake of {', '.join(repr(n) for n in names)}, which "
                    f"this entity cites as its source"),
            "policy": policy.describe()}


def _authorise_claim(policy: ExposurePolicy, commits: _Commits, document_node: str,
                     pointers: list[SourcePointer]) -> Optional[dict]:
    """A CLAIM edge ``D -> S`` is released (as a claim) only by a confirmation of D itself
    (R-019 t08 #5) — and only when every document its pointers name IS D: a pointer into another
    document, or into no provable document, is not evidence of what D claims."""
    own = frozenset(policy.document_names(document_node))
    for level in walk_pointers(pointers):
        identity = policy.document_identity(level)
        if identity is None:
            # Names no document (a registry cell the match was read from, a derivation wrapper):
            # it is evidence about the CANDIDATE, shown as a candidate (R-26), not about which
            # document is claiming.
            continue
        if identity is AMBIGUOUS or not (identity & own):
            return None
    rows = _document_rows(policy, commits, document_node)
    if not rows:
        return None
    return {"basis": "committed_ledger_row", "committed_rows": len(rows),
            "why": (f"a human confirmed an intake of '{entity_key(document_node)}', the document "
                    f"making this claim"),
            "policy": policy.describe()}


def _subject_node(policy: ExposurePolicy, row) -> Optional[str]:
    """The node id of a subject node row, else ``None``."""
    if row is None or row["kind"] not in policy.subject_kinds:
        return None
    return row["id"]


def _entity_authorised(store, policy: ExposurePolicy, commits: _Commits, entity_id: str) -> bool:
    """Would ``/evidence/trace/{entity_id}`` release this node? The same rule, asked of a
    neighbour, so a page never labels a neighbour its own trace would refuse."""
    if not policy.entity_exposed(entity_id):
        return True
    row = store.open_node_row(entity_id)
    if row is None:
        return False
    pointers = store.sources_for(entity_id, version_pk=row["rowid_pk"])
    return _authorise(policy, commits, exposed=True, pointers=pointers,
                      subject_id=_subject_node(policy, row)) is not None


def _not_yet_confirmed(entity_id: str) -> JSONResponse:
    return _refusal(
        "AWAITING_HUMAN_CONFIRMATION",
        f"'{entity_id}' exists, but it carries a subject's information (the bound packs declare "
        f"its kind a subject, or able to claim one, or it is grounded in material that names "
        f"one) and no committed ledger row authorises reading its evidence. Nothing about a "
        f"subject becomes a record until a named staff member checks identity in person, "
        f"selects a registry row and confirms (CLAUDE.md §Hard constraints 1 and 4). This is NOT "
        f"'no such entity' — it exists and is waiting on a human.",
        status=403)


# --- responses ---------------------------------------------------------------------------


def _envelope(entity_id: str, entity_type: str, *,
              node: Optional[dict] = None,
              edge: Optional[dict] = None,
              claim: Optional[dict] = None,
              relationships: Optional[list[dict]] = None,
              identity_claims: Optional[list[dict]] = None,
              sources: list[dict],
              ledger_entries: list[dict],
              authorisation: dict,
              ingestion: Optional[dict] = None,
              caveat_kind: Optional[str] = None) -> dict:
    """One response shape for every entity type, so a caller never has to guess which keys exist.

    ``identity_caveat`` is populated only when something on this page IS an unsettled claim. It is
    worded for the claimed subject's kind (``caveat_kind``, GRAPH6 FIN-09: the pack's own noun and
    attestation), and neutrally when the kind is unknown or the claims name several kinds.
    """
    claims = identity_claims or []
    return {
        "entity_id": entity_id, "entity_type": entity_type, "found": True,
        "node": node,
        "edge": edge,
        "claim": claim,
        "relationships": relationships or [],
        "identity_claims": claims,
        "identity_caveat": identity_claim_caveat(caveat_kind) if (claims or claim) else None,
        "authorisation": authorisation,
        "sources": sources,
        "ledger_entries": ledger_entries,
        "ingestion": ingestion,
        "limitation": LIMITATION,
    }


def _one_kind(kinds) -> Optional[str]:
    """The single subject kind a caveat is about, or None (several, or none known)."""
    found = sorted({str(k) for k in kinds if k})
    return found[0] if len(found) == 1 else None


def _attribute_evidence(attrs: dict, attr_sources: dict[str, SourcePointer]) -> dict:
    """Per VALUE: the value, its own pointer, its captured snippet, and a re-verify of THAT value."""
    return {key: {"value": attrs.get(key), **_pointer_evidence(pointer, value=attrs.get(key))}
            for key, pointer in sorted(attr_sources.items())}


def _node_response(app: FastAPI, store, policy: ExposurePolicy, commits: _Commits,
                   runs: _RunIndex, entity_id: str, row) -> Any:
    exposed = policy.entity_exposed(entity_id)
    is_subject = row["kind"] in policy.subject_kinds
    subject_id = _subject_node(policy, row)
    # Authorised BEFORE the row is materialised, so a refused entity's values are never even
    # assembled, and a kind the bound vocabulary cannot construct still gets a clean refusal.
    version_pointers = store.sources_for(entity_id, version_pk=row["rowid_pk"])
    authorisation = _authorise(policy, commits, exposed=exposed, pointers=version_pointers,
                               subject_id=subject_id)
    if authorisation is None:
        return _not_yet_confirmed(entity_id)
    try:
        node = row_to_node(store, row)
    except (KeyError, ValueError):
        return _refusal("NOT_IN_BOUND_VOCABULARY",
                        f"'{entity_id}' is stored with a kind no bound pack declares, so it cannot "
                        f"be read as a node of this deployment's vocabulary", status=409)
    sources = _trace_sources(app, store, policy, node.sources)

    ledger_entries: list[dict] = []
    if is_subject and subject_id:
        ledger_entries = [_ledger_row_to_dict(r) for r in commits.for_subject(subject_id)]
    elif authorisation["basis"] == "committed_ledger_row":
        # The rows for the documents this entity's own pointers identify (the same identities the
        # authorisation used) — plus, for a document node, the rows for the node itself.
        names: set[str] = set()
        for level in walk_pointers(version_pointers):
            identity = policy.document_identity(level)
            if identity is not None and identity is not AMBIGUOUS:
                names |= identity
        if row["kind"] in policy.document_kinds:
            names |= {r_name for r_name in policy.document_names(entity_id)}
        ledger_entries = [_ledger_row_to_dict(r) for r in commits.for_names(sorted(names))]

    relationships, claims = _split_incident_edges(store, policy, commits, node.id)
    claimed_kinds = ({row["kind"]} if is_subject else
                     {c.get("other_kind") for c in claims
                      if c.get("other_kind") in policy.subject_kinds})
    return _envelope(
        entity_id, "node",
        node={
            "id": node.id, "kind": node.kind.value, "label": node.label,
            "meta_kind": getattr(node.kind.meta, "value", None),
            "canonical_id": node.canonical_id,
            "valid_from": node.valid_from.isoformat() if node.valid_from else None,
            "valid_to": node.valid_to.isoformat() if node.valid_to else None,
            "system_from": node.system_from.isoformat() if node.system_from else None,
            "system_to": node.system_to.isoformat() if node.system_to else None,
            "attrs": node.attrs,
            # Per value, so nothing downstream can present one citation as covering all of them.
            "attribute_sources": {k: _pointer_dict(v) for k, v in node.attr_sources.items()},
            "attribute_evidence": _attribute_evidence(node.attrs, node.attr_sources),
            "unresolved": node.unresolved,
            "produced_by": _produced_by(node.attrs),
        },
        relationships=relationships,
        identity_claims=claims,
        sources=sources,
        ledger_entries=ledger_entries,
        authorisation=authorisation,
        ingestion=_ingestion_block(runs, [*node.sources, *node.attr_sources.values()], sources),
        caveat_kind=_one_kind(claimed_kinds))


def _edge_response(app: FastAPI, store, policy: ExposurePolicy, commits: _Commits,
                   runs: _RunIndex, entity_id: str, row) -> Any:
    version_pointers = store.sources_for(entity_id, version_pk=row["rowid_pk"])
    exposed = policy.edge_exposed(row["kind"], row["src_id"], row["dst_id"], version_pointers)
    # A CLAIM edge (R-26) is authorised by its document's commit and then reported as a CLAIM —
    # the rejected candidate is shown as a candidate, by id, which is that page's whole point. Any
    # OTHER edge touching a subject publishes that subject's relationships, so every subject end
    # needs a confirmation naming it (R-017 t08 #1/#2).
    subject_ends: tuple[str, ...] = ()
    claim_document: Optional[str] = None
    if row["kind"] not in claimed_identity_edges():
        subject_ends = tuple(end for end in (row["src_id"], row["dst_id"])
                             if policy.is_subject(end))
    else:
        # A claim is released only by a confirmation of ITS document (R-019 t08 #5); a claim
        # whose ends are not one document and one subject has nothing that could confirm it.
        claim_document, _subject = _claim_nodes(policy, row["src_id"], row["dst_id"])
        if claim_document is None and exposed:
            return _not_yet_confirmed(entity_id)
    authorisation = _authorise(policy, commits, exposed=exposed, pointers=version_pointers,
                               subject_ids=subject_ends, claim_document=claim_document)
    if authorisation is None:
        return _not_yet_confirmed(entity_id)
    try:
        edge = row_to_edge(store, row)
    except (KeyError, ValueError):
        return _refusal("NOT_IN_BOUND_VOCABULARY",
                        f"'{entity_id}' is stored with a kind no bound pack declares, so it cannot "
                        f"be read as an edge of this deployment's vocabulary", status=409)
    sources = _trace_sources(app, store, policy, edge.sources)
    ingestion = _ingestion_block(runs, [*edge.sources, *edge.attr_sources.values()], sources)

    is_identity, backing = _identity_backing(policy, commits, edge)
    ledger_entries = [_ledger_row_to_dict(r) for r in backing]
    if is_identity and not backing:
        # NOT an edge in this response: the unsettled case gets a shape of its own — `edge` is
        # null, and what the document CLAIMS is reported as a claim.
        document_id, claimed_subject = _claim_endpoints(policy, edge)
        claim = _edge_payload(edge)
        claim.update({
            "established": False,
            "established_by": [],
            "candidate_phrase": _candidate_phrase(edge),
            "document_id": document_id,
            # Named `patient_id` on the wire for the contract the UI already renders; it is the
            # SUBJECT key, whatever the bound pack calls its subject kind.
            "patient_id": claimed_subject,
        })
        _, subject_node = _claim_nodes(policy, edge.src_id, edge.dst_id)
        subject_row = store.open_node_row(subject_node) if subject_node else None
        return _envelope(entity_id, "identity_claim", claim=claim, sources=sources,
                         ledger_entries=[], authorisation=authorisation, ingestion=ingestion,
                         caveat_kind=subject_row["kind"] if subject_row is not None else None)

    payload = _edge_payload(edge)
    payload.update({"established": True, "established_by": [r["rowid_pk"] for r in backing],
                    "attribute_evidence": _attribute_evidence(edge.attrs, edge.attr_sources)})
    return _envelope(entity_id, "edge", edge=payload, sources=sources,
                     ledger_entries=ledger_entries, authorisation=authorisation,
                     ingestion=ingestion)


def _ledger_entry_response(app: FastAPI, store, policy: ExposurePolicy, ledger: Any,
                           entity_id: str, row: dict) -> dict:
    pointer = ledger.pointer_of(row)
    # A committed row IS an authorisation root: it exists only because a named human checked
    # identity in person, selected a registry row and confirmed. Nothing further to ask.
    return _envelope(
        entity_id, "ledger_entry",
        sources=_trace_sources(app, store, policy, [pointer]),
        ledger_entries=[_ledger_row_to_dict(row)],
        authorisation={"basis": "committed_ledger_row", "committed_rows": 1,
                       "why": "this entity IS a committed ledger row"})


def _recent(store, policy: ExposurePolicy, limit: int) -> dict:
    """The most recently believed ordinary entities — a way into the page that names nothing it
    could not release. Subject-bearing entities are COUNTED, never listed."""
    rows = sorted(store.open_nodes(), key=lambda r: (r["system_from"] or "", r["id"]),
                  reverse=True)
    shown, withheld, examined = [], 0, 0
    for row in rows:
        if len(shown) >= limit or examined >= MAX_RECENT_SCAN:
            break
        examined += 1
        if policy.entity_exposed(row["id"]):
            withheld += 1
            continue
        shown.append({"id": row["id"], "kind": row["kind"], "label": row["label"],
                      "system_from": row["system_from"]})
    # `withheld` is counted over the rows EXAMINED (newest first), and says so: a count over part
    # of the graph presented as a count over all of it would be the wrong-number failure.
    return {"entities": shown, "total": len(rows), "examined": examined, "withheld": withheld,
            "withheld_reason": ("subject-bearing entities are counted here and never listed; "
                                "trace one by id and the confirmation gate answers")
            if withheld else None}


def register_evidence_routes(app: FastAPI) -> None:
    """Attach ``/evidence/trace/{entity_id}``, ``/evidence/recent``, ``/evidence/sources`` and
    ``/evidence/sources/{source}``."""
    install_auth_handler(app)

    @app.get("/evidence/trace/{entity_id:path}")
    def evidence_trace(entity_id: str,
                       _principal: AuthenticatedPrincipal = Depends(require_principal)) -> Any:
        store = graph_store_of(app)
        if store is None:
            return _refusal("EVIDENCE_UNAVAILABLE", "the graph store is not initialised",
                            status=503)
        ledger = getattr(app.state, "ledger", None)
        policy = ExposurePolicy(store)
        commits = _Commits(ledger, policy)
        runs = _RunIndex()

        # -- a committed ledger row, addressed directly: "ledger:<rowid>" ------------------
        if entity_id.startswith("ledger:"):
            raw = entity_id.split(":", 1)[1]
            if not raw.isdigit():
                return _refusal("BAD_ENTITY_ID",
                                f"'{entity_id}' is not a valid ledger row id (expected "
                                f"'ledger:<row number>')")
            rowid = int(raw)
            row = next((r for r in (ledger.entries() if ledger is not None else [])
                        if r["rowid_pk"] == rowid), None)
            if row is None:
                return _refusal("UNKNOWN_ENTITY", f"no ledger row '{entity_id}'", status=404)
            return _ledger_entry_response(app, store, policy, ledger, entity_id, row)

        # -- a graph node --------------------------------------------------------------------
        node_versions = store.all_node_versions(entity_id)
        if node_versions:
            return _node_response(app, store, policy, commits, runs, entity_id,
                                  node_versions[-1])

        # -- a graph edge (the store's own locked read path; no method addresses an edge by id)
        edge_rows = store.fetchall(
            "SELECT * FROM edges WHERE id = ? ORDER BY system_from, rowid_pk", (entity_id,))
        if edge_rows:
            return _edge_response(app, store, policy, commits, runs, entity_id, edge_rows[-1])

        return _refusal(
            "UNKNOWN_ENTITY",
            f"'{entity_id}' matches no graph node, graph edge, or ledger row "
            f"('ledger:<row number>')", status=404)

    @app.get("/evidence/recent")
    def evidence_recent(limit: int = 25,
                        _principal: AuthenticatedPrincipal = Depends(require_principal)) -> Any:
        store = graph_store_of(app)
        if store is None:
            return _refusal("EVIDENCE_UNAVAILABLE", "the graph store is not initialised",
                            status=503)
        return _recent(store, ExposurePolicy(store), max(1, min(limit, MAX_RECENT)))

    @app.get("/evidence/sources")
    def evidence_sources(_principal: AuthenticatedPrincipal = Depends(require_principal)) -> Any:
        """Every ingestion source on disk: its runs, the latest run's ledger counts, and every
        exclusion reason with its count (P3/P7). Empty — and said to be empty — when none ran."""
        summaries = source_summaries()
        return {"sources": summaries,
                "note": None if summaries else
                "no ingestion run has written its ledgers yet, so there is nothing to report — "
                "this is an empty register, not a clean one"}

    @app.get("/evidence/sources/{source}")
    def evidence_source(source: str,
                        _principal: AuthenticatedPrincipal = Depends(require_principal)) -> Any:
        runs = list_runs(source=source)
        if not runs:
            return _refusal("UNKNOWN_SOURCE", "no ingestion run on disk for that source",
                            status=404)
        return {"source": source, "runs": [r.summary() for r in runs]}

    # Deliberately NO route that addresses the capture store by digest. Captured content (and its
    # meta — a title can name a person) is released only through a pointer on an entity this page
    # may release, as a bounded snippet; a digest must not be a way to read around the gate.


__all__ = ["LIMITATION", "graph_store_of", "register_evidence_routes"]
