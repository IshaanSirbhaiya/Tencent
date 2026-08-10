"""Read-only privilege probe (09_FINAL_PRD §15.1).

Sources must use read-only credentials. The probe checks whether the connected role can
WRITE; if it can, the source is refused/flagged (warned + proceed flagged) — a writable
source role is one of the four independent walls (§15.7) being breached. Dialect-aware:
Postgres uses ``has_table_privilege``; other dialects use a transactional write probe.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

from sqlalchemy import Engine, inspect, text


@dataclass(frozen=True)
class ProbeResult:
    read_only: bool
    can_write: bool
    flagged: bool          # True ⇒ a writable source role was detected (§15.1)
    detail: str


def probe_readonly(engine: Engine, *, warn: bool = True) -> ProbeResult:
    dialect = engine.dialect.name
    if dialect == "postgresql":
        tables = inspect(engine).get_table_names()
        if not tables:
            return ProbeResult(True, False, False, "postgres: no user tables to probe")
        with engine.connect() as conn:
            can = conn.execute(text("SELECT has_table_privilege(:t, 'INSERT')"),
                               {"t": tables[0]}).scalar()
        if can:
            res = ProbeResult(False, True, True,
                              f"postgres role has INSERT on {tables[0]} — source should be read-only")
        else:
            res = ProbeResult(True, False, False, "postgres role is read-only")
    else:
        # generic write probe: a CREATE/DROP TEMP table in a transaction
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TEMP TABLE _databridge_probe (x INTEGER)"))
                conn.execute(text("DROP TABLE _databridge_probe"))
            res = ProbeResult(False, True, True,
                              f"{dialect}: write probe succeeded (no read-only role separation)")
        except Exception as e:  # noqa: BLE001
            res = ProbeResult(True, False, False, f"{dialect}: write refused ({e})")

    if res.flagged and warn:
        warnings.warn(f"sql source is NOT read-only — {res.detail} (proceeding, flagged §15.1)",
                      stacklevel=2)
    return res
