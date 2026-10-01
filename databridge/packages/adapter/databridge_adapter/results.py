"""What the port returns: `GroundedRows`, `AdapterResult`, `ReadPlan`, `Cost`, `PointerResolution`.

The Core exists to make one guarantee: **no cell leaves without a pointer.** Not "usually";
structurally. There is no field on any type in this module that carries a bare value.

A note on what the `GroundedRows` validator does and does not do, because the spec is emphatic
about it and it is the sentence people skip: the validator checks that every cell is *accounted
for* - arity, and that a withheld or absent cell names the column it stands in. It does **not**
check that a `GroundedValue`'s value is what its pointer cites. That is `assert_grounded`
(`grounding.py`), it runs at emission against the bytes the read returned, and no amount of
structural validation substitutes for it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import Absent, GroundedValue, SnapshotIdentity, SourcePointer, Withheld

from .errors import BudgetExceeded
from .verdicts import Verdict

#: A cell in any grounded result set (spine D4). Exactly one of the three states - never a
#: degenerate `GroundedValue`, because `pointer` is non-Optional and withholding is not a citation.
Cell = Union[GroundedValue, Withheld, Absent]


def _column_of(path: str) -> str:
    """The column a dotted field path names. ``core_banking.party.notes`` -> ``notes``."""
    return path.rsplit(".", 1)[-1]


class Cost(BaseModel):
    """What a read cost, or is estimated to cost. Additive.

    ``bytes_egressed`` is tracked separately from rows because the egress class is about bytes
    leaving a boundary, not about how many rows they were arranged into.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    llm_tokens: int = Field(default=0, ge=0)
    wall_ms: int = Field(default=0, ge=0)
    rows: int = Field(default=0, ge=0)
    bytes_egressed: int = Field(default=0, ge=0)
    usd: float = Field(default=0.0, ge=0.0)

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            llm_tokens=self.llm_tokens + other.llm_tokens,
            wall_ms=self.wall_ms + other.wall_ms,
            rows=self.rows + other.rows,
            bytes_egressed=self.bytes_egressed + other.bytes_egressed,
            usd=self.usd + other.usd,
        )


