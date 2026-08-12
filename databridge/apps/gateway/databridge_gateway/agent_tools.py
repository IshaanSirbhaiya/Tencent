"""The tool surface `POST /intelligence/ask` hands the agent — sealed, read-only, structural.

`packages/agent` imports no graph on purpose: it declares the interface and the composition root
implements it (the same inversion `ClinicHistory` uses). This module is that composition root for
the `/intelligence` page, and everything it decides is a decision about **what the agent can
reach**, which is the only kind of decision that survives a fully suborned model.

## Three tools, one capability, no arguments

Every adapter here carries `ClinicStructuralRead` — counts and structure across the clinic, naming
no patient. `CandidateRead` and `PatientScopedRead` are **deliberately absent**:

* a `CandidateRead` tool would put registry candidates on a business-intelligence page, and the
  page's contract (`intelligence_routes.py`, `app/intelligence/page.tsx`) is that it reports
  identity work and never does any;
* a `PatientScopedRead` needs a `PatientReadGrant`, and a grant is *an assertion made by the code
  that constructed it* (`sealed.py`). Constructing one honestly requires an authenticated staff
  session, and this system has none — `staff_id` has no auth behind it (`STATUS.md` §Accepted
  limitations). A grant minted here would be this process asserting that a human it cannot
  identify attested to something it did not witness.

And **no tool takes an argument at all.** That is stronger than screening the arguments: a schema
with no properties, closed at every level, has nothing for an injected document to fill in. The
model's whole vocabulary here is *which of three reads to make, and which returned values to
report* — `policy.validate_arguments` refuses everything else before the adapter is entered.

## One pointer per value, and every pointer names a graph entity

`ToolRow` enforces the per-value wall (a value without its own `SourcePointer` is refused at
construction). What this module adds is a convention the page depends on:

> every pointer emitted here is `kind="derived"`, its `source_id` is a **graph entity id**, its
> `derivation` names the rule that produced the value, and its `derived_from` carries the entity
> pointers actually read.

`source_id` being an entity id is what lets a citation in the answer link to
`GET /intelligence/subgraph/{entity_id}` — the drill-down that refuses rather than showing values
whose evidence cannot be loaded. A citation you cannot walk to is indistinguishable from a
literal, which is the thing this whole surface exists not to be.

**What a `derived` pointer here does and does not establish** (`REMEDIATION.md` R-32, stated
rather than papered over): it establishes that the named entity exists in the graph, that these
pointers are what grounded it, and that this named rule was run. It does **not** carry every input
the rule consumed — the counting rule reads the edge table, and an edge's own pointers are not
included. It is provenance for the entity and the rule, not a re-executable proof of the number.

**Labels have no pointer of their own** (R-31). A node's `label` is grounded only by the pointers
that established the node, and the derivation string on every label value says exactly that rather
than implying a span was read for it.

## What this module must never contain

It defines `SealedReadAdapter` subclasses, so `sealed.py` reads this file at class definition and
refuses it if it imports a transport or contains a write-shaped call. That scan is a hygiene wall,
not a sandbox — see `_ReadOnlyGraphView` for the honest limit, and the report in `STATUS.md` for
what the gateway process actually holds.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Optional

from databridge_agent import (
    ClinicStructuralRead,
    SealedReadAdapter,
    Tool,
    ToolRegistry,
    ToolResult,
    ToolRow,
)
from databridge_core import SourcePointer

from .intelligence_surface import SurfaceNames

#: The sentence every code-authored `waiting_on` / `finding` value is built from. Kept here, as
#: data this repo wrote, so that nothing a document said can reach a staff-facing line except as a
#: node LABEL — which `render.present` neutralises and redacts on the way out.
AMBIGUOUS_TEXT = ("more than one registry row matched this document; a person settles it at the "
                  "counter")
UNLINKED_TEXT = ("no registry row matched this document; the patient is registered from scratch "
                 "at the counter")
COVERAGE_GAP_TEXT = ("requested on a document, and no package in the catalogue includes it; the "
                     "patient may be asked to pay for it")
#: Said when the label this page WOULD have shown turns out to be addressed to an automated
#: reader. The page no longer renders any text a document supplied (`intelligence_surface`), so
#: the payload cannot be reproduced even redacted — but "a document in this result is talking to a
#: machine" is worth saying, and this is the code-authored sentence that says it.
INJECTED_LABEL_TEXT = ("the title this entity carries in the graph is addressed to an automated "
                       "reader rather than to a person; it was not read as an instruction and is "
                       "not reproduced here")

#: The derivation every LABEL value carries. A label has no pointer of its own (`REMEDIATION.md`
#: R-31), so the citation says what it actually establishes — that this node exists and these
#: pointers are what put it there — rather than implying a span was read for the label itself.
LABEL_RULE = "graph node label; labels carry no pointer of their own, so this cites the node"


class _ReadOnlyGraphView:
    """The three graph reads the tools need, and nothing else.

    `GraphStore` has writers on it (`add_node`, `add_edge`, `append_sources`, `replace_node`). The
    adapters below never call one — but "never calls one" is a property of the code as written,
    and the module scan that checks it reads one file and does not follow imports. Handing the
    adapters a view that exposes only reads means the ordinary way this rots — somebody adds a
    convenient write to a tool on a deadline — has to get past a `AttributeError` first.

    **This is hygiene, not an authorization boundary, and must never be described as one.** The
    bound methods captured below still carry `__self__`, so in-process code that wants the store
    can have it. The real containment is that this process should not hold a writable ledger
    handle or network egress on the strength of anything in this file.
    """

    def __init__(self, store: Any) -> None:
        # Bound methods, not the store: the view has no attribute holding the object itself.
        self.open_nodes: Callable[[], list] = store.open_nodes
        self.open_edges: Callable[[], list] = store.open_edges
        self.sources_for: Callable[..., list] = store.sources_for


class _GraphSnapshot:
    """One traversal, shared by every tool in one answer.

    Computed lazily and once. Three tools each walking the graph independently could disagree —
    an upload landing between two reads would put two different denominators in one report, and
    the report would look like a contradiction rather than a race.
    """

    def __init__(self, view: _ReadOnlyGraphView, as_of: Optional[dt.datetime] = None) -> None:
        self._view = view
        self._as_of = as_of
        self._analytics: Any = None
        #: The one naming policy this page has (`intelligence_surface`). Held here rather than
        #: reimplemented per tool, so a tool cannot decide on its own that its labels are fine.
        self.names = SurfaceNames(view)

    @property
    def as_of_text(self) -> str:
        return self._as_of.isoformat() if self._as_of else "now"

    def analytics(self) -> Any:
        if self._analytics is None:
            # The SAME traversal `GET /intelligence` renders. A second implementation of these
            # figures would drift from the one on the page, and the agent would then be quoting
            # numbers a reader could not reconcile with the cards beside it.
            from databridge_graph.analytics import compute

            self._analytics = compute(self._view, as_of=self._as_of)
        return self._analytics

    def pointer(self, entity_id: str, derivation: str,
                also: tuple[str, ...] = ()) -> Optional[SourcePointer]:
        """A `derived` pointer for one value, or `None` when the evidence cannot be read.

        `None` is the fail-closed answer: the caller sets the value to `None` too, and `ToolRow`
        then names it in `unresolved`. Emitting the value with an empty citation would render an
        unchecked figure in the place a checkable one goes (`REMEDIATION.md` R-21).
        """
        pointers = list(self._entity_pointers(entity_id))
        for other in also:
            pointers.extend(p for p in self._entity_pointers(other) if p not in pointers)
        if not pointers:
            return None
        # Both halves go through the naming policy. `source_id` is what a citation button links
        # to, and the nested pointers are what a reader opens: a `doc_span` in `derived_from`
        # carries the filename in TWO fields, so projecting only the outer id would have left the
        # thing being hidden sitting one level down in the same payload.
        return SourcePointer(source_id=self.names.public_id(entity_id), kind="derived",
                             derivation=f"{derivation} [as of {self.as_of_text}]",
                             derived_from=[self.names.project(p) for p in pointers])

    def _entity_pointers(self, entity_id: str) -> list:
        try:
            return list(self._view.sources_for(entity_id))
        except Exception:                                            # noqa: BLE001
            # A pointer read that fails must cost the VALUE, not the answer. The caller turns a
            # missing pointer into an unresolved name, which is the honest degrade.
            return []

    def display(self, entity_id: str) -> Optional[str]:
        """What this page may call an entity — its catalogue name, or an opaque handle.

        Never the raw graph label. A seeded `DOCUMENT` label is the supplied filename, so
        `Alice_Tan_referral.pdf` is an ordinary, non-injection-shaped label that would render
        unchanged; the same is true of an organisation minted from a chit's employer field. The
        rule is about where the text came from, not what it says — see `intelligence_surface`.
        """
        text, _handle = self.names.display(entity_id)
        return text

    def label_is_addressed_to_a_machine(self, entity_id: str) -> bool:
        """Is the graph label this page declined to print an instruction to an automated reader?

        Read for the FLAG only. The label itself never leaves this method.
        """
        from databridge_agent.guardrails import looks_like_injection

        row = self.names.rows().get(entity_id)
        return bool(row is not None and looks_like_injection(row["label"] or ""))


def _row(pairs: dict) -> ToolRow:
    """Build a row from `{key: (value, pointer)}`, dropping any value whose pointer is missing.

    The wall in `ToolRow` refuses a value without a pointer; this is where that refusal is turned
    into "not found" rather than into a 500. A value whose evidence could not be read is `None`
    here and appears under *Asked for, not found* in the answer.
    """
    values: dict[str, Any] = {}
    sources: dict[str, SourcePointer] = {}
    for key, (value, pointer) in pairs.items():
        if value is None or pointer is None:
            values[key] = None
            continue
        values[key] = value
        sources[key] = pointer
    return ToolRow(values=values, value_sources=sources)


class _StructuralRead(SealedReadAdapter):
    """Base for the three tools. Structural capability, no arguments, one shared snapshot."""

    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    breadth = "narrow"

    def __init__(self, snapshot: _GraphSnapshot) -> None:
        super().__init__(ClinicStructuralRead())
        self._snapshot = snapshot


class IssuerDocumentMix(_StructuralRead):
    """Who sent the clinic paperwork, and how many documents each of them sent."""

    name = "issuer_document_mix"
    description = (
        "One row per organisation that has an `issued` edge to a document in the clinic graph, "
        "with how many documents it issued. Counted over every document the graph holds — there "
        "is no date window on this read. Names no patient.")

    def read(self, **_: Any) -> ToolResult:
        analytics = self._snapshot.analytics()
        rows = []
        for issuer in analytics.issuer_mix:
            count_rule = (
                f"analytics.compute — `issued` edges to document nodes, over "
                f"{analytics.documents} document(s) in the graph")
            pairs = {
                "issuer": (self._snapshot.display(issuer.issuer_id),
                           self._snapshot.pointer(issuer.issuer_id, LABEL_RULE)),
                "documents": (issuer.n,
                              self._snapshot.pointer(issuer.issuer_id, count_rule,
                                                     also=issuer.documents)),
            }
            if self._snapshot.label_is_addressed_to_a_machine(issuer.issuer_id):
                pairs["note"] = (INJECTED_LABEL_TEXT,
                                 self._snapshot.pointer(issuer.issuer_id, LABEL_RULE))
            rows.append(_row(pairs))
        note = ("Counted over every document in the clinic graph. This read has no date filter, "
                "so it cannot answer a question about one week on its own.")
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class DocumentsNeedingAPerson(_StructuralRead):
    """Documents whose structure means an intake cannot proceed without a human."""

    name = "documents_needing_a_person"
    description = (
        "One row per document in the clinic graph that a person has to settle: it matched more "
        "than one registry row, or it matched none. It says WHAT is waiting and never WHO — "
        "there is no candidate name, no ordering and no score anywhere in this read, because "
        "identity is checked in person by staff.")

    def read(self, **_: Any) -> ToolResult:
        analytics = self._snapshot.analytics()
        rows = []
        # Sorted by id, in one code-fixed order. The graph decides membership; nothing here and
        # nothing downstream re-orders these by any notion of likelihood.
        blocked = [(doc_id, AMBIGUOUS_TEXT,
                    "analytics.compute — open `for_patient` edges to more than one "
                    "registry row, so the resolver narrowed it to none")
                   for doc_id in sorted(analytics.ambiguity.members)]
        blocked += [(doc_id, UNLINKED_TEXT,
                     "analytics.compute — no open `for_patient` edge at all, so no "
                     "registry row matched it")
                    for doc_id in sorted(analytics.unlinked_documents.members)]
        for doc_id, waiting_on, rule in blocked:
            pairs = {
                # A handle and a document type from the closed `DocumentType` enum — never the
                # graph label, which is the supplied filename and can therefore be a person's
                # name (`intelligence_surface`). What a reader loses is the filename; what they
                # keep is the kind of document, the count, and a citation that still walks.
                "document": (self._snapshot.display(doc_id),
                             self._snapshot.pointer(doc_id, LABEL_RULE)),
                "waiting_on": (waiting_on, self._snapshot.pointer(doc_id, rule)),
            }
            if self._snapshot.label_is_addressed_to_a_machine(doc_id):
                pairs["note"] = (INJECTED_LABEL_TEXT,
                                 self._snapshot.pointer(doc_id, LABEL_RULE))
            rows.append(_row(pairs))
        note = (f"{len(rows)} of {analytics.documents} document(s) in the graph need a person. "
                f"Waiting on a human is this system working, not failing.")
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


class CoverageGaps(_StructuralRead):
    """Tests a document asked for that no package in the catalogue includes."""

    name = "coverage_gaps"
    description = (
        "One row per catalogue test that some document requests and no package includes — walked "
        "as `requests_test` minus `includes_test`. This is what a patient may end up paying for, "
        "found before they arrive. It quotes no price: money is not on this surface.")

    def read(self, **_: Any) -> ToolResult:
        analytics = self._snapshot.analytics()
        rows = []
        for entity in analytics.tests_outside_any_package:
            gap_rule = (
                f"analytics.compute — the target of a `requests_test` edge and of no "
                f"`includes_test` edge, over {analytics.tests_in_catalogue} catalogue test(s)")
            rows.append(_row({
                "test": (self._snapshot.display(entity.id),
                         self._snapshot.pointer(entity.id, LABEL_RULE)),
                "finding": (COVERAGE_GAP_TEXT, self._snapshot.pointer(entity.id, gap_rule)),
            }))
        note = ("Structural only: whether a given visit is billed for these is decided per intake "
                "from the config table, by a human, and no amount appears here.")
        return ToolResult(tool=self.name, rows=tuple(rows), note=note)


#: The closed set. Adding a tool is an edit here, in a file a reviewer reads beside the route.
STRUCTURAL_TOOLS = (IssuerDocumentMix, DocumentsNeedingAPerson, CoverageGaps)


def build_structural_registry(store: Any, *,
                              as_of: Optional[dt.datetime] = None) -> ToolRegistry:
    """The registry `POST /intelligence/ask` hands `ClinicAgent`. Structural reads only.

    `as_of` comes from the page's own control — it is staff-supplied and server-side, never a tool
    argument, so the model has no way to move the window it is reading over.
    """
    snapshot = _GraphSnapshot(_ReadOnlyGraphView(store), as_of=as_of)
    return ToolRegistry([Tool(adapter=cls(snapshot)) for cls in STRUCTURAL_TOOLS])


__all__ = [
    "AMBIGUOUS_TEXT",
    "INJECTED_LABEL_TEXT",
    "COVERAGE_GAP_TEXT",
    "CoverageGaps",
    "DocumentsNeedingAPerson",
    "IssuerDocumentMix",
    "STRUCTURAL_TOOLS",
    "UNLINKED_TEXT",
    "build_structural_registry",
]
