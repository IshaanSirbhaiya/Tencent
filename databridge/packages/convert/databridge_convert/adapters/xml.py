"""XML adapter → TypedRecord (09_FINAL_PRD §5.1, §21.9).

The LOAD-BEARING control is the parser config: ``resolve_entities=False, no_network=True,
load_dtd=False`` means no external/parameter entity is ever resolved and no file/URL is
read — XXE is disabled regardless of payload or encoding. The raw-byte DOCTYPE/ENTITY
pre-scan is a defence-in-depth FAST-PATH for ASCII/UTF-8 (it can be bypassed by a UTF-16
document, where the parser config still holds). Element→dict convention: attributes under
``"@name"``, element text under ``"#text"``, child elements nested by tag (repeated → list).
"""
from __future__ import annotations

import re

from lxml import etree

from ..ir import TypedRecord
from ..router.registry import AdapterInfo, register_adapter
from ._util import AdapterError, snapshot_for

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


def _elem_to_obj(el) -> object:
    text = (el.text or "").strip()
    children = list(el)
    attrs = dict(el.attrib)
    # A leaf with no attributes is just its text (the common case).
    if not children and not attrs:
        return text
    d: dict = {f"@{k}": v for k, v in attrs.items()}
    if text:                                     # mixed content → keep text under #text
        d["#text"] = text
    for child in children:
        co = _elem_to_obj(child)
        if child.tag in d:
            if not isinstance(d[child.tag], list):
                d[child.tag] = [d[child.tag]]
            d[child.tag].append(co)
        else:
            d[child.tag] = co
    return d


def read(data: bytes) -> TypedRecord:
    if _DOCTYPE_RE.search(data):
        raise AdapterError("xml: DOCTYPE/ENTITY present — refusing (XXE guard, §21.9)")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             dtd_validation=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise AdapterError(f"xml: parse error ({e})") from e
    return TypedRecord(root={root.tag: _elem_to_obj(root)}, snapshot=snapshot_for("xml", data))


def write(ir: TypedRecord) -> bytes:
    def build(tag: str, val: object):
        el = etree.Element(tag)
        if isinstance(val, dict):
            for k, v in val.items():
                if k.startswith("@"):
                    el.set(k[1:], str(v))
                elif k == "#text":
                    el.text = str(v)
                else:
                    for it in (v if isinstance(v, list) else [v]):
                        el.append(build(k, it))
        else:
            el.text = str(val)
        return el

    tag, val = next(iter(ir.root.items()))
    return etree.tostring(build(tag, val))


register_adapter(AdapterInfo(
    format="xml", modality="structured", ir="TypedRecord", lossiness="lossy",
    limits=["XXE/DTD disabled (refused)", "mixed-content ordering lossy (warned)"],
    read=read, write=write))
