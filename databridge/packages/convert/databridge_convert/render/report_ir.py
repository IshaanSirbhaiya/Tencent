"""Finalise a ReportIR — blocks + **frozen Claims** (C18, 09_FINAL_PRD §12.4 / §13.4).

A report is assembled from grounded prose: each grounded paragraph carries the SourcePointers of
the facts it cites (from C17's ``GenerationResult.facts_used``). A Claim is anchored to the BLOCK it
was added for (not by a lexical search of the rendered text — that could land on a coincidental
match in a heading or table cell). ``finalise_report`` renders the deterministic Markdown once, reads
each grounded block's span from the renderer, and sets ``claim.text`` to the **rendered** block text
with ``char_start..char_end`` bracketing exactly that block. So the invariants hold by construction:
``md[claim.char_start:claim.char_end] == claim.text`` AND the span is the claim's OWN block — even if
the renderer sanitised the text.

**Claims are frozen at approval (§12.4).** The returned ``ReportIR`` is an immutable (frozen)
pydantic model. ``approval_digest`` is a **semantic** fingerprint over the claim texts + their cited
pointer identities (NOT the char offsets — a cosmetic layout edit elsewhere in the report must not
break a freeze over an unchanged assertion). ``verify_frozen`` re-checks a report against its digest;
``claims_resolve`` independently checks the span/render consistency.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from databridge_core import SnapshotIdentity, SourcePointer

from ..ir import Block, Claim, ReportIR
from .md_renderer import render_markdown, render_markdown_with_spans


@dataclass
class ClaimSpec:
    """A grounded block to freeze into a Claim: the index of the block (in ``blocks``) whose rendered
    text IS the claim, and the pointers of the facts that ground it (≥1 — an uncited claim is a
    fabrication risk and is rejected at finalise)."""
    block_index: int
    fact_pointers: list[SourcePointer] = field(default_factory=list)


@dataclass
class ReportBuilder:
    """Accumulates blocks and (optionally) the grounded claim over a block, in document order.

    ``add_grounded_paragraph`` appends a Paragraph block AND records a Claim anchored to exactly that
    block. ``add_heading`` / ``add_table`` / ``add_paragraph`` add structure that carries no claim
    (a heading or a raw table is not a grounded prose assertion)."""
    blocks: list[Block] = field(default_factory=list)
    claim_specs: list[ClaimSpec] = field(default_factory=list)

    def add_heading(self, text: str, level: int = 1) -> "ReportBuilder":
        self.blocks.append(Block(kind="Heading", text=text, level=level))
        return self

    def add_paragraph(self, text: str) -> "ReportBuilder":
        self.blocks.append(Block(kind="Paragraph", text=text))
        return self

    def add_grounded_paragraph(self, text: str,
                               fact_pointers: list[SourcePointer]) -> "ReportBuilder":
        if not fact_pointers:
            raise ValueError("a grounded paragraph must cite ≥1 fact pointer (P4 / §12.4 — an "
                             "uncited claim is exactly what the report layer must never emit)")
        if not text or not text.strip():
            raise ValueError("a grounded paragraph must have non-empty text (a claim must cover "
                             "real prose, never an empty span)")
        self.blocks.append(Block(kind="Paragraph", text=text))
        self.claim_specs.append(ClaimSpec(block_index=len(self.blocks) - 1,
                                          fact_pointers=list(fact_pointers)))
        return self

    def add_table(self, rows: list[list[str]]) -> "ReportBuilder":
        self.blocks.append(Block(kind="Table", rows=rows))
        return self

    def add_block(self, block: Block) -> "ReportBuilder":
        self.blocks.append(block)
        return self

    def finalise(self, snapshot: SnapshotIdentity) -> ReportIR:
        return finalise_report(snapshot, self.blocks, self.claim_specs)


def finalise_report(snapshot: SnapshotIdentity, blocks: list[Block],
                    claim_specs: list[ClaimSpec]) -> ReportIR:
    """Build the frozen ReportIR. Each claim is anchored to its block's span in the deterministic
    Markdown render (``claim.text`` = the rendered block text). Raises if a claim cites no pointers,
    its ``block_index`` is out of range, or its block renders empty."""
    draft = ReportIR(snapshot=snapshot, blocks=blocks, claims=[])
    md, spans = render_markdown_with_spans(draft)

    claims: list[Claim] = []
    for spec in claim_specs:
        if not spec.fact_pointers:
            raise ValueError("claim has no fact pointers — every claim must be cited (P4)")
        if spec.block_index not in spans:
            raise ValueError(f"claim block_index {spec.block_index} out of range "
                             f"(report has {len(blocks)} blocks)")
        start, end = spans[spec.block_index]
        text = md[start:end]
        if not text.strip():
            raise ValueError(f"grounded block {spec.block_index} renders empty — a claim must "
                             "cover real prose")
        claims.append(Claim(text=text, char_start=start, char_end=end,
                            fact_pointers=list(spec.fact_pointers)))
    return ReportIR(snapshot=snapshot, blocks=blocks, claims=claims)


def claims_cited(report: ReportIR) -> bool:
    """Every claim cites ≥1 fact pointer (the §12.4 'every claim is cited' demo property)."""
    return all(len(c.fact_pointers) >= 1 for c in report.claims)


def claims_resolve(report: ReportIR) -> bool:
    """Every claim's span indexes its own text in the deterministic Markdown render — the proof
    that a frozen claim points at a real region of the approved report."""
    md = render_markdown(report)
    return all(0 <= c.char_start <= c.char_end <= len(md) and md[c.char_start:c.char_end] == c.text
               for c in report.claims)


def approval_digest(report: ReportIR) -> str:
    """Stable SEMANTIC fingerprint of the frozen claims (text + cited pointer identities), in claim
    order. Two reports with the same claims+citations share a digest; editing a claim's text or its
    pointers changes it (§12.4 freeze). Char offsets are deliberately EXCLUDED so a cosmetic layout
    change elsewhere does not break a freeze over an unchanged assertion."""
    h = hashlib.sha256()
    for c in report.claims:
        h.update(c.text.encode("utf-8"))
        h.update(b"\x1f")
        for p in c.fact_pointers:
            h.update(p.model_dump_json().encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def verify_frozen(report: ReportIR, digest: str) -> bool:
    """True iff ``report``'s claims still match the approval ``digest`` (no post-approval drift)."""
    return approval_digest(report) == digest
