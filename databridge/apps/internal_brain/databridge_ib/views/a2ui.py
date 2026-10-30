"""Compile checked view blocks into A2UI v0.9 messages (https://a2ui.org, Apache-2.0).

Three messages: ``createSurface`` (our catalog), ``updateComponents`` (the component tree) and
``updateDataModel`` (every value). The split is the point: the tree only BINDS paths
(``{"path": "/blocks/0"}``), it never carries a value, and the data model is written by this code
from checked blocks. A model never writes either message. The web renders the surface with
``@a2ui/react`` and the Walnut catalog (``components/views/catalog.tsx``), whose components are
inert: no actions, no links out, text as React text nodes.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

VERSION = "v0.9"
#: an identifier only (never fetched); the web registers the same id for its catalog
CATALOG_ID = "https://walnut.sg/a2ui/catalogs/view/v1.json"

COMPONENT = {"facts": "WalnutFacts", "table": "WalnutTable", "chart": "WalnutChart",
             "steps": "WalnutSteps", "timeline": "WalnutTimeline",
             "conversation": "WalnutConversation", "sections": "WalnutSections",
             "sources": "WalnutSources", "missing": "WalnutMissing",
             # composed pages (views/pagespec.py)
             "hero": "WalnutHero", "stats": "WalnutStats", "prose": "WalnutProse",
             "people": "WalnutPeople", "cards": "WalnutCards", "callout": "WalnutCallout"}


def messages(surface_id: str, title: str, meta: Mapping[str, Any],
             blocks: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]
             ) -> list[dict[str, Any]]:
    children = [f"b{i}" for i in range(len(blocks))]
    components: list[dict[str, Any]] = [{
        "id": "root", "component": "WalnutPage", "title": {"path": "/title"},
        "meta": {"path": "/meta"}, "children": children}]
    for i, b in enumerate(blocks):
        components.append({"id": f"b{i}", "component": COMPONENT[b["type"]],
                           "data": {"path": f"/blocks/{i}"}, "sources": {"path": "/sources"}})
    return [
        {"version": VERSION, "createSurface": {"surfaceId": surface_id, "catalogId": CATALOG_ID}},
        {"version": VERSION, "updateComponents": {"surfaceId": surface_id,
                                                  "components": components}},
        {"version": VERSION, "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": {
            "title": title, "meta": dict(meta), "blocks": list(blocks), "sources": list(sources)}}},
    ]


__all__ = ["messages", "CATALOG_ID", "COMPONENT", "VERSION"]