class Budget(BaseModel):
    """Admission control (`ADAPTER_AGENT_SPEC` §10): refuse BEFORE the call, not after.

    After the fact is too late in the only way that matters - for an egress-classed source the
    rows have already left, and no amount of accounting brings them back. The Policy layer (3C)
    owns where budgets come from; the type lives here because the port is what enforces them.

    ``None`` on a limit means *unbounded for this dimension*, which is a decision the caller makes
    explicitly; there is no partially-specified budget that silently permits everything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rows: Optional[int] = Field(default=None, ge=0)
    max_wall_ms: Optional[int] = Field(default=None, ge=0)
    max_llm_tokens: Optional[int] = Field(default=None, ge=0)
    max_bytes_egressed: Optional[int] = Field(default=None, ge=0)
    max_usd: Optional[float] = Field(default=None, ge=0.0)

    def admit(self, estimate: Cost, *, source_id: str = "") -> Cost:
        """Return the estimate, or raise `BudgetExceeded` naming the dimension that blew."""
        where = f" for source {source_id!r}" if source_id else ""
        checks = (
            ("rows", estimate.rows, self.max_rows),
            ("wall_ms", estimate.wall_ms, self.max_wall_ms),
            ("llm_tokens", estimate.llm_tokens, self.max_llm_tokens),
            ("bytes_egressed", estimate.bytes_egressed, self.max_bytes_egressed),
            ("usd", estimate.usd, self.max_usd),
        )
        for name, value, cap in checks:
            if cap is not None and value > cap:
                raise BudgetExceeded(
                    f"budget{where} exhausted on {name}: estimate {value} exceeds {cap}. The read "
                    f"is refused before it runs - after it runs the cost is already incurred and, "
                    f"for an egress-classed source, the rows have already left")
        return estimate


class GroundedRows(BaseModel):
    """Every cell carries its pointer. There is no path that returns a bare value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...] = ()
    snapshot: SnapshotIdentity

    @model_validator(mode="after")
    def _every_cell_accounted_for(self) -> "GroundedRows":
        if not self.columns:
            raise ValueError("GroundedRows must name its columns: unnamed cells cannot be cited")
        dupes = sorted({c for c in self.columns if self.columns.count(c) > 1})
        if dupes:
            raise ValueError(f"duplicate columns {dupes}: a pointer could not say which was cited")

        for r_i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"row {r_i}: arity {len(row)} != {len(self.columns)} columns")
            for c_i, cell in enumerate(row):
                column = self.columns[c_i]
                # A withheld or absent cell NAMES the question that was asked. If it names a
                # different column than the one it sits in, the denial has been attributed to the
                # wrong field - which is worse than no record, because it reads as an answer.
                if isinstance(cell, Withheld):
                    if _column_of(cell.record.field_path) != column:
                        raise ValueError(
                            f"row {r_i} column {column!r} holds a Withheld naming "
                            f"{cell.record.field_path!r}: a denial attributed to the wrong field "
                            f"is not an audit record")
                elif isinstance(cell, Absent):
                    if _column_of(cell.field_path) != column:
                        raise ValueError(
                            f"row {r_i} column {column!r} holds an Absent naming "
                            f"{cell.field_path!r}: it must name the question that was asked")
                elif not isinstance(cell, GroundedValue):            # pragma: no cover - typing
                    raise ValueError(
                        f"row {r_i} column {column!r} is a bare {type(cell).__name__}; a cell is "
                        f"GroundedValue | Withheld | Absent and nothing else")
        return self

    @property
    def withheld_count(self) -> int:
        return sum(1 for row in self.rows for c in row if isinstance(c, Withheld))

    @property
    def absent_count(self) -> int:
        return sum(1 for row in self.rows for c in row if isinstance(c, Absent))


class ReadPlan(BaseModel):
    """What `plan()` returns: cost/row estimate plus the exact operations. Deterministic.

    Bound to the request by ``read_hash`` so a plan cannot be shown for one read and executed for
    another. ``operations`` is a human- and diff-readable list of the steps the Core will run -
    the thing an operator reads before approving a read of a `RESTRICTED_INTERNAL` source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    read_hash: str
    source_id: str
    entity: str
    operations: tuple[str, ...]
    estimated_rows: int = Field(default=0, ge=0)
    estimated_cost: Cost = Cost()
    requires_capture: bool = False
    #: True when the plan cannot promise a complete incremental read - a lossy watermark, a known
    #: sequence gap, at-least-once delivery. Declared so a caller cannot infer completeness from
    #: the absence of an error.
    completeness_caveat: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "ReadPlan":
        if not self.read_hash.strip():
            raise ValueError("a plan must name the read it plans, by read_hash")
        if not self.operations:
            raise ValueError(
                "a plan with no operations explains nothing; if the read is a no-op, say so as an "
                "operation")
        if self.estimated_cost.llm_tokens:
            raise ValueError(
                "a ReadPlan may not estimate LLM tokens: planning is deterministic and no model "
                "runs in the read path (D1). A model in the Shell plans the REQUEST, not the read")
        return self


class AdapterResult(BaseModel):
    """What `resolve()` returns: grounded rows plus what they cost.

    The invariant worth the class existing: ``rows.snapshot.query_hash`` **is** the request's
    ``read_hash`` (spine D2). That is what makes a pointer verifiable by re-execution - replay the
    request against the pinned snapshot and compare digests. A result whose snapshot cites a
    different read is unverifiable by construction, so it is refused here rather than discovered
    by an auditor in 2029.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    read_hash: str
    rows: GroundedRows
    cost: Cost = Cost()
    #: Non-fatal observations - a truncated page, a lossy cursor, a skipped known gap. Never used
    #: to smuggle a partial result past a caller: a partial result that looks complete is the only
    #: outcome worse than an error, so anything that makes the result INCOMPLETE raises instead.
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _snapshot_cites_this_read(self) -> "AdapterResult":
        if not self.read_hash.strip():
            raise ValueError("a result must name the read it answers, by read_hash")
        if self.rows.snapshot.query_hash != self.read_hash:
            raise ValueError(
                f"snapshot.query_hash {self.rows.snapshot.query_hash!r} != request read_hash "
                f"{self.read_hash!r}. read_hash BECOMES query_hash (D2); when they differ, no "
                f"pointer in this result can be verified by re-executing the request that "
                f"produced it")
        return self


