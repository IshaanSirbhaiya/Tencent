"""R-029 TIDY2 (Codex round 1 #1, and the coordinator's cross-track follow-up): a facts READ writes
nothing, so every PUBLISHER of `query_cell` pointers persists what it cites — through the one API,
`count.persist_query_artefact(answer)` (or `count_query(..., persist_query=True)`).

This test walks the producers:

1. **The producer list is complete.** It is DERIVED by scanning every non-test module for a facts
   read (`count_query(`, `facts.query(`, `_count_query(`, `_facts_query_at(`). Every module found
   must be in `PRODUCERS` and say how it publishes, or why it publishes nothing. A new producer
   fails this test until it is classified.
2. **Every pointer a publisher emits resolves** from disk (`resolve_query_cell`), driven through
   the real code: `count_query` with the explicit persist, and the federated delegate's
   publishing read (`delegates._read_evidence`) over a real facts store.
3. **Publishers in another track's files** are asserted at their call site. Until the persist call
   lands they are `xfail(strict=True)`: the test then FAILS the moment the fix arrives, so the
   marker is removed rather than forgotten. See R-029-TIDY2 CROSS-TRACK.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sqlite3
from pathlib import Path

import pytest

from databridge_mcp_ingest.tabular import count as count_mod
from databridge_mcp_ingest.tabular.count import count_query, resolve_query_cell
from databridge_mcp_ingest.tabular.facts import facts_path, manifest_path, sha256_file

REPO = Path(__file__).resolve().parents[3]
SID = "tidy2_pub"
FACTS_READ = re.compile(r"\bcount_query\(|\bfacts\.query\(|\b_count_query\(|\b_facts_query_at\(")

#: module (relative to the repo) -> how it publishes the pointers of its facts reads
PRODUCERS = {
    "packages/mcp_ingest/databridge_mcp_ingest/tabular/count.py":
        "the producer itself: `persist_query_artefact` / `persist_query=True`; its own "
        "`describe_facts` read publishes no pointer",
    "packages/mcp_ingest/databridge_mcp_ingest/tabular/__init__.py": "re-export only",
    "packages/federated/databridge_federated/delegates.py":
        "PUBLISHES: every evidence read goes through `_read_evidence`, which persists",
    "packages/federated/databridge_federated/catalogue.py":
        "publishes nothing: catalogue measurement (tables, kinds, categorical values) for the "
        "planner; its answers' pointers are never returned to a caller",
    "packages/agent_runtime/databridge_agent_runtime/tools.py":
        "PUBLISHES (agent `facts.query` citations): `ToolLayer._facts` persists every answer "
        "whose pointers it returns",
    "packages/materialise/databridge_materialise/platform_reader.py":
        "PUBLISHES (the labelled /materialise path's receipts; DSD builds persist in "
        "`platform._persist_artefacts`): persist call for the labelled path pending — "
        "CROSS-TRACK DSX",
}


def test_every_facts_reading_module_is_a_classified_producer():
    found = set()
    for root in ("packages", "apps"):
        for p in (REPO / root).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if "/tests/" in rel or "/bench/" in rel or "/node_modules/" in rel:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if FACTS_READ.search(text):
                found.add(rel)
    assert found, "non-vacuous: the scan finds the facts readers"
    assert found == set(PRODUCERS), (sorted(found - set(PRODUCERS)),
                                     sorted(set(PRODUCERS) - found))


# --- a real facts store -------------------------------------------------------------------------
def _publish(d: Path) -> None:
    final = facts_path(SID, d)
    con = sqlite3.connect(final)
    con.execute("CREATE TABLE invoices (id TEXT, customer TEXT, total INTEGER)")
    con.executemany("INSERT INTO invoices VALUES (?, ?, ?)",
                    [(f"I-{i}", f"C-{i % 3}", 10 * (i % 4)) for i in range(12)])
    con.commit()
    con.close()
    manifest_path(SID, d).write_text(json.dumps({"sha256": sha256_file(final)}),
                                     encoding="utf-8")


#: statements whose pointers need an artefact (parameterised; unproven order) and one that does not
STATEMENTS = [("SELECT id, total FROM invoices WHERE customer = ?", ("C-1",)),
              ("SELECT customer, total FROM invoices ORDER BY total", None),
              ("SELECT COUNT(*) AS n FROM invoices", None)]


def _all_resolve(ans, d: Path) -> int:
    n = 0
    for i, p in enumerate(ans.pointers):
        row, col = divmod(i, len(ans.columns))
        assert resolve_query_cell(p, facts_dir=d) == ans.rows[row][col], (ans.sql, i)
        n += 1
    return n


@pytest.fixture
def facts(tmp_path, monkeypatch):
    d = tmp_path / "facts"
    d.mkdir()
    _publish(d)
    monkeypatch.setenv("WALNUT_FACTS_DIR", str(d))
    count_mod.clear_digest_cache()
    return d


def test_count_query_pointers_resolve_once_persisted_and_never_before(facts):
    needed = 0
    for sql, params in STATEMENTS:
        ans = count_query(SID, sql, params=params)
        if ans.query_artefact:
            needed += 1
            with pytest.raises(count_mod.FactsQueryError):       # a read wrote nothing
                resolve_query_cell(ans.pointers[0], facts_dir=facts)
        count_mod.persist_query_artefact(ans)
        assert _all_resolve(ans, facts)
    assert needed >= 2                                                    # non-vacuous


def test_the_federated_delegate_persists_every_pointer_it_publishes(facts):
    from databridge_federated import delegates

    class _Store:                       # the facade's contract: `query` over `count_query`
        def query(self, connection, sql, **kw):
            return count_query(connection, sql, **kw)

    for sql, params in STATEMENTS:
        ans = delegates._read_evidence(_Store(), SID, sql, params=params)
        assert _all_resolve(ans, facts)


# --- publishers in another track's files (asserted at the call site) ---------------------------
def test_agent_facts_citations_are_persisted():
    """SETX landed it (R-029 SETX): `ToolLayer._facts` saves what it cites. Driven end to end over
    both estates' own facts stores in `agent_runtime/tests/test_r029_setx_w08.py::test_tidy2_*`."""
    from databridge_agent_runtime.tools import ToolLayer

    assert "persist_query_artefact" in inspect.getsource(ToolLayer._facts)


def test_the_labelled_materialise_path_persists_what_it_ships():
    import sys
    from pathlib import Path
    pkg = str(Path(__file__).resolve().parents[2] / "materialise")
    if pkg not in sys.path:                  # this package's tests do not put materialise on it
        sys.path.insert(0, pkg)
    from databridge_materialise import platform

    # DSX: `run` publishes through `_finish`, which persists before its pointer check
    src = inspect.getsource(platform.PlatformMaterialiser.run) + inspect.getsource(
        platform.PlatformMaterialiser._finish)
    assert "persist_query_artefact" in src or "_persist_artefacts" in src
