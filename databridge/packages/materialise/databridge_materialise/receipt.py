"""The `MaterialisationReceipt` (SYSTEM_DESIGN §6.5), plus the one field this build adds.

**`shaped_by`.** PHASE_PLAN Phase 6 carries a founder decision: for now the DATA AGENT produces
the formats directly, and the deterministic materialiser follows. The decision is a reasonable
staging choice and it costs one field to keep honest:

> A `Receipt` MUST record which pathway produced the dataset. Because on the agent path
> `plan_hash` no longer identifies the bytes — re-running the same request may produce a different
> dataset — and a dataset that is not reproducible must never be mistaken later for one that is.

So `shaped_by: agent | plan`, and `reproducible` is derived from it rather than asserted, because
a boolean somebody can set is a boolean somebody will set. The honest failure is a receipt that
says "agent-shaped, not reproducible"; the dangerous one is a receipt that looks identical to a
compiled dataset.

Everything else here is §6.5 as written. The parts worth reading twice:

* **The three missing classes are reported apart.** `unresolved`, `policy_suppressed` and
  `grounding_dropped` are separate counters and summing them would hide exactly the covert channel
  §6.4's leak check exists to detect.
* **Verdicts are reported.** A dataset built partly on `UNVERIFIABLE` reads has to say so; a
  receipt that reports only a row count asserts a verification it never performed.
* **`unresolved` is a regulatory artefact**, not a defect log: AI Act Art. 10 §2(h) asks for "data
  gaps and shortcomings", and this is that field.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_adapter import Verdict

ShapedBy = Literal["agent", "plan"]


class RowCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    emitted: int = Field(default=0, ge=0)
    rejected_by_policy: int = Field(default=0, ge=0)
    #: Right-censoring guard. A row whose outcome fell inside the window but had NOT yet been
    #: recorded by `T_b - maturity_lag` is rejected here — never silently labelled negative.
    rejected_by_maturity: int = Field(default=0, ge=0)
    #: A cohort row whose decision instant or join key was not disclosed to this principal, so no
    #: point-in-time cut could be computed for it at all.
    rejected_incomplete: int = Field(default=0, ge=0)
    #: E2E run 4 (Q-07): the label window has CLOSED and no outcome was recorded inside it. Apart
    #: from maturity (waiting will not produce a label) and never a negative example.
    rejected_unlabelled: int = Field(default=0, ge=0)

    @property
    def considered(self) -> int:
        return (self.emitted + self.rejected_by_policy + self.rejected_by_maturity
                + self.rejected_incomplete + self.rejected_unlabelled)


class CellCounts(BaseModel):
    """The three missing-reason classes, apart. Summing them hides the covert channel (§6.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grounded: int = Field(default=0, ge=0)
    unresolved: int = Field(default=0, ge=0)
    policy_suppressed: int = Field(default=0, ge=0)
    grounding_dropped: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return (self.grounded + self.unresolved + self.policy_suppressed
                + self.grounding_dropped)


class LeakCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_class_delta_observed: float
    threshold: float
    passed: bool
    #: The column and label classes the worst delta was observed on, so a failure is actionable.
    worst_column: Optional[str] = None
    detail: str = ""


class PointerManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: int = Field(ge=0)
    digest: str
    uri: Optional[str] = None


class ExecutorRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    backend: str
    airgapped: bool = False


class Receipt(BaseModel):
    """What was actually built, and enough to re-run it — or to know that re-running will not
    reproduce it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_version: Literal["walnut.materialisation/v1"] = "walnut.materialisation/v1"
    kind: Literal["MaterialisationReceipt"] = "MaterialisationReceipt"

    plan_hash: str
    #: `agent` or `plan`. There is no default: a receipt that omitted it would read as compiled.
    shaped_by: ShapedBy
    executed_at: datetime
    executor: ExecutorRecord

    rows: RowCounts = RowCounts()
    cells: CellCounts = CellCounts()
    #: Per-verdict counts over the pointers this dataset cites. A dataset built partly on
    #: `UNVERIFIABLE` reads must say so.
    verdicts: dict[str, int] = Field(default_factory=dict)

    missingness_leak_check: Optional[LeakCheckResult] = None
    pointer_manifest: Optional[PointerManifest] = None
    content_hash: str = ""
    policy_applied: str = ""
    policy_bundle_version: str = ""
    purpose: str = ""

    #: Columns in which no row holds a value. A feature emptied by the temporal cut is a finding
    #: about the source, and it is reported rather than dropped.
    columns_fully_absent: tuple[str, ...] = ()
    #: Columns the plan asked for and the compiler or policy refused, with the reason.
    columns_refused: tuple[str, ...] = ()
    #: Per feature column, how many emitted cells are Absent because no version of the value had
    #: been RECORDED by the row's decision instant (the system-time cut) — the source does hold
    #: it. Counted apart so "not known at T_d" is never read as "not in the source" (Q-07).
    features_unknown_at_decision: dict[str, int] = Field(default_factory=dict)

    croissant_uri: Optional[str] = None
    output_uri: Optional[str] = None
    #: Non-fatal observations. Never used to smuggle a partial result past a caller.
    notes: tuple[str, ...] = ()
    #: The agent pathway only: what the agent chose that the plan did not fix.
    shaping_directives: tuple[str, ...] = ()
    reproduced_from: Optional[str] = None

    @model_validator(mode="after")
    def _honest(self) -> "Receipt":
        if self.executed_at.tzinfo is None:
            raise ValueError(
                "executed_at must be timezone-aware: a build instant that depends on the reader's "
                "clock cannot be replayed")
        if self.shaped_by == "agent" and self.reproduced_from is not None:
            raise ValueError(
                "an agent-shaped dataset cannot claim `reproduced_from`: byte-verifying against a "
                "prior receipt is a statement that re-running the plan produced the same bytes, "
                "and on the agent path the plan does not determine the bytes")
        bad = sorted(v for v in self.verdicts if v not in {x.value for x in Verdict})
        if bad:
            raise ValueError(
                f"verdicts names {bad}, which are not in the eight-state lattice. A verdict "
                f"outside the lattice is not a verification result")
        return self

    @property
    def reproducible(self) -> bool:
        """Derived, never asserted. On the agent path `plan_hash` does not identify the bytes."""
        return self.shaped_by == "plan"

    @property
    def honest_summary(self) -> str:
        """One line an operator can read without opening the file."""
        if self.reproducible:
            claim = (f"plan-shaped: re-executing {self.plan_hash[:12]}… against the pinned "
                     f"snapshots must reproduce content_hash {self.content_hash[:12]}…")
        else:
            claim = (f"agent-shaped: NOT reproducible. plan_hash {self.plan_hash[:12]}… names the "
                     f"request, not these bytes; re-running may produce a different dataset")
        return (f"{self.rows.emitted} rows, {self.cells.grounded} grounded cells, "
                f"{self.cells.policy_suppressed} withheld, {self.cells.unresolved} absent — "
                f"{claim}")

    def to_yaml(self) -> str:
        import yaml

        return yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def verdict_counts(verdicts: Any) -> dict[str, int]:
    """Tally an iterable of `Verdict` into the receipt's shape."""
    out: dict[str, int] = {}
    for verdict in verdicts:
        key = verdict.value if isinstance(verdict, Verdict) else str(verdict)
        out[key] = out.get(key, 0) + 1
    return out


__all__ = ["Receipt", "RowCounts", "CellCounts", "LeakCheckResult", "PointerManifest",
           "ExecutorRecord", "ShapedBy", "verdict_counts"]
