"""`ADAPTER_AGENT_SPEC` §11 — **the eight things that must be true before this is real.**

> "If those eight pass for two adapters — one relational source, one mailbox — the design is real
> and every further source is an exercise."

One adapter exists (`PostgresCore` over the synthetic finance estate), so this file establishes
half of that bar. Every test runs against the real estate through the real Core: the same
SQLAlchemy code path, the same PostgreSQL DDL, the same seed rows, the same `roles.yaml`. Nothing
here is mocked, because a mock agrees with whatever the code does.

Each test is named `test_acceptance_N_...` so the mapping to §11 survives a rename.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from databridge_adapter import (
    CannotPinPast,
    CapabilityRequest,
    Projection,
    UngroundedValue,
    UntrustedInControlFlow,
    Verdict,
    assert_grounded,
    screen_sinks,
)
from databridge_adapter.sources.postgres import PostgresCore
# The three cell states live in `databridge_core` - `databridge_adapter` re-exports the UNION
# (`Cell`) rather than the members, deliberately, so nothing here can shadow them.
from databridge_core import Absent as CoreAbsent
from databridge_core import GroundedValue
from databridge_core import GroundedValue as CoreGroundedValue
from databridge_core import Trust
from databridge_core import Withheld as CoreWithheld
from databridge_adapter.testing.estate import DATABRIDGE_ROOT, SOURCES_YAML, mutate


# =======================================================================================
# 1. No ungrounded cell survives validation.
# =======================================================================================

def test_acceptance_1_every_cell_is_a_grounded_value_a_withheld_or_an_absent(core, party_request):
    """> "every cell is a `GroundedValue` with a pointer, a `Withheld`, or an `Absent`."

    Scope stated honestly, as §11 asks: `model_construct()` bypasses validation, so this is a
    guarantee about values that went through the constructor, not a law of physics.
    """
    result = core.resolve(party_request(
        projection=Projection(entity="party",
                              fields=("party_id", "legal_name", "notes", "trading_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=40))
    assert result.rows.rows, "the estate has parties; a read that returns none proves nothing"
    for row in result.rows.rows:
        for cell in row:
            assert isinstance(cell, (GroundedValue, CoreWithheld, CoreAbsent))
            if isinstance(cell, GroundedValue):
                assert cell.pointer is not None
                assert cell.pointer.trust in (Trust.TRUSTED, Trust.UNTRUSTED)


def test_acceptance_1_the_wall_runs_at_emission_against_the_row_that_was_read(core, party_request,
                                                                             finance_engine):
    """§10: "`assert_grounded` at emission — *not* the `GroundedRows` validator, which checks
    arity only." So re-run the wall over every emitted cell against the source row itself."""
    from sqlalchemy import text

    req = party_request(projection=Projection(entity="party",
                                              fields=("party_id", "legal_name", "kyc_status"),
                                              key=("party_id",)),
                        order_by=("party_id",), limit=20)
    result = core.resolve(req)
    with finance_engine.connect() as conn:
        for row in result.rows.rows:
            row_id = row[0].value
            raw = conn.execute(text("SELECT party_id, legal_name, kyc_status FROM "
                                    "core_banking.party WHERE party_id = :p"),
                               {"p": row_id}).mappings().one()
            for cell in row:
                if isinstance(cell, GroundedValue):
                    assert_grounded(cell, dict(raw))


def test_acceptance_1_a_fabricated_cell_is_refused_by_the_wall(core, party_request):
    """The failure that passes every structural check: a well-formed pointer beside a value the
    source never said. Take a REAL pointer from a REAL read and hang a lie on it."""
    result = core.resolve(party_request(limit=1))
    real = result.rows.rows[0][1]
    forged = CoreGroundedValue(value="Definitely Not This Borrower Ltd", pointer=real.pointer)
    with pytest.raises(UngroundedValue, match="fabricated"):
        assert_grounded(forged, fetched=real.value)


# =======================================================================================
# 2. Every pointer re-resolves, per tier.
# =======================================================================================

