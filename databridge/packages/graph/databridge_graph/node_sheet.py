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

Three properties are load-bearing, and all three were found broken by review on 2026-08-12.

**Every value carries its own citation.** The sheet used to print each attribute and then list
``node.sources`` under a *Sources* heading that claimed "every value above is traceable to one of
these". It was not true: a patient node carries name and ID pointers, and the sheet printed sex,
nationality and date of birth beneath them. One citation appearing to vouch for values it had
never seen is worse than no citation, because it survives being checked casually. Values now
render beside their own pointer, and a value without one is not rendered as a value at all — it
appears under *Unresolved*, which is the honest thing to show the model.

**Every untrusted string is encoded.** Labels, attribute values and even document ids come from
document text, which is attacker-controlled. The old escaper handled ``[]()*_``` and not newlines
or raw HTML, so a label containing a blank line and ``<!--`` could comment out everything after
it — including the identity caveat — or inject headings into the model's context. ``_text()`` is
now the only way a foreign string reaches this document.

**The ``FOR_PATIENT`` caveat is structural.** A document naming a patient is a CLAIM, resolved
against the registry into candidates a human chooses between. Those claims render in their own
section, under a caveat emitted before any untrusted text and outside every line that contains
any — so there is no string an attacker can supply that lands inside the warning or ahead of it.
Dropping it would feed the model an identity assertion the rest of the system refuses to make
(CLAUDE.md §Hard constraints 1).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Optional
from urllib.parse import quote

from databridge_core import SourcePointer

from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind

# The relation that carries an unresolved identity claim. Named once; every renderer below checks
# against this rather than matching a string, so renaming the vocabulary cannot silently drop the
# caveat — which is precisely how `ARROW_KINDS` lost every arrowhead after ADR-0009.
CLAIMED_IDENTITY_EDGES = {EdgeKind.FOR_PATIENT}

IDENTITY_CAVEAT = (
    "**CLAIMED ON THE DOCUMENT — NOT VERIFIED.** These link a document to registry candidates. "
    "Identity is checked in person by clinic staff; the system never confirms it, and nothing "
    "below may be read as a determination of who this patient is."
)

# Longest untrusted string reproduced verbatim. A label is a name, a code or a short phrase; a
# 40KB one is an attempt to push the caveat out of the model's attention, and truncating is the
# cheap defence that costs a legitimate value nothing.
MAX_TEXT = 240

# Characters that can begin a markdown construct or raw HTML in PROSE position. Escaped, never
# stripped: dropping characters silently changes the value, and a value that renders differently
# from what the source says is its own kind of fabrication. `.`, `-` and `!` are deliberately not
# here — they can only start a construct at the beginning of a line, and no untrusted string ever
# reaches the beginning of one (newlines are neutralised below, so a value cannot create a line).
_MD_ACTIVE = "\\`*_[]()<>&|~#"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_SPACE_RUN = re.compile(r"\s{2,}")
_BACKTICKS = re.compile(r"`+")


def _plain(value: object) -> str:
    """Flatten an untrusted value to one line, bounded — before any context-specific encoding.

    Newlines and control characters become single spaces, so no foreign string can open a block,
    close one, or start an HTML comment that swallows the identity caveat. Everything downstream
    can then assume it is handling a single line.
    """
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    text = _CONTROL.sub(" ", text).replace("\n", " ").replace("\r", " ")
    text = _SPACE_RUN.sub(" ", text).strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"… [truncated, {len(text)} chars]"
    return text


def _text(value: object) -> str:
    """An untrusted value in PROSE position — the only door into this document for foreign text."""
    return "".join("\\" + ch if ch in _MD_ACTIVE else ch for ch in _plain(value))


def _code(value: object) -> str:
    r"""An untrusted value in a CODE SPAN, where markdown is already inert.

    Backslash-escaping here would be worse than useless: `\_` is not an escape inside a code span,
    it is two literal characters, so an id would render wrong and no longer match the store. The
    only thing that can escape a code span is a backtick run, so the fence is widened past the
    longest run in the value — the CommonMark rule, and it leaves the value itself untouched.
    """
    text = _plain(value)
    if "`" in text:
        fence = "`" * (max(len(m) for m in _BACKTICKS.findall(text)) + 1)
        return f"{fence} {text} {fence}"
    return f"`{text}`"


