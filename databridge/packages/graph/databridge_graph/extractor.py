"""IR → graph extraction (T08 Part 1; 04_PRD §5.3 — the LLM schema-constrained path).

Takes any IR already produced by C12/C13/C14 (``DocumentRecord``; email is a DocumentRecord with
``email_part`` block provenance) and runs C15-style schema-constrained extraction to pull typed
**entities + relations** (closed vocab, ADR-0008) into the bi-temporal graph.

Anti-fabrication wall (04_PRD §5.3, the same shape as C15's grounding wall):
  * the model extracts ONLY closed-vocab kinds — an unknown kind is SKIPPED, never coined (P3);
  * every entity MUST carry character offsets whose text CONTAINS the entity name (fuzzy ≥0.9);
    an entity whose name does not appear in the source is REFUSED (it was invented);
  * a relation must connect two grounded entities and carry evidence offsets, else REFUSED.
Each surviving entity/relation gets a ``doc_span`` (or the block's intrinsic) SourcePointer, so
nothing ungrounded can reach the store.

The model call goes through the Harness (``task_class="entity_extraction"``). In CI it is driven
by a FakeBackend gold handler (``gold_extraction_handler``) — the deterministic plumbing proof;
the accuracy number on a real model is the separate EV-BRAIN run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from pydantic import BaseModel, Field

from databridge_core import SourcePointer
from databridge_harness import Harness

from .merge import upsert_edge, upsert_node
from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind, node_id
from .store import GraphStore

# closed-vocab string → enum (anything not here is SKIPPED — never invented)
_NODE_KINDS = {k.value: k for k in NodeKind}
_EDGE_KINDS = {k.value: k for k in EdgeKind}
_NAME_MATCH = 0.9          # fuzzy floor: the span text must contain the entity name


# --- structured extraction contract (what the model returns) ----------------------
class ExtractedEntity(BaseModel):
    name: str
    kind: str                              # must be a NodeKind value, else skipped
    char_start: int
    char_end: int
    canonical_id: Optional[str] = None     # email / domain when the model can supply one


class ExtractedRelation(BaseModel):
    src_name: str
    rel_type: str                          # must be an EdgeKind value, else skipped
    dst_name: str
    evidence_char_start: int
    evidence_char_end: int


class ExtractionDraft(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


@dataclass
class GraphExtractionResult:
    entities: list[GraphNode] = field(default_factory=list)
    relations: list[GraphEdge] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)        # human-readable refusal reasons
    upserts: list = field(default_factory=list)             # UpsertOutcome when a store is given


def doc_text(doc) -> str:
    """Flatten a DocumentRecord's blocks into one text the offsets index into."""
    return "\n".join(b.text for b in doc.blocks)


def _wordch(c: str) -> bool:
    return c.isalnum() or c == "_"


def _boundary_find(text: str, name: str) -> Optional[tuple[int, int]]:
    """Locate ``name`` in ``text`` as a whole token (word boundaries), case-insensitive. Returns
    None if the name does not appear as an identity — NOT as a substring of a larger word. This is
    the C15/C17 lesson: never ground by containment ('man' must not ride inside 'management')."""
    m = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
    return (m.start(), m.end()) if m else None


def _span_grounds(text: str, name: str, start: int, end: int) -> Optional[tuple[int, int]]:
    """Return the (start, end) that grounds ``name`` in ``text``, or None if it is not present.

    Boundary-aware (no substring rides) and length-guarded (a 1-char "name" is never an entity).
    Tolerant of model offset drift — the value must be REAL, the offset need not be exact: trust the
    model's span only when it sits on FULL-TEXT word boundaries and equals the name (or fuzzy-matches
    at comparable length); else relocate to the first whole-token occurrence. None ⇒ not present ⇒
    fabrication ⇒ refuse. Boundaries are checked against the FULL text (never the isolated slice,
    which would fake a boundary for 'man' carved out of 'management')."""
    name = name.strip()
    if len(name) < 2:                                  # a 1-char token is not a groundable entity
        return None
    if 0 <= start < end <= len(text):
        before_ok = start == 0 or not _wordch(text[start - 1])
        after_ok = end == len(text) or not _wordch(text[end])
        if before_ok and after_ok:
            span = text[start:end]
            if span.lower() == name.lower():
                return start, end
            if len(span) <= 2 * len(name) + 4 and \
                    SequenceMatcher(None, span.lower(), name.lower()).ratio() >= _NAME_MATCH:
                return start, end
    return _boundary_find(text, name)


def _relation_evidence(text: str, src_name: str, dst_name: str):
    """The smallest span containing a whole-token occurrence of BOTH endpoint names → (start, end),
    or (None, None) if they do not co-occur. A relation whose endpoints never appear together in
    the source is unsupported (the wall refuses it) — no whole-document fallback that would ground
    a fabricated edge to 'everything'."""
    a = _boundary_find(text, src_name)
    b = _boundary_find(text, dst_name)
    if a is None or b is None:
        return None, None
    return min(a[0], b[0]), max(a[1], b[1])


