"""`describe()` — the **capability matrix**, not just a schema (`ADAPTER_AGENT_SPEC` §3).

A schema tells the orchestrator what a source contains. It does not tell it what the source can
*prove*, and planning on the first while assuming the second is how a system ends up citing a
mailbox that has since been edited.

So a description must declare, and the types below make each one non-optional:

* **pinning tier** - and a `T0` claim needs its mechanism and its retention window declared with
  it. Ordinary PostgreSQL is `T1`: a repeatable-read transaction gives a consistent read, not time
  travel (spine D6's explicit caution).
* **whether deletes are detectable** - `hard`, `soft_only`, or `none`. "None" is the common case
  and it changes what a full resync means.
* **whether the watermark is reliable** - a clock-based `updated_at` loses rows written inside the
  same second, so it can neither reconstruct the past nor guarantee complete forward replay.
* **the EFFECTIVE retention, not the documented one**. Gmail's history is documented as "typically
  at least one week"; a 404 means full resync. Both are recorded, separately, because the gap
  between them is the operational fact.
* **the egress class**.
* **whether benign drift is detectable at all** (§5) - see `verdicts.py`.

The shape mirrors what a real estate declares in ``fixtures/packs/finance/sources.yaml``; that
file and this module are two renderings of one set of facts, and `from_source_yaml` reads it so
they cannot drift apart by transcription.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from databridge_core import Trust

from .verdicts import Verdict

PinningTier = Literal["T0", "T1", "T2", "T3"]
DeleteDetectability = Literal["hard", "soft_only", "none"]
EgressClass = Literal["PUBLIC", "INTERNAL", "RESTRICTED_INTERNAL", "LICENSED_THIRD_PARTY"]
ReplayDirection = Literal["forward_only", "best_effort", "none"]


class CursorDescription(BaseModel):
    """The source's change cursor, and its honest limits.

    ``lossy`` is the field that decides whether a "complete" incremental read is complete. A
    clock-based watermark with two writers is lossy whatever the documentation says, and declaring
    it here is what lets the planner refuse to claim completeness it cannot have.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str                                   # "lsn" | "timestamp" | "monotonic_sequence" | ...
    field: Optional[str] = None
    lossy: bool = True
    replay: ReplayDirection = "none"

    @model_validator(mode="after")
    def _check(self) -> "CursorDescription":
        if not self.kind.strip():
            raise ValueError("cursor kind must be named; use kind: 'none' for a source with no "
                             "watermark at all, so that absence is a declaration")
        if self.kind != "none" and not self.lossy and self.replay == "none":
            raise ValueError(
                f"cursor kind={self.kind!r} claims lossless but declares no replay direction: a "
                f"watermark that cannot replay cannot be shown to have lost nothing")
        return self


class FieldDescription(BaseModel):
    """One readable field, with the fact that decides everything downstream: who can WRITE it.

    ``trust`` is per FIELD, never per source (spine D3). Ownership of the table is irrelevant:
    subject-supplied free text copied into a first-party column is attacker-writable, so
    ``party.notes`` is UNTRUSTED in an otherwise trusted table. R06a's finding that free-text
    notes columns are exactly where national identifiers end up is the same observation from the
    PII side.

    There is no default. An adapter author who never thought about trust must not get a working
    field description; that is the whole argument for the required field in `SourcePointer`,
    applied one level up where the declaration is actually made.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str                                   # the PACK's declared type name, not the dialect's
    trust: Trust
    sensitive: bool = False                     # PII / restricted; drives redaction to `derived`


class EntityDescription(BaseModel):
    """One readable entity (table, label, folder) and its fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    fields: tuple[FieldDescription, ...]
    key: tuple[str, ...] = ()                   # the stable row identity a pointer cites

    @model_validator(mode="after")
    def _check(self) -> "EntityDescription":
        if not self.fields:
            raise ValueError(f"entity {self.name!r} declares no fields")
        names = [f.name for f in self.fields]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"entity {self.name!r} declares duplicate fields: {dupes}")
        missing = [k for k in self.key if k not in names]
        if missing:
            raise ValueError(
                f"entity {self.name!r} declares key columns it does not describe: {missing}. A "
                f"pointer cites the key, so an undescribed key is an uncitable row")
        return self

    def field(self, name: str) -> FieldDescription:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"entity {self.name!r} has no field {name!r}")