def _vocab(value: object) -> str:
    """A member of a CLOSED vocabulary — a NodeKind, EdgeKind or SourcePointerKind.

    These come from `schema.py` and `provenance.py`, never from a document, so they are the one
    class of string here that is ours. Encoding them would render `for_patient` as `for\\_patient`,
    which is unreadable to a person and, worse, stops matching for anything reading these sheets.
    """
    return str(value)


def _fmt_time(value: Optional[dt.datetime]) -> str:
    return value.date().isoformat() if value else "—"


def _fmt_pointer(pointer: SourcePointer, *, inline: bool = False) -> str:
    """One pointer, rendered so a person can go and look at the thing it names.

    Every locator field is encoded: a `document_id` comes from a filename and a `derivation` can
    quote document text, so neither is trusted here just because it lives on a pointer.
    """
    kind = pointer.kind
    if kind == "doc_span":
        where = f"{_code(pointer.document_id)} chars {pointer.char_start}–{pointer.char_end}"
    elif kind == "table_cell":
        where = (f"{_code(pointer.source_id)} → {_text(pointer.table)}"
                 f"[{_text(pointer.row_id)}].{_text(pointer.column)}")
    elif kind == "ocr_bbox":
        where = (f"{_code(pointer.media_id)} page {pointer.page} "
                 f"({pointer.x0},{pointer.y0})–({pointer.x1},{pointer.y1}), "
                 f"OCR confidence {pointer.ocr_conf}")
    elif kind == "derived":
        # A derived pointer names the RULE, and keeps what the rule read. Both matter: the rule
        # alone is unfalsifiable, the inputs alone do not say what was done to them.
        where = _text(pointer.derivation or "derived")
        if pointer.derived_from:
            where += f" — from {len(pointer.derived_from)} source(s)"
    else:
        where = _code(pointer.source_id)
    if inline:
        return f"**{_vocab(kind)}** {where}"
    line = f"- **{_vocab(kind)}** — {where}"
    if pointer.child is not None:
        # A chained citation: extracted text → the region of the original it was read from.
        line += f"\n  - ↳ {_fmt_pointer(pointer.child).lstrip('- ')}"
    return line


def _fmt_attrs(attrs: dict, attr_sources: dict) -> list[str]:
    """Attributes that have a value AND a pointer. Anything else belongs under *Unresolved*."""
    lines = []
    for key in sorted(attrs):
        value = attrs[key]
        pointer = attr_sources.get(key)
        if value is None or pointer is None:
            continue
        lines.append(f"- {_code(key)}: {_text(value)}\n"
                     f"  - cited: {_fmt_pointer(pointer, inline=True)}")
    return lines or ["_No grounded attributes recorded._"]


def _fmt_unresolved(attrs: dict, attr_sources: dict) -> list[str]:
    """Every attribute the graph has no answer for, named rather than omitted.

    An absent field that simply does not appear reads as "not applicable". Named, it reads as
    "asked, and not found" — which is what it is, and what a human needs in order to go and look.
    """
    missing = [k for k in sorted(attrs)
               if attrs[k] is None or attr_sources.get(k) is None]
    return [f"- {_code(k)}: `None`" for k in missing]


def _wikilink(label: str, entity_id: str) -> str:
    """A navigable reference to another node.

    Mirrors the frontend's existing convention (`markdown-panel.tsx`): an ENCODED label as the
    link text and a URL-ENCODED id as the href, so neither a label containing markdown nor a
    forged href can do anything — the renderer decodes the id and navigates only to that exact
    node. Labels here come from document text, which is attacker-controlled in the real world.
    """
    return f"[{_text(label)}](#wiki:{quote(entity_id, safe='')})"


