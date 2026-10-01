"""The port is five verbs, and conformance is checked properly (§3).

R02 and R03 converged on the same five independently. What matters for the code is that the
orchestrator knows ONLY this Protocol: the moment it has to know which source it is talking to,
the fleet stops being a fleet and every new source becomes an integration.

`runtime_checkable` is not enough on its own and the test says so out loud: it checks that the
attributes EXIST. An object with ``resolve = None`` or ``def resolve(self, sql)`` passes
`isinstance`. `assert_conforms` is what registration uses.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from databridge_adapter import (
    VERBS,
    Adapter,
    AdapterResult,
    CapabilityRequest,
    Cost,
    CursorDescription,
    GroundedRows,
    NotAnAdapter,
    ReadPlan,
    SourceDescription,
    Verdict,
    assert_conforms,
)
from databridge_core import GroundedValue, SnapshotIdentity, SourcePointer, Trust

AS_OF = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


class StubCore:
    """The smallest thing that is honestly an adapter. No I/O - it answers from a literal."""

    source_id = "core_banking"
    capabilities = frozenset({"row_read"})
    pinning_tier = "T1"

    def describe(self) -> SourceDescription:
        return SourceDescription(
            source_id=self.source_id, capabilities=self.capabilities, pinning_tier="T1",
            cursor=CursorDescription(kind="lsn", lossy=False, replay="forward_only"),
            deletes_detectable="soft_only", effective_retention={"default": "P7Y"},
            capture_required=True, egress_class="RESTRICTED_INTERNAL",
            reachable_verdicts=frozenset({Verdict.VERIFIED, Verdict.UNVERIFIABLE}))

    def snapshot(self, req: CapabilityRequest) -> SnapshotIdentity:
        return SnapshotIdentity(source_id=self.source_id, as_of=AS_OF, content_hash="ch",
                                schema_hash="sh", query_hash=req.read_hash)

    def plan(self, req: CapabilityRequest) -> ReadPlan:
        return ReadPlan(read_hash=req.read_hash, source_id=self.source_id,
                        entity=req.projection.entity,
                        operations=("SELECT party_id, legal_name FROM party ORDER BY party_id",),
                        estimated_rows=1, estimated_cost=Cost(rows=1))

    def resolve(self, req: CapabilityRequest) -> AdapterResult:
        snap = self.snapshot(req)
        cells = tuple(
            GroundedValue(value="P-1" if f == "party_id" else "Acme Pte Ltd",
                          pointer=SourcePointer(source_id=self.source_id, kind="query_cell",
                                                snapshot=snap, query_hash=req.read_hash,
                                                row_ordinal=0, column=f, row_id="P-1",
                                                trust=Trust.TRUSTED))
            for f in req.projection.fields)
        return AdapterResult(
            read_hash=req.read_hash,
            rows=GroundedRows(columns=req.projection.fields, rows=(cells,), snapshot=snap),
            cost=Cost(rows=1, wall_ms=1))

    def verify(self, pointer: SourcePointer) -> Verdict:
        return Verdict.VERIFIED


def test_the_port_names_five_verbs():
    assert VERBS == ("describe", "snapshot", "plan", "resolve", "verify")


def test_a_conforming_core_passes_both_checks():
    core = StubCore()
    assert isinstance(core, Adapter)
    assert_conforms(core)


def test_a_missing_verb_is_refused():
    class Partial(StubCore):
        verify = None                                             # type: ignore[assignment]

    with pytest.raises(NotAnAdapter, match="verify"):
        assert_conforms(Partial())


def test_isinstance_alone_is_not_enough():
    """The reason `assert_conforms` exists: a runtime-checkable Protocol checks presence, not
    shape. This object passes isinstance and would take raw SQL."""
    class TakesSql(StubCore):
        def resolve(self, sql: str):                              # type: ignore[override]
            return sql

    sneaky = TakesSql()
    assert isinstance(sneaky, Adapter)
    with pytest.raises(NotAnAdapter, match="resolve"):
        assert_conforms(sneaky)


def test_an_unrecognised_pinning_tier_is_refused():
    class Optimistic(StubCore):
        pinning_tier = "T-1"

    with pytest.raises(NotAnAdapter, match="pinning_tier"):
        assert_conforms(Optimistic())


def test_an_adapter_advertising_nothing_is_refused():
    class Silent(StubCore):
        capabilities = frozenset()

    with pytest.raises(NotAnAdapter, match="no capabilities"):
        assert_conforms(Silent())


# --- the port end to end ------------------------------------------------------------

def test_resolve_returns_cells_that_all_carry_pointers(req):
    result = StubCore().resolve(req)
    for row in result.rows.rows:
        for cell in row:
            assert isinstance(cell, GroundedValue)
            assert cell.pointer.query_hash == req.read_hash


def test_the_snapshot_query_hash_is_the_requests_read_hash(req):
    """D2, end to end: this is what makes the pointer verifiable by re-execution."""
    assert StubCore().snapshot(req).query_hash == req.read_hash
    assert StubCore().resolve(req).rows.snapshot.query_hash == req.read_hash


def test_a_plan_is_bound_to_the_read_it_plans(req):
    assert StubCore().plan(req).read_hash == req.read_hash


def test_a_plan_may_not_estimate_llm_tokens(req):
    """Planning is deterministic; no model runs in the read path (D1)."""
    with pytest.raises(ValueError, match="LLM tokens"):
        ReadPlan(read_hash=req.read_hash, source_id="core_banking", entity="party",
                 operations=("scan",), estimated_cost=Cost(llm_tokens=10))


def test_a_plan_with_no_operations_explains_nothing(req):
    with pytest.raises(ValueError, match="operations"):
        ReadPlan(read_hash=req.read_hash, source_id="core_banking", entity="party",
                 operations=())
