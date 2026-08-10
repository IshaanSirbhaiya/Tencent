"""Demo D11 part 1 (09_FINAL_PRD §27): Database + document facts → grounded MD/DOCX report.

The S→U report path end-to-end, combining TWO grounded sources into ONE report:

  * **Database facts** — disputes from the fintech fixture (in-process SQLite): per-dispute
    amounts (each a ``table_cell`` pointer) and their SUM (a ``derived`` aggregate Fact whose
    pointer carries the per-row provenance + the SUM predicate). C17 ``fact_extractor``.
  * **Document facts** — a dispute-resolution policy PDF-shaped DocumentRecord run through the C15
    extraction pipeline (grounding wall → ``doc_span`` pointers). C15 ``extract``.

Both fact sets feed C17 ``synthesise`` (template-first; emitted only if ``claim_check`` is clean —
a fabrication is quarantined, never shipped), producing grounded prose. Those paragraphs + a
disputes table are finalised into ONE **ReportIR** with **frozen Claims** (C18 ``finalise_report``;
each claim's span resolves in the deterministic md render), then rendered to **Markdown and DOCX**
(C18). The demo asserts: every claim is cited, every claim resolves to its rendered span, and a
**deterministic re-render** is BYTE-IDENTICAL for both md and docx (§13.4).

The model surface is the gold-backend pattern (``FakeBackend`` returning a fact-copying sentence) —
deterministic in CI, exactly as D6/D9 drive their model seams. The faithful sentences are clean by
construction, so the demo ALSO runs a FABRICATED variant through the same path and asserts it is
QUARANTINED (``wall_blocks_fabrication``) — proof the claim_check wall is live here, not vacuous.
(Adversarial breadth — absent-fact + numeric-paraphrase — is EV-GF's job, in C17.)
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text

from databridge_core import SourcePointer
from databridge_harness import FakeBackend, Harness

from ..adapters._docutil import make_document
from ..extract import extract
from ..generate import render_date, render_int, render_name, synthesise
from ..generate.fact_extractor import Fact, sum_fact
from ..generate.templates import get_template
from ..ir import Block
from ..render import (
    ReportBuilder,
    approval_digest,
    claims_cited,
    claims_resolve,
    render_docx,
    render_markdown,
)
from .d9 import fintech_sqlite_engine

# routing cfg for the document-extraction + data-to-text seams (gold-backend, no keys)
_CFG = {"routing": {"document_extraction": "m", "data_to_text": "m"},
        "aliases": {"m": {"model": "openai/gpt-4.1"}},
        "profiles": {"cloud": {}, "airgapped": {}}}

# A dispute-resolution policy as a DocumentRecord (label:value blocks → grounds to doc_span).
_POLICY_BLOCKS = [
    Block(kind="Heading", text="Dispute Resolution Policy", level=1),
    Block(kind="Paragraph", text="Policy ID: POL-2024-007"),
    Block(kind="Paragraph", text="Effective Date: 2024-01-01"),
    Block(kind="Paragraph", text="Maximum Resolution Window: 45 days"),
]
_POLICY_SCHEMA = {
    "properties": {
        "policy_id": {"type": "string", "description": "policy id"},
        "effective_date": {"type": "string", "format": "date", "description": "effective date"},
        "max_resolution_days": {"type": "integer",
                                "description": "maximum resolution window in days"},
    },
    "required": ["policy_id"],
}
_POLICY_GOLD = ('{"policy_id":"POL-2024-007","effective_date":"2024-01-01",'
                '"max_resolution_days":45}')


def _db_facts() -> list[Fact]:
    """SUM of disputed amounts over the fintech ``disputes`` table → an aggregate Fact grounded to
    each row's ``table_cell`` pointer (the number traces to the rows it summarises, never invented)."""
    engine = fintech_sqlite_engine()
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in
                conn.execute(text("SELECT id, amount_disputed FROM disputes "
                                  "WHERE amount_disputed IS NOT NULL ORDER BY id"))]
    pointers = [SourcePointer(source_id="fintech", kind="table_cell", table="disputes",
                              column="amount_disputed", row_id=str(r["id"])) for r in rows]
    total = sum_fact("F1", "total_disputed", rows, "amount_disputed", pointers, kind="money")
    return [total], len(rows)