def _relation_line(edge: GraphEdge, other_label: str, other_id: str, outgoing: bool) -> str:
    """One relationship, with the pointers that ground *this edge* — not the node's.

    Relationship lines used to print a date and a candidate count and cite neither, beside a
    Sources list belonging to the subject node. An edge is a claim in its own right and carries
    its own evidence; this renders that evidence next to the claim it supports.
    """
    arrow = "→" if outgoing else "←"
    line = f"- {arrow} **{_vocab(edge.kind.value)}** {_wikilink(other_label, other_id)} " \
           f"({_code(other_id)})"
    if edge.valid_from:
        line += f" — since {_fmt_time(edge.valid_from)}"
    for key in sorted(edge.attrs):
        value, pointer = edge.attrs[key], edge.attr_sources.get(key)
        if value is None or pointer is None:
            line += f"\n  - {_code(key)}: `None` — not recorded"
        else:
            line += f"\n  - {_code(key)}: {_text(value)} — cited: {_fmt_pointer(pointer, inline=True)}"
    for pointer in edge.sources:
        line += f"\n  - evidence for this relationship: {_fmt_pointer(pointer, inline=True)}"
    return line


def _candidate_phrase(edge: GraphEdge) -> str:
    """How many candidates this claim is one of.

    An UNKNOWN count must produce the MORE cautious wording, never the less. The eligibility path
    had the inverse bug: a missing count read as falsy and emitted the stronger, unqualified
    phrasing for a link that was never resolved against the registry. A count present but uncited
    is treated as unknown — an identity figure nobody can check does not get to make the sheet
    sound more certain.
    """
    count = edge.attrs.get("candidate_count")
    # Edge attributes are an untyped dict, so "the candidate count" can be any JSON value that
    # was stored under that key — including a string carrying newlines, a heading or model
    # instructions. It was interpolated straight into the identity block, which is the one place
    # in this document where injected text would be read as part of the caveat. It must be a
    # positive integer to be believed at all, and it is encoded regardless.
    if (isinstance(count, int) and not isinstance(count, bool) and count > 0
            and edge.attr_sources.get("candidate_count") is not None):
        return f"one of {_text(count)} candidate(s)"
    return "one of an unrecorded number of candidates"


def _node_at(store, entity_id: str,
             as_of: Optional[dt.datetime]) -> tuple[Optional[GraphNode], bool]:
    """The version of ``entity_id`` valid at ``as_of``, and whether that is a superseded belief.

    ``as_of`` used to filter edges only, so rewinding the sheet still showed the subject's and
    every endpoint's CURRENT label and attributes: a replay that leaks the present is not a
    replay.

    Preferring the open version is right, but *only* preferring it opened a hole. `replace_node`
    closes the old version on BOTH axes, so after a correction the interval the old version
    covered has no open row at all: a rewind to March finds v1 system-closed and v2 not yet
    valid, and answers `None` — "there was no such entity then", which is false and is the
    strongest thing this function can say. The honest answer is the superseded record, returned
    with a flag so the sheet can label it as a belief we no longer hold rather than passing it
    off as current.
    """
    from .store import row_to_node

    if as_of is None:
        row = store.open_node_row(entity_id)
        return (row_to_node(store, row) if row is not None else None), False

    def covers(row) -> bool:
        valid_from, valid_to = _parse(row["valid_from"]), _parse(row["valid_to"])
        if valid_from is not None and valid_from > as_of:
            return False
        return not (valid_to is not None and valid_to <= as_of)

    versions = store.all_node_versions(entity_id)
    for row in reversed(versions):
        if row["system_to"] is None and covers(row):
            return row_to_node(store, row), False
    # No belief we still hold covers that instant. Fall back to the most recently superseded
    # record that does — latest system_from wins, because that was our last word on the matter.
    superseded = [r for r in versions if r["system_to"] is not None and covers(r)]
    if superseded:
        best = max(superseded, key=lambda r: (r["system_from"], r["rowid_pk"]))
        return row_to_node(store, best), True
    return None, False


