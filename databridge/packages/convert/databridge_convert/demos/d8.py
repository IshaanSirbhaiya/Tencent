"""Demo D8 (09_FINAL_PRD §27, §11.6): Email + attachment → CRM record, provenance CHAIN.

.eml (C14) → the invoice PDF attachment RE-ENTERS the router as its own source → C15 extracts a
CRM record against a SQL-DDL target → every CRM field's provenance resolves through ALL FOUR
hops: **email → attachment → page → span**.

  hop 1 (email)      : email_part pointer carries the message_id
  hop 2 (attachment) : email_part.part == "attachment:0"
  hop 3 (page)       : the span's char range maps to a page via the attachment's page_map
  hop 4 (span)       : email_part.child is the doc_span inside the attachment IR

The CRM field names differ from the invoice labels (customer_name ← "Bill To"), so the mapping
is done by the LLM stage and CONFIRMED by grounding — no value enters the CRM record unless it
appears in the attachment.
"""
from __future__ import annotations

from typing import Optional

from databridge_harness import Harness, FakeBackend

from ..adapters.source import email as s_email
from ..adapters.source import pdf_text
from ..extract import extract

# the CRM target, as SQL DDL (exercises the SQL-DDL → TargetRecordModel compiler).
CRM_DDL = (
    "CREATE TABLE crm_deal ("
    " invoice_number VARCHAR NOT NULL,"
    " customer_name VARCHAR NOT NULL,"
    " deal_amount NUMERIC NOT NULL)"
)


def _page_for_span(doc, char_start: int) -> Optional[int]:
    """hop 3 — resolve which page a span falls on via the attachment's page_map (§11.2). Returns
    None on no match (NO fallback — a vacuous 'page 0' would make the hop assert nothing)."""
    if not doc.page_map:
        return None
    for ps in doc.page_map:
        if ps.char_start <= char_start < ps.char_end:
            return ps.page
    return None


def run_d8(harness: Optional[Harness] = None) -> dict:
    from fixtures.email.build import INVOICE_GOLD, eml_bytes

    cfg = {"routing": {"document_extraction": "m"}, "aliases": {"m": {"model": "openai/gpt-4.1"}},
           "profiles": {"cloud": {}, "airgapped": {}}}
    # gold backend maps invoice fields → CRM field names (the LLM's job; grounding confirms).
    gold = {"invoice_number": INVOICE_GOLD["invoice_number"],
            "customer_name": INVOICE_GOLD["bill_to"],
            "deal_amount": float(INVOICE_GOLD["total_due"])}
    import json
    harness = harness or Harness(
        FakeBackend(lambda model, messages, guided_json: json.dumps(gold)),
        config=cfg, profile="cloud")

    # 1) parse the email; 2) the attachment RE-ENTERS the router as its own source.
    parsed = s_email.parse(eml_bytes())
    att = parsed.attachments[0]
    parent = att.parent_pointer                                  # email_part(attachment:0)
    attachment_doc = pdf_text.read(att.data)

    # 3) C15 extracts the CRM record from the attachment IR.
    res = extract(attachment_doc, CRM_DDL, harness, name="crm_deal", source_id=att.source_id)

    # 4) build + verify the 4-hop chain for every grounded CRM field.
    chains = {}
    all_four_hops = True
    for name, fr in res.fields.items():
        if not fr.passed or fr.pointer is None:
            all_four_hops = False
            continue
        chain = s_email.chain_pointer(parent, fr.pointer)        # email→attachment→(span)
        # hop 3 uses the field's REAL span start (no char_start=0 shortcut) so the page lookup
        # genuinely discriminates on a multi-page attachment.
        page = _page_for_span(attachment_doc,
                              fr.pointer.char_start if fr.pointer.kind == "doc_span" else -1)
        hops = {
            "email": chain.kind == "email_part" and bool(chain.message_id),
            "attachment": chain.part == "attachment:0",
            "page": page is not None,
            "span": chain.child is not None and chain.child.kind == "doc_span",
        }
        chains[name] = {"message_id": chain.message_id, "part": chain.part,
                        "page": page, "span": (chain.child.char_start, chain.child.char_end)
                        if chain.child else None, "hops_ok": all(hops.values())}
        all_four_hops = all_four_hops and all(hops.values())

    return {
        "outcome": res.outcome,
        "crm_record": res.record(),
        "target": res.target,
        "message_id": parsed.message_id,
        "attachment": {"filename": att.filename, "format": att.format_guess,
                       "source_id": att.source_id},
        "provenance_chains": chains,
        "chain_resolves_all_four_hops": all_four_hops and bool(chains),
    }
