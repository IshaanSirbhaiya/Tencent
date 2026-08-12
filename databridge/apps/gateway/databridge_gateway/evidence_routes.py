"""``GET /evidence/trace/{entity_id}`` — the evidence chain, made visible end to end (T-37).

The provenance model already exists and is enforced everywhere (``GroundedValue`` /
``SourcePointer``, the anti-fabrication wall in ``databridge_graph.schema``, the append-only
ledger). What is missing is a single place a judge can point at ANY fact — a graph node, a graph
edge, or a row that was actually committed to the evidence ledger — and read the whole chain:

    source (document / config file) -> pointer -> extracted value -> graph node/edge
        -> committed ledger row (confirmed_by / confirmed_at)

**Read-only.** This module never writes to the graph store, the ledger, or the intake map. It only
reuses ``GraphStore.sources_for()`` and ``EvidenceLedger.entries()`` — the two reads that already
carry this information — and assembles them into one response.

**Ingestion route is reported, never guessed.** ``DocumentRef.channel`` (``upload`` /
``email_forward`` / ``walk_in``) is only known for a document that actually passed through
``/intake/upload`` in THIS process (``app.state.intakes``, keyed by intake_id, holding the draft
that carries the original ``DocumentRef``). The demo's ``/brain`` graph is seeded independently at
boot (``BrainTools.load_seed()``) and never touches ``app.state.intakes`` at all, so for most graph
entities the honest answer is ``"unknown"`` — that is not a bug in this route, it is this route
telling the truth about a seam nobody has wired yet. A ``table_cell`` pointer into
``coverage_rules.yaml`` did not arrive through any intake channel at all; it is static
configuration shipped with the app, reported as ``"config"`` rather than stretched to fit the
upload/email/walk-in vocabulary.

**Two kinds of citation, never merged.** ``sources`` grounds the ENTITY — that this node or edge
exists and was read from somewhere. ``attribute_sources`` grounds each value individually. The
route used to return ``attrs`` beside one pooled list, which is the shape a reviewer named: a
citation appearing to vouch for values it had never seen. Anything the graph could not ground is
listed in ``unresolved`` rather than returned as a value.

**Pointers are read for the version being shown.** ``sources_for()`` was keyed by entity id alone,
so a node's pointer list was every pointer ever attached to that id and version 2's values could
render beside version 1's citations. It now takes the row's ``version_pk``. What remains is
narrower and is still declared in every response (``"limitation"``).

**A `FOR_PATIENT` edge is a CLAIM until a human settles it (R-26).** The graph stores one such
edge per registry candidate — two candidates, two edges — because collapsing an unresolved
identity into a single answer is the thing this system must never do (CLAUDE.md §Hard constraints
1). This route used to render every one of them as an established relationship, so a candidate a
human had *rejected* still appeared, on the evidence page itself, as the document's patient. A
document→patient edge is now reported as a **relationship** only when a committed ledger row names
the same document AND the same ``selected_patient_id``; every other one is reported as an
``identity_claim`` — a separate list, carrying the same ``IDENTITY_CAVEAT`` the graph's own
evidence sheet prints (``databridge_graph.node_sheet``), so the two surfaces cannot describe the
same fact differently. Claims are never hidden: the count of candidates is exactly what a reviewer
needs, and a missing candidate is its own kind of lie on an evidence page.

**Nothing is served without an authorisation root (R-28).** "Nothing is recorded until a human
confirms" was true of the ledger and quietly untrue of this read: an abandoned, never-confirmed
intake's document — extracted values, pointers, the patients it named — was fully readable by id.
An entity's evidence is now served only when something authorises it, and the boundary is drawn
where the data changes character rather than where it is convenient:

* **Static configuration needs no ledger row.** A node grounded ENTIRELY in ``coverage_rules.yaml``
  ``table_cell`` pointers (the insurers, packages, tests and coverage rules) is app configuration
  that ships in the repo. It contains no patient data, it is the same for every patient, and
  refusing it would make the page useless for explaining a billing determination while protecting
  nobody. ``ingestion_route == "config"`` on every one of the entity's pointers IS the test —
  a single non-config pointer means the entity carries document- or registry-derived evidence and
  falls to the rule below. Mixed evidence fails closed.
* **Anything touching a patient needs a committed row.** A document, a patient, a visit, a clinic
  or an employer node is served only if a completed confirmation stands behind it: a committed row
  whose own pointer names one of the documents this entity cites, or — for a patient — a committed
  row whose ``selected_patient_id`` is this patient. That is precisely "a human checked identity in
  person, selected this row, and confirmed"; nothing weaker authorises a read.
* **The refusal says which fact it is.** ``AWAITING_HUMAN_CONFIRMATION`` (403) means *the entity
  exists and its evidence is not yet releasable*; ``UNKNOWN_ENTITY`` (404) means *no such thing*.
  Collapsing the two would make "we are waiting on a human" indistinguishable from "we lost it",
  and this repo has been bitten by exactly that conflation before.

The authorisation root is read from the LEDGER, not from ``app.state.intakes``. The intakes map is
process memory and empties on restart; a gate that evaporated on restart would refuse every real
confirmation the clinic ever made. Committed rows carry their own ``SourcePointer``, so the
document a row confirms is a durable property of the row.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from databridge_core import SourcePointer
from databridge_graph import GraphEdge, NodeKind, node_id, row_to_edge, row_to_node
# The identity vocabulary is imported, never restated. `/evidence` and the graph's own evidence
# sheet (`node_sheet.node_markdown`) describe the same FOR_PATIENT edges, and a second copy of the
# caveat — or a second opinion about which edge kinds carry an unsettled identity — is a copy that
# drifts. `_candidate_phrase` is private and imported anyway, deliberately: it encodes the rule
# that an UNCITED count must produce the MORE cautious wording, and re-deriving that rule here is
# how the two surfaces would come to disagree about the same edge. If it is renamed, this import
# fails loudly, which is the correct failure.
from databridge_graph.node_sheet import (  # noqa: PLC2701 - see comment above
    CLAIMED_IDENTITY_EDGES,
    IDENTITY_CAVEAT,
    _candidate_phrase,
)
from databridge_intake.ledger import EvidenceLedger

LIMITATION = (
    "sources_for() is read for the bi-temporal VERSION shown here, so a superseded value's "
    "citations no longer appear beside its replacement. What this route still does not do is "
    "rewind: it answers about the version open now, not about what was believed at some earlier "
    "moment, so a pointer learned after that moment can still appear."
)

# The closed vocabulary this route reports for `ingestion_route`. Never widened by a guess —
# `_ingestion_route` only ever returns one of these, or "unknown".
_KNOWN_DOCUMENT_KINDS = {NodeKind.DOCUMENT}

# The one `ingestion_route` value that means "this did not come from a patient-facing channel at
# all". Named once because the authorisation boundary turns on it.
CONFIG_ROUTE = "config"


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _pointer_dict(pointer: SourcePointer) -> dict:
    # The literal pointer, not a summary of it ("showcase the pointer" is literal per spec).
    return pointer.model_dump(mode="json", exclude_none=True)


def _document_id_of(pointer: SourcePointer) -> Optional[str]:
    """Which document (if any) this pointer's evidence lives in.

    Checks the pointer itself, then its ``child`` (the OCR pointer chain, T-36:
    ``doc_span`` citing extracted text, with a ``child`` ``ocr_bbox`` citing the scan region).
    Returns ``None`` honestly for pointer kinds that name no document at all (e.g. a bare
    ``derived`` aggregation) rather than guessing.
    """
    if pointer.document_id:
        return pointer.document_id
    if pointer.child is not None:
        return _document_id_of(pointer.child)
    return None


def _ingestion_route(app: FastAPI, document_id: Optional[str],
                      source_id: str) -> tuple[str, Optional[str]]:
    """``(route, intake_id)``. Route is one of upload/email_forward/walk_in/"config"/"unknown".

    Looked up from ``app.state.intakes`` — the live map of drafts this gateway process has
    actually read through ``/intake/upload``. A document that only exists via the ``/brain`` seed
    (loaded independently at boot) is not in that map, so it is honestly "unknown".
    """
    if document_id is None:
        if source_id == "coverage_rules.yaml":
            # Static configuration shipped with the app — never ingested through any channel.
            return CONFIG_ROUTE, None
        return "unknown", None
    intakes = getattr(app.state, "intakes", None) or {}
    for intake_id, held in intakes.items():
        draft = held.get("draft") if isinstance(held, dict) else None
        ref = getattr(getattr(draft, "record", None), "document", None)
        if ref is not None and ref.document_id == document_id:
            return (ref.channel or "unknown"), intake_id
    return "unknown", None


def _document_summary(store, document_id: str) -> Optional[dict]:
    """The DOCUMENT node this document_id names in the graph, if the graph has one."""
    doc_node_id = node_id(NodeKind.DOCUMENT, document_id)
    row = store.open_node_row(doc_node_id)
    if row is None:
        versions = store.all_node_versions(doc_node_id)
        row = versions[-1] if versions else None
    if row is None:
        return None
    node = row_to_node(store, row)
    return {
        "id": node.id,
        "label": node.label,
        "document_type": node.attrs.get("document_type"),
        "valid_from": node.valid_from.isoformat() if node.valid_from else None,
        "valid_to": node.valid_to.isoformat() if node.valid_to else None,
    }


def _trace_sources(app: FastAPI, store, pointers: list[SourcePointer]) -> list[dict]:
    out = []
    for pointer in pointers:
        document_id = _document_id_of(pointer)
        route, intake_id = _ingestion_route(app, document_id, pointer.source_id)
        out.append({
            "pointer": _pointer_dict(pointer),
            "document_id": document_id,
            "document": _document_summary(store, document_id) if document_id else None,
            "ingestion_route": route,
            "intake_id": intake_id,
        })
    return out


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
    """The committed ledger rows this request needs, read once.

    Every question this route asks the ledger — "is anything committed for this document?", "for
    this patient?", "for this document AND this patient?" — is a filter over the same table, and
    the authorisation check asks them again for each incident edge. One read per request, indexed
    here, instead of a full scan (and a JSON parse per row) per question.

    **Documents are matched by the row's OWN pointer**, and only then by ``app.state.intakes``.
    The intakes map is process memory: it empties on restart while the ledger does not, so a gate
    that consulted only the map would refuse every confirmation made before the last restart. A
    committed row's pointer names the document it was read from, which is a durable property of
    the row itself.
    """

    def __init__(self, app: FastAPI, ledger: EvidenceLedger) -> None:
        self._rows = ledger.entries()
        self._by_intake: dict[str, list[dict]] = {}
        self._intakes_by_document: dict[str, set[str]] = {}
        self._by_patient: dict[str, list[dict]] = {}
        for row in self._rows:
            self._by_intake.setdefault(row["intake_id"], []).append(row)
            document_id = _document_id_of(ledger.pointer_of(row))
            if document_id:
                self._intakes_by_document.setdefault(document_id, set()).add(row["intake_id"])
            patient_id = row.get("selected_patient_id")
            if patient_id:
                self._by_patient.setdefault(patient_id, []).append(row)
        intakes = getattr(app.state, "intakes", None) or {}
        for intake_id, held in intakes.items():
            draft = held.get("draft") if isinstance(held, dict) else None
            ref = getattr(getattr(draft, "record", None), "document", None)
            if ref is not None and ref.document_id and intake_id in self._by_intake:
                self._intakes_by_document.setdefault(ref.document_id, set()).add(intake_id)

    def for_document(self, document_id: str) -> list[dict]:
        rows: list[dict] = []
        for intake_id in sorted(self._intakes_by_document.get(document_id, ())):
            rows.extend(self._by_intake.get(intake_id, ()))
        return sorted(rows, key=lambda r: r["rowid_pk"])

    def for_patient(self, patient_id: str) -> list[dict]:
        return self._by_patient.get(patient_id, [])

    def for_document_and_patient(self, document_id: str, patient_id: str) -> list[dict]:
        return [r for r in self.for_document(document_id)
                if r.get("selected_patient_id") == patient_id]


def _endpoint_with_kind_prefix(kind: NodeKind, src_id: str, dst_id: str) -> Optional[str]:
    """The bare key (after ``kind:``) of whichever endpoint is of this kind, if either is.

    Node ids are ``f"{kind.value}:{key}"`` (``schema.node_id``); for DOCUMENT and PATIENT nodes
    that key IS the document_id / patient_id (`clinic_seed.document_node_id` /
    `clinic_seed.patient_node_id` mint them directly, with no further slugging), so splitting the
    id string is exact here rather than an approximation.
    """
    prefix = f"{kind.value}:"
    for candidate in (src_id, dst_id):
        if candidate.startswith(prefix):
            return candidate[len(prefix):]
    return None


# --- R-26: a claim is not a relationship -------------------------------------------------


def _identity_backing(commits: _Commits, edge: GraphEdge) -> tuple[bool, list[dict]]:
    """``(this edge asserts an identity, the committed rows that settle it)``.

    An edge kind outside ``CLAIMED_IDENTITY_EDGES`` is an ordinary relationship and is not this
    function's business. A ``FOR_PATIENT`` edge is settled only by a row committed for **this**
    document naming **this** patient as the one a human selected in person; a row for the same
    document naming a DIFFERENT patient settles nothing about this edge — that is the rejected
    candidate, and it is the case that made this fix Critical.
    """
    if edge.kind not in CLAIMED_IDENTITY_EDGES:
        return False, []
    document_id = _endpoint_with_kind_prefix(NodeKind.DOCUMENT, edge.src_id, edge.dst_id)
    patient_id = _endpoint_with_kind_prefix(NodeKind.PATIENT, edge.src_id, edge.dst_id)
    if not (document_id and patient_id):
        # An identity edge whose endpoints this route cannot name is not one it may vouch for.
        return True, []
    return True, commits.for_document_and_patient(document_id, patient_id)


def _edge_payload(edge: GraphEdge) -> dict:
    return {
        "id": edge.id, "kind": edge.kind.value, "src_id": edge.src_id, "dst_id": edge.dst_id,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "attrs": edge.attrs,
        "attribute_sources": {k: _pointer_dict(v) for k, v in edge.attr_sources.items()},
        "unresolved": edge.unresolved,
    }


def _incident_entry(store, edge: GraphEdge, subject_id: str, *, established: bool,
                    established_by: list[dict]) -> dict:
    """One edge incident to the node being traced, as the page will list it."""
    outgoing = edge.src_id == subject_id
    other_id = edge.dst_id if outgoing else edge.src_id
    other_row = store.open_node_row(other_id)
    payload = _edge_payload(edge)
    payload.update({
        "direction": "outgoing" if outgoing else "incoming",
        "other_id": other_id,
        "other_label": other_row["label"] if other_row is not None else None,
        "established": established,
        "established_by": [r["rowid_pk"] for r in established_by],
        "sources": [_pointer_dict(p) for p in edge.sources],
        # Only meaningful for a claim; `_candidate_phrase` is the graph's own wording, uncited
        # counts included, so the two surfaces say the same thing about the same edge.
        "candidate_phrase": None if established else _candidate_phrase(edge),
    })
    return payload


def _split_incident_edges(store, commits: _Commits, subject_id: str) -> tuple[list[dict],
                                                                             list[dict]]:
    """``(relationships, identity_claims)`` for the node being traced.

    Superseded versions are skipped: ``edges_touching`` returns every version of every edge, and
    listing a replaced belief beside its replacement with no way to tell them apart is the defect
    the node sheet had. Belief is read as of now.
    """
    relationships: list[dict] = []
    claims: list[dict] = []
    for row in store.edges_touching(subject_id):
        if row["system_to"] is not None:
            continue
        edge = row_to_edge(store, row)
        is_identity, backing = _identity_backing(commits, edge)
        if is_identity and not backing:
            claims.append(_incident_entry(store, edge, subject_id,
                                          established=False, established_by=[]))
        else:
            relationships.append(_incident_entry(store, edge, subject_id,
                                                 established=True, established_by=backing))
    return relationships, claims


# --- R-28: the authorisation root --------------------------------------------------------


def _authorise(commits: _Commits, sources: list[dict], *,
               patient_id: Optional[str]) -> Optional[dict]:
    """What authorises serving this entity's evidence, or ``None`` if nothing does.

    See the module docstring for why the boundary sits here. Two ways in, and no third:

    1. **Static configuration** — every pointer is a ``coverage_rules.yaml`` cell. No patient is
       involved, so no confirmation could exist to gate it and none is required. An entity with no
       pointers at all does NOT qualify (the graph refuses to construct one, but an empty list
       must not read as "all of them are config").
    2. **A committed ledger row** — a confirmation that names a document this entity cites, or
       that named this patient as the row a human selected in person.
    """
    if sources and all(s["ingestion_route"] == CONFIG_ROUTE and s["document_id"] is None
                       for s in sources):
        return {"basis": "static_configuration", "committed_rows": 0,
                "why": ("this entity is grounded entirely in coverage_rules.yaml, the app's own "
                        "configuration — it carries no patient data, so no confirmation gates it")}
    for source in sources:
        document_id = source["document_id"]
        rows = commits.for_document(document_id) if document_id else []
        if rows:
            return {"basis": "committed_ledger_row", "committed_rows": len(rows),
                    "why": (f"a human confirmed an intake of '{document_id}', which this entity "
                            f"cites as its source")}
    patient_rows = commits.for_patient(patient_id) if patient_id else []
    if patient_rows:
        return {"basis": "committed_ledger_row", "committed_rows": len(patient_rows),
                "why": ("a human checked this patient's identity in person, selected this "
                        "registry row and confirmed")}
    return None


def _not_yet_confirmed(entity_id: str) -> JSONResponse:
    return _refusal(
        "AWAITING_HUMAN_CONFIRMATION",
        f"'{entity_id}' exists, but no committed ledger row authorises reading its evidence. "
        f"Nothing this system reads becomes a record until a named staff member checks identity "
        f"in person, selects a registry row and confirms (CLAUDE.md §Hard constraints 1 and 4); "
        f"until then an intake is a draft and its extracted values, pointers and candidate "
        f"patients are not releasable. This is NOT 'no such entity' — it exists and is waiting "
        f"on a human.",
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
              authorisation: dict) -> dict:
    """One response shape for every entity type, so a caller never has to guess which keys exist.

    ``identity_caveat`` is populated only when something on this page IS an unsettled identity
    claim — the same rule the evidence sheet follows, and the reason it is safe for a renderer to
    treat "caveat present" as "there is a claim here that no human has settled".
    """
    claims = identity_claims or []
    return {
        "entity_id": entity_id, "entity_type": entity_type, "found": True,
        "node": node,
        "edge": edge,
        "claim": claim,
        "relationships": relationships or [],
        "identity_claims": claims,
        "identity_caveat": IDENTITY_CAVEAT if (claims or claim) else None,
        "authorisation": authorisation,
        "sources": sources,
        "ledger_entries": ledger_entries,
        "limitation": LIMITATION,
    }


def _node_response(app: FastAPI, store, commits: _Commits, entity_id: str, row) -> Any:
    node = row_to_node(store, row)
    # `row_to_node` already read the pointers for THIS version. Re-reading them by entity id was
    # the exact pooling defect this route's own notice claimed had been fixed — the guard and the
    # claim about the guard disagreeing, in one function.
    sources = _trace_sources(app, store, node.sources)
    patient_id = node.canonical_id if node.kind == NodeKind.PATIENT else None
    if node.kind == NodeKind.PATIENT and not patient_id:
        patient_id = entity_id.split(":", 1)[-1]

    authorisation = _authorise(commits, sources, patient_id=patient_id)
    if authorisation is None:
        return _not_yet_confirmed(entity_id)

    ledger_entries: list[dict] = []
    if node.kind == NodeKind.DOCUMENT:
        document_id = node.canonical_id or entity_id.split(":", 1)[-1]
        ledger_entries = [_ledger_row_to_dict(r) for r in commits.for_document(document_id)]
    elif node.kind == NodeKind.PATIENT and patient_id:
        ledger_entries = [_ledger_row_to_dict(r) for r in commits.for_patient(patient_id)]

    relationships, claims = _split_incident_edges(store, commits, node.id)
    return _envelope(
        entity_id, "node",
        node={
            "id": node.id, "kind": node.kind.value, "label": node.label,
            "canonical_id": node.canonical_id,
            "valid_from": node.valid_from.isoformat() if node.valid_from else None,
            "valid_to": node.valid_to.isoformat() if node.valid_to else None,
            "system_from": node.system_from.isoformat() if node.system_from else None,
            "system_to": node.system_to.isoformat() if node.system_to else None,
            "attrs": node.attrs,
            # Per value, so nothing downstream can present one citation as covering all of them.
            "attribute_sources": {k: _pointer_dict(v) for k, v in node.attr_sources.items()},
            "unresolved": node.unresolved,
        },
        relationships=relationships,
        identity_claims=claims,
        sources=sources,
        ledger_entries=ledger_entries,
        authorisation=authorisation)


def _edge_response(app: FastAPI, store, commits: _Commits, entity_id: str, row) -> Any:
    edge = row_to_edge(store, row)
    sources = _trace_sources(app, store, edge.sources)
    patient_id = _endpoint_with_kind_prefix(NodeKind.PATIENT, edge.src_id, edge.dst_id)

    authorisation = _authorise(commits, sources, patient_id=patient_id)
    if authorisation is None:
        return _not_yet_confirmed(entity_id)

    is_identity, backing = _identity_backing(commits, edge)
    ledger_entries = [_ledger_row_to_dict(r) for r in backing]
    if is_identity and not backing:
        # NOT an edge in this response. The whole finding is that returning one here made the
        # page assert a relationship the system is forbidden to assert, so the unsettled case gets
        # a shape of its own: `edge` is null, and what the document CLAIMS is reported as a claim.
        claim = _edge_payload(edge)
        claim.update({
            "established": False,
            "established_by": [],
            "candidate_phrase": _candidate_phrase(edge),
            "document_id": _endpoint_with_kind_prefix(NodeKind.DOCUMENT,
                                                      edge.src_id, edge.dst_id),
            "patient_id": patient_id,
        })
        return _envelope(entity_id, "identity_claim", claim=claim, sources=sources,
                         ledger_entries=[], authorisation=authorisation)

    payload = _edge_payload(edge)
    payload.update({"established": True, "established_by": [r["rowid_pk"] for r in backing]})
    return _envelope(entity_id, "edge", edge=payload, sources=sources,
                     ledger_entries=ledger_entries, authorisation=authorisation)


def _ledger_entry_response(app: FastAPI, store, ledger: EvidenceLedger, entity_id: str,
                           row: dict) -> dict:
    pointer = ledger.pointer_of(row)
    # A committed row IS an authorisation root: it exists only because a named human checked
    # identity in person, selected a registry row and confirmed. Nothing further to ask.
    return _envelope(
        entity_id, "ledger_entry",
        sources=_trace_sources(app, store, [pointer]),
        ledger_entries=[_ledger_row_to_dict(row)],
        authorisation={"basis": "committed_ledger_row", "committed_rows": 1,
                       "why": "this entity IS a committed ledger row"})


def register_evidence_routes(app: FastAPI) -> None:
    """Attach ``GET /evidence/trace/{entity_id}``.

    Registered by ``create_app()``, and also tested standalone over the real clinic seed and a
    real ``EvidenceLedger`` (``apps/gateway/tests/test_evidence_routes.py``) so the route's
    behaviour is pinned without depending on the rest of the gateway's boot order.
    """

    @app.get("/evidence/trace/{entity_id}")
    def evidence_trace(entity_id: str) -> Any:
        brain = getattr(app.state, "brain", None)
        ledger = getattr(app.state, "ledger", None)
        if brain is None or ledger is None:
            return _refusal("EVIDENCE_UNAVAILABLE",
                            "the graph store or evidence ledger is not initialised", status=503)
        store = brain.store
        commits = _Commits(app, ledger)

        # -- a committed ledger row, addressed directly: "ledger:<rowid>" ------------------
        if entity_id.startswith("ledger:"):
            raw = entity_id.split(":", 1)[1]
            if not raw.isdigit():
                return _refusal("BAD_ENTITY_ID",
                                f"'{entity_id}' is not a valid ledger row id (expected "
                                f"'ledger:<row number>')")
            rowid = int(raw)
            row = next((r for r in ledger.entries() if r["rowid_pk"] == rowid), None)
            if row is None:
                return _refusal("UNKNOWN_ENTITY", f"no ledger row '{entity_id}'", status=404)
            return _ledger_entry_response(app, store, ledger, entity_id, row)

        # -- a graph node --------------------------------------------------------------------
        node_versions = store.all_node_versions(entity_id)
        if node_versions:
            return _node_response(app, store, commits, entity_id, node_versions[-1])

        # -- a graph edge -----------------------------------------------------------------
        # No store method addresses an edge by its OWN id (edges_touching finds edges incident
        # to a node id, which is a different query) — `fetchall` is the store's own public,
        # locked read path (used the same way by `query.py` / `clinic_history.py` inside
        # `packages/graph`), reused here rather than adding a new store method for one caller.
        edge_rows = store.fetchall(
            "SELECT * FROM edges WHERE id = ? ORDER BY system_from, rowid_pk", (entity_id,))
        if edge_rows:
            return _edge_response(app, store, commits, entity_id, edge_rows[-1])

        return _refusal(
            "UNKNOWN_ENTITY",
            f"'{entity_id}' matches no graph node, graph edge, or ledger row "
            f"('ledger:<row number>')", status=404)
