"""FIN-09 (finance dry run, 2026-10-02): finance answers said "No document on file names this
patient". The identity caveats are now worded from the subject KIND, its pack's attestation mode
and its pack's optional `attested_by` / `held_by` wording. Healthcare output is byte-identical to
the strings it replaced; every other binding names its own subject; no single subject -> neutral.
"""
from __future__ import annotations

import datetime as dt

import pytest

from databridge_core import SourcePointer
from databridge_graph.identity import identity_caveat
from databridge_graph.node_sheet import identity_claim_caveat, node_markdown, registry_row_caveat
from databridge_graph.packs import deployment
from databridge_graph.query import nl_query
from databridge_graph.schema import GraphNode, NodeKind
from databridge_graph.store import GraphStore

T0 = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)

#: The healthcare strings as they were before FIN-09 -- a byte-identity pin, not an expectation of
#: new behaviour.
OLD_HEALTHCARE = {
    "claimed": ("A document naming a patient is a claim on the document, not a verified identity; "
                "staff confirm identity in person."),
    "registry": ("No document on file names this patient; this is a registry row, not a verified "
                 "identity. Names are not unique in the registry, and staff confirm identity in "
                 "person."),
    "sheet_claim": ("**CLAIMED ON THE DOCUMENT — NOT VERIFIED.** These link a document to registry "
                    "candidates. Identity is checked in person by clinic staff; the system never "
                    "confirms it, and nothing below may be read as a determination of who this "
                    "patient is."),
    "sheet_row": ("**A REGISTRY ROW IS NOT AN IDENTIFICATION.** This sheet describes a row the "
                  "clinic holds on file. It does not establish who any person at the counter is; "
                  "identity is checked in person by clinic staff, and the system never confirms "
                  "it. Names are not unique in the registry — several people on file share one."),
}


def test_healthcare_wording_is_byte_identical():
    with deployment("healthcare") as v:
        (subject,) = v.subject_kinds
        assert identity_caveat(claimed=True, kind=subject) == OLD_HEALTHCARE["claimed"]
        assert identity_caveat(claimed=False, kind=subject) == OLD_HEALTHCARE["registry"]
        assert identity_caveat(claimed=False) == OLD_HEALTHCARE["registry"]   # one subject bound
        assert identity_claim_caveat(subject) == OLD_HEALTHCARE["sheet_claim"]
        assert registry_row_caveat(subject) == OLD_HEALTHCARE["sheet_row"]


def _subject_store(kind: str, label: str) -> GraphStore:
    store = GraphStore()
    ptr = SourcePointer(source_id="register", kind="table_cell", table="t", column="name",
                        row_id="1")
    store.add_node(GraphNode(id=f"{kind}:r1", kind=NodeKind(kind), label=label, sources=[ptr],
                             valid_from=T0, system_from=T0))
    return store


@pytest.mark.parametrize("pack", ["finance", "healthcare"])
def test_every_subject_kind_is_named_as_itself_never_as_another_packs(pack):
    with deployment(pack) as v:
        other_nouns = {k.replace("_", " ") for p in ("finance", "healthcare") if p != pack
                       for k in _subjects_of(p)}
        assert other_nouns
        for kind in sorted(v.subject_kinds):
            noun = kind.replace("_", " ")
            store = _subject_store(kind, "Avery Quinlan-Moss")
            answer = nl_query(store, "Who is Avery Quinlan-Moss?").answer
            sheet = node_markdown(store, f"{kind}:r1")
            assert f"No document on file names this {noun}" in answer
            assert f"No document on file names this {noun}." in sheet
            for text in (answer, sheet, identity_claim_caveat(kind)):
                for other in other_nouns - {noun}:
                    assert f"this {other}" not in text, (kind, other)
            by, held = v.subject_wording(kind)
            assert by and held and by in sheet and held in sheet


def _subjects_of(pack: str) -> tuple[str, ...]:
    with deployment(pack) as v:
        return tuple(v.subject_kinds)


def test_no_single_subject_and_no_kind_gets_the_neutral_wording():
    with deployment("finance", "healthcare") as v:
        assert len(v.subject_kinds) > 1
        text = identity_caveat(claimed=False)
        for kind in v.subject_kinds:
            assert f"this {kind.replace('_', ' ')};" not in text
        assert "not a verified identity" in text