def test_acceptance_2_every_returned_pointer_re_resolves_to_a_positive_verdict(core,
                                                                              party_request):
    """§11.2's caution is honoured: `VERIFIED` for a live re-read, `VERIFIED_AS_OF_CAPTURE` for a
    captured artefact, and `UNVERIFIABLE` is a legitimate outcome for a tier that cannot answer —
    a test demanding `VERIFIED` everywhere would assert something the design does not claim."""
    result = core.resolve(party_request(limit=15))
    checked = 0
    for row in result.rows.rows:
        for cell in row:
            if not isinstance(cell, GroundedValue):
                continue
            verdict = core.verify(cell.pointer)
            assert verdict.is_positive, (cell.pointer.column, verdict)
            checked += 1
    assert checked >= 30, "too few pointers checked for this to mean anything"


def test_acceptance_2_a_pointer_is_re_resolvable_through_the_advertised_resolve_verb(
        core, party_request, analyst):
    """Re-fetching a pointer is part of the contract, not a side door: a resolve-only request
    carries the pointers and mints no ordinals, so it is exempt from `order_by`."""
    result = core.resolve(party_request(limit=3))
    pointers = tuple(c.pointer for c in result.rows.rows[0] if isinstance(c, GroundedValue))
    again = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=analyst,
        projection=result.rows.rows and Projection(
            entity="party", fields=("party_id", "legal_name", "kyc_status"), key=("party_id",)),
        resolve=pointers, limit=10)
    assert again.read_hash != result.read_hash, (
        "a resolution request for specific pointers is a different read and must not collide")
    assert core.resolve(again).rows.rows


# =======================================================================================
# 3. `read_hash` determinism.
# =======================================================================================

_PACKAGES = ("packages/core", "packages/graph", "packages/harness", "packages/convert",
             "packages/intake", "packages/adapter")


def test_acceptance_3_read_hash_is_identical_across_processes_and_hash_seeds(tmp_path: Path):
    """> "Same request → same hash across processes and Python runs (watch dict ordering and float
    formatting)."

    A fresh interpreter each time, with `PYTHONHASHSEED` set explicitly rather than left ambient:
    dict ordering and set iteration are the two things that would make this environment-dependent,
    and the bug would only ever appear on somebody else's machine.
    """
    script = textwrap.dedent(f"""
        import sys, pathlib
        sys.path[:0] = {[str((DATABRIDGE_ROOT / p).resolve()) for p in _PACKAGES]!r}
        from datetime import date
        from decimal import Decimal
        from databridge_adapter import (CapabilityRequest, Principal, Projection,
                                        purpose_vocabulary, use_purposes)
        with use_purposes(purpose_vocabulary(("finance", "government", "healthcare"),
                                             pathlib.Path({str(DATABRIDGE_ROOT / 'packs')!r}))):
            p = Principal(actor="orchestrator@walnut", on_behalf_of="analyst-7",
                          purpose="lending.underwriting", grant_id="g1")
            r = CapabilityRequest(
                source_id="core_banking", capability="row_read", principal=p,
                projection=Projection(entity="party",
                                      fields=("party_id", "legal_name", "kyc_status"),
                                      key=("party_id",)),
                order_by=("legal_name", "party_id"), limit=25,
                params={{"since": date(2026, 1, 1), "floor": Decimal("1000.50")}})
            print(r.read_hash)
    """)
    path = tmp_path / "probe.py"
    path.write_text(script, encoding="utf-8")
    import os

    seen = set()
    for seed in ("0", "1", "99991"):
        out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                             env={**os.environ, "PYTHONHASHSEED": seed})
        assert out.returncode == 0, out.stderr
        seen.add(out.stdout.strip())
    assert len(seen) == 1, f"read_hash differed across processes: {seen}"


def test_acceptance_3_the_hash_the_core_stamps_is_the_requests_read_hash(core, party_request):
    """D2 in one assertion: `read_hash` BECOMES `query_hash`, on the result AND on every cell."""
    req = party_request(limit=5)
    result = core.resolve(req)
    assert result.rows.snapshot.query_hash == req.read_hash
    for row in result.rows.rows:
        for cell in row:
            if isinstance(cell, GroundedValue) and cell.pointer.kind == "query_cell":
                assert cell.pointer.query_hash == req.read_hash
                assert cell.pointer.snapshot.query_hash == req.read_hash


