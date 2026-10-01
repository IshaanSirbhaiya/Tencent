"""Verify a loaded estate: row counts, declared referential integrity, planted orphans.

Every number here comes from a query. The only literals are the *expectations*, and
those are read out of the estate's own ground-truth files — `expected_counts.json`
in finance, `manifest.json` in government — discovered by shape, not by path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backends import EstateConnection, ForeignKey
from .discovery import EstateSpec, RelationalSource

_PCT = re.compile(r"~?\s*(\d+(?:\.\d+)?)\s*%")


@dataclass
class TableCount:
    schema: str
    name: str
    kind: str
    rows: int
    expected: int | None = None

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def verdict(self) -> str:
        if self.expected is None:
            return "no expectation"
        return "OK" if self.rows == self.expected else "MISMATCH"


@dataclass
class IntegrityResult:
    fk: ForeignKey
    declared: bool
    child_rows: int
    non_null: int
    orphans: int
    declared_rate: float | None = None
    note: str = ""
    checked: bool = True  # False when the FK's target schema is not in this connection

    @property
    def rate(self) -> float:
        return (self.orphans / self.non_null) if self.non_null else 0.0

    # A declared orphan rate is a *generator parameter* ("~11%"), not a row count:
    # the realised rate is one draw from a binomial, so a small table wobbles. Half
    # the declared rate, floored at 2 points, is wide enough not to cry wolf and
    # narrow enough to catch a fixture that lost its orphans entirely.
    @property
    def tolerance(self) -> float:
        return max(0.02, (self.declared_rate or 0.0) * 0.50)

    @property
    def verdict(self) -> str:
        if not self.checked:
            return "not checked"
        if self.declared:
            return "OK" if self.orphans == 0 else "VIOLATION"
        if self.declared_rate is None:
            return "measured"
        if self.orphans == 0 and self.declared_rate > 0:
            return "ORPHANS-ABSENT"
        return "OK" if abs(self.rate - self.declared_rate) <= self.tolerance else "OFF-DECLARED-RATE"


@dataclass
class EstateVerification:
    pack: str
    backend: str
    counts: list[TableCount] = field(default_factory=list)
    integrity: list[IntegrityResult] = field(default_factory=list)
    expectations_file: Path | None = None
    unmatched_expectations: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)      # hard: the load is wrong
    observations: list[str] = field(default_factory=list)  # soft: the fixture is not what it says

    @property
    def total_rows(self) -> int:
        return sum(c.rows for c in self.counts if c.kind == "table")


# ---------------------------------------------------------------------------
# expectations
# ---------------------------------------------------------------------------

_COUNT_KEYS = ("counts", "row_counts")


def load_expectations(estate: EstateSpec) -> tuple[dict[str, int], Path | None]:
    """Find the pack's declared counts by shape: a JSON dict under `counts`/`row_counts`.

    Both estates ship one, in different places and under different key names. Neither
    path is written down here.
    """
    candidates: list[Path] = sorted(estate.pack_dir.glob("*.json"))
    candidates += sorted(estate.pack_dir.glob("*/*.json"))
    best: tuple[dict[str, int], Path] | None = None
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        for key in _COUNT_KEYS:
            block = doc.get(key)
            if isinstance(block, dict) and block and all(isinstance(v, int) for v in block.values()):
                if best is None or len(block) > len(best[0]):
                    best = ({str(k): int(v) for k, v in block.items()}, path)
    return best if best else ({}, None)


# ---------------------------------------------------------------------------
# counts
# ---------------------------------------------------------------------------


def count_relations(conn: EstateConnection, rels: list[RelationalSource]) -> list[TableCount]:
    out: list[TableCount] = []
    for rel in rels:
        for name, kind in conn.relations(rel.schema):
            n = conn.scalar(f"SELECT count(*) FROM {conn.quote(rel.schema, name)}")
            out.append(TableCount(rel.schema, name, kind, int(n or 0)))
    return out


# ---------------------------------------------------------------------------
# referential integrity
# ---------------------------------------------------------------------------


def _anti_join(conn: EstateConnection, fk: ForeignKey) -> tuple[int, int, int]:
    child = conn.quote(fk.schema, fk.table)
    parent = conn.quote(fk.ref_schema, fk.ref_table)
    total = int(conn.scalar(f"SELECT count(*) FROM {child}") or 0)
    non_null = int(
        conn.scalar(f'SELECT count(*) FROM {child} WHERE "{fk.column}" IS NOT NULL') or 0
    )
    orphans = int(
        conn.scalar(
            f'SELECT count(*) FROM {child} c LEFT JOIN {parent} p'
            f' ON c."{fk.column}" = p."{fk.ref_column}"'
            f' WHERE c."{fk.column}" IS NOT NULL AND p."{fk.ref_column}" IS NULL'
        )
        or 0
    )
    return total, non_null, orphans


def _parse_ref(text: str) -> tuple[str, str, str] | None:
    parts = str(text).split(".")
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 else None


def undeclared_foreign_keys(estate: EstateSpec) -> list[tuple[ForeignKey, float | None, str]]:
    """The FKs the estate says it deliberately left undeclared, from `sources.yaml`."""
    out: list[tuple[ForeignKey, float | None, str]] = []
    for src in estate.relational:
        for item in src.raw.get("undeclared_foreign_keys") or []:
            if not isinstance(item, dict):
                continue
            child = _parse_ref(item.get("column", ""))
            parent = _parse_ref(item.get("references", ""))
            if not child or not parent:
                continue
            rate = item.get("orphan_rate")
            out.append(
                (
                    ForeignKey(child[0], child[1], child[2], parent[0], parent[1], parent[2]),
                    float(rate) if isinstance(rate, (int, float)) else None,
                    f"declared undeclared-FK on {src.id}",
                )
            )
    return out


def check_integrity(
    conn: EstateConnection, estate: EstateSpec, rels: list[RelationalSource]
) -> list[IntegrityResult]:
    results: list[IntegrityResult] = []
    known = {r.schema for r in rels}

    for rel in rels:
        for fk in conn.foreign_keys(rel.schema):
            total, non_null, orphans = _anti_join(conn, fk)
            results.append(IntegrityResult(fk, True, total, non_null, orphans))

    for fk, rate, note in undeclared_foreign_keys(estate):
        if fk.schema not in known or fk.ref_schema not in known:
            results.append(
                IntegrityResult(
                    fk, False, 0, 0, 0, rate,
                    note + " — target schema is not in this connection", checked=False,
                )
            )
            continue
        total, non_null, orphans = _anti_join(conn, fk)
        results.append(IntegrityResult(fk, False, total, non_null, orphans, rate, note))
    return results


# ---------------------------------------------------------------------------


def verify_estate(
    conn: EstateConnection, estate: EstateSpec, rels: list[RelationalSource], backend_name: str
) -> EstateVerification:
    res = EstateVerification(pack=estate.pack, backend=backend_name)
    expectations, path = load_expectations(estate)
    res.expectations_file = path

    res.counts = count_relations(conn, rels)
    seen: set[str] = set()
    for c in res.counts:
        if c.key in expectations:
            c.expected = expectations[c.key]
            seen.add(c.key)
            if c.rows != c.expected:
                res.problems.append(
                    f"{c.key}: loaded {c.rows} rows, expected {c.expected} "
                    f"({c.rows - c.expected:+d})"
                )

    loaded_schemas = {r.schema for r in rels}
    res.unmatched_expectations = {
        k: v for k, v in expectations.items() if k not in seen and k.split(".")[0] in loaded_schemas
    }
    for k, v in res.unmatched_expectations.items():
        res.problems.append(f"{k}: expected {v} rows but no such relation was loaded")

    res.integrity = check_integrity(conn, estate, rels)
    for r in res.integrity:
        if r.verdict == "VIOLATION":
            res.problems.append(
                f"declared FK {r.fk.label()} has {r.orphans} orphan(s) — integrity is broken"
            )
        elif r.verdict == "ORPHANS-ABSENT":
            res.problems.append(
                f"{r.fk.label()}: the estate plants ~{r.declared_rate:.0%} orphans here and "
                "none are present — the fixture's hazard is missing, not merely off-rate"
            )
        elif r.verdict == "OFF-DECLARED-RATE":
            res.observations.append(
                f"{r.fk.label()}: {r.orphans}/{r.non_null} = {r.rate:.1%} orphans, estate "
                f"declares ~{r.declared_rate:.0%} (tolerance ±{r.tolerance:.0%}). The orphans "
                "are present; the realised rate differs from the generator parameter."
            )
    return res


def prose_orphan_rate(raw: dict[str, Any]) -> float | None:
    """A declared orphan rate that only exists in prose (`orphan_note: '~8% are ...'`)."""
    for key, value in raw.items():
        if "orphan" in key and isinstance(value, str):
            m = _PCT.search(value)
            if m:
                return float(m.group(1)) / 100.0
    return None
