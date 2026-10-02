"""DSD: a tabular delegate can return each row's RECORD TIME as a cited column, on BOTH estates.

`SubTask.record_time` asks the delegate to read, IN THE SAME STATEMENT, the facts store's own
extraction instant (`_facts_meta.extracted_at`) beside each row: the time the platform's copy of
that row became valid. It is a column (metadata), never a filter, and every cell of it has a
`query_cell` pointer that re-reads from the facts file. A table that already has a column of that
name is refused rather than shadowed.

Both estates' facts stores are written by `test_fin_ans6…`'s loader from the repo's seed SQL;
expectations come from those rows and the manifest the loader wrote.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from test_fin_ans6_counts_spellings_direct_facts import ESTATES, _service, estates  # noqa: F401


def _stamp(root, conn: str) -> str:
    """Record the extraction instant in `_facts_meta`, as a real facts store does
    (`tabular.facts.build_facts`); this loader kept it only in the manifest. Returns it."""
    from databridge_mcp_ingest.tabular.facts import sha256_file
    mp = root / "facts" / f"{conn}_facts.json"
    m = json.loads(mp.read_text("utf-8"))
    path = root / "facts" / f"{conn}_facts.sqlite"
    con = sqlite3.connect(path)
    con.execute("INSERT OR REPLACE INTO _facts_meta VALUES ('extracted_at', ?)",
                (m["extracted_at"],))
    con.commit()
    con.close()
    m["sha256"] = sha256_file(path)
    mp.write_text(json.dumps(m), encoding="utf-8")
    return m["extracted_at"]


@pytest.mark.parametrize("est", sorted(ESTATES))
def test_record_time_is_a_cited_column_never_a_filter(estates, est):  # noqa: F811
    from databridge_federated import CatalogueAdmission, SubTask
    from databridge_federated.contracts import RECORD_TIME_FIELD
    from databridge_mcp_ingest.tabular.count import resolve_query_cell

    root, es = estates
    e = es[est]
    extracted = _stamp(root, e["conn"])
    svc = _service(root)
    task = SubTask(id="t1", adapter_agent=e["conn"], entity_kinds=(e["table"],),
                   fields=(e["key"], e["fcol"]), record_time=True, limit=5000)
    res = svc.run_subtask(task, admission=CatalogueAdmission("dana.ops"))
    assert res.error is None, res.error
    assert len(res.rows) == len(e["rows"]), "every row: the record time filters nothing"
    for row in res.rows:
        cell = row.cells[RECORD_TIME_FIELD]
        assert cell.value == extracted
        assert cell.pointer.kind == "query_cell"
        assert resolve_query_cell(cell.pointer, facts_dir=root / "facts") == cell.value
    # without the flag the column is not there (and the statement is unchanged)
    plain = svc.run_subtask(task.model_copy(update={"record_time": False}),
                            admission=CatalogueAdmission("dana.ops"))
    assert all(RECORD_TIME_FIELD not in r.cells for r in plain.rows)


@pytest.mark.parametrize("est", sorted(ESTATES))
def test_a_column_named_like_the_record_time_is_refused(estates, est):  # noqa: F811
    from databridge_federated import CatalogueAdmission, SubTask
    from databridge_federated.contracts import RECORD_TIME_FIELD
    from databridge_mcp_ingest.tabular.facts import sha256_file

    root, es = estates
    e = es[est]
    path = root / "facts" / f"{e['conn']}_facts.sqlite"
    con = sqlite3.connect(path)
    con.execute(f'ALTER TABLE "{e["table"]}" ADD COLUMN "{RECORD_TIME_FIELD}" TEXT')
    con.commit()
    con.close()
    mp = root / "facts" / f"{e['conn']}_facts.json"
    m = json.loads(mp.read_text("utf-8"))
    m["sha256"] = sha256_file(path)
    mp.write_text(json.dumps(m), encoding="utf-8")
    svc = _service(root)
    res = svc.run_subtask(SubTask(id="t1", adapter_agent=e["conn"], entity_kinds=(e["table"],),
                                  fields=(e["key"],), record_time=True),
                          admission=CatalogueAdmission("dana.ops"))
    assert res.error and "reserves for the record time" in res.error