def node_markdown(store,
                  node_id: str,
                  *,
                  as_of: Optional[dt.datetime] = None) -> Optional[str]:
    """The evidence sheet for ``node_id``, or ``None`` if no such node existed at ``as_of``.

    ``as_of`` rewinds the whole sheet — subject, attributes, relationships and endpoint labels —
    so it answers "what did we know about this entity on that day?" rather than showing today's
    node wearing that day's edges.
    """
    node, superseded = _node_at(store, node_id, as_of)
    if node is None:
        return None

    lines: list[str] = [
        f"# {_text(node.label)}",
        "",
        f"**Kind:** {_code(node.kind.value)}  ·  **Id:** {_code(node.id)}",
    ]
    if node.canonical_id:
        lines.append(f"**Canonical id:** {_code(node.canonical_id)}")
    lines.append("_Title, id and canonical id are grounded by the entity citations at the foot of "
                 "this sheet. Everything else carries its own citation inline._")
    if as_of is not None:
        lines.append(f"**Shown as of:** {_fmt_time(as_of)} (valid time; belief as held today)")
    if superseded:
        lines.append(
            "**This is a SUPERSEDED record.** A later correction replaced it, so nothing here is "
            "a belief the clinic currently holds. It is shown because it is what was on file at "
            "the instant requested.")
    lines += [
        f"**Known from:** {_fmt_time(node.valid_from)}"
        + (f" until {_fmt_time(node.valid_to)}" if node.valid_to else " (still current)"),
        "",
        "## Attributes",
        "_Each value is followed by the citation for that value alone._",
        "",
        *_fmt_attrs(node.attrs, node.attr_sources),
    ]

    unresolved = _fmt_unresolved(node.attrs, node.attr_sources)
    if unresolved:
        lines += [
            "",
            "## Unresolved",
            "_Asked for and not found in any source. Never guessed, inferred or defaulted._",
            "",
            *unresolved,
        ]

    claims: list[str] = []
    relations: list[str] = []
    for edge_row in store.edges_touching(node.id):
        if edge_row["system_to"] is not None:
            # A SUPERSEDED belief, not a relationship. `edges_touching` returns every version of
            # every edge, so without this the sheet listed an edge twice — once as it used to be
            # and once as it is — and the reader had no way to tell which was which. Belief is
            # read as of now; `as_of` below rewinds valid time, which is the other axis.
            continue
        edge = _edge_from_row(store, edge_row)
        if as_of is not None:
            start, end = edge.valid_from, edge.valid_to
            if (start and start > as_of) or (end and end <= as_of):
                continue
        outgoing = edge.src_id == node.id
        other_id = edge.dst_id if outgoing else edge.src_id
        other, _ = _node_at(store, other_id, as_of)
        if other is None:
            # The endpoint did not exist at this instant. Rendering its current label here is
            # exactly the leak `as_of` is supposed to prevent, so the id stands alone.
            label = other_id
        else:
            label = other.label
        line = _relation_line(edge, label, other_id, outgoing)
        (claims if edge.kind in CLAIMED_IDENTITY_EDGES else relations).append(
            line + (f"\n  - {_candidate_phrase(edge)}"
                    if edge.kind in CLAIMED_IDENTITY_EDGES else ""))

    if claims:
        # A SEPARATE, TRUSTED BLOCK. The caveat is emitted before any untrusted text and is not
        # part of any line that contains some, so no supplied label, attribute or id can sit
        # inside it, precede it, or comment it out.
        lines += ["", "## Claimed identity — NOT VERIFIED", "", f"> {IDENTITY_CAVEAT}", ""]
        lines += claims

    lines += ["", "## Relationships"]
    lines += relations or ["_No relationships recorded at this point in time._"]

    lines += [
        "",
        "## Entity citations",
        f"_{len(node.sources)} citation(s) for this node's IDENTITY — that it exists, and that it "
        f"is the thing named in the title. They ground the title, the id and the canonical id, "
        f"and NOT the attributes or relationships above: each of those carries its own._",
        "",
    ]
    lines += [_fmt_pointer(p) for p in node.sources] or ["_None._"]
    return "\n".join(lines)


def _edge_from_row(store, row) -> GraphEdge:
    # Pointers for THIS version of the edge. Reading them unscoped puts a superseded claim's
    # evidence beside the current one — on a FOR_PATIENT edge that means a citation for a
    # candidate the resolver no longer returns, under the identity caveat.
    version = row["rowid_pk"]
    return GraphEdge(
        id=row["id"], src_id=row["src_id"], dst_id=row["dst_id"],
        kind=EdgeKind(row["kind"]), weight=row["weight"],
        attrs=json.loads(row["attrs_json"] or "{}"),
        attr_sources=store.attr_sources_for(row["id"], version_pk=version),
        sources=store.sources_for(row["id"], version_pk=version),
        valid_from=_parse(row["valid_from"]), valid_to=_parse(row["valid_to"]),
        system_from=_parse(row["system_from"]))


def _parse(value) -> Optional[dt.datetime]:
    if not value:
        return None
    return dt.datetime.fromisoformat(value) if isinstance(value, str) else value
