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

**Known limitation, shown on the page, not hidden here:** ``sources_for()`` is keyed by entity id,
not by bi-temporal version, so the pointer list returned for a node/edge is EVERY pointer ever
attached to that id — including ones a later ``as_of`` snapshot would not yet have known. This
route does not attempt to filter by version; it says so in every response (``"limitation"``).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from databridge_core import SourcePointer
from databridge_graph import NodeKind, node_id, row_to_edge, row_to_node
from databridge_intake.ledger import EvidenceLedger

LIMITATION = (
    "sources_for() is keyed by entity id, not by bi-temporal version: the pointer list below is "
    "EVERY SourcePointer ever attached to this id, including ones learned after an earlier "
    "as_of snapshot. An as_of query against a past moment can therefore surface pointers that "
    "moment did not actually know about yet."
)

# The closed vocabulary this route reports for `ingestion_route`. Never widened by a guess —
# `_ingestion_route` only ever returns one of these, or "unknown".
_KNOWN_DOCUMENT_KINDS = {NodeKind.DOCUMENT}


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
            return "config", None
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


def _intake_ids_for_document(app: FastAPI, document_id: str) -> list[str]:
    intakes = getattr(app.state, "intakes", None) or {}
    out = []
    for intake_id, held in intakes.items():
        draft = held.get("draft") if isinstance(held, dict) else None
        ref = getattr(getattr(draft, "record", None), "document", None)
        if ref is not None and ref.document_id == document_id:
            out.append(intake_id)
    return out


def _ledger_rows_for_document(app: FastAPI, ledger: EvidenceLedger, document_id: str) -> list[dict]:
    rows: list[dict] = []
    for intake_id in _intake_ids_for_document(app, document_id):
        rows.extend(ledger.entries(intake_id))
    return [_ledger_row_to_dict(r) for r in rows]


def _ledger_rows_for_patient(ledger: EvidenceLedger, patient_id: str) -> list[dict]:
    return [_ledger_row_to_dict(r) for r in ledger.entries()
            if r.get("selected_patient_id") == patient_id]


def _ledger_rows_for_document_and_patient(app: FastAPI, ledger: EvidenceLedger,
                                          document_id: str, patient_id: str) -> list[dict]:
    rows: list[dict] = []
    for intake_id in _intake_ids_for_document(app, document_id):
        rows.extend(r for r in ledger.entries(intake_id)
                    if r.get("selected_patient_id") == patient_id)
    return [_ledger_row_to_dict(r) for r in rows]


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


def _node_response(app: FastAPI, store, ledger: EvidenceLedger, entity_id: str, row) -> dict:
    node = row_to_node(store, row)
    pointers = store.sources_for(entity_id)
    ledger_entries: list[dict] = []
    if node.kind == NodeKind.DOCUMENT:
        document_id = node.canonical_id or entity_id.split(":", 1)[-1]
        ledger_entries = _ledger_rows_for_document(app, ledger, document_id)
    elif node.kind == NodeKind.PATIENT:
        patient_id = node.canonical_id or entity_id.split(":", 1)[-1]
        ledger_entries = _ledger_rows_for_patient(ledger, patient_id)
    return {
        "entity_id": entity_id, "entity_type": "node", "found": True,
        "node": {
            "id": node.id, "kind": node.kind.value, "label": node.label,
            "canonical_id": node.canonical_id,
            "valid_from": node.valid_from.isoformat() if node.valid_from else None,
            "valid_to": node.valid_to.isoformat() if node.valid_to else None,
            "system_from": node.system_from.isoformat() if node.system_from else None,
            "system_to": node.system_to.isoformat() if node.system_to else None,
            "attrs": node.attrs,
        },
        "edge": None,
        "sources": _trace_sources(app, store, pointers),
        "ledger_entries": ledger_entries,
        "limitation": LIMITATION,
    }


def _edge_response(app: FastAPI, store, ledger: EvidenceLedger, entity_id: str, row) -> dict:
    edge = row_to_edge(store, row)
    pointers = store.sources_for(entity_id)
    document_id = _endpoint_with_kind_prefix(NodeKind.DOCUMENT, edge.src_id, edge.dst_id)
    patient_id = _endpoint_with_kind_prefix(NodeKind.PATIENT, edge.src_id, edge.dst_id)
    ledger_entries: list[dict] = []
    if document_id and patient_id:
        # e.g. a FOR_PATIENT edge: a candidate is a CLAIM, not a determination (CLAUDE.md
        # Hard constraint 1) — these rows exist only if a human later attested and selected
        # exactly this patient for exactly this document.
        ledger_entries = _ledger_rows_for_document_and_patient(app, ledger, document_id, patient_id)
    return {
        "entity_id": entity_id, "entity_type": "edge", "found": True,
        "node": None,
        "edge": {
            "id": edge.id, "kind": edge.kind.value, "src_id": edge.src_id, "dst_id": edge.dst_id,
            "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
            "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
            "attrs": edge.attrs,
        },
        "sources": _trace_sources(app, store, pointers),
        "ledger_entries": ledger_entries,
        "limitation": LIMITATION,
    }


def _ledger_entry_response(app: FastAPI, store, ledger: EvidenceLedger, entity_id: str,
                           row: dict) -> dict:
    pointer = ledger.pointer_of(row)
    return {
        "entity_id": entity_id, "entity_type": "ledger_entry", "found": True,
        "node": None, "edge": None,
        "sources": _trace_sources(app, store, [pointer]),
        "ledger_entries": [_ledger_row_to_dict(row)],
        "limitation": LIMITATION,
    }


def register_evidence_routes(app: FastAPI) -> None:
    """Attach ``GET /evidence/trace/{entity_id}``.

    NOT called by ``create_app()`` — this module is owned and tested standalone (see
    ``apps/gateway/tests/test_evidence_routes.py``). Wiring it into the running gateway is a
    one-line addition to ``main.py``: ``register_evidence_routes(app)`` alongside
    ``_register_intake_routes(app)``.
    """

    @app.get("/evidence/trace/{entity_id}")
    def evidence_trace(entity_id: str) -> Any:
        brain = getattr(app.state, "brain", None)
        ledger = getattr(app.state, "ledger", None)
        if brain is None or ledger is None:
            return _refusal("EVIDENCE_UNAVAILABLE",
                            "the graph store or evidence ledger is not initialised", status=503)
        store = brain.store

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
            return _node_response(app, store, ledger, entity_id, node_versions[-1])

        # -- a graph edge -----------------------------------------------------------------
        # No store method addresses an edge by its OWN id (edges_touching finds edges incident
        # to a node id, which is a different query) — `fetchall` is the store's own public,
        # locked read path (used the same way by `query.py` / `clinic_history.py` inside
        # `packages/graph`), reused here rather than adding a new store method for one caller.
        edge_rows = store.fetchall(
            "SELECT * FROM edges WHERE id = ? ORDER BY system_from, rowid_pk", (entity_id,))
        if edge_rows:
            return _edge_response(app, store, ledger, entity_id, edge_rows[-1])

        return _refusal(
            "UNKNOWN_ENTITY",
            f"'{entity_id}' matches no graph node, graph edge, or ledger row "
            f"('ledger:<row number>')", status=404)
