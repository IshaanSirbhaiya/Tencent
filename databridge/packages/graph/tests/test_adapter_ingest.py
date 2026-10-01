"""The graph, built through the adapter fleet — `adapter_ingest.py`.

STATUS.md §7 named two gaps in one breath: graph pointers (`estate_seed._cell`) and adapter
pointers (a Core's own `SourcePointer`) are different populations, and `estate_seed.py` grounds a
whole DATASET at one `trust:` value while `sources.yaml` declares trust per FIELD. This file proves
the bridge that closes both — `packages/graph/databridge_graph/adapter_ingest.py`, wired into
`estate_seed.build_pack_graph` as an opt-in, per-dataset override (`adapter_rows=`) — and pins the
real, current findings the two packs' own designated "build the graph" purposes hit when asked to
admit a read for real, rather than working around either.

**What is proven positively (finance, `packages/graph/databridge_graph/finance_seed.py`):**
`cb_facility_adapter` reads `core_banking.facility` through a real `PostgresCore`, behind a real
`PolicyEngine.admit()`, under `compliance_officer`/`kyc.screening` (the only role+purpose on this
estate that both clears `core_banking`'s `RESTRICTED_INTERNAL` egress ceiling and does not deny or
mask `facility.notes`). The graph node this produces carries the CORE's own `query_cell` pointers —
`trusted` for `interest_basis`, `untrusted` for `notes` — sitting on the SAME node, which is exactly
what a dataset-level `trust:` stamp cannot express.

**What is proven as a refusal, pinned rather than routed around (government,
`packages/graph/databridge_graph/government_seed.py`):** `legacy_records_adapter` reads
`legacy_records` through a real `DocumentCore`. Two independent, real policy-layer facts currently
block it — neither is a bug in this bridge, and neither is worked around here:

1. `roles.yaml`'s `adapter_service` role — the pack's own INTENDED reader for `register.graph_build`
   -> `ingest` — writes every grant `condition: ":principal.bound_source_id == '...'"`, and
   `rolemodel.py`'s OWN loader excludes every conditional grant from `Role.allow`: "this build
   cannot evaluate a credential binding... the grant is kept on `Grant.condition` and left OUT of
   `allow`". `adapter_service.allow == {}` — reaches nothing, for any source.
2. `auditor`/`audit` (bound from `register.pointer_verification`, `subject_scoped: false`, already
   in the real `fixtures/packs/government/purpose_bindings.yaml`) DOES grant `legacy_records.
   LEGACY-MASTER` unconditionally — but `roles.yaml` writes that object in the ESTATE's own
   casing, and `DocumentCore`'s `FixedWidthReader` derives its entity name from the COPYBOOK,
   lower-cased and underscored: `legacy_master`. The native `grant_list` loader does not reconcile
   the two, so the read is refused at the object-reachability stage — a naming mismatch between the
   role file's declared object and the Core's reader-level entity name, not a deliberate refusal to
   disclose.

Also finance's `finance.graph_build` — the pack's own build-time-crawl purpose — is pinned unbound,
exactly as `packages/adapter/databridge_adapter/config/purpose_bindings.yaml`'s own header
documents.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from databridge_adapter import (
    Budget,
    CapabilityRequest,
    PolicyEngine,
    Principal,
    PrincipalBinding,
    Projection,
    load_purpose_bindings,
    load_role_model,
    purpose_vocabulary,
    use_purposes,
)
from databridge_adapter.errors import PurposeNotBound, PurposeNotPermitted
from databridge_adapter.rolemodel import PURPOSE_BINDINGS_FILE
from databridge_adapter.sources.document import DocumentCore
from databridge_adapter.sources.postgres import PostgresCore
from databridge_adapter.testing.estate import ROLES_YAML, SOURCES_YAML, load_finance_estate
from databridge_core import GroundedValue, SourcePointer, Trust
from databridge_graph import finance_seed, government_seed
from databridge_graph.adapter_ingest import AdapterRow, rows_from_core
from databridge_graph.estate_seed import SeedMapError, build_pack_graph, load_seed_map

PACKS_ROOT = Path(__file__).resolve().parents[3] / "packs"


# =======================================================================================
# `AdapterRow` / `rows_from_core` — the bridge itself, against a minimal fake Core
# =======================================================================================


class _FakeRows:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows


class _FakeResult:
    def __init__(self, columns, rows):
        self.rows = _FakeRows(columns, rows)


class _FakeCore:
    """Just enough of a Core's `resolve()` for `rows_from_core` to exercise — no real adapter
    machinery, so this test is fast and has no filesystem or Policy dependency."""

    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows

    def resolve(self, req):
        return _FakeResult(self._columns, self._rows)


def test_rows_from_core_pairs_grounded_values_with_their_pointers():
    ptr = SourcePointer(source_id="s1", kind="table_cell", table="t", column="a", row_id="1",
                        trust=Trust.TRUSTED)
    cell = GroundedValue(value="hello", pointer=ptr)
    core = _FakeCore(("a",), [(cell,)])
    rows = list(rows_from_core(core, lambda: object()))
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, AdapterRow)
    assert row["a"] == "hello"
    assert row.pointer_for("a") == ptr
    assert row.pointer_for("a").trust is Trust.TRUSTED


def test_rows_from_core_yields_none_with_no_pointer_for_a_non_grounded_cell():
    from databridge_core import Absent

    absent = Absent(field_path="t.a")
    core = _FakeCore(("a",), [(absent,)])
    rows = list(rows_from_core(core, lambda: object()))
    assert len(rows) == 1
    assert rows[0]["a"] is None
    assert rows[0].pointer_for("a") is None


def test_a_plain_dict_row_has_no_pointer_for_and_falls_through_to_the_dataset_stamp():
    """`estate_seed._pointer` duck-types `pointer_for`; a plain dict (every existing reader's row
    shape) simply does not have it, which is the whole compatibility story in one assertion."""
    row = {"a": 1}
    assert getattr(row, "pointer_for", None) is None


# =======================================================================================
# The pinned government-side findings
# =======================================================================================


def test_adapter_service_ingest_purpose_is_currently_unreachable():
    """`register.graph_build` -> `ingest`: the pack's own intended build-time-crawl route.

    Pinned at the `RoleModel` level, which is where the exclusion actually happens
    (`rolemodel.py`'s own docstring: "this build cannot evaluate a credential binding... left OUT
    of `allow`") — a load-time fact, not something that depends on which source is asked for.
    """
    role_model = load_role_model(government_seed.GOV_ROLES_YAML)
    adapter_service = role_model.roles["adapter_service"]
    assert adapter_service.allow == {}
    assert "ingest" in adapter_service.purposes
    # Every one of its grants is conditional — the mechanism the loader cannot evaluate — so NONE
    # of them survived into `allow`, for any source, not merely `legacy_records`.
    assert adapter_service.grants
    assert all(g.conditional for g in adapter_service.grants)


def test_legacy_records_via_adapter_is_refused_by_an_entity_name_mismatch():
    """`auditor`/`audit` DOES grant `legacy_records.LEGACY-MASTER` unconditionally — the read is
    refused one stage later, on the ESTATE's object name vs the Core's reader-level entity name
    (`legacy_master`), not on the grant itself. `government_seed.legacy_records_via_adapter` is
    real, working wiring; this is what it currently returns, and why."""
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(PurposeNotPermitted, match=r"legacy_records\.legacy_master"):
            government_seed.legacy_records_via_adapter(capture_root=Path(td) / "captures",
                                                        limit=10)


def test_the_auditor_role_does_grant_legacy_records_unconditionally():
    """The other half of the previous test's claim, checked directly against the role file so the
    refusal above is provably an entity-naming gap and not a simple lack of a grant."""
    role_model = load_role_model(government_seed.GOV_ROLES_YAML)
    auditor = role_model.roles["auditor"]
    assert "legacy_records.LEGACY-MASTER" in auditor.purpose_grants.get("audit", frozenset())
    assert "legacy_records.LEGACY-MASTER.LM-KEY" not in auditor.deny_columns


def test_the_government_purpose_bindings_file_binds_what_this_module_uses():
    role_model = load_role_model(government_seed.GOV_ROLES_YAML)
    binding = load_purpose_bindings(role_model.estate, path=government_seed.GOV_PURPOSE_BINDINGS_YAML,
                                    known_estate_purposes=role_model.estate_purposes)
    assert binding.bindings["register.pointer_verification"] == "audit"
    assert binding.bindings["register.graph_build"] == "ingest"


# =======================================================================================
# The pinned finance-side finding
# =======================================================================================


def test_finance_graph_build_purpose_is_currently_unbound():
    """`finance.graph_build`: the pack's own build-time-crawl purpose. Deliberately unbound in the
    PACKAGED `purpose_bindings.yaml` — its own header explains why at length. Pinned here so a
    future binding is a deliberate, visible change to this test, not a silent behaviour shift."""
    role_model = load_role_model(ROLES_YAML)
    binding = load_purpose_bindings(role_model.estate,
                                    known_estate_purposes=role_model.estate_purposes)
    with pytest.raises(PurposeNotBound):
        binding.require("finance.graph_build")
    # The packaged file really is the one consulted by default — confirms the test above is not
    # reading a copy that happens to agree.
    assert PURPOSE_BINDINGS_FILE.is_file()


# =======================================================================================
# The positive proof — finance `facility`, real per-field trust on one node
# =======================================================================================


@pytest.fixture(scope="module")
def facility_rows(tmp_path_factory):
    estate_dir = tmp_path_factory.mktemp("finance-adapter-estate")
    return finance_seed.facility_via_adapter(estate_dir=estate_dir, limit=1000)


def test_facility_via_adapter_admits_and_returns_real_rows(facility_rows):
    assert len(facility_rows) > 100  # the fixture's facility table is in the low hundreds


def test_a_trusted_field_carries_a_query_cell_pointer_stamped_trusted(facility_rows):
    row = next(r for r in facility_rows if r.get("interest_basis") is not None)
    ptr = row.pointer_for("interest_basis")
    assert ptr is not None
    assert ptr.kind == "query_cell"
    assert ptr.trust is Trust.TRUSTED
    assert ptr.source_id == "core_banking"


def test_the_untrusted_field_is_disclosed_and_stamped_untrusted_on_the_same_row(facility_rows):
    """The concrete fix for STATUS.md §7: per-field trust, not per-dataset. `notes` is untrusted
    and `interest_basis` on the SAME row is trusted — the dataset-level `_cell()` path cannot say
    that; the Core's own pointer can."""
    with_notes = [r for r in facility_rows if r.get("notes")]
    assert len(with_notes) >= 10, "the fixture is known to carry free text on ~65 facility rows"
    row = with_notes[0]
    notes_ptr = row.pointer_for("notes")
    basis_ptr = row.pointer_for("interest_basis")
    assert notes_ptr.trust is Trust.UNTRUSTED
    assert basis_ptr.trust is Trust.TRUSTED
    assert notes_ptr.kind == "query_cell"


def test_build_pack_graph_with_no_adapter_rows_never_touches_the_new_dataset():
    """The opt-in guarantee: an ordinary call (what `build_finance_graph()` makes) does not even
    see `cb_facility_adapter` exists."""
    nodes, edges, report = build_pack_graph("finance", max_rows_per_dataset=5,
                                            only=["cb_facility"])
    assert report.rows_read.get("cb_facility_adapter") is None
    assert len(nodes) > 0


def test_build_pack_graph_refuses_an_explicit_request_with_no_producer():
    with pytest.raises(SeedMapError, match="cb_facility_adapter"):
        build_pack_graph("finance", only=["cb_facility_adapter"])


def test_facility_via_adapter_builds_a_real_graph_with_the_cores_own_pointers(tmp_path):
    nodes, edges, report = build_pack_graph(
        "finance", only=["cb_facility_adapter"],
        adapter_rows={"cb_facility_adapter":
                      finance_seed.facility_adapter_rows(estate_dir=tmp_path, limit=500)})
    assert len(nodes) > 100
    facility_nodes = [n for n in nodes if n.kind.value == "facility"]
    assert facility_nodes
    node = facility_nodes[0]
    basis_ptr = node.attr_sources.get("interest_basis")
    assert basis_ptr is not None
    assert basis_ptr.kind == "query_cell"
    assert basis_ptr.trust is Trust.TRUSTED

    # At least one node carries the untrusted, disclosed `notes` value with its own pointer,
    # sitting beside `interest_basis`'s trusted one on the SAME node.
    with_notes = [n for n in facility_nodes if n.attrs.get("notes")]
    assert with_notes
    notes_node = with_notes[0]
    notes_ptr = notes_node.attr_sources["notes"]
    assert notes_ptr.trust is Trust.UNTRUSTED
    assert notes_ptr.kind == "query_cell"
    # The SAME node's other fields are still trusted — per-field, not per-node.
    assert notes_node.attr_sources["interest_basis"].trust is Trust.TRUSTED
    # `local_key` traceability (flagged for the next workstream, cross-source entity links): the
    # node's identity and its `canonical_id` are derived from the row exactly as any other
    # reader's would be — nothing about routing through a Core changes how a node id is computed.
    assert notes_node.canonical_id
    assert notes_node.id == f"facility:{notes_node.canonical_id.lower()}"
