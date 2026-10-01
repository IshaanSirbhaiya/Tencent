"""E2E run 4, Q-01 root cause: concurrent first-use writes of one query artefact must not fail.

The temp file used to be named by process id only, so threads in one gateway raced on it and the
loser raised ``PermissionError`` — which surfaced as a catalogue with no tables.
"""
from __future__ import annotations

import json
import threading

from databridge_mcp_ingest.tabular import count


def test_concurrent_first_writes_of_one_artefact_all_succeed(tmp_path):
    body = {"query_hash": "q" * 16, "sql": "SELECT 1", "rows": 1}
    workers = 16
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []
    refs: list[str] = []

    def write() -> None:
        barrier.wait()
        try:
            refs.append(count._write_query_artefact("src", tmp_path, "q" * 16, body))
        except BaseException as exc:  # noqa: BLE001 — the test reports any failure
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(refs) == workers                               # non-vacuous: every writer returned
    final = count.queries_dir("src", tmp_path) / f"{'q' * 16}.json"
    assert json.loads(final.read_text(encoding="utf-8")) == body
    assert not list(final.parent.glob("*.tmp"))               # no temp file left behind
