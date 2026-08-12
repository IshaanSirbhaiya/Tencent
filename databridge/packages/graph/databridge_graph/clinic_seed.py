"""Clinic knowledge-graph seed — built from the real fixtures, not from prose.

Replaces the Acme Corp ``demo_seed.py``. Every node here comes from data that exists on disk:

* **patients** ← ``patient_registration_synthetic.csv``  (``table_cell`` pointers)
* **issuers, coverage rules, packages, tests** ← ``coverage_rules.yaml``  (``table_cell`` pointers)
* **documents, employers, clinics, visits** ← the 9 chit letters  (``doc_span`` pointers)

so the anti-fabrication wall in ``schema.py`` is not merely satisfied, it is satisfied by pointers
that resolve to something a person can go and read.

**Three deliberate omissions, all about not asserting more than the data supports.**

*Only connected nodes are seeded.* The registry holds 300 patients and the catalogue 33 tests, but
a patient no document refers to and a test no package includes have no relationship to represent.
Seeding them would add hundreds of orphans — nodes that answer no question and inflate every count
on the dashboard. ``load_clinic_seed`` reports how many rows it skipped for this reason, so the
number is visible rather than quietly dropped.

*A document that names no subject gets no ``FOR_PATIENT`` edge.* The evidence for such a claim is
the span the subject was read from, and there is no substitute for it: citing the document-level
pointer attached a placeholder ``0:1`` span — one character of letterhead — to a patient-identity
claim. With no subject span the document links to no candidate, which is the honest outcome (we
cannot say who it is about). ``load_clinic_seed`` reports the dropped claims and names the
documents, and each such document carries ``claimed_subject_field: None`` in its ``unresolved``.

*``COVERED_BY``, ``EMPLOYED_BY`` and ``SCHEDULED_AS`` are reserved and unused here.* All three
would state a fact **about a specific registry patient** — that this person is insured by that
insurer, employed by that company, booked for that package. At seed time no patient is confirmed:
the documents yield *candidates*, and chits 3 and 5 yield two apiece. Writing those edges from a
candidate would smuggle an identity determination into the graph under a different name
(CLAUDE.md §Hard constraints 1). The honest chain is already traversable —
``patient ←FOR_PATIENT— document ←ISSUED— insurer`` — and it keeps the document visible as the
basis for the claim, which is exactly what staff need to see. The three edge kinds exist in the
vocabulary for the post-confirmation path (Phase 4's ledger), where a human has attested.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional

from databridge_core import GroundedValue, SourcePointer

from .merge import upsert_edge, upsert_node
from .schema import EdgeKind, GraphEdge, GraphNode, NodeKind, node_id
from .store import GraphStore

# When the clinic "learned" the fixture corpus (system_time). Fixed so counts, the timeline and
# every screenshot are reproducible on any machine and in CI.
SEED_SYSTEM_TIME = dt.datetime(2026, 8, 11, tzinfo=dt.timezone.utc)

# Default valid_from for standing configuration (the rules table, the catalogue). The clinic's
# coverage rules are in force from the start of the dataset's operating year.
_EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

RULES_SOURCE_ID = "coverage_rules.yaml"

# Legal-form suffixes stripped when deciding whether two printed employer names are the same
# organisation. "Coral Harbour Pte Ltd", "CORAL HARBOUR PTE LIMITED" and "CORAL HARBOUR PTE LTD"
# appear across chits 1, 3 and 9 and are one employer.
#
# This is ORGANISATION name normalisation and nothing else. It is emphatically not applied to
# patients: registry names are matched exactly (see `resolve.py`), because merging two similarly
# named people is the failure this whole architecture exists to prevent.
_LEGAL_SUFFIXES = ("pte limited", "pte ltd", "private limited", "limited", "ltd", "llp", "inc")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _NON_ALNUM.sub("-", str(s).lower()).strip("-")


def org_canonical(name: str) -> str:
    """Merge key for an organisation printed with varying legal-form suffixes and casing."""
    cleaned = _NON_ALNUM.sub(" ", str(name).lower()).strip()
    for suffix in _LEGAL_SUFFIXES:
        if cleaned.endswith(" " + suffix):
            cleaned = cleaned[: -len(suffix) - 1].strip()
            break
    return _slug(cleaned)


def _yaml_ptr(table: str, column: str, row_id: object) -> SourcePointer:
    """A ``table_cell`` pointer into ``coverage_rules.yaml``.

    The YAML is a table in every sense that matters here — a list of rows with named fields — so a
    cell pointer is the honest description of where a value came from, and it resolves to a
    findable place in a file a human can open.
    """
    return SourcePointer(source_id=RULES_SOURCE_ID, kind="table_cell",
                         table=table, column=column, row_id=str(row_id))


def _doc_ptr(document_id: str, length: Optional[int] = None) -> SourcePointer:
    """A DOCUMENT node's self-citation: the span of the whole document.

    Used ONLY to ground the document node in itself. It is deliberately not used as evidence for
    any relationship — a whole-document span localises nothing, and a placeholder ``0:1`` span is
    worse still, since it cites one character of letterhead as proof. Relationship edges carry the
    span the specific value was read from.
    """
    return SourcePointer(source_id=document_id, kind="doc_span", document_id=document_id,
                         char_start=0, char_end=length if length is not None else 1)


_DATE_PARTS = re.compile(r"[/\-.\s]+")


def _document_two_digit_year(day: str, month: str, year: str, *, today: dt.date) -> Optional[str]:
    """Resolve a two-digit year printed on a DOCUMENT — never delegate this to a DOB parser.

    ``resolve.parse_claimed_dob`` (and the registry's own ``parse_dob_two_digit_year`` it shares)
    pivot a two-digit year toward "the most recent reading that is NOT in the future" — correct for
    a date of birth, which can never legitimately be ahead of today. A document date is routinely
    the opposite case: a voucher's "Valid From" or an appointment date is often a few weeks *ahead*
    of the day the document was read. Reusing the DOB rule reads ``Valid From 01/09/26`` (three
    weeks out, with DATASET_TODAY = 2026-08-11) as **1926-09-01** — a hundred years off — because
    every 20xx candidate that lands after today gets discarded regardless of how close it is.

    The honest pivot for a document is whichever century's reading falls CLOSER to today in
    absolute days: both recent-past and near-future are plausible for a chit letter, a century
    either way is not. Returns the same ``DD/MM/YYYY`` text with the century resolved, so the
    caller can hand it to ``parse_claimed_dob`` and let its own (already-tested) 4-digit-year branch
    do the actual date construction — one parser, one place, for everything after the century guess.
    """
    if not (year.isdigit() and len(year) == 2):
        return None
    yy = int(year)
    candidates = []
    for century in (2000, 1900):
        try:
            candidates.append(dt.date(century + yy, int(month), int(day)))
        except ValueError:
            continue
    if not candidates:
        return None
    best = min(candidates, key=lambda d: abs((d - today).days))
    return f"{day}/{month}/{best.year}"


def _document_date(record) -> Optional[tuple[dt.datetime, SourcePointer, str]]:
    """A date printed on the document, its pointer, and WHICH printed field it came from.

    The pointer comes back alongside the date because the date is also written to the node as an
    attribute, and an attribute travels with its citation or it is not written at all.

    The field name comes back because the two candidates do not mean the same thing. A voucher's
    "Valid From" and an appointment date are both dates on the letter, and neither is the date the
    letter was *issued* — which no fixture prints at all. Recording either under `issued_at` put a
    pointer next to a claim it does not support: the span says "appointment: 15/10/26", and the
    attribute said the document was issued then. The value is now named after the field it was
    actually read from.

    Every document node used to carry the same `_EPOCH` valid_from, which made the bi-temporal axis
    meaningless for documents ("what did we believe on 1 March?" returned everything or nothing) and
    made every seeded referral look seven months old to the conflict window. The date printed on the
    letter is the honest `valid_from`: it is when the referral became true in the world (ADR-0003).

    Unparseable or absent → None, and the caller falls back. Never guessed.
    """
    from databridge_intake.registry import DATASET_TODAY
    from databridge_intake.resolve import parse_claimed_dob

    for field, gv in (("valid_from", record.authorisation.valid_from),
                      ("appointment_datetime", record.authorisation.appointment_datetime)):
        if gv is None:
            continue
        raw = gv.value
        text = raw
        if isinstance(raw, str):
            # A two-digit-year, day-first date (`DD/MM/YY`) is the one shape `parse_claimed_dob`
            # cannot be trusted with for a document (see `_document_two_digit_year`). Everything
            # else — 4-digit years, ISO order, "9 August 2026" — already has no future/past bias in
            # `parse_claimed_dob`, so it is left to handle those exactly as it does for a DOB.
            parts = _DATE_PARTS.split(raw.strip())
            if len(parts) == 3 and all(p.isdigit() for p in parts) and len(parts[0]) != 4:
                resolved = _document_two_digit_year(*parts, today=DATASET_TODAY)
                if resolved is not None:
                    text = resolved
        parsed = parse_claimed_dob(text)
        if parsed is not None:
            return (dt.datetime.combine(parsed, dt.time.min, tzinfo=dt.timezone.utc),
                    _span_of(gv), field)
    return None


def _span_of(gv: GroundedValue) -> SourcePointer:
    """Reuse the pointer the extractor already proved — never mint a fresh one.

    A seed that built its own spans would be asserting provenance it had not checked; the grounding
    wall verified this one against the source text in Phase 1.
    """
    return gv.pointer


#: The printed fields that can name a document's subject, most specific first. Order matters: an
#: identifier localises a person better than a name does, so where both are printed the identifier's
#: span is the evidence.
SUBJECT_FIELDS = ("claimed_id_number", "claimed_name")


def _subject_span(record) -> tuple[Optional[str], Optional[SourcePointer]]:
    """WHICH printed field names this document's subject, and the exact span it was read from.

    The **field name** comes back, never the value: the value is a patient's name or NRIC and the
    graph has no business carrying either as a node attribute. The name is what makes an absence
    legible — a document node whose ``claimed_subject_field`` is ``None`` puts that key in
    ``unresolved``, so "this letter names nobody" is a recorded answer rather than a gap a reader
    has to notice.

    Returning ``(None, None)`` is load-bearing: it is the ONLY thing that may stop a ``FOR_PATIENT``
    claim being written, and the caller must not substitute anything for the missing span.
    """
    for field in SUBJECT_FIELDS:
        gv = getattr(record.subject, field, None)
        if gv is not None:
            return field, _span_of(gv)
    return None, None


def _derived_ptr(derivation: str, derived_from: list[SourcePointer]) -> SourcePointer:
    """A pointer for a value this seed COMPUTED rather than read.

    Counts, scores and classifications are not printed in any document, so citing a span for them
    would be a lie in the other direction. `derived` is the honest kind: it names the rule and
    keeps the pointers the rule consumed, so the value stays checkable without pretending it was
    read off a page.
    """
    return SourcePointer(source_id=RULES_SOURCE_ID, kind="derived", derivation=derivation,
                         derived_from=derived_from)


def _grounded_attrs(pairs: dict) -> tuple[dict, dict]:
    """Split ``{name: (value, pointer)}`` into a node's ``attrs`` and its ``attr_sources``.

    **A value with no pointer is not recorded as a fact.** It becomes ``None``, which puts its
    name in ``unresolved`` — the question stays visible and unanswered instead of being answered
    by something nobody can check. One rule, in one place, so no call site has to remember it.
    """
    attrs: dict = {}
    ptrs: dict = {}
    for name, (value, pointer) in pairs.items():
        if value is None or pointer is None:
            attrs[name] = None
        else:
            attrs[name] = value
            ptrs[name] = pointer
    return attrs, ptrs


# --- node builders ----------------------------------------------------------------


def _node(kind: NodeKind, node_key: str, label: str, sources: list[SourcePointer], *,
          canonical_id: Optional[str] = None, attrs: Optional[dict] = None,
          attr_sources: Optional[dict] = None,
          valid_from: Optional[dt.datetime] = None) -> GraphNode:
    return GraphNode(id=node_id(kind, node_key), kind=kind, label=label,
                     canonical_id=canonical_id, attrs=attrs or {},
                     attr_sources=attr_sources or {}, sources=sources,
                     valid_from=valid_from or _EPOCH, system_from=SEED_SYSTEM_TIME)


def _edge(src_id: str, kind: EdgeKind, dst_id: str, sources: list[SourcePointer], *,
          attrs: Optional[dict] = None, attr_sources: Optional[dict] = None) -> GraphEdge:
    """One relationship. ``valid_from`` is set provisionally and corrected by
    ``_reconcile_edge_validity`` once every node's own date is known — see that function for why it
    cannot be decided here."""
    return GraphEdge(id=f"{src_id}|{kind.value}|{dst_id}", src_id=src_id, kind=kind, dst_id=dst_id,
                     attrs=attrs or {}, attr_sources=attr_sources or {}, sources=sources,
                     valid_from=_EPOCH, system_from=SEED_SYSTEM_TIME)


def _reconcile_edge_validity(nodes: list[GraphNode],
                             edges: list[GraphEdge]) -> list[GraphEdge]:
    """Lift every edge's ``valid_from`` to the later of its own and both endpoints'.

    **A relationship cannot begin before both things it relates exist.** That reads as obvious and
    the seed violated it 63 times out of 111: `_edge` stamped every relationship with the standing
    configuration date (2026-01-01) while a document node correctly carried the date printed on the
    letter (Aug 2026). So the store asserted that chit 1 had *requested a test* seven months before
    chit 1 existed.

    It survived because the renderer masks it — `brain-graph.tsx` refuses to draw an edge unless
    both endpoints are visible, so the picture self-corrects while the data underneath is wrong.
    That is the wrong way round for a system whose entire pitch is provenance, and everything that
    reads these dates rather than drawing them — `as_of` replay, `analytics.py`'s trend, the
    claims-defensibility answer "what did the rules say on the day we billed this?" — was reading
    a date nobody could defend.

    Done as a pass over the finished lists rather than inside `_edge` because an edge is often
    constructed before both of its endpoint nodes are; at that moment the correct date is not yet
    knowable. A single reconciliation is also the only place a future edge-builder cannot forget.
    """
    starts = {n.id: n.valid_from for n in nodes if n.valid_from is not None}
    out: list[GraphEdge] = []
    for edge in edges:
        bounds = [d for d in (starts.get(edge.src_id), starts.get(edge.dst_id)) if d is not None]
        if edge.valid_from is not None:
            bounds.append(edge.valid_from)
        latest = max(bounds) if bounds else edge.valid_from
        # `model_copy` rather than mutation: GraphEdge is frozen, and it should stay that way —
        # the anti-fabrication validators run at construction.
        out.append(edge if latest == edge.valid_from else edge.model_copy(update={"valid_from": latest}))
    return out


# Node-key helpers. Every node is BUILT through the matching helper below, so an edge that
# references a node by key can never disagree with the node's own id. Building an id inline was
# how the first draft produced `coverage_rule:rule-mol-pee226` edges pointing at
# `coverage_rule:rule_mol_pee226` nodes — five silently dangling edges and five orphan rules.
def patient_key(patient_id: str) -> str:
    return patient_id


def org_key(name: str) -> str:
    return org_canonical(name)


def package_key(package_code: str, issuer_code: str) -> str:
    """Package keys are scoped by ISSUER.

    ``coverage_rules.yaml`` only enforces uniqueness on ``(issuer_code, package_code)``, so two
    insurers may both define a package called ``BASIC``. Keying the graph node on the code alone
    fuses them: one node, both issuers' test edges, and whichever attributes were written first.
    Nothing fails — the graph just quietly answers the wrong question about what a package covers.
    """
    return f"{_slug(issuer_code)}-{_slug(package_code)}"


def catalogue_test_key(canonical: str) -> str:
    return canonical


def rule_key(rule_id: str) -> str:
    return _slug(rule_id)


def patient_node_id(patient_id: str) -> str:
    return node_id(NodeKind.PATIENT, patient_key(patient_id))


def org_node_id(name: str) -> str:
    return node_id(NodeKind.ORG, org_key(name))


def package_node_id(package_code: str, issuer_code: str) -> str:
    return node_id(NodeKind.PACKAGE, package_key(package_code, issuer_code))


def catalogue_test_node_id(canonical: str) -> str:
    return node_id(NodeKind.TEST, catalogue_test_key(canonical))


def rule_node_id(rule_id: str) -> str:
    return node_id(NodeKind.COVERAGE_RULE, rule_key(rule_id))


def document_node_id(document_id: str) -> str:
    return node_id(NodeKind.DOCUMENT, document_id)


def _build(intakes, resolutions, table, registry_by_id, texts=None):
    """Assemble every node and edge. Returns ``(nodes, edges, skipped)``."""
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    def add(node: GraphNode) -> str:
        # First writer wins on identity; later sightings contribute sources rather than replacing
        # the node, which is what `upsert_node`'s merge does at store level too.
        existing = nodes.get(node.id)
        if existing is None:
            nodes[node.id] = node
        elif node.sources[0] not in existing.sources:
            nodes[node.id] = existing.model_copy(
                update={"sources": [*existing.sources, *node.sources]})
        return node.id

    # -- issuers (coverage_rules.yaml) ---------------------------------------------
    for i, issuer in enumerate(table.issuers):
        attrs, attr_ptrs = _grounded_attrs({
            "kind": (issuer.kind.value, _yaml_ptr("issuers", "kind", i)),
            "issuer_code": (issuer.code, _yaml_ptr("issuers", "code", i)),
            "billing_party": (issuer.billing_party.value,
                              _yaml_ptr("issuers", "billing_party", i))})
        add(_node(NodeKind.ORG, org_key(issuer.name), issuer.name,
                  [_yaml_ptr("issuers", "name", i)],
                  canonical_id=issuer.code, attrs=attrs, attr_sources=attr_ptrs))

    issuer_node_by_code = {issuer.code: org_node_id(issuer.name) for issuer in table.issuers}

    # -- tests, packages, coverage rules -------------------------------------------
    # Only tests something actually references. See the module docstring.
    referenced_tests: set[str] = set()
    for package in table.packages:
        referenced_tests.update(package.includes_tests)
    for record in intakes.values():
        for gv in (*record.authorisation.requested_tests, *record.authorisation.add_ons):
            match = table.match_test(str(gv.value))
            if match.matched:
                referenced_tests.add(match.canonical)

    for i, entry in enumerate(table.test_catalogue):
        if entry.canonical not in referenced_tests:
            continue
        attrs, attr_ptrs = _grounded_attrs({
            "category": (entry.category, _yaml_ptr("test_catalogue", "category", i)),
            # An uncatalogued price stays None. `billing.py` must show "not priced", never SGD 0.
            "list_price_sgd": (str(entry.list_price_sgd)
                               if entry.list_price_sgd is not None else None,
                               _yaml_ptr("test_catalogue", "list_price_sgd", i))})
        add(_node(NodeKind.TEST, catalogue_test_key(entry.canonical),
                  entry.aliases[0] if entry.aliases else entry.canonical,
                  [_yaml_ptr("test_catalogue", "canonical", i)],
                  canonical_id=entry.canonical, attrs=attrs, attr_sources=attr_ptrs))

    for i, package in enumerate(table.packages):
        rule_ptr = _yaml_ptr("packages", "id", i)
        rule_attrs, rule_ptrs = _grounded_attrs({
            "issuer_code": (package.issuer_code, _yaml_ptr("packages", "issuer_code", i)),
            "package_code": (package.package_code, _yaml_ptr("packages", "package_code", i)),
            "questionnaire": (package.questionnaire.value,
                              _yaml_ptr("packages", "questionnaire", i)),
            "billed_to": (package.billing.covered_by.value,
                          _yaml_ptr("packages", "billing.covered_by", i)),
            "eligibility": (package.eligibility.describe(),
                            _yaml_ptr("packages", "eligibility", i)),
            "tests_from_document": (package.tests_from_document,
                                    _yaml_ptr("packages", "tests_from_document", i))})
        rule_id = add(_node(NodeKind.COVERAGE_RULE, rule_key(package.id), package.name, [rule_ptr],
                            canonical_id=package.id,
                            attrs=rule_attrs, attr_sources=rule_ptrs))
        if package.package_code:
            pkg_attrs, pkg_ptrs = _grounded_attrs({
                "name": (package.name, _yaml_ptr("packages", "name", i)),
                "eligibility": (package.eligibility.describe(),
                                _yaml_ptr("packages", "eligibility", i)),
                "questionnaire": (package.questionnaire.value,
                                  _yaml_ptr("packages", "questionnaire", i))})
            pkg_id = add(_node(NodeKind.PACKAGE,
                               package_key(package.package_code, package.issuer_code),
                               package.package_code,
                               [_yaml_ptr("packages", "package_code", i)],
                               canonical_id=package.package_code,
                               attrs=pkg_attrs, attr_sources=pkg_ptrs))
            edges.append(_edge(rule_id, EdgeKind.AUTHORISES, pkg_id, [rule_ptr]))
            for canonical in package.includes_tests:
                edges.append(_edge(pkg_id, EdgeKind.INCLUDES_TEST, catalogue_test_node_id(canonical),
                                   [_yaml_ptr("packages", "includes_tests", i)]))

    # -- documents, and everything each one claims ---------------------------------
    # Claims NOT written because the document names no subject. Counted, not swallowed: a seed that
    # is quietly smaller than the corpus is indistinguishable from one that lost rows to a bug.
    dropped_claims = 0
    documents_with_no_subject: list[str] = []
    for document_id, record in intakes.items():
        doc_ptr = _doc_ptr(document_id, (texts or {}).get(document_id))
        # Read BEFORE the document node is built, because its absence is itself a recorded fact.
        subject_field, subject_span = _subject_span(record)
        # `valid_from` is the date ON the document where it carries one, falling back to the fixed
        # seed instant — NOT `record.document.received_at`, which is the wall clock and would make
        # the seed non-reproducible: an undated document would land in the future relative to
        # `SEED_SYSTEM_TIME` and vanish from an `as_of` query at that instant.
        dated = _document_date(record)
        issued_at = dated[0] if dated is not None else SEED_SYSTEM_TIME
        doc_attrs, doc_ptrs = _grounded_attrs({
            "document_type": (record.document_type.value, _span_of(record.document_type)),
            # An UNDATED document still needs a `valid_from` (the temporal axis needs an instant),
            # but it records no date attribute: the fallback is our bookkeeping, not the letter's
            # date, and printing it as an attribute presents a machine default as something read
            # off the page. `unresolved` then says so out loud.
            #
            # `document_date_field` names WHICH printed field the date came from, so nothing
            # downstream has to assume it means "issued". Both share the one pointer, and both
            # are things that span genuinely establishes.
            "document_date": (dated[0].isoformat() if dated is not None else None,
                              dated[1] if dated is not None else None),
            "document_date_field": (dated[2] if dated is not None else None,
                                    dated[1] if dated is not None else None),
            # WHICH printed field named the subject — the same pattern as `document_date_field`,
            # and for the same reason. `None` here is the honest record of a letter that names
            # nobody, and it is exactly the case in which NO `FOR_PATIENT` edge is written below.
            # `unresolved` on this node then carries the unanswered claim.
            "claimed_subject_field": (subject_field, subject_span),
            "unresolved_count": (len(record.unresolved),
                                 _derived_ptr("count of IntakeRecord.unresolved after extraction",
                                              [doc_ptr])),
            "refused_count": (len(record.refused),
                              _derived_ptr("count of IntakeRecord.refused after the grounding wall",
                                           [doc_ptr]))})
        doc_id = add(_node(NodeKind.DOCUMENT, document_id, document_id, [doc_ptr],
                           canonical_id=document_id,
                           valid_from=issued_at,
                           attrs=doc_attrs, attr_sources=doc_ptrs))

        issuer_code = (record.issuer.issuer_code.value
                       if record.issuer.issuer_code is not None else None)
        if issuer_code and issuer_code in issuer_node_by_code:
            edges.append(_edge(issuer_node_by_code[issuer_code], EdgeKind.ISSUED, doc_id,
                               [_span_of(record.issuer.issuer_code)]))

        # FOR_PATIENT — a CLAIM, one edge per candidate. Chits 3 and 5 produce two each, and that
        # is the point: the graph must not collapse an unresolved identity into a single answer.
        #
        # The evidence is the span the SUBJECT was read from — the printed name, or the identifier
        # when the document carries one. **There is no fallback.** This used to read
        # `[subject_span or doc_ptr]`, so a document naming nobody produced a patient-identity
        # claim evidenced by `_doc_ptr`'s placeholder `0:1` span: one character of letterhead,
        # cited as proof of who a letter is about. That is constraints 1 and 2 broken at once —
        # a citation that looks authoritative, supports nothing, and points at a person.
        #
        # With no subject span the honest outcome is NO EDGE: the document links to no candidate,
        # because we cannot say who it is about. The claim is recorded instead — as
        # `claimed_subject_field: None` in the document node's `unresolved`, and in the seed's
        # `skipped` report, so the smaller number is visible rather than merely smaller.
        resolution = resolutions[document_id]
        claimable = resolution.candidates if subject_span is not None else ()
        if subject_span is None:
            dropped_claims += len(resolution.candidates)
            documents_with_no_subject.append(document_id)
        for candidate in claimable:
            patient = registry_by_id[candidate.patient_id]
            # Each registry field carries the `table_cell` pointer it was read from. These were
            # written with only the name and ID pointers on the node, so an evidence sheet printed
            # a patient's sex, nationality and date of birth under a citation list that had never
            # seen them.
            pat_attrs, pat_ptrs = _grounded_attrs({
                "sex": (patient.sex.value, patient.sex.pointer),
                "nationality": (patient.nationality.value, patient.nationality.pointer),
                "date_of_birth": (str(patient.date_of_birth.value),
                                  patient.date_of_birth.pointer)})
            pat_id = add(_node(NodeKind.PATIENT, patient_key(candidate.patient_id),
                               str(patient.full_name.value),
                               [patient.full_name.pointer, patient.id_number.pointer],
                               canonical_id=candidate.patient_id,
                               attrs=pat_attrs, attr_sources=pat_ptrs))
            # Every one of these is the RESOLVER's output, not the letter's text, so each cites
            # the rule that produced it and keeps the subject span that rule consumed.
            # `candidate_count` matters most: a renderer that cannot cite it falls back to the
            # LESS cautious wording for an identity claim, which is the wrong direction to fail.
            claim_evidence = [subject_span]
            edge_attrs, edge_ptrs = _grounded_attrs({
                "basis": ("claimed_on_document",
                          _derived_ptr("the document names a subject; the registry was searched "
                                       "for candidates", claim_evidence)),
                "match_score": (candidate.score,
                                _derived_ptr("resolve.py candidate score", claim_evidence)),
                "matched_on": (",".join(candidate.matched_on),
                               _derived_ptr("resolve.py fields that matched", claim_evidence)),
                "requires_human_verification": (True,
                                                _derived_ptr("invariant: identity is verified in "
                                                             "person, never by this system "
                                                             "(CLAUDE.md constraint 1)",
                                                             claim_evidence)),
                "candidate_count": (len(resolution.candidates),
                                    _derived_ptr("number of registry candidates returned",
                                                 claim_evidence))})
            edges.append(_edge(doc_id, EdgeKind.FOR_PATIENT, pat_id,
                               [subject_span],
                               attrs=edge_attrs, attr_sources=edge_ptrs))

        # REQUESTS_TEST — what the document printed, mapped to the catalogue.
        for gv in (*record.authorisation.requested_tests, *record.authorisation.add_ons):
            match = table.match_test(str(gv.value))
            if match.matched:
                edges.append(_edge(doc_id, EdgeKind.REQUESTS_TEST,
                                   catalogue_test_node_id(match.canonical), [_span_of(gv)],
                                   attrs={"as_printed": str(gv.value)},
                                   attr_sources={"as_printed": _span_of(gv)}))

        # AUTHORISES — the coverage rule this document selects.
        package_code = (record.authorisation.package_code.value
                        if record.authorisation.package_code is not None else None)
        rule = table.package(issuer_code, package_code) if issuer_code else None
        if rule is not None:
            # With a package code printed, that span IS the evidence. Without one, the rule was
            # selected by ISSUER (the document-defined fallback), so the issuer code's span is the
            # honest citation — not a placeholder span at character 0.
            source = (_span_of(record.authorisation.package_code)
                      if record.authorisation.package_code is not None
                      else _span_of(record.issuer.issuer_code))
            edges.append(_edge(doc_id, EdgeKind.AUTHORISES, rule_node_id(rule.id), [source]))

        # Employers / agencies the document names — REFERENCES, not EMPLOYED_BY: the document
        # cites the organisation, it does not establish that a specific registry patient works
        # there (see the module docstring).
        for gv in (record.employer.employer_name, record.employer.agency):
            if gv is None:
                continue
            org_id = add(_node(NodeKind.ORG, org_key(str(gv.value)), str(gv.value),
                               [_span_of(gv)], canonical_id=org_canonical(str(gv.value)),
                               attrs={"kind": "employer"},
                               attr_sources={"kind": _derived_ptr(
                                   "read from the document's employer/agency field",
                                   [_span_of(gv)])}))
            edges.append(_edge(doc_id, EdgeKind.REFERENCES, org_id, [_span_of(gv)]))

        # A VISIT exists only where the document actually schedules one.
        location = record.authorisation.appointment_location
        appointment = record.authorisation.appointment_datetime
        if location is not None:
            clinic_id = add(_node(NodeKind.CLINIC, _slug(str(location.value)),
                                  str(location.value), [_span_of(location)],
                                  canonical_id=_slug(str(location.value))))
            visit_attrs, visit_ptrs = _grounded_attrs({
                "appointment": (str(appointment.value) if appointment is not None else None,
                                _span_of(appointment) if appointment is not None else None)})
            visit_id = add(_node(NodeKind.VISIT, f"{document_id}-visit",
                                 f"Visit scheduled by {document_id}",
                                 [_span_of(appointment) if appointment is not None
                                  else _span_of(location)],
                                 attrs=visit_attrs, attr_sources=visit_ptrs))
            edges.append(_edge(visit_id, EdgeKind.AT_CLINIC, clinic_id, [_span_of(location)]))
            edges.append(_edge(doc_id, EdgeKind.REFERENCES, visit_id, [_span_of(location)]))

    skipped = {
        "catalogue_tests_not_referenced": len(table.test_catalogue) - len(referenced_tests),
        "registry_patients_not_referenced": len(registry_by_id) - sum(
            1 for n in nodes.values() if n.kind is NodeKind.PATIENT),
        # A document that names no subject links to no candidate. That is the honest outcome, and
        # it makes the graph smaller — so the number is reported here rather than left to be
        # noticed. The ids are listed because "which letters?" is the next question anyone asks.
        "for_patient_claims_dropped_no_subject_span": dropped_claims,
        "documents_with_no_subject_span": sorted(documents_with_no_subject),
    }
    node_list = list(nodes.values())
    return node_list, _reconcile_edge_validity(node_list, edges), skipped


def _fixture_intakes():
    """Extract every chit fixture under the deterministic gold backend.

    Imported lazily so ``packages/graph`` keeps working without ``packages/intake`` on the path —
    the graph package is the engine, the clinic seed is the product using it.
    """
    from databridge_intake.extract import extract_intake
    from databridge_intake.gold import gold_harness
    from databridge_intake.paths import fixtures_dir

    harness = gold_harness()
    intakes, lengths = {}, {}
    for path in sorted((fixtures_dir() / "chits").glob("*")):
        text = path.read_text(encoding="utf-8")
        intakes[path.name] = extract_intake(text, document_id=path.name, harness=harness)
        lengths[path.name] = len(text)
    return intakes, lengths


def build_seed(intakes=None, texts=None) -> tuple[list[GraphNode], list[GraphEdge], dict]:
    """Nodes, edges and the skipped-row counts, without touching a store.

    ``texts`` maps document_id → source length, used only so a document's self-citation spans the
    whole document instead of a placeholder character.
    """
    from databridge_intake.registry import registry
    from databridge_intake.resolve import resolve
    from databridge_intake.rules import rules

    if intakes is None:
        intakes, fixture_lengths = _fixture_intakes()
        texts = texts if texts is not None else fixture_lengths
    resolutions = {name: resolve(record.subject) for name, record in intakes.items()}
    registry_by_id = {r.patient_id: r for r in registry()}
    return _build(intakes, resolutions, rules(), registry_by_id, texts)


def build_seed_nodes() -> list[GraphNode]:
    return build_seed()[0]


def build_seed_edges() -> list[GraphEdge]:
    return build_seed()[1]


def load_clinic_seed(store: GraphStore, intakes=None, texts=None) -> dict:
    """Load the clinic graph into ``store`` (idempotent).

    Returns the created counts plus ``skipped``, which names the registry and catalogue rows that
    were deliberately not seeded because nothing in the fixture corpus relates to them.
    """
    before = store.counts()
    nodes, edges, skipped = build_seed(intakes, texts)
    for node in nodes:
        upsert_node(store, node)
    for edge in edges:
        upsert_edge(store, edge)
    after = store.counts()
    return {
        "nodes_created": after["nodes"] - before["nodes"],
        "edges_created": after["edges"] - before["edges"],
        # Reported apart for the same reason the store counts them apart. Summing them would
        # hide which kind grew; reporting only the entity count — as this did after the split —
        # made the per-attribute provenance invisible in the one number the demo shows for it.
        "sources_attached": after["sources"] - before["sources"],
        "attribute_sources_attached": (after["attribute_sources"]
                                       - before["attribute_sources"]),
        "skipped": skipped,
    }


def fresh_clinic_store() -> GraphStore:
    """A new in-memory store with the clinic seed loaded and a pinned system clock."""
    store = GraphStore(clock=lambda: SEED_SYSTEM_TIME)
    load_clinic_seed(store)
    return store


def orphan_node_ids(nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]) -> list[str]:
    """Node ids with no incident edge — asserted to be empty by the seed's tests."""
    connected: set[str] = set()
    for edge in edges:
        connected.add(edge.src_id)
        connected.add(edge.dst_id)
    return sorted(n.id for n in nodes if n.id not in connected)
