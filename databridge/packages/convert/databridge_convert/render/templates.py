"""Report templates over the IR (C18, 09_FINAL_PRD §12.4).

Two shipped templates:

  * **data_dictionary(AnnotatedSchema)** — "the system documents the customer's data" (§12.4: cheap,
    loved demo). A deterministic template OVER an ``AnnotatedSchema`` itself: one section per table
    (concept + role + a column grid of name / semantic type / description / PII), then a
    relationships grid. It documents schema *metadata*, not row values, so it carries structure, not
    per-fact Claims — the grounding here is that every line traces to a field of the confirmed
    schema (``confirmed_by``), which the report header states.

  * **generic_report(...)** — a thin wrapper over ``ReportBuilder`` for a titled report whose body is
    grounded prose (each paragraph cites its facts) interleaved with structural blocks.

Both return a finalised, frozen ``ReportIR`` (claims located against the deterministic md render).
"""
from __future__ import annotations

from typing import Optional, Sequence, Union

from databridge_core import AnnotatedSchema, SnapshotIdentity, SourcePointer

from ..ir import Block, ReportIR
from .report_ir import ClaimSpec, ReportBuilder, finalise_report


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def data_dictionary(schema: AnnotatedSchema, snapshot: SnapshotIdentity, *,
                    title: Optional[str] = None) -> ReportIR:
    """Render a confirmed ``AnnotatedSchema`` as a data-dictionary report (deterministic)."""
    blocks: list[Block] = []
    head = title or f"Data Dictionary — {schema.domain}"
    blocks.append(Block(kind="Heading", text=head, level=1))
    src = schema.confirmed_by or "UNCONFIRMED"
    blocks.append(Block(kind="Paragraph",
                        text=f"Source: {schema.source_id} (v{schema.version}); confirmed by {src}."))

    for table in schema.tables:
        blocks.append(Block(kind="Heading", text=f"{table.table} — {table.concept}", level=2))
        if table.summary:
            blocks.append(Block(kind="Paragraph", text=table.summary))
        rows: list[list[str]] = [["Column", "Semantic Type", "Description", "PII"]]
        for col in table.columns:
            pii = col.pii_kind if col.is_pii else "no"
            sem = getattr(col.semantic_type, "value", str(col.semantic_type))  # clean enum value
            rows.append([col.column, sem, col.description, pii])
        blocks.append(Block(kind="Table", rows=rows))

    if schema.relationships:
        blocks.append(Block(kind="Heading", text="Relationships", level=2))
        rrows: list[list[str]] = [["Name", "From", "To", "Via", "Cardinality", "Declared"]]
        for rel in schema.relationships:
            card = getattr(rel.cardinality, "value", str(rel.cardinality))
            rrows.append([rel.name, rel.src_table, rel.dst_table, rel.via,
                          card, _yn(rel.declared)])
        blocks.append(Block(kind="Table", rows=rrows))

    # data dictionary documents metadata → structural, no per-fact Claims (see module docstring).
    return finalise_report(snapshot, blocks, claim_specs=[])


# A report section: a heading, a structural table, a raw paragraph, or a GROUNDED paragraph.
GroundedParagraph = tuple                        # ("grounded", text, [SourcePointer, ...])
Section = Union[Block, GroundedParagraph]


def generic_report(title: str, sections: Sequence[Section],
                   snapshot: SnapshotIdentity) -> ReportIR:
    """Build a titled report. A section is either a ``Block`` (heading/paragraph/table, added as-is)
    or the tuple ``("grounded", text, [pointers])`` — a grounded paragraph that becomes a cited
    Claim. Returns the finalised, frozen ReportIR."""
    b = ReportBuilder()
    b.add_heading(title, level=1)
    for sec in sections:
        if isinstance(sec, Block):
            b.add_block(sec)
        elif isinstance(sec, tuple) and sec and sec[0] == "grounded":
            _, text, pointers = sec
            b.add_grounded_paragraph(text, list(pointers))
        else:
            raise TypeError(f"unknown section: {sec!r}")
    return b.finalise(snapshot)


def grounded(text: str, pointers: list[SourcePointer]) -> GroundedParagraph:
    """Sugar for a grounded-paragraph section in ``generic_report``."""
    return ("grounded", text, pointers)