def extract_graph(doc, harness: Harness, *, store: Optional[GraphStore] = None,
                  valid_from=None, system_from=None, seed: int = 7) -> GraphExtractionResult:
    """Extract a grounded subgraph from ``doc`` and (optionally) upsert it into ``store``."""
    text = doc_text(doc)
    sys_msg = {
        "role": "system",
        "content": (
            "Extract a knowledge graph from the document. Return entities and relations.\n"
            f"Entity kinds (use ONLY these): {sorted(_NODE_KINDS)}.\n"
            f"Relation types (use ONLY these): {sorted(_EDGE_KINDS)}.\n"
            "Every entity MUST include char_start/char_end offsets into the document text where its "
            "name appears. Every relation MUST include evidence offsets. Skip anything you cannot "
            "ground in the text — never invent a type or a fact. Do NOT resolve pronouns."),
    }
    user_msg = {"role": "user", "content": f"DOCUMENT TEXT:\n{text}"}
    result = harness.complete_structured(
        "entity_extraction", [sys_msg, user_msg], ExtractionDraft, seed=seed,
        source_id=doc.document_id)
    draft: ExtractionDraft = result.parsed

    vf = valid_from or doc.snapshot.as_of
    sf = system_from or (store.now() if store is not None else doc.snapshot.as_of)
    out = GraphExtractionResult()

    # --- entities: closed-vocab + grounding wall --------------------------------
    name_to_node: dict[str, GraphNode] = {}
    for ent in draft.entities:
        kind = _NODE_KINDS.get(ent.kind)
        if kind is None:
            out.refused.append(f"entity {ent.name!r}: unknown kind {ent.kind!r} (skipped)")
            continue
        grounded = _span_grounds(text, ent.name, ent.char_start, ent.char_end)
        if grounded is None:
            out.refused.append(f"entity {ent.name!r}: name not found in source (REFUSED — fabrication)")
            continue
        gs, ge = grounded
        ptr = SourcePointer(source_id=doc.document_id, kind="doc_span",
                            document_id=doc.document_id, char_start=gs, char_end=ge)
        slug = (ent.canonical_id or ent.name).lower()
        node = GraphNode(id=node_id(kind, slug), kind=kind, label=ent.name,
                         canonical_id=ent.canonical_id, sources=[ptr],
                         valid_from=vf, system_from=sf)
        out.entities.append(node)
        name_to_node[ent.name.lower()] = node

    # --- relations: must connect two grounded entities + carry evidence ---------
    for rel in draft.relations:
        ekind = _EDGE_KINDS.get(rel.rel_type)
        if ekind is None:
            out.refused.append(f"relation {rel.rel_type!r}: unknown type (skipped)")
            continue
        src = name_to_node.get(rel.src_name.lower())
        dst = name_to_node.get(rel.dst_name.lower())
        if src is None or dst is None:
            out.refused.append(
                f"relation {rel.src_name!r} -{rel.rel_type}-> {rel.dst_name!r}: endpoint not a "
                f"grounded entity (REFUSED)")
            continue
        # the evidence span must REALLY contain both endpoint names (co-occurrence), not the model's
        # self-asserted offsets and never a whole-doc fallback (that pointer would localise nothing).
        es, ee = _relation_evidence(text, rel.src_name, rel.dst_name)
        if es is None:
            out.refused.append(
                f"relation {rel.src_name!r} -{rel.rel_type}-> {rel.dst_name!r}: endpoints do not "
                f"co-occur in the source (REFUSED — unsupported relation)")
            continue
        ptr = SourcePointer(source_id=doc.document_id, kind="doc_span",
                            document_id=doc.document_id, char_start=es, char_end=ee)
        edge = GraphEdge(id=f"{src.id}|{ekind.value}|{dst.id}", src_id=src.id, dst_id=dst.id,
                         kind=ekind, sources=[ptr], valid_from=vf, system_from=sf)
        out.relations.append(edge)

    if store is not None:
        for node in out.entities:
            out.upserts.append(upsert_node(store, node))
        for edge in out.relations:
            out.upserts.append(upsert_edge(store, edge))
    return out


# --- deterministic gold handler for CI (no live model) ----------------------------
def gold_extraction_handler(entities: list[dict], relations: list[dict]):
    """Build a FakeBackend handler returning a fixed ExtractionDraft — the EV-BRAIN extraction
    plumbing proof (accuracy on a real model is a separate run, mirroring e1.gold_backend)."""
    payload = ExtractionDraft(entities=[ExtractedEntity(**e) for e in entities],
                              relations=[ExtractedRelation(**r) for r in relations]).model_dump_json()

    def handler(*, model, messages, guided_json) -> str:
        return payload

    return handler