def _policy_facts(harness: Harness) -> list[Fact]:
    """Run C15 extraction on the policy doc; convert each grounded field to a canonical Fact."""
    doc = make_document(_POLICY_BLOCKS, b"dispute-policy", source_id="dispute_policy")
    res = extract(doc, _POLICY_SCHEMA, harness, name="DisputePolicy", source_id="dispute_policy")
    gr = res.grounded_record()
    facts = [
        Fact(fid="P1", label="policy_id", kind="text",
             rendered=render_name(gr["policy_id"].value), value=gr["policy_id"].value,
             pointer=gr["policy_id"].pointer),
        Fact(fid="P2", label="effective_date", kind="date",
             rendered=render_date(gr["effective_date"].value), value=gr["effective_date"].value,
             pointer=gr["effective_date"].pointer),
        Fact(fid="P3", label="max_resolution_days", kind="int",
             rendered=render_int(gr["max_resolution_days"].value),
             value=gr["max_resolution_days"].value, pointer=gr["max_resolution_days"].pointer),
    ]
    return facts, res.outcome


def _synthesise(sentence: str, facts: list[Fact]):
    """Drive C17 synthesise with a gold-backend sentence (FakeBackend = deterministic CI; the
    claim_check wall still runs on the returned text). Returns the GenerationResult."""
    h = Harness(FakeBackend(sentence), config=_CFG, profile="cloud")
    return synthesise(h, facts, template=get_template("metric_statement"))


def _grounded_paragraph(sentence: str, facts: list[Fact]):
    """A FAITHFUL gold sentence (copies rendered facts verbatim) → it passes the wall and ships."""
    gen = _synthesise(sentence, facts)
    if not gen.ok:                                  # the wall rejected a faithful sentence — a bug
        raise AssertionError(f"D11 synthesis quarantined (should be clean): {gen.reason}")
    return gen.text, gen.facts_used


def run_d11() -> dict:
    # 1) facts from two grounded sources
    db_facts, n_disputes = _db_facts()
    extract_harness = Harness(FakeBackend(_POLICY_GOLD), config=_CFG, profile="cloud")
    policy_facts, policy_outcome = _policy_facts(extract_harness)
    total_fact = db_facts[0]

    # 2) grounded prose via C17 (each sentence copies the rendered facts verbatim)
    db_sentence = (f"Across {n_disputes} open disputes, the total disputed amount was "
                   f"{total_fact.rendered}.")
    policy_sentence = (f"Per policy {policy_facts[0].rendered}, effective "
                       f"{policy_facts[1].rendered}, disputes are resolved within "
                       f"{policy_facts[2].rendered} days.")
    db_text, db_used = _grounded_paragraph(db_sentence, db_facts)
    policy_text, policy_used = _grounded_paragraph(policy_sentence, policy_facts)

    # the wall is LIVE in this report path, not vacuous: a FABRICATED variant (a total that is not a
    # cited fact) over the SAME facts must be QUARANTINED — never assembled into the report.
    fabricated = _synthesise("Across 2 open disputes, the total disputed amount was $9,999.99.",
                             db_facts)
    wall_blocks_fabrication = (not fabricated.ok)

    # 3) assemble ONE ReportIR via C18 (blocks + frozen claims). The report's snapshot anchor is the
    # policy document's (the report documents that source; per-fact provenance lives on the Claims).
    doc = make_document(_POLICY_BLOCKS, b"dispute-policy", source_id="dispute_policy")
    builder = ReportBuilder()
    builder.add_heading("Dispute Report — Q-to-date", 1)
    builder.add_heading("Summary", 2)
    builder.add_grounded_paragraph(db_text, db_used)
    builder.add_grounded_paragraph(policy_text, policy_used)
    builder.add_heading("Policy reference", 2)
    builder.add_table([["Field", "Value"],
                       ["Policy ID", policy_facts[0].rendered],
                       ["Effective date", policy_facts[1].rendered],
                       ["Max resolution (days)", policy_facts[2].rendered]])
    report = builder.finalise(doc.snapshot)

    # 4) render to md + docx, twice each → byte-identity
    md1 = render_markdown(report)
    md2 = render_markdown(report)
    docx1 = render_docx(report)
    docx2 = render_docx(report)

    return {
        "status": "DONE (part 1: md + docx) — DB + document facts → ONE grounded ReportIR",
        "n_disputes": n_disputes,
        "total_disputed": total_fact.rendered,
        "policy_extract_outcome": policy_outcome,
        "n_claims": len(report.claims),
        "every_claim_cited": claims_cited(report),
        "every_claim_resolves": claims_resolve(report),
        "wall_blocks_fabrication": wall_blocks_fabrication,
        "claim_pointer_kinds": sorted({p.kind for c in report.claims for p in c.fact_pointers}),
        "approval_digest": approval_digest(report),
        "md_deterministic": md1.encode("utf-8") == md2.encode("utf-8"),
        "docx_deterministic": docx1 == docx2,
        "markdown": md1,
        "docx_bytes": len(docx1),
        "pdf": "pending (C19)",
    }