def test_acceptance_3_a_mutated_pointer_cannot_change_an_issued_identity(core, party_request):
    """R-3A finding 5. `SourcePointer` holds `derived_from` as a LIST and Pydantic does not
    deep-freeze it, so a request built over a pointer could have its identity changed after the
    fact — and its `__hash__` changed after it had been used as a dict key."""
    result = core.resolve(party_request(limit=1))
    pointer = next(c.pointer for c in result.rows.rows[0] if isinstance(c, GroundedValue))
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=result and party_request().principal,
        projection=Projection(entity="party", fields=("party_id", "legal_name"), key=("party_id",)),
        resolve=(pointer,), limit=5)
    before = req.read_hash
    seen = {req: "kept"}
    pointer.__dict__.setdefault("facts", None)
    object.__setattr__(pointer, "derived_from", [pointer])       # the mutation Pydantic allows
    assert req.read_hash == before
    assert seen[req] == "kept"


# =======================================================================================
# 4. T1 refuses the past.
# =======================================================================================

def test_acceptance_4_the_past_is_refused_and_the_refusal_says_where_to_go(core, party_request):
    """§10: "`snapshot(at=past)` on T1/T2/T3 → **raise** `CannotPinPast`" — never substitute now.

    The estate is emphatic that this holds *whatever `t0_capable` says*, so the assertion is on
    the refusal AND on the flag being present: a source that had no PITR option would refuse for
    an uninteresting reason.
    """
    assert core._contract.entry.get("t0_capable") is True
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(CannotPinPast) as exc:
        core.snapshot(party_request(as_of=yesterday))
    assert "Capture Store" in str(exc.value) or "SEPARATE" in str(exc.value)
    with pytest.raises(CannotPinPast):
        core.resolve(party_request(as_of=yesterday))


def test_acceptance_4_a_present_read_still_works_so_the_refusal_is_about_the_past(core,
                                                                                 party_request):
    assert core.snapshot(party_request(limit=5)).content_hash


def test_acceptance_4_the_refusal_is_not_a_silent_nearest_instant(core, party_request):
    """The failure mode is a caller that believes it received the past. `as_of` must never be
    quietly dropped, so a refused read returns NOTHING rather than the present."""
    old = datetime.now(timezone.utc) - timedelta(days=400)
    with pytest.raises(CannotPinPast):
        core.resolve(party_request(as_of=old, limit=1))


# =======================================================================================
# 5. Untrusted cannot parameterise — but CAN be re-resolved.
# =======================================================================================

def test_acceptance_5_untrusted_cannot_parameterise_but_can_be_resolved(core, compliance,
                                                                       analyst):
    """> "A value derived from an untrusted pointer, placed into any `TRUSTED_SINKS` position,
    > raises; the *same* untrusted pointer passed in `resolve` succeeds, because retrieval is not
    > action. **Both halves are the test.**"
    """
    from databridge_adapter import RegistryTaintMap

    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=compliance,
        projection=Projection(entity="party", fields=("party_id", "notes"), key=("party_id",)),
        order_by=("party_id",), limit=400)
    result = core.resolve(req)
    untrusted = [c for row in result.rows.rows for c in row
                 if isinstance(c, GroundedValue) and c.trust is Trust.UNTRUSTED and c.value]
    assert untrusted, "party.notes is declared untrusted by the contract and must come back so"

    taint = RegistryTaintMap()
    cell = untrusted[0]
    taint.register_value(cell)

    # HALF ONE — the value may not steer anything.
    poisoned = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=compliance,
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=10, params={"needle": cell.value})
    with pytest.raises(UntrustedInControlFlow, match="quotable, not actionable"):
        screen_sinks(poisoned, taint)

    # HALF TWO — the same untrusted POINTER may be re-fetched. Using a locator to retrieve bytes
    # is not the same as letting the bytes decide something.
    refetch = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=compliance,
        projection=Projection(entity="party", fields=("party_id", "notes"), key=("party_id",)),
        resolve=(cell.pointer,), limit=10)
    screen_sinks(refetch, taint)
    assert core.resolve(refetch).rows.rows


