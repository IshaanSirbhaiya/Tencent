"""R-029 TIDY2 item 8 (BRN cross-track): the facts file is hashed ONCE per snapshot, not per statement.

`count_query` hashed the whole facts file on EVERY statement (~5 ms at 5 MB; 94 statements per
catalogue build). The digest is now cached per snapshot — keyed by the file's identity (size,
mtime, inode, device) and kept only if the file did not change while it was hashed — and the
integrity guarantees hold:

* a new snapshot (an atomic replace) is hashed again, and its pointers carry ITS digest;
* the manifest check still refuses a file altered after publication;
* a statement whose file changed WHILE it ran is refused (the result must belong to the digest);
* a cached digest is re-verified after `DIGEST_TTL_S`, so an in-place edit that restores the
  file's size and mtime is still caught.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from databridge_mcp_ingest.tabular import count as count_mod
from databridge_mcp_ingest.tabular.count import FactsQueryError, count_query
from databridge_mcp_ingest.tabular.facts import facts_path, manifest_path, sha256_file

SID = "tidy2_facts"


def _publish(d: Path, rows: list[tuple]) -> Path:
    """An atomically published facts store (a new file, then `os.replace`) and its manifest."""
    final = facts_path(SID, d)
    tmp = final.with_name(final.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE invoices (id TEXT PRIMARY KEY, total INTEGER)")
    con.executemany("INSERT INTO invoices VALUES (?, ?)", rows)
    con.commit()
    con.close()
    os.replace(tmp, final)
    manifest_path(SID, d).write_text(json.dumps({"sha256": sha256_file(final),
                                                 "extracted_at": "2026-10-03T00:00:00+00:00"}),
                                     encoding="utf-8")
    return final


@pytest.fixture
def hashed(monkeypatch):
    calls: list[str] = []
    real = count_mod.sha256_file

    def counting(p):
        calls.append(str(p))
        return real(p)

    monkeypatch.setattr(count_mod, "sha256_file", counting)
    count_mod.clear_digest_cache()
    yield calls
    count_mod.clear_digest_cache()


def test_many_statements_hash_the_file_once(tmp_path, hashed):
    final = _publish(tmp_path, [(f"I-{i}", i) for i in range(50)])
    answers = [count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)
               for _ in range(20)]
    assert len(hashed) == 1                                                # once, not 20 times
    want = sha256_file(final)
    assert {a.facts_sha256 for a in answers} == {want}
    assert all(p.capture_digest == want for a in answers for p in a.pointers)


def test_a_new_snapshot_is_hashed_again_and_cited_by_its_own_digest(tmp_path, hashed):
    _publish(tmp_path, [("I-1", 1)])
    first = count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)
    final = _publish(tmp_path, [("I-1", 1), ("I-2", 2)])
    second = count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)
    assert len(hashed) == 2 and second.scalar == 2
    assert second.facts_sha256 == sha256_file(final) != first.facts_sha256


def test_a_file_altered_after_publication_is_still_refused(tmp_path, hashed):
    final = _publish(tmp_path, [("I-1", 1)])
    count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)
    con = sqlite3.connect(final)
    con.execute("INSERT INTO invoices VALUES ('I-9', 9)")
    con.commit()
    con.close()
    with pytest.raises(FactsQueryError, match="manifest"):
        count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)


def test_an_edit_that_restores_size_and_mtime_is_caught_after_the_ttl(tmp_path, hashed,
                                                                      monkeypatch):
    final = _publish(tmp_path, [("I-1", 1), ("I-2", 2)])
    count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)
    st = os.stat(final)
    raw = bytearray(final.read_bytes())
    i = raw.rfind(b"I-2")
    raw[i:i + 3] = b"I-3"                                    # same size, same page count
    with open(final, "r+b") as fh:                           # in place: the same inode
        fh.write(raw)
    os.utime(final, ns=(st.st_atime_ns, st.st_mtime_ns))     # the mtime restored
    assert os.stat(final).st_size == st.st_size
    clock = [count_mod._now() + count_mod.DIGEST_TTL_S + 1]
    monkeypatch.setattr(count_mod, "_now", lambda: clock[0])
    with pytest.raises(FactsQueryError, match="manifest"):
        count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)


def test_a_file_replaced_while_the_statement_ran_is_refused(tmp_path, hashed, monkeypatch):
    _publish(tmp_path, [("I-1", 1)])
    real_run = count_mod._run

    def racing(path, *a, **k):
        out = real_run(path, *a, **k)
        _publish(tmp_path, [("I-1", 1), ("I-2", 2)])         # a new snapshot lands meanwhile
        return out

    monkeypatch.setattr(count_mod, "_run", racing)
    with pytest.raises(FactsQueryError, match="changed while"):
        count_query(SID, "SELECT COUNT(*) FROM invoices", facts_dir=tmp_path)
