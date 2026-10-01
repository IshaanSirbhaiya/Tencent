"""W1 (R-019-extractor-graph-r2 follow-ups): the prose builder upserts SUBJECT mentions id-only
(never fuzzy- or canonically merged — hard constraint 1) and tells the extractor where each chunk
starts (`source_meta["char_offset"]`), so a mention id carries its span in the WHOLE document and
two same-named subjects in one document stay two nodes."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from databridge_mcp_ingest.build import ProseBuilder, chunk_spans
from databridge_mcp_ingest.screen import load_screen

NAME = "Rowan Achterberg"


@pytest.fixture
def workspace_vocab():
    from databridge_graph.packs import deployment

    with deployment("workspace") as vocab:
        yield vocab


def _store(path: Path):
    from databridge_graph.store import GraphStore

    path.parent.mkdir(parents=True, exist_ok=True)
    return GraphStore(str(path))


def _harness(kinds: dict[str, str]):
    """Extraction names the FIRST occurrence of each name in the text it was sent (grounded)."""
    from databridge_harness import FakeBackend, Harness

    def handler(*, model, messages, guided_json) -> str:
        last = messages[-1]["content"]
        if not last.startswith("DOCUMENT TEXT:\n"):
            return json.dumps({})
        text = last[len("DOCUMENT TEXT:\n"):]
        ents = [{"name": n, "kind": k, "char_start": text.find(n),
                 "char_end": text.find(n) + len(n)} for n, k in kinds.items() if n in text]
        return json.dumps({"entities": ents, "relations": []})

    return Harness(FakeBackend(handler), profile="cloud")


def _two_chunk_text() -> tuple[str, list[tuple[int, int]], int]:
    """A document of two chunks with NAME at the same chunk-relative offset in each."""
    base = ("lorem ipsum dolor sit amet " * 120)[:2600]
    spans = chunk_spans(base)
    assert len(spans) == 2, spans
    k = 12
    second = spans[1][0] + k
    chars = list(base)
    for at in (k, second):
        chars[at - 1:at + len(NAME) + 1] = f" {NAME} "        # a whole word, grounded
    text = "".join(chars)
    assert chunk_spans(text) == spans, "placing the names must not move the chunk boundaries"
    return text, spans, second


def _build(store, vocab, text: str, kinds: dict[str, str]):
    builder = ProseBuilder(store, _harness(kinds), source_id="files", vocab=vocab,
                           doc_kind="document", screen=load_screen(), bridges=False)
    return builder.build(item_id="i" * 64, sha="b" * 64, raw_text=text, masked_text=text,
                         title="memo.md", uri="file:///memo.md",
                         retrieved_at=dt.datetime.now(dt.timezone.utc))


def test_two_same_named_subjects_in_two_chunks_stay_two_nodes(tmp_path, workspace_vocab):
    assert workspace_vocab.is_subject("person")
    text, spans, second = _two_chunk_text()
    store = _store(tmp_path / "g.db")
    built = _build(store, workspace_vocab, text, {NAME: "person"})
    people = [r for r in store.open_nodes() if r["kind"] == "person"]
    assert built.subject_mentions == 2, built.refused
    assert len(people) == 2, [p["id"] for p in people]
    # each id carries its span in the WHOLE document (derived from the placement, not written down)
    want = {f"#{a}-{a + len(NAME)}" for a in (12, second)}
    assert {"#" + p["id"].rsplit("#", 1)[1] for p in people} == want


def test_subject_mentions_are_upserted_id_only_and_other_kinds_are_not(
        tmp_path, workspace_vocab, monkeypatch):
    import databridge_graph.merge as merge

    real = merge.upsert_node
    seen: list[tuple[str, bool]] = []

    def spy(store, node, vocab=None, **kw):
        seen.append((str(node.kind), bool(kw.get("id_only", False))))
        return real(store, node, vocab, **kw)

    monkeypatch.setattr(merge, "upsert_node", spy)
    org = "Pellucid Analytics"
    text = f"{NAME} of {org} signed the renewal on Tuesday.\n"
    kinds = {NAME: "person", org: "organisation"}
    assert workspace_vocab.is_subject("person") and not workspace_vocab.is_subject("organisation")
    _build(_store(tmp_path / "g.db"), workspace_vocab, text, kinds)
    by_kind = {k: flag for k, flag in seen if k in kinds.values()}
    assert set(by_kind) == set(kinds.values()), seen          # non-vacuous: both were upserted
    for kind, flag in by_kind.items():
        assert flag == workspace_vocab.is_subject(kind), (kind, flag)