class SourceDescription(BaseModel):
    """What `describe()` returns. Schema, capability matrix, retention reality, egress class.

    No data - and no credentials, no transport secrets, no connection string. A description is
    the thing an orchestrator may hold in memory and log.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    title: str = ""
    kind: str = ""                              # "postgresql" | "rfc5322_drop" | "csv" | ...
    capabilities: frozenset[str]                # the advertised closed set
    entities: tuple[EntityDescription, ...] = ()

    # --- pinning ---
    pinning_tier: PinningTier
    t0_mechanism: Optional[str] = None          # e.g. "point_in_time_recovery"
    t0_retention_days: Optional[int] = None

    # --- change detection ---
    cursor: CursorDescription
    deletes_detectable: DeleteDetectability
    detects_benign_drift: bool = False          # the optional advertised capability (§5)
    benign_drift_signal: Optional[str] = None   # "xmin" | "etag" | "historyId" | "mtime" | ...

    # --- retention, capture, egress ---
    effective_retention: Mapping[str, str]      # what the source ACTUALLY keeps
    documented_retention: Optional[Mapping[str, str]] = None   # what its docs claim, if different
    capture_required: bool
    egress_class: EgressClass
    reachable_verdicts: frozenset[Verdict]

    @model_validator(mode="after")
    def _check_matrix_is_honest(self) -> "SourceDescription":
        if not self.source_id.strip():
            raise ValueError("source_id must name a source")
        if not self.capabilities:
            raise ValueError(
                f"{self.source_id!r} advertises no capabilities: an adapter that can be asked "
                f"for nothing is not registrable, and an empty set would make the capability "
                f"screen vacuous")

        # D6's caution, enforced: a T0 claim is a claim about the DEPLOYMENT, not the product.
        if self.pinning_tier == "T0":
            if not (self.t0_mechanism or "").strip() or self.t0_retention_days is None:
                raise ValueError(
                    f"{self.source_id!r} claims T0 without declaring t0_mechanism and "
                    f"t0_retention_days. T0 means the deployment can actually serve a past "
                    f"instant; ordinary PostgreSQL is T1, and PITR is T0 only for instants inside "
                    f"its retention window (spine D6)")
            if self.t0_retention_days < 1:
                raise ValueError("t0_retention_days must be at least 1 day to mean anything")
        elif self.t0_mechanism or self.t0_retention_days is not None:
            raise ValueError(
                f"{self.source_id!r} is {self.pinning_tier} but declares T0 machinery. A "
                f"retention window beside a non-T0 tier reads as a time-travel promise nothing "
                f"honours")

        # "Anything ledger-bound is captured. T3 always." (spine D6)
        if self.pinning_tier == "T3" and not self.capture_required:
            raise ValueError(
                f"{self.source_id!r} is T3 and does not require capture. An unversioned source "
                f"cannot re-produce what it said, so a pointer into it that does not resolve "
                f"against the Capture Store cites nothing that can be checked")

        if not self.effective_retention:
            raise ValueError(
                f"{self.source_id!r} declares no effective retention. 'Unknown' is a planning "
                f"input, so say it: {{'default': 'unknown'}} is a declaration; omission is not")

        if not self.reachable_verdicts:
            raise ValueError(f"{self.source_id!r} declares no reachable verdicts")
        if self.detects_benign_drift and not (self.benign_drift_signal or "").strip():
            raise ValueError(
                f"{self.source_id!r} advertises detects_benign_drift without naming the signal. "
                f"Benign drift is only detectable from a cheap entity-level change signal stored "
                f"beside the projection hash; unnamed, it cannot be reviewed (§5)")
        # R-3A finding 9: the two advertisements are independent fields and could disagree, and
        # `assert_verdict_advertised` only noticed at EMISSION time - so a description could sit
        # in a registry for a year, internally contradictory, and nobody would learn which half
        # was true. They are now required to agree, in BOTH directions, at construction.
        if Verdict.DRIFT_BENIGN in self.reachable_verdicts and not self.detects_benign_drift:
            raise ValueError(
                f"{self.source_id!r} lists DRIFT_BENIGN as reachable but does not advertise "
                f"detects_benign_drift. A projection-only hash cannot see a non-projected column "
                f"move, so the honest verdict there is VERIFIED (§5)")
        if self.detects_benign_drift and Verdict.DRIFT_BENIGN not in self.reachable_verdicts:
            raise ValueError(
                f"{self.source_id!r} advertises detects_benign_drift but does not list "
                f"DRIFT_BENIGN in reachable_verdicts. One of the two is wrong and a reader cannot "
                f"tell which: a capability matrix that contradicts itself is worse than one that "
                f"claims less (§5)")

        names = [e.name for e in self.entities]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{self.source_id!r} describes duplicate entities: {dupes}")
        return self

    def entity(self, name: str) -> EntityDescription:
        for e in self.entities:
            if e.name == name:
                return e
        raise KeyError(f"source {self.source_id!r} does not describe entity {name!r}")

    def trust_of(self, entity: str, field: str) -> Trust:
        """The field's declared trust class - what a Core stamps onto a pointer (spine D3).

        A lookup, never an inference. There is deliberately no fallback: an undescribed field has
        no trust class, and guessing one is precisely the fail-open the required label exists to
        prevent.
        """
        return self.entity(entity).field(field).trust

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


_TIER_FROM_YAML: Mapping[str, str] = {"T0": "T0", "T1": "T1", "T2": "T2", "T3": "T3"}


def source_description_from_yaml(entry: Mapping[str, Any], *,
                                 capabilities: frozenset[str],
                                 entities: tuple[EntityDescription, ...] = ()
                                 ) -> SourceDescription:
    """Build a `SourceDescription` from one ``sources.yaml`` entry.

    ``fixtures/packs/*/sources.yaml`` is the shape a real estate declares, and reading it here
    means the capability matrix an adapter advertises and the one the estate documents cannot
    drift apart by transcription. Capabilities and entities are passed separately because
    ``sources.yaml`` describes the SOURCE; what an adapter can do with it, and which fields carry
    which trust class, are the pack's source-contract statements.
    """
    tier = _TIER_FROM_YAML.get(str(entry.get("pinning_tier", "")).upper())
    if tier is None:
        raise ValueError(f"source {entry.get('id')!r}: unknown pinning_tier "
                         f"{entry.get('pinning_tier')!r}")
    cursor_raw = dict(entry.get("cursor") or {"kind": "none"})
    cursor = CursorDescription(
        kind=str(cursor_raw.get("kind", "none")),
        field=(str(cursor_raw["field"]) if cursor_raw.get("field") else
               (str(cursor_raw["fallback_field"]) if cursor_raw.get("fallback_field") else None)),
        lossy=bool(cursor_raw.get("lossy", True)),
        replay=cursor_raw.get("replay", "none"),
    )
    verdicts = frozenset(Verdict(str(v).lower()) for v in (entry.get("reachable_verdicts") or ()))
    retention = {str(k): str(v) for k, v in (entry.get("retention") or {}).items()}
    t0 = bool(entry.get("t0_capable"))
    return SourceDescription(
        source_id=str(entry["id"]),
        title=str(entry.get("title") or ""),
        kind=str(entry.get("kind") or ""),
        capabilities=capabilities,
        entities=entities,
        pinning_tier=tier,
        # `t0_capable: true` in the estate means "a deployment OPTION exists", which is not the
        # same as this deployment serving it - so the tier stays what the file declares, and the
        # T0 machinery is only carried when the declared tier is actually T0.
        t0_mechanism=(str(entry.get("t0_mechanism")) if (t0 and tier == "T0") else None),
        t0_retention_days=(int(entry["t0_retention_days"])
                           if (t0 and tier == "T0" and entry.get("t0_retention_days")) else None),
        cursor=cursor,
        deletes_detectable=str(entry.get("deletes_detectable") or "none"),
        detects_benign_drift=Verdict.DRIFT_BENIGN in verdicts,
        benign_drift_signal=(str(entry.get("benign_drift_signal") or cursor.kind)
                             if Verdict.DRIFT_BENIGN in verdicts else None),
        effective_retention=retention or {"default": "unknown"},
        capture_required=bool(entry.get("capture_required", tier == "T3")),
        egress_class=str(entry.get("egress_class") or "INTERNAL"),
        reachable_verdicts=verdicts,
    )