def test_acceptance_5_the_sink_inventory_covers_the_fields_that_steer_a_read(core, compliance):
    """R-3A finding 7: `source_id`, `purpose`, `grant_id`, `mode`, `as_of` and `limit` all change
    what happens next, and none of them was screened."""
    from databridge_adapter import ADAPTER_SINKS, RegistryTaintMap

    for sink in ("source_id", "principal.purpose", "principal.grant_id", "mode", "as_of", "limit"):
        assert sink in ADAPTER_SINKS

    taint = RegistryTaintMap()
    taint.register("core_banking", Trust.UNTRUSTED)
    req = CapabilityRequest(
        source_id="core_banking", capability="row_read", principal=compliance,
        projection=Projection(entity="party", fields=("party_id",), key=("party_id",)),
        order_by=("party_id",), limit=5)
    with pytest.raises(UntrustedInControlFlow, match="source_id"):
        screen_sinks(req, taint)


# =======================================================================================
# 6. `withheld` != `unresolved`.
# =======================================================================================

def test_acceptance_6_a_policy_denied_field_is_withheld_and_carries_a_reason_code(core,
                                                                                 party_request):
    """`roles.yaml`'s own `withheld_is_not_absent` case: a credit analyst asking for operator
    notes gets "a Withheld cell naming the rule, not NULL and not an empty string"."""
    result = core.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "notes", "date_of_birth"),
                              key=("party_id",)),
        order_by=("party_id",), limit=10))
    notes = [row[1] for row in result.rows.rows]
    assert notes and all(isinstance(c, CoreWithheld) for c in notes)
    record = notes[0].record
    # From the estate's own `reason_codes` vocabulary, not a constant in engine code.
    assert record.reason_code == "POLICY_COLUMN_DENIED"
    assert record.field_path == "party.notes"
    assert record.policy_id and record.principal and record.purpose
    assert record.policy_version, "a denial must name the policy bundle that was in force"


def test_acceptance_6_withheld_is_distinguishable_from_absent(core, party_request):
    """The two facts must never collapse: "policy stopped me" and "the source did not have it".

    `party.trading_name` is genuinely NULL for many rows and is not denied to anyone;
    `party.notes` is denied to this role. One read shows both, in different cells.
    """
    result = core.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "trading_name", "notes"),
                              key=("party_id",)),
        order_by=("party_id",), limit=60))
    kinds = {(type(row[1]).__name__, type(row[2]).__name__) for row in result.rows.rows}
    assert ("Absent", "Withheld") in kinds, (
        "the estate has parties with a NULL trading_name; if none came back, the fixture changed")
    assert result.rows.absent_count and result.rows.withheld_count
    for row in result.rows.rows:
        if isinstance(row[1], CoreAbsent):
            assert row[1].field_path == "party.trading_name"
            assert not hasattr(row[1], "record"), "an Absent carries no denial"
        assert not hasattr(row[2], "value"), "a Withheld carries no value"
        assert not hasattr(row[2], "pointer"), "withholding is not a citation"


def test_acceptance_6_an_empty_string_is_a_value_not_an_absence(core, party_request,
                                                               mutable_estate, policy):
    """The estate uses `''` and NULL inconsistently on purpose. Deciding they mean the same thing
    is a data-quality judgement an adapter has no standing to make."""
    mutate(mutable_estate,
           "UPDATE core_banking.party SET trading_name = '' WHERE party_id = 'CB-P-000001'")
    core2 = PostgresCore(source_id="core_banking", engine=mutable_estate, policy=policy,
                         sources_yaml=SOURCES_YAML)
    result = core2.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "trading_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=1))
    cell = result.rows.rows[0][1]
    assert isinstance(cell, GroundedValue) and cell.value == ""


