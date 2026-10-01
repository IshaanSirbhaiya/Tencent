"""Test helpers shared by the adapter-agent tests. A uniquely named module (not `conftest`), so
importing it never collides with another package's conftest when the whole suite is collected."""
from __future__ import annotations


#: Chunk length the fake uses for `governance_chunks.jsonl`. Any positive value works: the tests
#: read spans back from the ledger rather than recomputing them.
CHUNK = 24


def item_id(source_id: str, uri: str) -> str:
    """The document identity `mcp_ingest` uses — imported, never re-derived."""
    from databridge_mcp_ingest.crawl import item_identity

    return item_identity(source_id, uri)


def item_uri(source_id: str, rel: str) -> str:
    return f"file:///{source_id}/{rel}"


def refusal_reason(r: dict) -> str:
    """A refusal line exactly as `mcp_ingest.build` writes it: `chunk <n>: <extractor text>`, where
    the extractor text is `databridge_graph.extractor`'s own wording."""
    if r.get("axis", "node") == "edge":
        body = (f"relation {r['kind']!r}: unknown type — not in the closed vocabulary "
                f"(REFUSED, not coerced)")
    else:
        body = (f"entity {r.get('entity', 'X')!r}: unknown kind {r['kind']!r} — not in the "
                f"closed vocabulary (REFUSED, not coerced)")
    return f"chunk {r.get('chunk', 0)}: {body}"


class RecordingFakeHarness:
    """Stands in for `databridge_harness.Harness`: every result names its model in `.event`."""

    def __init__(self, model: str = "fake/extractor-v1") -> None:
        self.model = model

    def complete_structured(self, task_class, messages, response_model, **kw):
        class _Event:
            pass

        class _Result:
            pass

        e = _Event()
        e.task_class, e.model_used, e.tokens, e.cost_usd, e.cached = (task_class, self.model,
                                                                      7, 0.0, False)
        r = _Result()
        r.event, r.parsed = e, None
        return r


