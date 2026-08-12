"""A node's **evidence sheet** — everything the graph knows about one entity, as markdown.

Two audiences, one document, and the second is the reason this exists:

1. **A person.** Clicking a node on `/brain` currently shows a small inspector. A dot on a canvas
   cannot be audited; a document can. The sheet is what the clinic actually knows about this
   entity, including where each piece of it came from.

2. **The model.** ``build_context()`` serialises a neighbourhood ad hoc. Composing these sheets
   instead gives the LLM a better-shaped grounding unit — the same text a human would read, so an
   answer can be checked against the evidence a person was shown rather than against a private
   serialisation. Retrieval-then-generate is only trustworthy if the retrieved thing is legible.

**Nothing here is generated.** Every line is assembled from stored values, stored pointers and
stored edges. There is no model in this path and no prose the graph cannot support — a sheet that
narrated would be a fabrication surface pointed straight at the LLM's context window
(CLAUDE.md §Hard constraints 2).

**The `FOR_PATIENT` caveat is load-bearing.** A document naming a patient is a CLAIM, resolved
against the registry into candidates that a human chooses between. Any rendering of that edge must
carry the caveat, and `PLAN.md` §6.6 flags exactly this: a patient reached at hop 2 needs the same
annotation the 1-hop path adds. Dropping it here would feed the model an identity assertion the
rest of the system refuses to make (CLAUDE.md §Hard constraints 1).
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional
from urllib.parse import quote

from databridge_core import SourcePointer

from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind

# The relation that carries an unresolved identity claim. Named once; every renderer below checks
# against this rather than matching a string, so renaming the vocabulary cannot silently drop the
# caveat — which is precisely how `ARROW_KINDS` lost every arrowhead after ADR-0009.
CLAIMED_IDENTITY_EDGES = {EdgeKind.FOR_PATIENT}

IDENTITY_CAVEAT = (
    "**CLAIMED ON THE DOCUMENT — NOT VERIFIED.** This links a document to a registry candidate. "
    "Identity is checked in person by clinic staff; the system never confirms it."
)


def _fmt_time(value: Optional[dt.datetime]) -> str:
    return value.date().isoformat() if value else "—"


def _fmt_pointer(pointer: SourcePointer) -> str:
    """One pointer, rendered so a person can go and look at the thing it names.

    The raw object is included too. "Showcase the pointer" means a reader can check the citation,
    not take our word for its shape.
    """
    kind = pointer.kind
    if kind == "doc_span":
        where = f"`{pointer.document_id}` chars {pointer.char_start}–{pointer.char_end}"
    elif kind == "table_cell":
        where = f"`{pointer.source_id}` → {pointer.table}[{pointer.row_id}].{pointer.column}"
    elif kind == "ocr_bbox":
        where = (f"`{pointer.media_id}` page {pointer.page} "
                 f"({pointer.x0},{pointer.y0})–({pointer.x1},{pointer.y1}), "
                 f"OCR confidence {pointer.ocr_conf}")
    elif kind == "derived":
        where = pointer.derivation or "derived"
    else:
        where = pointer.source_id
    line = f"- **{kind}** — {where}"
    if pointer.child is not None:
        # A chained citation: extracted text → the region of the original it was read from.
        line += f"\n  - ↳ {_fmt_pointer(pointer.child).lstrip('- ')}"
    return line


def _fmt_attrs(attrs: dict) -> list[str]:
    if not attrs:
        return ["_No attributes recorded._"]
    out = []
    for key in sorted(attrs):
        value = attrs[key]
        rendered = json.dumps(value, default=str) if isinstance(value, (dict, list)) else value
        out.append(f"- `{key}`: {rendered}")
    return out


def _wikilink(label: str, entity_id: str) -> str:
    """A navigable reference to another node.

    Mirrors the frontend's existing convention (`markdown-panel.tsx`): an ESCAPED label as the link
    text and a URL-ENCODED id as the href, so neither a label containing markdown nor a forged href
    can do anything — the renderer decodes the id and navigates only to that exact node. Labels
    here come from document text, which is attacker-controlled in the real world.
    """
    safe = label.replace("\\", "\\\\")
    for ch in "[]()*_`":
        safe = safe.replace(ch, "\\" + ch)
    return f"[{safe}](#wiki:{quote(entity_id, safe='')})"


def _relation_line(edge: GraphEdge, other_label: str, other_id: str, outgoing: bool) -> str:
    arrow = "→" if outgoing else "←"
    line = f"- {arrow} **{edge.kind.value}** {_wikilink(other_label, other_id)} (`{other_id}`)"
    if edge.valid_from:
        line += f" — since {_fmt_time(edge.valid_from)}"
    if edge.kind in CLAIMED_IDENTITY_EDGES:
        count = edge.attrs.get("candidate_count")
        # An UNKNOWN candidate count must produce the MORE cautious wording, never the less. The
        # eligibility path had the inverse bug: a missing count read as falsy and emitted the
        # stronger, unqualified phrasing for a link that was never resolved against the registry.
        of_n = f"1 of {count} candidate(s)" if count else "one of an unrecorded number of candidates"
        line += f"\n  - {IDENTITY_CAVEAT} ({of_n})"
    return line


def node_markdown(store,
                  node_id: str,
                  *,
                  as_of: Optional[dt.datetime] = None) -> Optional[str]:
    """The evidence sheet for ``node_id``, or ``None`` if no such node is open.

    ``as_of`` filters the relationships to those valid at that instant, so the sheet answers "what
    did we know about this entity on that day?" — the same question the time slider asks, in prose.
    """
    from .store import row_to_node

    row = store.open_node_row(node_id)
    if row is None:
        return None
    node: GraphNode = row_to_node(store, row)

    lines: list[str] = [
        f"# {node.label}",
        "",
        f"**Kind:** `{node.kind.value}`  ·  **Id:** `{node.id}`",
    ]
    if node.canonical_id:
        lines.append(f"**Canonical id:** `{node.canonical_id}`")
    lines += [
        f"**Known from:** {_fmt_time(node.valid_from)}"
        + (f" until {_fmt_time(node.valid_to)}" if node.valid_to else " (still current)"),
        "",
        "## Attributes",
        *_fmt_attrs(node.attrs),
        "",
        "## Relationships",
    ]

    edges = [row for row in store.edges_touching(node.id)]
    rendered: list[str] = []
    labels = {}
    for edge_row in edges:
        for endpoint in (edge_row["src_id"], edge_row["dst_id"]):
            if endpoint not in labels:
                other = store.open_node_row(endpoint)
                labels[endpoint] = other["label"] if other is not None else endpoint

    for edge_row in edges:
        edge = GraphEdge(
            id=edge_row["id"], src_id=edge_row["src_id"], dst_id=edge_row["dst_id"],
            kind=EdgeKind(edge_row["kind"]), weight=edge_row["weight"],
            attrs=json.loads(edge_row["attrs_json"] or "{}"),
            sources=store.sources_for(edge_row["id"]),
            valid_from=_parse(edge_row["valid_from"]), valid_to=_parse(edge_row["valid_to"]),
            system_from=_parse(edge_row["system_from"]))
        if as_of is not None:
            start = edge.valid_from
            end = edge.valid_to
            if (start and start > as_of) or (end and end <= as_of):
                continue
        outgoing = edge.src_id == node.id
        other_id = edge.dst_id if outgoing else edge.src_id
        rendered.append(_relation_line(edge, labels.get(other_id, other_id), other_id, outgoing))

    lines += rendered or ["_No relationships recorded at this point in time._"]

    sources = node.sources
    lines += [
        "",
        "## Sources",
        f"_{len(sources)} citation(s). Every value above is traceable to one of these._",
        "",
    ]
    lines += [_fmt_pointer(p) for p in sources] or ["_None._"]
    return "\n".join(lines)


def _parse(value) -> Optional[dt.datetime]:
    if not value:
        return None
    return dt.datetime.fromisoformat(value) if isinstance(value, str) else value