# =======================================================================================
# 7. Shell has no credentials.
# =======================================================================================

def test_acceptance_7_the_shell_module_imports_no_driver_and_no_vendor_sdk():
    """§11.7's process-level test, in the form this repo can actually enforce: an AST scan.

    Stated precisely, as §1 requires: this constrains the DOCUMENTED path. It is not a proof that
    a compromised Shell process cannot open a socket — that is the egress firewall's job. The
    honest public claim is that the system is architecturally missing the exfiltration leg.
    """
    import ast

    import databridge_adapter.shell as shell_module

    forbidden = {"sqlalchemy", "psycopg", "psycopg2", "sqlite3", "pymysql", "duckdb", "socket",
                 "requests", "httpx", "urllib", "boto3", "googleapiclient",
                 "anthropic", "openai", "litellm"}
    tree = ast.parse(Path(shell_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & forbidden), f"the Shell imports {sorted(imported & forbidden)}"


def test_acceptance_7_no_constructor_parameter_could_carry_a_credential():
    """A Shell that can be HANDED a connection makes the boundary a convention."""
    import inspect

    from databridge_adapter.shell import AdapterShell

    names = set(inspect.signature(AdapterShell.__init__).parameters)
    for smell in ("url", "dsn", "engine", "conn", "connection", "password", "secret", "token",
                  "credential", "credentials", "core", "driver"):
        assert not any(smell in n.lower() for n in names), (
            f"AdapterShell.__init__ takes {smell!r}: it could then be handed a source")


def test_acceptance_7_a_live_shell_holds_no_secret_material_and_no_driver_handle(core):
    """Walk the instance. `SourceDescription` is metadata by construction — no connection string,
    no credentials — so a Shell built from one has nothing to leak."""
    import re

    from databridge_adapter.shell import AdapterShell
    from databridge_harness import FakeBackend, Harness

    shell = AdapterShell(description=core.describe(), harness=Harness(FakeBackend("{}")))
    dsn = re.compile(r"(postgres(ql)?|mysql|sqlite|mssql)(\+\w+)?://", re.I)

    def walk(obj, depth=0):
        if depth > 3:
            return
        if isinstance(obj, str):
            assert not dsn.search(obj), f"the Shell holds something that looks like a DSN: {obj!r}"
            return
        assert type(obj).__name__ != "Engine", "the Shell holds a driver handle"
        for value in (vars(obj).values() if hasattr(obj, "__dict__") else ()):
            walk(value, depth + 1)
        if isinstance(obj, (list, tuple, set, frozenset)):
            for value in obj:
                walk(value, depth + 1)
        if isinstance(obj, dict):
            for value in obj.values():
                walk(value, depth + 1)

    walk(shell)
    assert not hasattr(shell, "resolve") and not hasattr(shell, "execute"), (
        "the Shell plans reads; it must have no verb that performs one")
    # And the description it holds really is data-free.
    brief = shell.describe_for_model()
    assert "postgresql://" not in brief and "password" not in brief.lower()


# =======================================================================================
# 8. Drift classification.
# =======================================================================================

def test_acceptance_8_a_mutated_cited_value_is_always_drift_material(mutable_estate, policy,
                                                                    party_request):
    """> "Mutate a cited value → `DRIFT_MATERIAL`, for every adapter, always.\""""
    core2 = PostgresCore(source_id="core_banking", engine=mutable_estate, policy=policy,
                         sources_yaml=SOURCES_YAML)
    result = core2.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=1))
    cell = result.rows.rows[0][1]
    assert core2.verify(cell.pointer) is Verdict.VERIFIED

    mutate(mutable_estate,
           "UPDATE core_banking.party SET legal_name = :n WHERE party_id = :p",
           {"n": "Renamed Holdings Pte Ltd", "p": cell.pointer.row_id})
    verdict = core2.verify(cell.pointer)
    assert verdict is Verdict.DRIFT_MATERIAL
    assert verdict.must_escalate, "a changed cited value must always page someone (§5)"


