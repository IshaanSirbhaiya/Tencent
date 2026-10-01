"""`PostgresCore` — the first real Core (`ADAPTER_AGENT_SPEC` §7). **Deterministic. No LLM.**

This is the layer that owns the driver and the credentials, and its one job is the guarantee no
other layer can make: **no cell leaves without a pointer, and no pointer cites something the
source did not say.**

## What it reuses, and why not a rewrite

`packages/ingest/databridge_ingest/postgres.py` is already three-quarters of a Core, and §7 says
so: SQLAlchemy reflection that works identically against PostgreSQL and SQLite, and PII redaction
*before* anything leaves the connector. Both approaches are reused here — the same reflection API,
and the same redaction patterns (restated in `derivations.py` and pinned to the connector's by a
test, because importing the connector would drag SQLAlchemy into the driver-free contract layer).
What §7 says the connector lacks is pointer emission and snapshot pinning, and that is what this
module adds.

The practical payoff is that **the tests run the production code path**: the finance estate's
PostgreSQL DDL and seed load into SQLite with the schema ATTACHed as `core_banking`, so
``Table("party", schema="core_banking")`` renders and executes identically in both. No mock, no
in-memory stand-in for the driver.

## The five things §7 is emphatic about, and how each is enforced here

**1. A repeatable-read transaction is not time travel.** Ordinary PostgreSQL gives a consistent
read of *now*; it cannot serve `as_of=<last month>` from a live connection. So `as_of` raises
`CannotPinPast` unless the CONTRACT declares tier `T0` and the instant is inside its declared
window. `t0_capable: true` does not qualify, and the estate says so itself: *"it is NOT permission
for the ordinary read path to pin the past, and no component may read it as such."* A PITR restore
is a separate resource with its own `source_id`, its own `describe()` and its own authorisation —
see `PitrWindow`.

The finance estate revised this further while this Core was being built, and the revision is worth
naming because it changed the answer: `core_banking` is now **T2 source-wide** (current-addressable,
no history) with three append-only T1 *streams*, because "the source tier is the minimum over its
streams". §7's `pinning_tier = "T1"` sketch was optimistic about a real PostgreSQL deployment with
no retained replication slot — so the tier is read from the contract per source AND per stream,
never hard-coded.

**2. A snapshot pins nothing unless the read happens inside it.** One transaction, opened
REPEATABLE READ, or it is not a snapshot. `resolve()` establishes the snapshot on the same
connection that runs the query; `snapshot()` opens its own transaction and does the same thing, so
there is no path where a moment is named on one connection and rows are read on another.

**3. `pg_export_snapshot()` cannot be a durable content hash.** It names a snapshot only while the
exporting transaction is open and dies at COMMIT. Read consistency comes from the transaction;
*replayability* comes from an order-independent digest of what was actually read, computed after
the rows are in hand — so a query-planner change does not read as data drift (§5's second rule).

**4. Pointer identity needs stable row identity.** `order_by` is required, and required to be
TOTAL: the ordering must end in the entity's primary key, and this Core checks that against the
key it reflects rather than against the caller's claim. The pointer then cites the PRIMARY KEY,
with `row_ordinal` as a hint only.

**5. Trust is stamped per FIELD from the source contract.** Never per source, never inferred from
content. Ownership of the table is irrelevant: `party.notes` is UNTRUSTED in a database the bank
owns.

## Two deviations from §7's sketch, both forced by making `verify()` actually work

§7 puts the READ-level `SnapshotIdentity` on every cell's pointer. That makes `verify(pointer)`
impossible to implement correctly: the digest it holds covers every row of the read, so *any*
change anywhere in the result reads as material drift on *every* pointer, and the
`DRIFT_BENIGN`/`DRIFT_MATERIAL` split — which §5 says decides whether verification survives to
month three — collapses. So:

* **the pointer's `snapshot.content_hash` is the digest of THAT CELL**, not of the whole read. The
  read-level snapshot, with its order-independent `rows_digest`, is on `GroundedRows` where it
  belongs. `query_hash` is `req.read_hash` on both, so D2 holds either way.
* **the pointer's `rows_digest` carries the row's entity-level CHANGE SIGNAL**, where the contract
  declares one. That is the cheap signal §5 requires before an adapter may say `DRIFT_BENIGN` at
  all, and there is nowhere else on a `SourcePointer` to put it. `core_banking` declares
  `drift_benign_capable: false` — `updated_at` is absent from six of its eleven tables — so this
  Core never emits that verdict against this estate, and `VERIFIED` is the honest answer. The
  machinery is here for a source whose contract does advertise the capability.

And a third, smaller one: §7's `query_cell` pointer names no table, so `verify(pointer)` has no
way to find the row again. This Core sets `table` as well — it is an optional locator field and
filling it costs nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from sqlalchemy import Engine, MetaData, Table, create_engine, func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from databridge_core import (
    Absent,
    GroundedValue,
    SnapshotIdentity,
    SourcePointer,
    Trust,
    Withheld,
)

from ..canonical import canonical_json, canonical_scalar, sha256_of
from ..contract import CapabilityRequest, Projection
from ..derivations import redact_pii
from ..describe import SourceDescription
from ..errors import (
    AdapterError,
    CannotPinPast,
    CapabilityNotAdvertised,
    PolicyNotApplied,
    UnorderedPointeredRead,
)
from ..grounding import (
    DerivationError,
    assert_cell_grounded_in_row,
    assert_derived_from,
    parse_derivation,
    replay_derivation,
)
from ..policy import Admission, PolicyEngine
from ..results import AdapterResult, Budget, Cell, Cost, GroundedRows, ReadPlan
from ..verdicts import Verdict, assert_verdict_advertised, combine_verdicts
from .contract import SourceContract, load_source_contract

#: What this Core can be asked for. A closed set, advertised in `describe()` and screened by
#: Policy before anything runs. `predicate=` is refused by the contract itself until the portable
#: grammar of §6 exists, so `row_read` is the whole-projection ordered read and nothing else.
CAPABILITIES: frozenset[str] = frozenset({"structural_read", "row_read"})


class _NotFound:
    """Sentinel: a cited parent's current value could not be re-read (row or column is gone)."""

    def __repr__(self) -> str:                                       # pragma: no cover - display
        return "<cited parent value not found>"


_NOT_FOUND = _NotFound()



class ConnectionRefused(AdapterError):
    """The source is unreachable, or the credential is rejected. Fails closed, always.

    §10's first two rows: no partial graph write, and never a silent downgrade of scope. Both are
    the same rule — a partial result that looks complete is the only outcome worse than an error.
    """


@dataclass(frozen=True)
class PitrWindow:
    """A deployment's declaration that it can actually serve a past instant (spine D6).

    **Read the estate's own rule before using this.** `fixtures/packs/finance/sources.yaml`
    settles the question this class exists to ask, and settles it against the obvious answer:

    > "`t0_capable: true` means a deployment OPTION exists. It is NOT permission for the ordinary
    > read path to pin the past, and no component may read it as such. On every source in this
    > estate `snapshot(at=<past>)` raises `CannotPinPast` on the ordinary interface, whatever
    > `t0_capable` says."

    and:

    > "A PITR restore produces a SEPARATE resource with its own pointer namespace
    > (`core_banking_pitr://<restored_at>/`), its own describe(), and its own authorisation.
    > Pointers may not straddle the two namespaces, and a pointer into the live source is never
    > silently resolved against a restore."

    That is a better design than routing a past read through the live adapter, and this Core
    follows it: a `PitrWindow` is honoured **only** when the contract's own `pinning_tier` is
    literally `T0` — a genuinely time-travelling store (Iceberg v3, Delta) that serves a past
    instant over its ordinary interface. For a PostgreSQL deployment with PITR the tier is not T0,
    the refusal stands, and the restore is registered as its own `source_id`.

    ``restored_source_id`` is carried so the refusal can name where to go instead. A refusal that
    does not say what to do next gets worked around.
    """

    mechanism: str
    retention_days: int
    restored_source_id: Optional[str] = None

    def covers(self, as_of: datetime, *, now: datetime) -> bool:
        return (now - timedelta(days=self.retention_days)) <= as_of <= now


