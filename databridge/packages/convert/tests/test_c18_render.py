"""C18 — render/ ReportIR + md/docx renderers + templates (09_FINAL_PRD §13.4 / §12.4).

Covers: deterministic byte-identity (md AND docx — the §13.4 contract), claim freezing +
span resolution against the md render, the data-dictionary template over an AnnotatedSchema,
table-cell escaping (the md injection-equivalent), and the C17→C18 real-ReportIR (Demo D11).
"""
from __future__ import annotations

import datetime as _dt
import io
import zipfile

import pytest

from databridge_core import (
    AnnotatedSchema,
    ColumnAnnotation,
    RelationshipAnnotation,
    SemanticType,
    SnapshotIdentity,
    SourcePointer,
    TableAnnotation,
    TableRole,
)
from databridge_convert.ir import Block, ReportIR
from databridge_convert.render import (
    ReportBuilder,
    approval_digest,
    claims_cited,
    claims_resolve,
    data_dictionary,
    finalise_report,
    grounded,
    generic_report,
    render_docx,
    render_markdown,
    render_markdown_bytes,
    verify_frozen,
)
from databridge_convert.render.report_ir import ClaimSpec

SNAP = SnapshotIdentity(source_id="r", as_of=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
                        content_hash="abc", schema_hash="sh")


def _ptr(cell="B1"):
    return SourcePointer(source_id="wb", kind="workbook_cell", workbook_sha="x", sheet="S",
                         cell=cell)


def _sample_report() -> ReportIR:
    b = ReportBuilder()
    b.add_heading("Quarterly Report", 1)
    b.add_grounded_paragraph("Total revenue was $1,350.00.", [_ptr()])
    b.add_table([["merchant", "rev"], ["Acme", "100"]])
    return b.finalise(SNAP)


# --- §13.4 deterministic byte-identity (the core contract) ----------------------
def test_markdown_is_byte_identical_across_renders():
    rep = _sample_report()
    assert render_markdown_bytes(rep) == render_markdown_bytes(rep)


def test_docx_is_byte_identical_across_renders():
    rep = _sample_report()
    assert render_docx(rep) == render_docx(rep)


def test_two_reports_same_content_same_md_and_docx():
    """Determinism is over CONTENT, not object identity: two independently built equal reports
    render byte-identically (no hidden timestamp/ordering leak)."""
    a, b = _sample_report(), _sample_report()
    assert render_markdown_bytes(a) == render_markdown_bytes(b)
    assert render_docx(a) == render_docx(b)


def test_markdown_golden_layout():
    rep = _sample_report()
    md = render_markdown(rep)
    assert md == (
        "# Quarterly Report\n\n"
        "Total revenue was $1,350.00.\n\n"
        "| merchant | rev |\n| --- | --- |\n| Acme | 100 |\n"
    )


def test_docx_is_a_valid_openable_package():
    rep = _sample_report()
    data = render_docx(rep)
    zf = zipfile.ZipFile(io.BytesIO(data))            # opens = valid OOXML zip
    assert "word/document.xml" in zf.namelist()
    body = zf.read("word/document.xml").decode("utf-8")
    assert "Quarterly Report" in body and "1,350.00" in body


def test_docx_zip_is_host_pinned_for_cross_platform_identity():
    """Every member's 'version made by' host is pinned to Unix(3) so a Windows build and a Linux
    build of the same ReportIR are byte-identical; mod-times are pinned to the fixed epoch."""
    rep = _sample_report()
    zf = zipfile.ZipFile(io.BytesIO(render_docx(rep)))
    for zi in zf.infolist():
        assert zi.create_system == 3
        assert zi.date_time == (1980, 1, 1, 0, 0, 0)


def test_docx_document_xml_has_no_rsid():
    rep = _sample_report()
    zf = zipfile.ZipFile(io.BytesIO(render_docx(rep)))
    assert b"w:rsid" not in zf.read("word/document.xml")


def test_docx_control_chars_scrubbed():
    # an XML-illegal control char in untrusted text must not crash the render or corrupt the package
    rep = ReportIR(snapshot=SNAP, blocks=[Block(kind="Paragraph", text="ok\x07bell\x00nul")])
    data = render_docx(rep)
    zf = zipfile.ZipFile(io.BytesIO(data))
    body = zf.read("word/document.xml").decode("utf-8")
    assert "okbellnul" in body


