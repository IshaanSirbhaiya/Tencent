"""C14 — email adapter + attachment fan-out + provenance chain (§5.2, §11.6, §17.1)."""
from __future__ import annotations

import pytest
from databridge_core import SourcePointer

from databridge_convert.adapters import documents  # noqa: F401  (registers eml adapter)
from databridge_convert.adapters._util import AdapterError
from databridge_convert.adapters.source import email as e
from databridge_convert.adapters.source import pdf_text
from fixtures.email.build import INVOICE_GOLD, eml_bytes, eml_with_html_body


def test_typed_headers_extracted():
    p = e.parse(eml_bytes())
    assert p.headers["from"] == "billing@acme.example"
    assert p.headers["to"] == ["ap@buyer.example", "accounts@buyer.example"]   # list-typed
    assert p.headers["cc"] == ["manager@buyer.example"]
    assert p.headers["message-id"] == "<order-9921@acme.example>"
    assert "Invoice" in p.headers["subject"]


def test_body_blocks_carry_email_part_pointer():
    p = e.parse(eml_bytes())
    assert any("invoice INV-2024-0042" in b.text for b in p.doc.blocks)
    for ptr in p.doc.block_provenance.values():
        assert ptr.kind == "email_part" and ptr.part == "body"
        assert ptr.message_id == p.message_id


def test_attachment_fans_out_with_parent_pointer():
    atts = e.fan_out(eml_bytes())
    assert len(atts) == 1
    a = atts[0]
    assert a.filename == "invoice-0042.pdf" and a.format_guess == "pdf"
    assert a.parent_pointer.kind == "email_part"
    assert a.parent_pointer.part == "attachment:0"
    assert a.data[:5] == b"%PDF-"


def test_provenance_chain_email_attachment_page_span():
    """The §11.6 chain: email → attachment → page → span resolves through all hops."""
    p = e.parse(eml_bytes())
    att = p.attachments[0]
    adoc = pdf_text.read(att.data)                       # attachment re-enters the router
    # a span pointer into the attachment's OWN IR
    span = SourcePointer(source_id="pdf", kind="doc_span", document_id=adoc.document_id,
                         char_start=0, char_end=len(adoc.blocks[0].text))
    chain = e.chain_pointer(att.parent_pointer, span)
    assert chain.kind == "email_part"                    # hop 1: email
    assert chain.part == "attachment:0"                  # hop 2: attachment
    assert chain.child.kind == "doc_span"                # hop 3+4: page → span
    assert chain.child.document_id == adoc.document_id
    # the invoice gold values are present in the attachment IR (D8 will extract them)
    text = " ".join(b.text for b in adoc.blocks)
    assert INVOICE_GOLD["invoice_number"] in text


def test_chain_pointer_rejects_non_email_parent():
    bad = SourcePointer(source_id="x", kind="doc_span", document_id="d", char_start=0, char_end=1)
    with pytest.raises(AdapterError):
        e.chain_pointer(bad, bad)


def test_html_body_extracted_to_text():
    p = e.parse(eml_with_html_body())
    text = " ".join(b.text for b in p.doc.blocks)
    assert "INV-2024-0042" in text


def test_empty_input_refused():
    with pytest.raises(AdapterError):
        e.parse(b"")


def test_msg_without_library_refuses_loudly():
    import importlib.util
    if importlib.util.find_spec("extract_msg") is not None:
        pytest.skip("extract-msg installed; refusal path not exercised here")
    with pytest.raises(AdapterError):
        e.read_msg(b"\xd0\xcf\x11\xe0")                  # OLE2 magic, but no library
