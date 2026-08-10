"""Demo D5 (09_FINAL_PRD §27): XML → JSON via the container (no-LLM, structural) path.

The XML adapter (C04) reads an order-feed into a TypedRecord (tree preserved); the JSON
adapter writes that TypedRecord to JSON. The router confirms the pair is a clean s2s hop.
The XXE-refusal canary from C04 must still fire — a DTD/entity XML is REFUSED, not parsed."""
from __future__ import annotations

from ..adapters import json as json_adapter
from ..adapters import xml as xml_adapter
from ..adapters._util import AdapterError
from ..router import REGISTRY, TargetSpec, route

ORDER_FEED_XML = (
    b"<?xml version='1.0' encoding='utf-8'?>"
    b"<orders>"
    b"<order id='1001'><customer>Ana</customer><total>129.95</total>"
    b"<items><item><sku>A-1</sku><qty>2</qty></item></items></order>"
    b"<order id='1002'><customer>Ben</customer><total>4.50</total>"
    b"<items><item><sku>B-9</sku><qty>1</qty></item></items></order>"
    b"</orders>")

# the C04 XXE canary — external-entity expansion must be REFUSED (§21.8)
XXE_XML = (b"<?xml version='1.0'?>"
           b"<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
           b"<root>&xxe;</root>")


def run_d5() -> dict:
    # routing decision: xml → json is a clean structured (s2s) hop, no refusals
    decision = route("xml", TargetSpec(modality="structured", format="json"))

    tr = xml_adapter.read(ORDER_FEED_XML)
    json_bytes = json_adapter.write(tr)
    back = json_adapter.read(json_bytes)              # re-open the output (§19 readability)

    # XXE canary: the same reader must REFUSE the entity-bearing document
    xxe_refused = False
    try:
        xml_adapter.read(XXE_XML)
    except AdapterError:
        xxe_refused = True

    return {
        "decision_direction": decision.direction,
        "decision_refusals": decision.refusals,
        "json": json_bytes,
        "roundtrip_root": back.root,
        "source_tree": tr.root,
        "xxe_refused": xxe_refused,
        "xml_registered": REGISTRY.get("xml") is not None,
    }