# ---------------------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------------------


def _token(value: Any) -> Mapping[str, str]:
    """One cell value, canonicalised. Falls back for driver types `canonical_scalar` refuses.

    The fallback is deliberately typed and lossless-looking rather than `str(value)`: a digest
    over `str()` would make two different `memoryview`s of different bytes look identical, and a
    content hash that collides is a content hash that certifies drift did not happen.

    **Review R-005 finding 12.** A second fallback used to exist below this one, hashing
    `repr(value)` for anything neither `canonical_scalar` nor the bytes branch above recognised.
    That is refused now: `repr()` is not contracted to be deterministic across processes, across
    interpreter versions, or even across two calls on an object with no custom `__repr__` (the
    default includes the object's memory address). A content hash that is not reproducible is not
    a content address - it is `verify()` reporting drift, or reporting none, at random. Failing
    LOUDLY here, naming the type, means a genuinely new driver type gets a reviewed, registered
    canonical form (extend `canonical_scalar`, or a typed branch here) rather than a silent,
    unreplayable digest.
    """
    try:
        return canonical_scalar(value)
    except (TypeError, ValueError):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"t": "bytes", "v": sha256_of(bytes(value).hex())}
        raise ValueError(
            f"cannot compute a replayable content hash for a {type(value).__name__} cell value: "
            f"canonical_scalar does not support it and this Core has no registered canonical form "
            f"for it either. Hashing repr() would produce a content address with no guarantee of "
            f"being deterministic (§4a) - register a typed fallback here, or extend "
            f"canonical_scalar, instead of hashing prose") from None


def cell_digest(value: Any) -> str:
    """The content address of ONE cell. What `verify()` compares to decide material drift."""
    return sha256_of(canonical_json(_token(value)))


def row_digest(row: Mapping[str, Any], fields: Sequence[str], key: Sequence[str]) -> str:
    """One row, over the READ PROJECTION ONLY, keyed by its primary key."""
    return sha256_of(canonical_json({
        "key": [_token(row.get(k)) for k in key],
        "cells": {f: _token(row.get(f)) for f in fields},
    }))


def rows_digest(rows: Sequence[Mapping[str, Any]], fields: Sequence[str],
                key: Sequence[str]) -> str:
    """**Order-independent** digest of what was actually read (§5, rule 2).

    Sorted per-row digests, so a query-planner change, a parallel scan or a different pagination
    boundary does not read as data drift. Without this rule, verification produces noise within a
    week and somebody turns it off — which makes every pointer in the ledger decorative.
    """
    return sha256_of(canonical_json(sorted(row_digest(r, fields, key) for r in rows)))


def projection_schema_hash(entity: str, fields: Sequence[str], key: Sequence[str],
                           reflected: Mapping[str, Mapping[str, Any]]) -> str:
    """`schema_hash` over the **read projection only** (spine D6).

    R02's finding and the reason `DRIFT_BENIGN` exists at all: hashing the full source schema
    produces false-drift storms. An unrelated column added to a 200-column table would invalidate
    every pointer into it, the signal becomes noise, and verification gets switched off.
    """
    return sha256_of(canonical_json({
        "entity": entity,
        "key": list(key),
        "fields": [{"name": f,
                    "type": str(reflected.get(f, {}).get("type", "")),
                    "nullable": bool(reflected.get(f, {}).get("nullable", True))}
                   for f in fields],
    }))


def cell_schema_hash(entity: str, column: str, key: Sequence[str],
                     reflected: Mapping[str, Mapping[str, Any]]) -> str:
    """Schema fingerprint of the ONE column a cell's pointer cites (review R-005 finding 11).

    `projection_schema_hash` above is deliberately scoped to the READ's whole projection - it
    lives on `GroundedRows.snapshot`, not on a cell's own pointer, for the identical reason the
    module docstring gives for `content_hash`: a per-cell field must not move merely because a
    DIFFERENT read of the same column also happened to ask for other columns. Putting the
    whole-projection hash on the CELL (as an earlier version of this Core did) makes it useless
    for `verify()` two ways at once: two legitimate reads of the unchanged column would disagree
    because their field lists differ, and comparing against a freshly recomputed whole-projection
    hash at verify time (which does not know the ORIGINAL projection) is not even well-defined.

    So the pointer gets a fingerprint of exactly the column it cites, and `_verify` recomputes the
    identical function from current reflection and compares - the one comparison that was
    previously entirely absent: a `VARCHAR` silently narrowed to `CHAR(10)`, or a column that
    turned nullable, moved nothing `cell_digest` can see, and the value can look byte-identical
    while what it MEANS has changed.
    """
    return sha256_of(canonical_json({
        "entity": entity,
        "key": list(key),
        "column": column,
        "type": str(reflected.get(column, {}).get("type", "")),
        "nullable": bool(reflected.get(column, {}).get("nullable", True)),
    }))


# ---------------------------------------------------------------------------------------
# the Core
# ---------------------------------------------------------------------------------------