# --- md table escaping (the injection-equivalent for Markdown) -------------------
def test_table_cell_pipe_and_newline_escaped():
    rep = ReportIR(snapshot=SNAP, blocks=[
        Block(kind="Table", rows=[["a|b", "c\nd"], ["1", "2"]])])
    md = render_markdown(rep)
    # a literal pipe is escaped and a newline is flattened — the grid cannot be broken out of
    assert "a\\|b" in md
    assert "c d" in md and "c\nd" not in md.split("\n", 1)[0]


def test_heading_level_clamped():
    rep = ReportIR(snapshot=SNAP, blocks=[Block(kind="Heading", text="x", level=99)])
    assert render_markdown(rep).startswith("###### x")   # clamped to 6


def test_leading_equals_preserved_literally_in_md():
    # md is not a formula surface; a leading '=' must survive verbatim (no silent stripping)
    rep = ReportIR(snapshot=SNAP, blocks=[Block(kind="Paragraph", text="=1+2")])
    assert "=1+2" in render_markdown(rep)


# --- structural-injection hardening (text cannot break OUT of its block) ----------
def test_heading_newline_cannot_inject_a_new_heading():
    rep = ReportIR(snapshot=SNAP, blocks=[
        Block(kind="Heading", text="Title\n## INJECTED\nClaim", level=1)])
    md = render_markdown(rep)
    # newlines collapsed → ONE content line; "## INJECTED" survives only as INLINE inert text, it
    # can never START a line and become a structural heading.
    content = [ln for ln in md.split("\n") if ln.strip()]
    assert len(content) == 1 and content[0].startswith("# Title ")
    assert not any(ln.lstrip().startswith("## ") for ln in md.split("\n"))


def test_paragraph_newline_cannot_inject_blocks():
    rep = ReportIR(snapshot=SNAP, blocks=[
        Block(kind="Paragraph", text="Normal\n\n## Fake\n```\ncode\n```")])
    md = render_markdown(rep)
    # collapsed to a single content line: no blank-line split, no line-leading "##" or fence
    content = [ln for ln in md.split("\n") if ln.strip()]
    assert len(content) == 1
    assert not any(ln.lstrip().startswith(("## ", "```")) for ln in md.split("\n"))


def test_code_block_cannot_be_closed_early():
    rep = ReportIR(snapshot=SNAP, blocks=[
        Block(kind="Code", text="safe\n```\n## injected heading", lang="py")])
    md = render_markdown(rep)
    # the fence is longer than the inner ``` run, so nothing escapes the code block
    assert "````py" in md
    assert "## injected heading" in md                # present, but INSIDE the fence (inert)
    # exactly two fence lines (open/close) of the chosen length
    assert md.count("````") == 2


def test_image_alt_bracket_escaped():
    rep = ReportIR(snapshot=SNAP, blocks=[
        Block(kind="Image", text="img]fake](http://evil", media_id="real.png")])
    md = render_markdown(rep)
    assert "\\]" in md and "](real.png)" in md


# --- claims: frozen, cited, resolvable ------------------------------------------
def test_finalise_locates_claim_span_in_md():
    rep = _sample_report()
    md = render_markdown(rep)
    assert len(rep.claims) == 1
    c = rep.claims[0]
    assert md[c.char_start:c.char_end] == c.text == "Total revenue was $1,350.00."


def test_every_claim_cited_and_resolves():
    rep = _sample_report()
    assert claims_cited(rep) and claims_resolve(rep)


def test_repeated_claim_text_resolves_to_distinct_spans():
    p = _ptr()
    b = ReportBuilder()
    b.add_grounded_paragraph("Net was $5.00.", [p])
    b.add_grounded_paragraph("Net was $5.00.", [p])
    rep = b.finalise(SNAP)
    s0, s1 = rep.claims[0], rep.claims[1]
    assert s0.char_start != s1.char_start and claims_resolve(rep)


def test_claim_span_is_its_own_block_not_a_coincidental_match():
    """A claim whose text is ALSO the heading text must bracket the PARAGRAPH block, not the
    earlier heading occurrence (block-anchored, not lexical md.find)."""
    b = ReportBuilder()
    b.add_heading("Net was $5.00.", 1)                 # identical text in a heading first
    b.add_grounded_paragraph("Net was $5.00.", [_ptr()])
    rep = b.finalise(SNAP)
    md = render_markdown(rep)
    c = rep.claims[0]
    assert md[c.char_start:c.char_end] == "Net was $5.00."
    assert c.char_start > md.index("# Net was $5.00.")   # NOT the heading occurrence


def test_uncited_grounded_paragraph_rejected():
    b = ReportBuilder()
    with pytest.raises(ValueError):
        b.add_grounded_paragraph("Total was $9.99.", [])