def test_acceptance_8_a_non_projected_column_moving_is_verified_not_drift_benign(mutable_estate,
                                                                                policy,
                                                                                party_request):
    """> "Mutate a non-projected column → `DRIFT_BENIGN` **only for adapters that advertise the
    > capability**; for every other adapter the correct answer is `VERIFIED`, and asserting
    > otherwise tests a promise the design does not make."

    `core_banking` declares `drift_benign_capable: false`, and gives its reason: `updated_at` is
    absent from six of eleven tables and is written by whichever code path touched the row. So
    `VERIFIED` is the honest verdict here, and this test asserts the design's actual promise.
    """
    core2 = PostgresCore(source_id="core_banking", engine=mutable_estate, policy=policy,
                         sources_yaml=SOURCES_YAML)
    assert core2.describe().detects_benign_drift is False
    assert Verdict.DRIFT_BENIGN not in core2.describe().reachable_verdicts

    result = core2.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=1))
    cell = result.rows.rows[0][1]
    mutate(mutable_estate,
           "UPDATE core_banking.party SET risk_grade = 'ZZ-i', updated_at = '2027-01-01 00:00:00+00' "
           "WHERE party_id = :p", {"p": cell.pointer.row_id})
    assert core2.verify(cell.pointer) is Verdict.VERIFIED


def test_acceptance_8_drift_benign_is_reachable_only_where_it_is_advertised(mutable_estate, policy,
                                                                           party_request,
                                                                           monkeypatch):
    """The other half: where a contract DOES advertise the signal, the same mutation is benign.

    The advertisement is faked at the DESCRIPTION, not in the Core, which is the point — the Core
    reads the capability matrix and does not decide it.
    """
    core2 = PostgresCore(source_id="core_banking", engine=mutable_estate, policy=policy,
                         sources_yaml=SOURCES_YAML)
    result = core2.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=1))
    cell = result.rows.rows[0][1]

    honest = core2.describe()
    advertising = honest.model_copy(update={
        "detects_benign_drift": True,
        "benign_drift_signal": "updated_at",
        "reachable_verdicts": honest.reachable_verdicts | {Verdict.DRIFT_BENIGN},
    })
    monkeypatch.setattr(core2, "_described", advertising)

    mutate(mutable_estate,
           "UPDATE core_banking.party SET risk_grade = 'ZZ-i', "
           "updated_at = '2027-02-02 00:00:00+00' WHERE party_id = :p", {"p": cell.pointer.row_id})
    verdict = core2.verify(cell.pointer)
    assert verdict is Verdict.DRIFT_BENIGN
    assert not verdict.must_escalate, "benign drift must not page anyone, or verification dies"


def test_acceptance_8_a_deleted_row_is_reported_only_as_the_contract_allows(mutable_estate, policy,
                                                                           party_request):
    """`core_banking` lists `VANISHED` as reachable, so a hard-deleted row reports it.

    Worth stating why this is not automatic: the contract also says `deletes_detectable: none`
    estate-wide, and "an adapter that infers deletion from a row no longer appearing is
    fabricating". The verdict is available because the contract advertises it; where it is not,
    the Core answers `UNVERIFIABLE` instead of inventing the stronger claim.
    """
    core2 = PostgresCore(source_id="core_banking", engine=mutable_estate, policy=policy,
                         sources_yaml=SOURCES_YAML)
    result = core2.resolve(party_request(
        projection=Projection(entity="party", fields=("party_id", "legal_name"),
                              key=("party_id",)),
        order_by=("party_id",), limit=1))
    cell = result.rows.rows[0][1]
    row_id = cell.pointer.row_id
    mutate(mutable_estate, "DELETE FROM core_banking.party_contact WHERE party_id = :p",
           {"p": row_id})
    mutate(mutable_estate, "DELETE FROM core_banking.party_address WHERE party_id = :p",
           {"p": row_id})
    mutate(mutable_estate, "DELETE FROM core_banking.party WHERE party_id = :p", {"p": row_id})
    assert core2.verify(cell.pointer) is Verdict.VANISHED