class PointerResolution(BaseModel):
    """Re-resolving ONE existing pointer (`SYSTEM_DESIGN` §1.7, `ADAPTER_AGENT_SPEC` §3).

    **Deliberately not named `ResolutionResult`.** That name already means IDENTITY resolution and
    carries ``requires_human_selection: Literal[True]``. Merging the two would put the hard
    constraint on identity verification one autocomplete away from a data-fetch type, and the two
    are kept lexically distant for exactly that reason. Nothing in this class confirms anyone's
    identity, and nothing in it ever should.

    The verdict and the cell are constrained against each other, because the pairs that look
    harmless are the ones that mislead:

    * `FORBIDDEN` must carry a `Withheld` - policy denied it, so no value may accompany it;
    * `VANISHED` / `EXPIRED` must not carry a value - there is nothing to carry;
    * `VERIFIED` / `VERIFIED_AS_OF_CAPTURE` must carry a `GroundedValue` - a positive verdict with
      nothing behind it is an assertion, not a verification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pointer: SourcePointer
    verdict: Verdict
    resolved_at: datetime
    cell: Optional[Cell] = None
    snapshot: Optional[SnapshotIdentity] = None
    #: Why, in operator language. Required for every verdict that is not a plain pass, because
    #: "unverifiable" without a reason is indistinguishable from "we did not try".
    note: Optional[str] = None

    @model_validator(mode="after")
    def _verdict_and_cell_agree(self) -> "PointerResolution":
        v, cell = self.verdict, self.cell
        if v is Verdict.FORBIDDEN:
            if not isinstance(cell, Withheld):
                raise ValueError(
                    "FORBIDDEN must carry a Withheld record: a denial without a policy id, reason "
                    "code and principal is not an audit record, and a value beside a denial "
                    "defeats the denial")
        elif v in (Verdict.VANISHED, Verdict.EXPIRED):
            if isinstance(cell, GroundedValue):
                raise ValueError(
                    f"{v.value.upper()} carries a GroundedValue: if a value came back, the "
                    f"pointer did not vanish and its window did not close")
        elif v.is_positive:
            if not isinstance(cell, GroundedValue):
                raise ValueError(
                    f"{v.value.upper()} without a GroundedValue is an assertion, not a "
                    f"verification - the re-read has to produce the value it claims matched")
        if v is not Verdict.VERIFIED and not (self.note or "").strip():
            raise ValueError(
                f"verdict {v.value!r} requires a note: an operator acting on anything other than "
                f"a plain VERIFIED needs to know what moved, and 'unverifiable' with no reason is "
                f"indistinguishable from 'we did not try'")
        if self.resolved_at.tzinfo is None:
            raise ValueError("resolved_at must be timezone-aware: a verification instant that "
                             "depends on the reader's clock cannot be replayed")
        return self

    @property
    def must_escalate(self) -> bool:
        return self.verdict.must_escalate


def cell_value(cell: Cell) -> Any:
    """The value behind a cell, or ``None``.

    Exists so that ``isinstance`` checks live in one place. Note what it does NOT do: it gives a
    withheld cell and an absent cell the same answer, which is fine HERE because the caller still
    holds the cell - but it is why nothing downstream may branch on this function's result alone
    (spine D4, and O1b on why the distinction is sometimes deliberately erased at object level).
    """
    return cell.value if isinstance(cell, GroundedValue) else None
