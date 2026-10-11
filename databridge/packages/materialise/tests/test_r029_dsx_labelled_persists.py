"""R-029 (TIDY2 cross-track): `count_query` is a pure READ, so the labelled `/materialise` path
must publish the query artefacts of the `query_cell` cells it ships — or a receipt's pointers
stop re-reading in any later request (a fresh reader has no in-memory answer to fall back on).

The labelled path's reader records every facts answer it was given (`PlatformReader.answers`);
`_finish` persists the shipped ones (`platform._persist_artefacts`) before its pointer check.
Driven over the real workspace facts store (`build_workspace`), with a parameterised read (so the
statement alone is NOT the identity and the artefact is required).
"""
from __future__ import annotations

import pytest

from databridge_core import GroundedValue
from databridge_graph.packs import deployment
from databridge_materialise.platform_reader import PlatformReader
from databridge_materialise.testing import build_workspace


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    with deployment("workspace"):
        w = build_workspace(tmp_path_factory.mktemp("ws_dsx"), with_passages=False)
        yield w
        w.store.close()


def test_the_labelled_path_publishes_the_artefacts_of_the_cells_it_ships(ws):
    from databridge_mcp_ingest.tabular import count as cq
    from databridge_materialise.dataset import Dataset
    from databridge_materialise.platform import _persist_artefacts

    source = next(p.name[: -len("_facts.sqlite")] for p in ws.facts.glob("*_facts.sqlite"))
    with deployment("workspace") as vocab:
        reader = PlatformReader(store=ws.store, vocabulary=vocab, facts_root=ws.facts,
                                passages_root=ws.passages, capture_root=ws.capture,
                                principal="staff")
        table = reader._count_query(source, "SELECT facts_table FROM _facts_tables "
                                            "ORDER BY facts_table").rows[0][0]
        # a read whose identity needs its artefact: unordered rows of the table (not proven)
        ans = reader._count_query(source, f'SELECT * FROM "{table}" LIMIT 5')
    assert ans.pointers and ans.query_artefact, "non-vacuous: the pointers need an artefact"
    cells = [GroundedValue(value=v, pointer=ans.pointers[i * len(ans.columns) + j])
             for i, row in enumerate(ans.rows) for j, v in enumerate(row)]
    ds = Dataset(columns=tuple(f"c{k}" for k in range(len(cells))), rows=(tuple(cells),))
    p = cells[0].pointer
    with pytest.raises(cq.FactsQueryError):
        cq.resolve_query_cell(p, facts_dir=ws.facts)        # a pure read wrote nothing
    assert _persist_artefacts(ds, reader.answers, ws.facts) >= 1
    assert cq.resolve_query_cell(p, facts_dir=ws.facts) == cells[0].value
    # only what is SHIPPED is written: an answer no cell cites is not persisted
    assert len(list(cq.queries_dir(source, ws.facts).glob("*.json"))) == 1


def test_finish_persists_before_its_pointer_check():
    import inspect

    from databridge_materialise.platform import PlatformMaterialiser
    src = inspect.getsource(PlatformMaterialiser._finish)
    assert src.index("_persist_artefacts(") < src.index("pointer_check(")
