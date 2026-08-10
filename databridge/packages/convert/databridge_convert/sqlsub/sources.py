"""SQL source adapter (09_FINAL_PRD §15.1): a DB table/query → RowBatch + SnapshotIdentity.

Read-only credentials are REQUIRED — the privilege probe flags a writable source role.
Registered into the C02 registry as the ``sql`` modality-pair for DISCOVERY; because SQL
is connection-based (not byte-based) it is invoked via ``read_sql``, not the bytes
``read()`` of the AdapterInfo (documented deviation).
"""
from __future__ import annotations

from typing import Optional, Union

from sqlalchemy import Engine, create_engine, text

from ..ir import RowBatch
from ..router.registry import AdapterInfo, register_adapter
from .readonly_probe import probe_readonly
from .snapshot import sql_snapshot


def _engine(url_or_engine: Union[str, Engine]) -> tuple[Engine, bool]:
    if isinstance(url_or_engine, Engine):
        return url_or_engine, False
    return create_engine(url_or_engine), True


ROW_CAP = 1_000_000                          # default row cap (§15.8)


def read_sql(
    url_or_engine: Union[str, Engine],
    *,
    table: Optional[str] = None,
    query: Optional[str] = None,
    source_id: Optional[str] = None,
    enforce_readonly: bool = True,
    strict_readonly: bool = False,
    row_cap: Optional[int] = ROW_CAP,
) -> RowBatch:
    """Read a table or query into a RowBatch. Provenance = SnapshotIdentity(server/query/
    as-of/content-hash). ``strict_readonly=True`` REFUSES a writable source (the wall
    blocks, not just warns, §15.1/§15.7); a table read is capped at ``row_cap`` (§15.8)."""
    if not table and not query:
        raise ValueError("read_sql requires `table` or `query`")
    engine, owned = _engine(url_or_engine)
    try:
        if enforce_readonly:
            res = probe_readonly(engine)        # warns + flags if the role can write
            if strict_readonly and res.flagged:
                raise PermissionError(f"refusing writable SQL source (§15.1): {res.detail}")
        if query:
            sql = query
        else:
            ident = engine.dialect.identifier_preparer.quote(table)
            sql = f"SELECT * FROM {ident}" + (f" LIMIT {int(row_cap)}" if row_cap else "")
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(sql)).mappings().all()]
        sid = source_id or f"{engine.dialect.name}:{engine.url.database or 'db'}"
        return RowBatch.from_records(rows, sql_snapshot(sid, sql, rows))
    finally:
        if owned:
            engine.dispose()


register_adapter(AdapterInfo(
    format="sql", modality="structured", ir="RowBatch", lossiness="lossless",
    limits=["connection-based: invoked via sqlsub.read_sql/write_sql, not bytes",
            "read-only credentials required (privilege probe, §15.1)"],
    read=None, write=None))