class PostgresCore:
    """Deterministic. No LLM. Owns the driver and the credentials.

    **It cannot be constructed without a `PolicyEngine`, and it obtains its own admission inside
    every verb.** That is the in-process half of Appendix A's "a caller must not be able to invoke
    Core directly and skip Policy; today this is stated, not enforced" — there is no parameter on
    any verb through which a caller could supply a wider decision table, because the verbs take
    only what the port takes.
    """

    #: T1 by DEFAULT — see the module docstring. Raised to T0 only when a `PitrWindow` with a real
    #: recovery endpoint is supplied AND the contract declares the tier.
    pinning_tier: str = "T1"
    capabilities: frozenset[str] = CAPABILITIES

    def __init__(self, *, source_id: str, engine: Union[str, Engine], policy: PolicyEngine,
                 sources_yaml: Optional[Path] = None,
                 contract: Optional[SourceContract] = None,
                 pitr: Optional[PitrWindow] = None,
                 now: Optional[Callable[[], datetime]] = None,
                 pack_version: str = "") -> None:
        if policy is None:                                       # pragma: no cover - typing
            raise PolicyNotApplied(
                "a Core cannot be constructed without a PolicyEngine: the documented path to a "
                "source has to run through Policy, and an optional policy is no policy")
        if contract is None:
            if sources_yaml is None:
                raise ValueError("PostgresCore needs either `contract` or `sources_yaml`")
            contract = load_source_contract(source_id, sources_yaml=sources_yaml)
        elif contract.source_id != source_id:
            # Review R-005 finding 9. Nothing else checks this: `self._contract` is what stamps
            # trust, tier, sensitivity and reachable verdicts onto every pointer this Core mints,
            # while `self.source_id` is what those pointers CLAIM. A caller that passes a
            # `contract` for a different source would have rows from A reflected against B's
            # schema and stamped with B's declarations, cited as if they came from A - which is
            # not a data bug, it is a caller supplying its own trust label by construction.
            raise ValueError(
                f"contract is for source {contract.source_id!r}, not {source_id!r}: a Core's "
                f"pointers claim source_id, and a contract for a different source would stamp "
                f"trust, tier and sensitivity from declarations that source never made")
        self.source_id = source_id
        self._contract = contract
        self._policy = policy
        self._pitr = pitr
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pack_version = pack_version or contract.estate
        self._owns_engine = isinstance(engine, str)
        self._engine: Engine = create_engine(engine) if isinstance(engine, str) else engine
        self._schema = contract.schema
        self._metadata = MetaData()
        self._described: Optional[SourceDescription] = None
        # T0 is never actually reachable through THIS instance attribute - see `_check_as_of`
        # (review R-005 finding 1): this Core has no point-in-time query mechanism, so `as_of` is
        # refused unconditionally regardless of what the contract declares. Read straight from
        # `self._contract.declared_tier` rather than re-deriving a second copy from `contract.entry`
        # (review R-005 "tier defaults disagree", High): two independent readings of the same YAML
        # key with two different fallbacks ("T1" here, "T2" in `SourceContract.declared_tier`) is
        # how an instance and its own description end up advertising different tiers.
        self.pinning_tier = (self._contract.declared_tier
                             if self._contract.declared_tier != "T0" else "T1")

    # --- reflection helpers -----------------------------------------------------------
    def _table(self, entity: str) -> Table:
        key = f"{self._schema}.{entity}" if self._schema else entity
        cached = self._metadata.tables.get(key)
        if cached is not None:
            return cached
        try:
            return Table(entity, self._metadata, schema=self._schema, autoload_with=self._engine)
        except SQLAlchemyError as exc:
            raise ConnectionRefused(
                f"{self.source_id}: cannot reflect {key}: {exc}. Failing closed - a read that "
                f"cannot see the schema cannot stamp a schema hash, and a pointer without one "
                f"cannot be verified") from exc

    def _reflected_columns(self, entity: str) -> dict[str, dict[str, Any]]:
        insp = inspect(self._engine)
        return {str(c["name"]): dict(c)
                for c in insp.get_columns(entity, schema=self._schema)}

    def _primary_key(self, entity: str) -> tuple[str, ...]:
        insp = inspect(self._engine)
        pk = insp.get_pk_constraint(entity, schema=self._schema) or {}
        return tuple(str(c) for c in (pk.get("constrained_columns") or ()))

    def _version_field(self, entity: str) -> Optional[str]:
        """The entity's cheap change signal — **declared by the contract, never guessed.**

        §5 permits `DRIFT_BENIGN` only where the adapter already gets an entity-level change signal
        for free. The finance contract makes the trap explicit: `updated_at` "is absent from six
        tables and is written by whichever code path happened to touch the row", so a Core that
        picked the column up because the name looked right would be *inventing* the signal — which
        is the one thing §5 says must not happen. So this reads the stream's declared cursor field
        and returns `None` when the contract declares none.

        `None` is not a degradation. It means `verify()` cannot distinguish "nothing moved" from
        "something we did not read moved", and `VERIFIED` is then the honest answer.
        """
        declared = self._contract.change_signal_field(entity)
        if declared and declared in self._reflected_columns(entity):
            return declared
        return None

    # --- verb 1: describe -------------------------------------------------------------
    def describe(self) -> SourceDescription:
        """Schema, capability matrix, retention reality, egress class. **No data.**

        Reflects TABLES and VIEWS. An earlier version only called `get_table_names()`; a role
        scoped through a view subquery (`roles.yaml` naming e.g. `v_team_parties`) would compile
        fine today (the compiled table only names the subquery's `table`/columns as strings and
        never checks they are in `describe()`), but the view would be invisible to `describe()`
        itself and to `entity_description`'s trust stamping - dormant while no estate declares one,
        and a silent gap in the schema a reader is shown the moment one does.
        """
        if self._described is not None:
            return self._described
        insp = inspect(self._engine)
        names = sorted(set(insp.get_table_names(schema=self._schema))
                       | set(insp.get_view_names(schema=self._schema)))
        entities = []
        for name in names:
            entities.append(self._contract.entity_description(
                name, columns=insp.get_columns(name, schema=self._schema),
                key=self._primary_key(name)))
        self._described = self._contract.describe(capabilities=self.capabilities,
                                                  entities=tuple(entities))
        return self._described

    # --- verb 2: snapshot -------------------------------------------------------------
    def snapshot(self, req: CapabilityRequest) -> SnapshotIdentity:
        """Pin a point in time. Takes the whole request because `schema_hash` covers the
        projection, and a method that never sees the projection cannot compute it."""
        admission = self._admit(req).check()
        self._check_as_of(req)
        with self._read_transaction(req.as_of) as conn:
            snap, _rows = self._snapshot_and_rows(conn, req, admission)
        return snap

    # --- verb 3: plan -----------------------------------------------------------------
    def plan(self, req: CapabilityRequest) -> ReadPlan:
        """Cost/row estimate plus the exact operations. Deterministic; metadata I/O only."""
        admission = self._admit(req).check()
        entity = req.projection.entity
        table = self._table(entity)
        key = self._require_total_order(req)
        selected = self._select_columns(req, key, admission)
        stmt = self._compile(req, table, selected, admission)
        operations = [str(stmt.compile(self._engine, compile_kwargs={"literal_binds": False}))]
        for rule in admission.decisions.rules:
            if rule.kind == "in_subquery":
                operations.append(f"-- row rule {rule.rule_id}: {rule.describe()}")
        withheld = [c.column for c in admission.decisions.columns if c.withheld]
        if withheld:
            operations.append(f"-- withheld by policy, not selected: {', '.join(withheld)}")

        estimated = self._estimate_rows(entity, req.limit)
        caveat = self._completeness_caveat(entity)
        return ReadPlan(
            read_hash=req.read_hash, source_id=self.source_id, entity=entity,
            operations=tuple(operations), estimated_rows=estimated,
            estimated_cost=Cost(rows=estimated),
            requires_capture=bool(self._contract.entry.get("capture_required", False)),
            completeness_caveat=caveat)

    def _completeness_caveat(self, entity: str) -> Optional[str]:
        """What this entity's own cursor honestly cannot promise.

        Declared, not inferred, and per STREAM: the contract's source-level cursor is `kind: none`
        while three append-only streams carry lossless compound-key cursors. A plan that read only
        the source-level cursor would caveat every read of every table identically, which is the
        same as caveating none of them.
        """
        stream = self._contract.stream(entity) or {}
        cursor = dict(stream.get("cursor") or self._contract.entry.get("cursor") or {})
        kind = str(cursor.get("kind") or "none")
        if kind == "none":
            return (f"{entity} has no change cursor at all, so an incremental read of it is not "
                    f"expressible and a full read cannot be shown to have missed nothing")
        if cursor.get("lossy"):
            return (f"cursor {kind!r} on {entity} is declared lossy: it may PRIORITISE a re-read "
                    f"and may never PROVE completeness")
        protocol = cursor.get("overlap_protocol") or {}
        if protocol.get("required"):
            return (f"cursor {kind!r} on {entity} is lossless ONLY under its declared overlap "
                    f"protocol (re-read {protocol.get('re_read_window')}, dedupe on "
                    f"{protocol.get('dedupe_on')}); without it the stream is T2, not T1")
        if cursor.get("known_gaps"):
            return f"declared sequence gap(s): {cursor['known_gaps']}"
        return None

    # --- verb 4: resolve --------------------------------------------------------------
    def resolve(self, req: CapabilityRequest) -> AdapterResult:
        """Execute. Returns `GroundedRows`: every cell carries a `SourcePointer`.

        **Review R-005 finding 3.** `structural_read` is the schema/counts/digests capability -
        `PolicyEngine.REASONS["METADATA_ONLY"]` names it exactly: "the role may see schema, counts
        and digests, never a value" - and it is the ONLY capability an `aggregate_only` role may
        request (`PolicyEngine.admit`, stage 7). `resolve()` is the one verb that emits actual cell
        VALUES, so it is the one verb `structural_read` must never reach: nothing before this line
        checked `req.capability` against what the verb actually discloses, so an admitted
        `structural_read` request could still walk straight through to real rows.

        Checked AFTER `_admit`, deliberately: "is `req.capability` advertised at all" is a more
        fundamental refusal than "is it the right capability for THIS verb", and a caller sending
        a capability this adapter has never heard of should see that answer, not a narrower one
        about `resolve()` specifically.
        """
        started = time.perf_counter()
        admission = self._admit(req).check()
        if req.capability != "row_read":
            raise CapabilityNotAdvertised(
                f"resolve() discloses cell VALUES and only accepts capability='row_read'; "
                f"{req.capability!r} is schema/counts/digests only (METADATA_ONLY) and must reach "
                f"snapshot() or plan(), never a verb that emits content")
        entity = req.projection.entity
        key = self._require_total_order(req)
        table = self._table(entity)
        selected = self._select_columns(req, key, admission)

        self._check_as_of(req)
        with self._read_transaction(req.as_of) as conn:
            subquery_sets = self._materialise_subqueries(conn, admission)
            stmt = self._compile(req, table, selected, admission)
            fetched = [dict(r) for r in conn.execute(stmt).mappings().all()]
            snap, rows_by_key = self._snapshot_and_rows(conn, req, admission, prefetched=fetched)

        version_field = self._version_field(entity)
        # Computed once per read, not once per cell: same reflection call `_select_columns` and
        # `_version_field` already make, reused here to stamp each cell's own schema fingerprint
        # (review R-005 finding 11).
        reflected = self._reflected_columns(entity)
        cells: list[tuple[Cell, ...]] = []
        for ordinal, row in enumerate(rows_by_key):
            denial = admission.decisions.row_verdict(
                row, attributes=admission.attribute_map, subquery_sets=subquery_sets)
            if denial is not None:
                # The row is outside the principal's scope. Skipped, and NEVER counted.
                #
                # **Review R-005 finding 4, and roles.yaml's own `existence_disclosure`.**
                # `own_portfolio_only`'s v1 promised "the response states that N rows were
                # withheld" and v2 retracted it: "That count IS the leak: it discloses how many
                # clients other RMs hold, and repeating the query with narrowing predicates turns
                # it into a population-count oracle." A row-scope predicate is pushed into the SQL
                # WHERE clause above as an optimisation, so `fetched` already excludes every row
                # this loop would deny - meaning a count taken HERE is not merely a leak risk, it
                # is almost always wrong too (zero, because the denied rows were never fetched to
                # be counted). Neither defect is fixable by making the count accurate: the estate's
                # policy is that no count may ever be disclosed for a scoped read, accurate or not
                # (`existence_disclosure.scope_declaration`: "A count is cardinality and cardinality
                # is the leak"). What discloses "this result is scoped" is the CONSTANT note Policy
                # already compiled into `admission.notes` ("this response is limited to your scope
                # under RULE-ID") - the same declaration whatever the projection, carrying no
                # number at all. `test_a_scoped_response_declares_the_scope_and_never_the_count`
                # is the test that would fail if a count ever reappeared here.
                continue
            cells.append(self._emit_row(req, row, ordinal, snap, key, version_field, admission,
                                        reflected))

        warnings = list(admission.notes)
        result_rows = GroundedRows(columns=req.projection.fields, rows=tuple(cells), snapshot=snap)
        return AdapterResult(
            read_hash=req.read_hash, rows=result_rows,
            cost=Cost(rows=len(cells), wall_ms=int((time.perf_counter() - started) * 1000)),
            warnings=tuple(warnings))

    # --- verb 5: verify ---------------------------------------------------------------
    def verify(self, pointer: SourcePointer, *, principal=None) -> Verdict:
        """Is this pointer still good? Returns the LATTICE, never a boolean (spine D6).

        ``principal`` is keyword-only with a default so the port's signature is unchanged. It is
        here because `FORBIDDEN` — "policy now denies what it once allowed" — is a per-principal
        fact, and `verify(pointer)` alone has nobody to deny. Without it that verdict is simply
        unreachable, which would be a quieter failure than saying so.
        """
        description = self.describe()
        verdict, _note = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description)

    def verify_with_note(self, pointer: SourcePointer,
                         *, principal=None) -> tuple[Verdict, Optional[str]]:
        """`verify` plus the operator-language reason, for building a `PointerResolution`."""
        description = self.describe()
        verdict, note = self._verify(pointer, description, principal)
        return assert_verdict_advertised(verdict, description), note

    def _verify(self, pointer: SourcePointer, description: SourceDescription,
                principal) -> tuple[Verdict, Optional[str]]:
        if pointer.source_id != self.source_id:
            return Verdict.UNVERIFIABLE, (
                f"pointer cites source {pointer.source_id!r}; this adapter serves "
                f"{self.source_id!r} and has no handle to the other")

        if pointer.kind == "derived":
            parents = list(pointer.derived_from or ())
            if not parents:
                return Verdict.UNVERIFIABLE, (
                    "a derived pointer with no cited parents cannot be re-resolved; its value "
                    "rests on a predicate bundle that has to be re-run against its snapshot")
            results = [self._verify(p, description, principal)[0] for p in parents]
            joined = combine_verdicts(results)
            if joined is not Verdict.VERIFIED:
                return joined, (f"derived value: the worst verdict over {len(parents)} cited "
                                f"parent(s) is {joined.value}")
            # Every parent still reads as it did. That establishes the INPUTS are unchanged; it
            # does NOT by itself establish the derivation still produces what THIS pointer cites.
            # Review R-005 finding 10 (R-3A finding 2 resurfacing on the verify() path, per
            # `wiki/40-reviews/R-005-adapter-fleet-triage.md`): "provenance structurally present"
            # is not "derivation verified", and the two must stay impossible to confuse. So the
            # rule is actually REPLAYED, over the parents' CURRENT values.
            return self._verify_derivation_replays(pointer, parents)

        if pointer.kind not in ("query_cell", "table_cell"):
            return Verdict.UNVERIFIABLE, (
                f"this Core re-resolves relational cells; pointer kind {pointer.kind!r} resolves "
                f"against the Capture Store, not against a live connection")

        entity = (pointer.table or "").split(".")[-1]
        column = pointer.column
        if not entity or not column or pointer.row_id is None:
            return Verdict.UNVERIFIABLE, (
                "the pointer does not name an entity, a column and a row id, so there is no row "
                "to go back to")

        if principal is not None:
            # The CHEAP half of a live policy re-check: fails fast, no query, for the bluntest
            # case (the principal was removed from the directory entirely).
            try:
                self._policy.binding_for(principal)
            except AdapterError:
                return Verdict.FORBIDDEN, ("the principal is no longer in the directory, so the "
                                           "read that produced this citation would be refused now")

        key = self._primary_key(entity)
        if len(key) != 1:
            return Verdict.UNVERIFIABLE, (
                f"{entity} has a composite primary key {key}; a single `row_id` cannot address "
                f"one of its rows")
        table = self._table(entity)
        with self._read_transaction() as conn:
            row = conn.execute(
                select(table).where(table.c[key[0]] == pointer.row_id)).mappings().first()

        if row is None:
            # `sources.yaml` declares which verdicts this source can PRODUCE. `core_banking`
            # declares `deletes_detectable: soft_only` and does not list VANISHED, so a missing
            # row cannot honestly be reported as vanished - it may be a hard delete this source
            # cannot detect. Saying UNVERIFIABLE is the honest answer, and emitting VANISHED here
            # would be refused by `assert_verdict_advertised` anyway.
            if Verdict.VANISHED in description.reachable_verdicts:
                return Verdict.VANISHED, f"row {pointer.row_id!r} is no longer in {entity}"
            return Verdict.UNVERIFIABLE, (
                f"row {pointer.row_id!r} is not in {entity}, and this source declares "
                f"deletes_detectable={description.deletes_detectable!r} - a missing row may be a "
                f"delete it cannot detect, so VANISHED would be a claim it cannot substantiate")

        if column not in row:
            return Verdict.DRIFT_MATERIAL, (
                f"column {column!r} is no longer present on {entity}: a cited value cannot have "
                f"stayed the same when the column it lived in is gone")

        if principal is not None:
            # Review R-005 finding 7. The cheap check above only catches "the principal is gone
            # entirely" - `FORBIDDEN` is a fact about a principal's binding CHANGING, and the
            # common case (a purpose no longer granted, a row-scope attribute reassigned, a column
            # newly masked) needs the real admission pipeline re-run for real, not simulated by
            # checking directory membership. Run it now that the ROW is in hand, since a row-scope
            # decision needs it.
            forbidden = self._forbidden_now(pointer, principal, entity, column, row, description)
            if forbidden is not None:
                return Verdict.FORBIDDEN, forbidden

        cited = pointer.snapshot.content_hash if pointer.snapshot else None
        if cited is None:
            return Verdict.UNVERIFIABLE, (
                "the pointer carries no cell digest, so a re-read has nothing to compare against")
        if cell_digest(row[column]) != cited:
            return Verdict.DRIFT_MATERIAL, (
                f"{entity}.{column} for row {pointer.row_id!r} no longer holds the value this "
                f"citation vouches for")

        # Review R-005 finding 11. The value is unchanged - but did what it MEANS change? A column
        # silently narrowed (`VARCHAR` -> `CHAR(10)`) or turned nullable moves nothing
        # `cell_digest` can see; `pointer.snapshot.schema_hash` exists precisely so this can be
        # checked, and nothing compared it against a fresh reflection until now.
        cited_schema = pointer.snapshot.schema_hash if pointer.snapshot else None
        if cited_schema is not None:
            current_schema = cell_schema_hash(entity, column, key, self._reflected_columns(entity))
            if current_schema != cited_schema:
                return Verdict.DRIFT_MATERIAL, (
                    f"{entity}.{column}'s schema has changed since this pointer was minted (its "
                    f"type or nullability moved) even though the cited value reads the same - a "
                    f"value that still looks identical cannot be trusted to still mean the same "
                    f"thing")

        # The cited value is unchanged. Did anything ELSE about the row move? That is only
        # answerable where the source hands us a cheap entity-level signal for free (§5), which is
        # why DRIFT_BENIGN is an advertised capability rather than a universal verdict.
        signal = pointer.rows_digest
        version_field = self._version_field(entity)
        if signal is not None and version_field and description.detects_benign_drift:
            if cell_digest(row[version_field]) != signal:
                return Verdict.DRIFT_BENIGN, (
                    f"nothing we cited changed; {entity}.{version_field} moved, so some column we "
                    f"did not read has been written")
        return Verdict.VERIFIED, None

    # --- verify() helpers (review R-005 findings 7 and 10) ----------------------------
    def _verify_derivation_replays(self, pointer: SourcePointer,
                                   parents: Sequence[SourcePointer]
                                   ) -> tuple[Verdict, Optional[str]]:
        """Replay a `derived` pointer's rule over its parents' CURRENT values (finding 10).

        Called only once every cited parent has already independently verified. That establishes
        the INPUTS are unchanged; replaying is the only thing that establishes the RULE, applied to
        those inputs, still produces the value THIS pointer cites — the two are not the same claim,
        and conflating them is exactly what `assert_provenance_present` is named at length to make
        impossible to do by accident (`grounding.py`).
        """
        cited = pointer.snapshot.content_hash if pointer.snapshot else None
        if cited is None:
            return Verdict.UNVERIFIABLE, (
                "the derived pointer carries no cell digest for the value it produced, so a "
                "replay has nothing to compare against")
        try:
            call = parse_derivation(pointer.derivation or "")
        except DerivationError as exc:
            return Verdict.UNVERIFIABLE, f"derivation cannot be parsed: {exc}"
        values: list[Any] = []
        for parent in parents:
            if parent.kind not in ("query_cell", "table_cell"):
                return Verdict.UNVERIFIABLE, (
                    f"a derived pointer whose parent is a {parent.kind!r} pointer cannot be "
                    f"replayed by this Core; it re-resolves relational cells only")
            value, note = self._read_cited_value(parent)
            if value is _NOT_FOUND:
                return Verdict.UNVERIFIABLE, note or "a cited parent value could not be re-read"
            values.append(value)
        try:
            produced = replay_derivation(call, values)
        except DerivationError as exc:
            return Verdict.UNVERIFIABLE, f"derivation {call.render()} is no longer replayable: {exc}"
        if cell_digest(produced) != cited:
            return Verdict.DRIFT_MATERIAL, (
                f"derivation {call.render()} replayed over the cited parent(s)' CURRENT value(s) "
                f"no longer produces the value this pointer cites: the rule, or an input outside "
                f"the cited parents, has changed")
        return Verdict.VERIFIED, None

    def _read_cited_value(self, pointer: SourcePointer) -> tuple[Any, Optional[str]]:
        """Re-read a `query_cell`/`table_cell` pointer's CURRENT value (not its verdict).

        A second, narrower read path alongside `_verify`'s own: that one answers "is this pointer
        still good", this one answers "what does it currently say" - the question a derivation
        replay needs answered for each of its parents.
        """
        entity = (pointer.table or "").split(".")[-1]
        column = pointer.column
        if not entity or not column or pointer.row_id is None:
            return _NOT_FOUND, (
                "a cited parent pointer does not name an entity, a column and a row id")
        key = self._primary_key(entity)
        if len(key) != 1:
            return _NOT_FOUND, (
                f"{entity} has a composite primary key {key}; a single row_id cannot address one "
                f"of its rows")
        table = self._table(entity)
        with self._read_transaction() as conn:
            row = conn.execute(
                select(table).where(table.c[key[0]] == pointer.row_id)).mappings().first()
        if row is None:
            return _NOT_FOUND, f"row {pointer.row_id!r} is no longer in {entity}"
        if column not in row:
            return _NOT_FOUND, f"column {column!r} is no longer present on {entity}"
        return row[column], None

    def _forbidden_now(self, pointer: SourcePointer, principal: Any, entity: str, column: str,
                       row: Mapping[str, Any], description: SourceDescription) -> Optional[str]:
        """Would THIS field, of THIS row, still be disclosed to `principal` today? (finding 7)

        The cheap directory-membership check the caller already ran catches only the bluntest
        case. `FORBIDDEN` means "policy now denies what it once allowed", and the common way that
        happens - a purpose no longer granted, a row-scope attribute reassigned, a column newly
        masked - is invisible to a directory lookup and needs the real admission pipeline re-run
        for real. Runs over an ISOLATED one-row budget so a `verify()` call never draws down the
        caller's own read budget - this reads nothing that was not already in hand.
        """
        try:
            req = CapabilityRequest(
                source_id=self.source_id, capability="row_read", principal=principal,
                projection=Projection(entity=entity, fields=(column,)),
                resolve=(pointer,), limit=1)
        except Exception as exc:                                     # noqa: BLE001 - see below
            # Any shape the request could not take (a purpose gone from the vocabulary, a source
            # mismatch reflected as a ValueError) is itself "this can no longer be expressed as a
            # request", which is the same fact `FORBIDDEN` exists to report.
            return f"the citation can no longer be re-expressed as a request: {exc}"
        try:
            admission = self._policy.admit(req, description=description, budget=Budget(max_rows=1))
        except AdapterError as exc:
            return f"policy now refuses the read that produced this citation: {exc}"
        decision = admission.decisions.decide(entity, column, row, principal)
        if decision.withheld:
            return (f"{entity}.{column} is now withheld from this principal "
                    f"({decision.reason_code}): policy denies what it once allowed")
        with self._read_transaction() as conn:
            subquery_sets = self._materialise_subqueries(conn, admission)
        denial = admission.decisions.row_verdict(
            row, attributes=admission.attribute_map, subquery_sets=subquery_sets)
        if denial is not None:
            return (f"row {pointer.row_id!r} of {entity} is no longer in this principal's row "
                    f"scope ({denial.reason_code}): policy denies what it once allowed")
        return None

    # ------------------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------------------
    def _admit(self, req: CapabilityRequest) -> Admission:
        """Every verb goes through Policy. There is no argument that bypasses this."""
        if req.source_id != self.source_id:
            raise PolicyNotApplied(
                f"request targets {req.source_id!r}; this adapter serves {self.source_id!r}")
        if req.capability not in self.capabilities:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises {sorted(self.capabilities)}, not "
                f"{req.capability!r}")
        if req.capability_version is not None:
            raise CapabilityNotAdvertised(
                f"{self.source_id!r} advertises only structural verbs, whose meaning is fixed by "
                f"the canonicalisation scheme; it declares no named queries, so "
                f"capability_version={req.capability_version!r} names a revision of something "
                f"that does not exist")
        return self._policy.admit(req, description=self.describe())

    def _read_transaction(self, as_of: Optional[datetime] = None):
        """One transaction, opened REPEATABLE READ. A snapshot pins nothing outside it.

        ``as_of`` is accepted only so call sites can pass ``req.as_of`` through without a branch;
        it does NOTHING with it, on purpose - see `_check_as_of`, review R-005 finding 1. Every
        call site routes through `_check_as_of` first, which now refuses a non-``None`` `as_of`
        unconditionally, so this should never observe one. The check here is not decorative: it is
        what turns "silently reads live data instead of the past" back into a loud failure if that
        invariant is ever violated by a future call site.
        """
        if as_of is not None:                                     # pragma: no cover - defensive
            raise CannotPinPast(
                f"{self.source_id!r}: _read_transaction was asked for as_of={as_of.isoformat()} "
                f"directly. This Core has no point-in-time query mechanism - a REPEATABLE READ "
                f"transaction over a live connection is a consistent read of NOW, never of the "
                f"past - so every call site must route through _check_as_of first, and that now "
                f"refuses every as_of unconditionally (review R-005 finding 1)")
        engine = self._engine
        try:
            conn = engine.connect()
        except SQLAlchemyError as exc:                            # pragma: no cover - live PG only
            raise ConnectionRefused(
                f"{self.source_id}: cannot connect: {exc}. Failing closed - no partial graph "
                f"write, and never a silent downgrade of scope (§10)") from exc
        try:
            if engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        except SQLAlchemyError as exc:                            # pragma: no cover - live PG only
            conn.close()
            raise ConnectionRefused(f"{self.source_id}: {exc}") from exc
        return conn

    def _check_as_of(self, req: CapabilityRequest) -> None:
        """The past is ALWAYS refused. Read the estate's own rule in `PitrWindow` first.

        Ordinary PostgreSQL is not a time machine: a repeatable-read transaction gives a CONSISTENT
        READ OF NOW. There is deliberately no branch that substitutes ``now()``. A caller that
        believes it received the past and did not is the failure the whole tier system exists to
        prevent, and it is silent — the rows look exactly like rows.

        **Review R-005 finding 1.** An earlier version of this check let `as_of` through whenever
        the contract declared tier `T0` and a `PitrWindow` covered the instant - and then did
        nothing with `as_of` at all: `_read_transaction` opens an ordinary connection to the LIVE
        engine regardless, so a "permitted" T0 read silently returned the PRESENT, stamped with a
        snapshot claiming to be the past. No PITR endpoint or database time-travel operation was
        ever selected, because none exists on this Core - it owns one SQLAlchemy `Engine`, and a
        REPEATABLE READ transaction on it is a consistent read of now, on every dialect, always.

        The fix is not to build that mechanism (a PITR restore is explicitly a SEPARATE resource
        with its own `source_id`, its own `describe()` and its own authorisation - see
        `PitrWindow`'s own docstring - never the live source read through). The fix is that this
        Core never claims to serve `as_of` at all: `t0_capable: true` and even `pinning_tier: T0`
        on THIS kind of source describe a deployment OPTION that lives elsewhere, never permission
        for this read path to pin the past.
        """
        if req.as_of is None:
            return
        entity_tier = self._contract.entity_tier(req.projection.entity)
        elsewhere = (f" A restore is a SEPARATE resource with its own pointer namespace and its "
                     f"own authorisation; register it as source "
                     f"{self._pitr.restored_source_id!r} and read it there."
                     if self._pitr is not None and self._pitr.restored_source_id else
                     " Resolve against the Capture Store instead.")
        capable = (" (`t0_capable: true`, and even `pinning_tier: T0` on this kind of source, "
                   "name a deployment OPTION that lives on a SEPARATE resource, never permission "
                   "for THIS live connection to pin the past)"
                   if (self._contract.entry.get("t0_capable")
                       or self._contract.declared_tier == "T0") else "")
        raise CannotPinPast(
            f"{self.source_id!r} is {self._contract.declared_tier} source-wide and "
            f"{req.projection.entity} is {entity_tier}; it cannot serve "
            f"as_of={req.as_of.isoformat()}{capable}. A repeatable-read transaction over this "
            f"Core's one live SQLAlchemy connection is a consistent read of NOW, not time travel, "
            f"and no readable snapshot of a past instant exists on it - this Core has no "
            f"point-in-time query mechanism at all, whatever the contract's pinning_tier "
            f"says (D6).{elsewhere}")

    def _require_total_order(self, req: CapabilityRequest) -> tuple[str, ...]:
        """The reflected primary key, checked against the ordering the request declared.

        This is where "order_by must be TOTAL" stops being a caller's assertion and becomes a
        fact: the key comes from reflection, so a request that claims the wrong key, or orders by
        something that leaves ties, is refused by the database's own schema rather than by its
        own paperwork.
        """
        entity = req.projection.entity
        key = self._primary_key(entity)
        if not key:
            raise UnorderedPointeredRead(
                f"{entity} has no primary key, so no ordering over it is total and no ordinal it "
                f"produces is a stable citation. Pointered reads need stable row identity (§7)")
        if len(key) > 1:
            # Review R-005 finding 8. `_emit_row` mints ONE `row_id` per pointer by joining the
            # key columns with "|" - ambiguous on its own (`("a|b", "c")` and `("a", "b|c")`
            # collide) - and BOTH `_verify` and resolve-mode re-fetch already refuse a composite
            # key outright (`len(key) != 1`). A discovery read was the one path left that would
            # still mint a pointer for a composite-key entity: usable to disclose a value once, and
            # never again resolvable or verifiable. Refused here, at MINT time, for every verb -
            # a pointer this Core cannot later re-resolve or verify is not a stable citation.
            raise UnorderedPointeredRead(
                f"{entity} has a composite primary key {key}; this Core mints one `row_id` per "
                f"pointer by joining key columns with '|', which is ambiguous, and neither "
                f"verify() nor resolve-mode can address a composite key at all (both require "
                f"exactly one column). Refused before a single pointer is minted rather than "
                f"emitted and orphaned (§7)")
        if req.resolve and not req.order_by:
            return key                                    # resolve-only: mints no ordinals
        declared = tuple(req.projection.key)
        if declared != key:
            raise UnorderedPointeredRead(
                f"the projection declares key {declared or '()'} and {entity}'s primary key is "
                f"{key}. A pointer cites the KEY, so a request that names a different one would "
                f"produce citations that address nothing")
        tail = tuple(t.lstrip("-") for t in req.order_by)[-len(key):]
        if set(tail) != set(key):
            raise UnorderedPointeredRead(
                f"order_by {req.order_by} does not end with {entity}'s primary key {key}: tied "
                f"rows have no defined position, so `row_ordinal` moves under pagination and plan "
                f"changes and is not a citation (§7)")
        return key

    def _select_columns(self, req: CapabilityRequest, key: Sequence[str],
                        admission: Optional[Admission] = None) -> tuple[str, ...]:
        """Projection + key + change signal + whatever the ROW RULES need, deduplicated.

        Only the PROJECTION is hashed and only the projection is emitted. The extra columns are
        machinery, and the third one is the easiest to forget: **a row rule cannot be evaluated
        against a column that was not read.** `relationship_manager` filters `party` on
        `primary_rm_code`, which nothing projects - and a local check against an absent column
        compares `None` to `"RM-007"`, denies every row, and returns an empty result that looks
        like "this RM has no clients". Fails closed, which is the right direction, and is still
        wrong.
        """
        entity = req.projection.entity
        available = set(self._reflected_columns(entity))
        wanted = list(req.projection.fields)
        extras = [*key, self._version_field(entity) or ""]
        if admission is not None:
            extras.extend(str(rule.column) for rule in admission.decisions.rules if rule.column)
        for extra in extras:
            if extra and extra in available and extra not in wanted:
                wanted.append(extra)
        return tuple(wanted)

    def _compile(self, req: CapabilityRequest, table: Table, selected: Sequence[str],
                 admission: Admission):
        """Build the statement. No string interpolation reaches SQL - ever.

        Columns are looked up on the reflected `Table`, so an identifier that is not a real column
        raises here rather than being concatenated into a query. `predicate` is refused by the
        contract (§6), and the only WHERE clause is the compiled row rule, pushed down as an
        OPTIMISATION - the enforcement is `row_verdict` re-evaluating it against each raw row.
        """
        missing = [c for c in selected if c not in table.c]
        if missing:
            raise PolicyNotApplied(
                f"{req.projection.entity} does not have column(s) {missing}: the projection names "
                f"something reflection cannot see")
        stmt = select(*[table.c[c] for c in selected])
        key = self._require_total_order(req)

        if req.resolve:
            # RESOLUTION MODE: re-fetch exactly the rows the given pointers address, and nothing
            # else. Restricting by row id rather than replaying the original predicate is the
            # point - a pointer is a locator, and re-resolving it must not depend on the filter
            # that happened to surface it the first time.
            #
            # The result is ordered by the KEY, not by the order the pointers arrived in.
            # `pointer_set_digest` is order-independent (asking for the same three pointers in a
            # different order is the same read), and that is only sound if the RESULT ordering is
            # independent of request order too - otherwise two identical read hashes would
            # describe two differently-ordered results. This is that contract, made true.
            # A resolve pointer that names a DIFFERENT entity than this request's projection has
            # no path to be re-resolved by it: the row_id it carries could coincidentally address
            # a real row of THIS entity, and this Core would silently disclose that unrelated row
            # under the caller's own column names instead of refusing. Checked only where the
            # pointer bothered to name a table at all - some pointer kinds this Core also accepts
            # in `resolve=` (a caller batching a mixed pointer set) may not.
            mismatched = [p for p in req.resolve
                         if p.table and p.table.split(".")[-1] != req.projection.entity]
            if mismatched:
                raise UnorderedPointeredRead(
                    f"resolve carries pointer(s) for {sorted({p.table for p in mismatched})}, not "
                    f"{req.projection.entity!r}: a resolution request re-fetches THIS entity, and "
                    f"a pointer for another one has no path to be re-resolved by it")
            # `key` is already guaranteed length 1 here - `_require_total_order` (called above,
            # before this branch) now refuses a composite key for every verb, not only this one
            # (review R-005 finding 8).
            row_ids = sorted({p.row_id for p in req.resolve if p.row_id is not None})
            stmt = stmt.where(table.c[key[0]].in_(row_ids)) if row_ids else stmt.where(
                text("1 = 0"))

        for clause in self._row_filter_clauses(table, admission):
            stmt = stmt.where(clause)

        terms = req.order_by or tuple(key)          # resolve-only reads are exempt from order_by
        for term in terms:
            desc, name = term.startswith("-"), term.lstrip("-")
            col = table.c[name]
            stmt = stmt.order_by(col.desc() if desc else col)
        return stmt.limit(req.limit)

    def _row_filter_clauses(self, table: Table, admission: Admission) -> list[Any]:
        """The WHERE conditions from admitted row rules, shared by the real read and by anything
        else that needs to know exactly what "in scope" means (today: only the real read; the
        method exists so the real read and any future caller can never quietly disagree).

        **Review R-005 finding 2.** An unbound principal attribute must match NOTHING - the `eq`
        branch already did this (`1 = 0`), but the `in_subquery` branch's `where_column` compared
        against ``value if value is not None else None``, which SQLAlchemy compiles to
        ``IS NULL``. A missing credential/attribute then matched every row whose `where_column`
        happens to be SQL NULL instead of matching none, turning a missing attribute into a wider
        grant than any bound one would produce. Both branches now fail closed identically.
        """
        clauses: list[Any] = []
        for rule in admission.decisions.rules:
            if rule.kind == "eq":
                expected = admission.attribute_map.get(str(rule.principal_attr))
                if expected is None:
                    # Fail closed: an unbound principal attribute must match nothing, and a
                    # missing WHERE clause here would match everything.
                    clauses.append(text("1 = 0"))
                    continue
                clauses.append(table.c[str(rule.column)] == expected)
            elif rule.kind == "in_subquery":
                sub = rule.subquery
                assert sub is not None
                sub_table = self._table(sub.table)
                inner = select(sub_table.c[sub.select_column])
                if sub.where_column:
                    value = admission.attribute_map.get(str(sub.where_principal_attr))
                    if value is None:
                        # Fail closed here too: `sub_table.c[...] == None` compiles to `IS NULL`
                        # and would admit every subquery row whose where_column happens to be
                        # NULL, for a principal the directory never bound this attribute for.
                        clauses.append(text("1 = 0"))
                        continue
                    inner = inner.where(sub_table.c[sub.where_column] == value)
                clauses.append(table.c[str(rule.column)].in_(inner))
        return clauses

    def _materialise_subqueries(self, conn, admission: Admission) -> dict[str, frozenset]:
        """Run each compiled subquery ONCE, inside the read transaction, into a value set.

        The Core executes exactly the subquery Policy compiled and nothing else — it holds a
        `Subquery` value object, not a string, so there is no widening available to it. The set is
        what `row_verdict` then evaluates each raw row against locally, which is §7's rule that
        row-level decisions happen where the raw rows are.
        """
        out: dict[str, frozenset] = {}
        for rule in admission.decisions.rules:
            if rule.kind != "in_subquery" or rule.subquery is None:
                continue
            sub = rule.subquery
            if sub.where_column:
                value = admission.attribute_map.get(str(sub.where_principal_attr))
                if value is None:
                    # Review R-005 finding 2, the local-evaluation twin of the SQL-side fix above.
                    # An empty set matches nothing (`_row_passes` treats `x in frozenset()` as
                    # False), which is the fail-closed answer for an unbound attribute - never a
                    # query that filters the subquery's own table by `where_column IS NULL` and
                    # admits whatever rows happen to hold a NULL there.
                    out[rule.rule_id] = frozenset()
                    continue
                sub_table = self._table(sub.table)
                stmt = select(sub_table.c[sub.select_column]).where(
                    sub_table.c[sub.where_column] == value)
            else:
                sub_table = self._table(sub.table)
                stmt = select(sub_table.c[sub.select_column])
            out[rule.rule_id] = frozenset(r[0] for r in conn.execute(stmt).all())
        return out

    def _estimate_rows(self, entity: str, limit: int) -> int:
        table = self._table(entity)
        with self._engine.connect() as conn:
            if self._engine.dialect.name == "postgresql":
                # Planner statistics: metadata, not a scan. A COUNT(*) over a large table is I/O
                # a plan() is not supposed to do.
                est = conn.execute(
                    text("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t)"),
                    {"t": f"{self._schema}.{entity}" if self._schema else entity}).scalar()
                total = int(est or 0)
            else:
                total = int(conn.execute(select(func.count()).select_from(table)).scalar() or 0)
        return min(total, limit)

    def _snapshot_and_rows(self, conn, req: CapabilityRequest, admission: Admission,
                           prefetched: Optional[Sequence[Mapping[str, Any]]] = None
                           ) -> tuple[SnapshotIdentity, list[Mapping[str, Any]]]:
        """The read-level snapshot, built AFTER the rows are in hand.

        `pg_export_snapshot()` names a snapshot only while the exporting transaction is open and
        dies at COMMIT, so it cannot be a durable content hash. Read consistency comes from the
        transaction we are already inside; replayability comes from an order-independent digest of
        what was actually read.
        """
        entity = req.projection.entity
        key = self._require_total_order(req)
        if prefetched is None:
            table = self._table(entity)
            selected = self._select_columns(req, key, admission)
            stmt = self._compile(req, table, selected, admission)
            prefetched = [dict(r) for r in conn.execute(stmt).mappings().all()]
        rows = [dict(r) for r in prefetched]
        as_of = self._now()
        snap = SnapshotIdentity(
            source_id=self.source_id,
            as_of=as_of,
            content_hash=rows_digest(rows, req.projection.fields, key),
            schema_hash=projection_schema_hash(entity, req.projection.fields, key,
                                               self._reflected_columns(entity)),
            query_hash=req.read_hash,           # D2 — the pointer identity lives HERE
        )
        return snap, rows

    # --- emission -------------------------------------------------------------------
    def _emit_row(self, req: CapabilityRequest, row: Mapping[str, Any], ordinal: int,
                  snap: SnapshotIdentity, key: Sequence[str], version_field: Optional[str],
                  admission: Admission, reflected: Mapping[str, Mapping[str, Any]]
                  ) -> tuple[Cell, ...]:
        entity = req.projection.entity
        row_id = "|".join(str(row[k]) for k in key)
        signal = cell_digest(row[version_field]) if version_field and version_field in row else None
        cells: list[Cell] = []
        for column in req.projection.fields:
            decision = admission.decisions.decide(entity, column, row, req.principal)
            if decision.withheld:
                # D4 — a denial, NOT an assertion that a value exists behind it. It carries no
                # pointer, because withholding is not a citation.
                cells.append(Withheld(record=decision.to_record(
                    field_path=f"{entity}.{column}", principal=req.principal,
                    policy_version=admission.decisions.policy_version)))
                continue

            raw = row[column]
            if raw is None:
                # "Not in the source" and "not permitted to you" are different facts (D4). A SQL
                # NULL is the first. An empty string is NOT: the estate deliberately uses both
                # inconsistently, and deciding they mean the same thing would be a data-quality
                # judgement an adapter has no standing to make.
                cells.append(Absent(field_path=f"{entity}.{column}"))
                continue

            cell = self._emit_cell(req, entity, column, raw, row_id, ordinal, snap, signal,
                                   decision.transform, key, reflected)
            # §4a — THE WALL RUNS HERE, at emission, against the row this query returned, indexed
            # by the pointer's own column. Not in a test, and not in review.
            assert_cell_grounded_in_row(cell, row)
            cells.append(cell)
        return tuple(cells)

    def _emit_cell(self, req: CapabilityRequest, entity: str, column: str, raw: Any,
                   row_id: str, ordinal: int, snap: SnapshotIdentity,
                   signal: Optional[str], transform: Optional[str], key: Sequence[str],
                   reflected: Mapping[str, Mapping[str, Any]]) -> GroundedValue:
        """Verbatim value keeps its pointer; a transformed value gets a `derived` one (§4a)."""
        raw_ptr = SourcePointer(
            source_id=self.source_id,
            kind="query_cell",
            snapshot=SnapshotIdentity(
                source_id=self.source_id, as_of=snap.as_of,
                # Per-cell, deliberately: see the module docstring on why the read-level snapshot
                # on every pointer makes `verify()` unable to tell material drift from any drift.
                content_hash=cell_digest(raw),
                # Review R-005 finding 11. `snap.schema_hash` is the WHOLE READ's projection
                # schema hash - two legitimate reads of this SAME, UNCHANGED column would stamp
                # DIFFERENT values here merely because they asked for different OTHER columns,
                # which makes it useless as an identity and impossible to usefully recompare at
                # `verify()` time (the original full field list is not knowable from one pointer).
                # `cell_schema_hash` is scoped to the one column this pointer actually cites, for
                # the identical reason `content_hash` was scoped down to the cell.
                schema_hash=cell_schema_hash(entity, column, key, reflected),
                query_hash=snap.query_hash),
            query_hash=req.read_hash,
            row_id=row_id,                      # stable identity — the PRIMARY KEY
            row_ordinal=ordinal,                # hint, not identity
            column=column,
            table=f"{self._schema}.{entity}" if self._schema else entity,
            # The row's entity-level change signal (§5). The only field on a `SourcePointer` whose
            # name fits a digest over the row, and DRIFT_BENIGN is unanswerable without it.
            rows_digest=signal,
            # Trust is per FIELD, from the domain pack's source contract — never "this database is
            # ours, so everything in it is trusted". A free-text `notes` column is attacker-
            # writable whoever owns the table.
            trust=self._contract.trust_of(entity, column),
        )
        if transform is None:
            return GroundedValue(value=raw, pointer=raw_ptr, confidence=1.0)

        produced = redact_pii(raw, column)
        if produced == raw:
            # The transformation was a no-op on this value (a DATE has no PII pattern to match).
            # Emitting a `derived` pointer anyway would claim a transformation that did not
            # happen, and would cost the cell its locator for nothing.
            return GroundedValue(value=raw, pointer=raw_ptr, confidence=1.0)
        derived_ptr = SourcePointer(
            source_id=self.source_id,
            kind="derived",
            # Review R-005 finding 10. An earlier version set no `snapshot` at all on a `derived`
            # pointer, so `verify()` had no digest of the PRODUCED value to replay against and
            # could only combine parent verdicts - "provenance structurally present" masquerading
            # as "derivation verified". `content_hash` here is the digest of what the derivation
            # actually produced, which is what `_verify_derivation_replays` compares a fresh
            # replay against.
            snapshot=SnapshotIdentity(
                source_id=self.source_id, as_of=snap.as_of, content_hash=cell_digest(produced),
                schema_hash=raw_ptr.snapshot.schema_hash, query_hash=snap.query_hash),
            query_hash=req.read_hash,
            # A REGISTERED, REPLAYABLE rule — not prose. `assert_grounded` re-runs it over the raw
            # cell and requires it to produce exactly this value, so the redaction is checkable
            # rather than merely declared.
            derivation=str(transform),
            derived_from=[raw_ptr],             # the raw cell, still cited, still verifiable
            trust=raw_ptr.trust,                # LUB — redaction does not launder trust
        )
        assert_derived_from(derived_ptr, raw_ptr)
        return GroundedValue(value=produced, pointer=derived_ptr, confidence=1.0)

    # --- lifecycle --------------------------------------------------------------------
    def close(self) -> None:
        if self._owns_engine:
            self._engine.dispose()
