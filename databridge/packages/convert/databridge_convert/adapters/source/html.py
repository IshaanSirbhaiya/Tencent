"""HTML SOURCE adapter — bytes → DocumentRecord (C12, §5.2).

lxml-based, with readability-style main-content extraction by DEFAULT (strips nav/ads/
boilerplate) and a ``full_dom`` mode that keeps the whole body. Headings, paragraphs,
tables, lists, and code map to blocks. lxml is configured with entity/network resolution
OFF (§21.8) — HTML parsing does not expand DTD entities, but we parse defensively."""
from __future__ import annotations

from lxml import etree, html as lxml_html

from .._docutil import decode_text, make_document
from ...ir import Block, DocumentRecord

_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "table", "ul", "ol", "pre",
               "blockquote", "li"}


def _safe_parser() -> etree.HTMLParser:
    # no network, no huge-tree blowups; HTML parser does not resolve external entities.
    return etree.HTMLParser(no_network=True, huge_tree=False, recover=True)


def _table_rows(el) -> list[list[str]]:
    rows = []
    for tr in el.iter("tr"):
        cells = [(" ".join(c.itertext())).strip() for c in tr if c.tag in ("td", "th")]
        if cells:
            rows.append(cells)
    return rows


def _walk(el, blocks: list[Block]) -> None:
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = " ".join(el.itertext()).strip()
        if text:
            blocks.append(Block(kind="Heading", level=int(tag[1]), text=text))
        return
    if tag == "p" or tag == "blockquote":
        text = " ".join(el.itertext()).strip()
        if text:
            blocks.append(Block(kind="Paragraph", text=text))
        return
    if tag == "pre":
        blocks.append(Block(kind="Code", text="".join(el.itertext())))
        return
    if tag == "table":
        rows = _table_rows(el)
        if rows:
            blocks.append(Block(kind="Table", rows=rows))
        return
    if tag in ("ul", "ol"):
        items = [" ".join(li.itertext()).strip() for li in el if li.tag == "li"]
        items = [it for it in items if it]
        if items:
            blocks.append(Block(kind="ListBlock", text="\n".join(f"- {it}" for it in items)))
        return
    for child in el:
        _walk(child, blocks)


def read(data: bytes, *, full_dom: bool = False) -> DocumentRecord:
    text = decode_text(data, label="html")
    loss = ["scripts / styles / inline event handlers dropped",
            "images and non-text media dropped"]
    # Parse the UNTRUSTED input ONCE under the safe parser (no network, huge-tree off), then
    # work from that bounded tree — readability must not re-parse raw hostile bytes (review H1).
    safe_root = lxml_html.fromstring(text, parser=_safe_parser())
    safe_html = lxml_html.tostring(safe_root, encoding="unicode")
    if full_dom:
        body = safe_root.find("body")
        content_root = body if body is not None else safe_root
        loss.append("full-DOM mode: boilerplate (nav/ads/footer) RETAINED")
    else:
        from readability import Document as Readability
        summary_html = Readability(safe_html).summary(html_partial=True)
        content_root = lxml_html.fromstring(summary_html, parser=_safe_parser())
        loss.append("readability main-content extraction: nav/ads/boilerplate stripped")

    blocks: list[Block] = []
    _walk(content_root, blocks)
    return make_document(blocks, data, source_id="html", loss=loss)
