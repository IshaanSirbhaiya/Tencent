"""The emitted dataset: a rectangle of CELLS, never of values.

Every cell is a `GroundedValue`, a `Withheld` or an `Absent` — the same three states the adapter
returns, carried all the way to the file. That is what makes `require_pointer_for_every_cell`
true of the artefact rather than of the pipeline: there is no stage at which a value is separated
from its pointer and no field on any type here that could hold a bare one.

**Missing reasons are three columns, not one.** `not_in_source`, `grounding_dropped` and
`policy_suppressed` are different facts about the world, the extractor and the reader
respectively, and summing them hides exactly the covert channel the missingness leak check is
looking for. `emit_missing_reason` therefore produces a `<column>_missing_reason` companion whose
values are those three tokens, never a boolean `is_null`.

**Nothing is imputed.** A `None` reaching a consumer with its reason attached is both the honest
answer and the ML-correct one: XGBoost, LightGBM and CatBoost each learn a default branch
direction for missing values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from databridge_adapter import canonical_json, sha256_of
from databridge_core import Absent, GroundedValue, MissingReason, SourcePointer, Withheld

from .derivations import content_digest

Cell = Any                                   # GroundedValue | Withheld | Absent (adapter's `Cell`)

#: The split labels a temporal split assigns. `none` is a real answer for a plan that declares no
#: split; it is not a missing value.
SPLITS = ("train", "val", "test", "none")


def missing_reason(cell: Cell) -> Optional[str]:
    """The reason a cell holds no value, or `None` when it holds one.

    A `Withheld` is `policy_suppressed` — missing NOT at random, and it must never be modelled as
    ordinary absence. An `Absent` is `not_in_source`. `grounding_dropped` is reachable only where
    a value was extracted and failed the wall, which cannot happen on a relational read path; it
    is in the vocabulary because a document shape reaches it, and leaving it out here would make
    the receipt's three-way split unrepresentable for a mixed dataset.
    """
    if isinstance(cell, GroundedValue):
        return None
    if isinstance(cell, Withheld):
        return MissingReason.POLICY_SUPPRESSED.value
    if isinstance(cell, Absent):
        return MissingReason.NOT_IN_SOURCE.value
    raise TypeError(f"not a cell: {type(cell).__name__}")        # pragma: no cover - defensive


def _render(value: Any, float_scale: int) -> Any:
    """A canonical rendering for hashing and for JSONL export.

    `float_scale` exists because a content hash over floats is otherwise a hash over the
    platform's formatting. Decimals are rendered at the declared scale for the same reason, and
    dates as ISO text rather than as whatever the driver handed back.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return format(round(value, float_scale), f".{float_scale}f")
    if isinstance(value, Decimal):
        return format(value, f".{float_scale}f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def cell_digest(cell: Cell, *, float_scale: int) -> str:
    """A cell's contribution to the content hash: its state AND its value, never one alone.

    The state is included because a withheld cell and an absent cell are different datasets even
    when both render as null — and because a policy change that turns one into the other must move
    the content hash, or a receipt could claim byte-reproduction across a policy change.
    """
    if isinstance(cell, GroundedValue):
        body = {"s": "grounded", "v": _render(cell.value, float_scale)}
    elif isinstance(cell, Withheld):
        body = {"s": "withheld", "r": cell.record.reason_code, "p": cell.record.policy_id}
    else:
        body = {"s": "absent", "f": cell.field_path}
    return sha256_of(canonical_json(body))


def pointer_of(cell: Cell) -> Optional[SourcePointer]:
    return cell.pointer if isinstance(cell, GroundedValue) else None


@dataclass(frozen=True)
class Dataset:
    """Columns, rows of cells, and the split assignment. No metadata beyond what a row needs.

    Everything descriptive — counts, verdicts, hashes, which pathway shaped it — lives on the
    `Receipt`, so that a dataset handed to a consumer cannot carry a self-description that
    disagrees with the audit record.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    splits: tuple[str, ...] = ()
    float_scale: int = 6

    def __post_init__(self) -> None:
        for i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"row {i}: arity {len(row)} != {len(self.columns)} columns")
        if self.splits and len(self.splits) != len(self.rows):
            raise ValueError("one split label per row, or none at all")

    # --- shape ------------------------------------------------------------------------
    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def index(self, column: str) -> int:
        return self.columns.index(column)

    def column_cells(self, column: str) -> tuple[Cell, ...]:
        i = self.index(column)
        return tuple(row[i] for row in self.rows)

    # --- accounting -------------------------------------------------------------------
    def cell_counts(self) -> dict[str, int]:
        """The three missing classes reported APART. Summing them would hide the covert channel."""
        counts = {"grounded": 0, "unresolved": 0, "policy_suppressed": 0, "grounding_dropped": 0}
        for row in self.rows:
            for cell in row:
                reason = missing_reason(cell)
                if reason is None:
                    counts["grounded"] += 1
                elif reason == MissingReason.POLICY_SUPPRESSED.value:
                    counts["policy_suppressed"] += 1
                elif reason == MissingReason.GROUNDING_DROPPED.value:
                    counts["grounding_dropped"] += 1
                else:
                    counts["unresolved"] += 1
        return counts

    def fully_absent_columns(self) -> tuple[str, ...]:
        """Columns in which no row holds a value.

        Reported rather than dropped. A feature that is empty under the temporal cut is a real
        finding about the source — usually that it keeps only current state and cannot answer
        what was known at a past instant — and silently removing the column would delete the
        finding along with it.
        """
        out = []
        for column in self.columns:
            cells = self.column_cells(column)
            if cells and all(not isinstance(c, GroundedValue) for c in cells):
                out.append(column)
        return tuple(out)

    def pointers(self) -> tuple[SourcePointer, ...]:
        return tuple(p for row in self.rows for p in (pointer_of(c) for c in row) if p is not None)

    # --- rendering --------------------------------------------------------------------
    def to_records(self, *, emit_missing_reason: bool = True) -> list[dict[str, Any]]:
        """One dict per row: values plus, optionally, the `*_missing_reason` companions."""
        out: list[dict[str, Any]] = []
        for r, row in enumerate(self.rows):
            record: dict[str, Any] = {}
            for column, cell in zip(self.columns, row):
                value = cell.value if isinstance(cell, GroundedValue) else None
                record[column] = _render(value, self.float_scale)
                if emit_missing_reason:
                    record[f"{column}_missing_reason"] = missing_reason(cell)
            if self.splits:
                record["_split"] = self.splits[r]
            out.append(record)
        return out

    def row_digests(self) -> tuple[str, ...]:
        return tuple(
            sha256_of(canonical_json([cell_digest(c, float_scale=self.float_scale) for c in row]))
            for row in self.rows)

    def content_hash(self) -> str:
        """Over the ORDERED row digests, under the plan's `row_order`. See SYSTEM_DESIGN §6.5."""
        return content_digest(self.row_digests())

    def sorted_by(self, order: Sequence[str]) -> "Dataset":
        """Re-order rows under a declared total order over columns.

        A cell with no value sorts LAST within its column rather than raising: a `Withheld` in a
        sort key would otherwise make the whole dataset unorderable for the one principal who
        cannot see that column, and the dataset is still totally ordered because the order
        includes the cohort key.
        """
        idx = [self.index(name) for name in order]

        def key(pair: tuple[int, tuple[Cell, ...]]):
            row = pair[1]
            out: list[tuple[int, str]] = []
            for i in idx:
                cell = row[i]
                if isinstance(cell, GroundedValue):
                    out.append((0, str(_render(cell.value, self.float_scale))))
                else:
                    out.append((1, ""))
            return tuple(out)

        pairs = sorted(enumerate(self.rows), key=key)
        rows = tuple(row for _, row in pairs)
        splits = tuple(self.splits[i] for i, _ in pairs) if self.splits else ()
        return Dataset(columns=self.columns, rows=rows, splits=splits,
                       float_scale=self.float_scale)


def write_jsonl(dataset: Dataset, path, *, emit_missing_reason: bool = True) -> int:
    import json
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = dataset.to_records(emit_missing_reason=emit_missing_reason)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n")
    return len(records)


def write_parquet(dataset: Dataset, path, *, emit_missing_reason: bool = True) -> int:
    """Parquet via pyarrow, or a refusal that names the missing dependency.

    It refuses rather than falling back to CSV: `output.format` is inside `plan_hash`, so quietly
    writing a different format would produce bytes that the plan does not describe.
    """
    from pathlib import Path

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as err:                                   # pragma: no cover - env-dependent
        raise RuntimeError(
            "output.format is 'parquet' and pyarrow is not installed. Refused rather than "
            "silently written as CSV: the format is inside plan_hash, so a different file would "
            "not be the dataset the plan names. Install databridge-materialise[parquet]") from err

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = dataset.to_records(emit_missing_reason=emit_missing_reason)
    if not records:
        table = pa.table({c: pa.array([], type=pa.string()) for c in dataset.columns})
    else:
        columns = list(records[0])
        table = pa.table({c: [r[c] for r in records] for c in columns})
    pq.write_table(table, path)
    return len(records)


__all__ = ["Cell", "Dataset", "SPLITS", "cell_digest", "missing_reason", "pointer_of",
           "write_jsonl", "write_parquet"]