def test_empty_grounded_paragraph_rejected():
    b = ReportBuilder()
    with pytest.raises(ValueError):
        b.add_grounded_paragraph("   ", [_ptr()])


def test_claim_block_index_out_of_range_rejected():
    with pytest.raises(ValueError):
        finalise_report(SNAP, blocks=[Block(kind="Paragraph", text="present")],
                        claim_specs=[ClaimSpec(block_index=5, fact_pointers=[_ptr()])])


# --- approval freeze -------------------------------------------------------------
def test_approval_digest_stable_and_detects_drift():
    rep = _sample_report()
    d = approval_digest(rep)
    assert verify_frozen(rep, d)
    # an edited report (extra cited claim) no longer matches the frozen digest
    b = ReportBuilder()
    b.add_heading("Quarterly Report", 1)
    b.add_grounded_paragraph("Total revenue was $1,350.00.", [_ptr()])
    b.add_grounded_paragraph("Tax was $1.00.", [_ptr("B2")])
    edited = b.finalise(SNAP)
    assert not verify_frozen(edited, d)


def test_approval_digest_is_semantic_not_layout():
    """The freeze is over the ASSERTION (claim text + cited pointers), not the rendered offsets — a
    cosmetic heading change that leaves the grounded claim untouched keeps the same digest."""
    def build(title):
        b = ReportBuilder()
        b.add_heading(title, 1)
        b.add_grounded_paragraph("Total revenue was $1,350.00.", [_ptr()])
        return b.finalise(SNAP)
    a, b = build("Quarterly Report"), build("A Much Longer Different Title")
    assert a.claims[0].char_start != b.claims[0].char_start      # offsets DID move
    assert approval_digest(a) == approval_digest(b)              # but the freeze holds


def test_report_ir_is_immutable():
    rep = _sample_report()
    with pytest.raises(Exception):
        rep.blocks = []                                # frozen pydantic model


# --- data-dictionary template over AnnotatedSchema -------------------------------
def _schema() -> AnnotatedSchema:
    cols = [
        ColumnAnnotation(column="id", semantic_type=SemanticType("entity_id"),
                         description="row id", confidence=1.0, provenance="human"),
        ColumnAnnotation(column="email", semantic_type=SemanticType("personal_identifier"),
                         description="user email", is_pii=True, pii_kind="email",
                         confidence=1.0, provenance="human"),
    ]
    t = TableAnnotation(table="users", concept="customer", role=TableRole("entity"),
                        columns=cols, summary="customer accounts")
    rel = RelationshipAnnotation(src_table="orders", dst_table="users", via="user_id",
                                 name="order BELONGS TO user", cardinality="N:1",
                                 declared=True, confidence=1.0)
    return AnnotatedSchema(source_id="db", version=1, tables=[t], relationships=[rel],
                           domain="commerce", confirmed_by="human")


def test_data_dictionary_renders_columns_and_relationships():
    rep = data_dictionary(_schema(), SNAP)
    md = render_markdown(rep)
    assert "# Data Dictionary — commerce" in md
    assert "## users — customer" in md
    assert "| id | entity_id | row id | no |" in md
    assert "| email | personal_identifier | user email | email |" in md     # PII surfaced
    assert "## Relationships" in md and "order BELONGS TO user" in md
    assert "N:1" in md


def test_data_dictionary_is_deterministic_md_and_docx():
    rep = data_dictionary(_schema(), SNAP)
    assert render_markdown_bytes(rep) == render_markdown_bytes(rep)
    assert render_docx(rep) == render_docx(rep)


def test_generic_report_grounded_section():
    rep = generic_report("R", [Block(kind="Heading", text="S", level=2),
                               grounded("Total was $7.00.", [_ptr()])], SNAP)
    assert claims_cited(rep) and claims_resolve(rep)
    assert "# R" in render_markdown(rep)


# --- Demo D11 (the real C17→C18 path) -------------------------------------------
def test_d11_grounded_report_cited_and_deterministic():
    from databridge_convert.demos.d11 import run_d11
    r = run_d11()
    assert r["every_claim_cited"] and r["every_claim_resolves"]
    assert r["md_deterministic"] and r["docx_deterministic"]
    assert r["wall_blocks_fabrication"]               # the wall is LIVE here, not vacuous
    # the report combines BOTH sources: a DB aggregate (derived) and a document fact (doc_span)
    assert set(r["claim_pointer_kinds"]) == {"derived", "doc_span"}
    assert r["n_claims"] == 2
