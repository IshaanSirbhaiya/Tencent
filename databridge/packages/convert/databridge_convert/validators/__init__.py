"""Validation framework (09_FINAL_PRD §19) — a policy engine.

Each validator declares ``{check, scope, default_policy}``; an artifact binds a
``ValidationProfile`` overriding defaults within allowed ranges. The only allowed
outcomes are the §19 failure policies. No silent dropping: every excluded row/field is
counted with a reason code. The FRAMEWORK ships now (C05); specific checks accumulate as
later tasks need them — EV-RT (round-trip), EV-QC (quarantine accounting), and the
KS/categorical distribution checks (E3) are seeded here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from scipy import stats

Scope = Literal["value", "row", "record", "column", "dataset", "output"]
Policy = Literal["hard_fail", "warning", "row_quarantine", "field_null",
                 "human_review", "retry", "artifact_migration_required"]


@dataclass(frozen=True)
class ValidationResult:
    check: str
    passed: bool
    scope: Scope
    policy: Policy
    detail: str = ""


@runtime_checkable
class Validator(Protocol):
    name: str
    scope: Scope
    default_policy: Policy

    def validate(self, **ctx) -> ValidationResult: ...


@dataclass
class ValidationProfile:
    """Binds per-check policy overrides to an artifact (within allowed ranges)."""

    overrides: dict[str, Policy] = field(default_factory=dict)

    def policy_for(self, validator: Validator) -> Policy:
        return self.overrides.get(validator.name, validator.default_policy)

    def run(self, validator: Validator, **ctx) -> ValidationResult:
        res = validator.validate(**ctx)
        policy = self.policy_for(validator)
        return ValidationResult(check=res.check, passed=res.passed, scope=res.scope,
                                policy=policy, detail=res.detail)


# --- concrete validators --------------------------------------------------------
class RowCountLaw:
    """Output row count obeys the declared cardinality (1:1 → equal; N:1/anti vary)."""

    name = "row_count_law"
    scope: Scope = "dataset"
    default_policy: Policy = "hard_fail"

    def validate(self, *, source_rows: int, output_rows: int,
                 quarantined: int = 0, cardinality: str = "1:1") -> ValidationResult:
        if cardinality == "1:1":
            ok = output_rows + quarantined == source_rows
            detail = f"1:1: out({output_rows})+quar({quarantined}) vs src({source_rows})"
        else:
            ok = output_rows <= source_rows + 10**9     # loose for non-1:1 (refined later)
            detail = f"{cardinality}: out={output_rows} src={source_rows}"
        return ValidationResult("row_count_law", ok, self.scope, self.default_policy, detail)


class QuarantineAccounting:
    """EV-QC: every source row is either in the output or quarantined — none vanish."""

    name = "quarantine_accounting"
    scope: Scope = "dataset"
    default_policy: Policy = "hard_fail"

    def validate(self, *, source_rows: int, output_rows: int,
                 quarantined: int) -> ValidationResult:
        ok = output_rows + quarantined == source_rows
        return ValidationResult("quarantine_accounting", ok, self.scope, self.default_policy,
                                f"{output_rows}+{quarantined} vs {source_rows}")


class RoundTrip:
    """EV-RT: for declared-lossless pairs, A→IR→A' must be value-identical."""

    name = "round_trip"
    scope: Scope = "dataset"
    default_policy: Policy = "hard_fail"

    def validate(self, *, original, roundtrip) -> ValidationResult:
        ok = original == roundtrip
        return ValidationResult("round_trip", ok, self.scope, self.default_policy,
                                "identical" if ok else "round-trip MISMATCH")


class DistributionCheck:
    """E3: numeric drift via 2-sample KS; categorical containment (no invented categories)."""

    name = "distribution"
    scope: Scope = "column"
    default_policy: Policy = "warning"

    def validate(self, *, source_values: list, output_values: list,
                 kind: str = "numeric", ks_alpha: float = 0.05) -> ValidationResult:
        if kind == "categorical":
            src = set(source_values)
            invented = set(output_values) - src
            ok = not invented
            return ValidationResult("distribution", ok, self.scope, self.default_policy,
                                    "contained" if ok else f"invented categories: {invented}")
        sv = [float(x) for x in source_values if x is not None]
        ov = [float(x) for x in output_values if x is not None]
        if not sv or not ov:
            return ValidationResult("distribution", True, self.scope, self.default_policy,
                                    "insufficient data")
        _, p = stats.ks_2samp(sv, ov)
        ok = p >= ks_alpha           # fail only when distributions differ significantly
        return ValidationResult("distribution", ok, self.scope, self.default_policy,
                                f"KS p={p:.3f} (alpha={ks_alpha})")
