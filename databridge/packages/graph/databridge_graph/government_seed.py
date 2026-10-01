"""Build the `government` pack's graph, deterministically, from its declared library.

The counterpart to `clinic_seed.py`, and deliberately a fraction of its size. `clinic_seed` is a
Python program that knows what a fixture means; this is a four-line entry point over
`estate_seed.py`, which executes the binding declared in `packs/government/seed_map.yaml`.

That difference is the deliverable, not an implementation detail. The pack's data map lives beside
the pack's ontology, both of them YAML, both of them reviewable by somebody who has never opened
this repository's Python — so adding a source, or changing what a column becomes, is an edit to a
pack. This module names nothing about the estate at all, which is why it needs no entry in
`test_no_domain_branching`'s quarantine list: there is no domain concept in it to quarantine.

What the build guarantees, all of it enforced in `estate_seed`:

* only kinds the pack's ontology declares — a map naming anything else fails at build time,
  because the point of this graph is that it is built from the PREDEFINED library, and something
  the library lacks goes through the growth protocol instead;
* every node and edge grounded by a pointer built from the coordinates the reader actually used;
* no identity determination, ever — identity claims are `about`-shaped, and the builder refuses a
  kind bound to an attested meta-kind before it constructs one;
* no merge by similarity: two rows that look alike stay two nodes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .adapter_ingest import AdapterRow, rows_from_core
from .estate_seed import (
    BuildReport,
    build_pack_graph,
    load_pack_graph,
    load_seed_map,
    orphan_node_ids,
)
from .packs import packs_root
from .schema import GraphEdge, GraphNode
from .store import GraphStore

PACK = "government"

# ======================================================================================
# `legacy_records`, read through the adapter fleet — the proof-of-mechanism half of "the graph
# gets built BY THE ADAPTER FLEET" (STATUS.md §7). See `seed_map.yaml`'s `legacy_records_adapter`
# dataset (reader: adapter, no file behind it) and `adapter_ingest.py`'s module docstring for what
# routing a read through `DocumentCore` actually changes: the pointer this graph cites for a
# legacy-record attribute becomes the SAME `doc_span`/`derived` pointer object `DocumentCore`
# minted and stamped with the CONTRACT's real per-field trust — not a graph-side `table_cell`
# reconstruction of one — so a later `/evidence` impact query or a `verify()` call resolves against
# the fleet's own pointer population, not a lookalike.
#
# Named here, not in `estate_seed.py`: this module is where the pack's own estate facts already
# live (`PACK`, the four-line entry points below), the same split `finance_seed.py`'s
# `WATCHLIST_DATASET` constant already draws for a different dataset id.
# ======================================================================================

LEGACY_RECORDS_ADAPTER_DATASET = "legacy_records_adapter"

#: databridge_graph/ -> graph -> packages -> databridge/
_DATABRIDGE_ROOT = Path(__file__).resolve().parents[3]
_GOV_FIXTURES = _DATABRIDGE_ROOT / "fixtures" / "packs" / "government"
GOV_SOURCES_YAML = _GOV_FIXTURES / "sources.yaml"
GOV_ROLES_YAML = _GOV_FIXTURES / "roles.yaml"
GOV_PURPOSE_BINDINGS_YAML = _GOV_FIXTURES / "purpose_bindings.yaml"

#: Every field the `legacy_records_adapter` dataset's node spec reads (its `sources:` and `attrs:`
#: columns, deduplicated) — kept as one tuple so the seed map and this producer cannot silently
#: drift apart: a field added to one and not the other fails LOUDLY (a `KeyError` reading a column
#: `estate_seed._get` never receives), rather than quietly reading as an absence.
LEGACY_RECORDS_FIELDS: tuple[str, ...] = (
    "lm_legacy_key", "lm_rec_type", "lm_surname", "lm_forename", "lm_dob_cymd", "lm_status_cd",
    "lm_amount_val", "lm_amount_sign", "lm_src_office", "lm_opened_cymd",
)


def legacy_records_via_adapter(*, capture_root: Path, packs_dir: Optional[Path] = None,
                               limit: int = 10_000) -> list[AdapterRow]:
    """Read `legacy_records` through `DocumentCore`, behind a REAL `PolicyEngine.admit()`.

    The role and purpose are read out of `seed_map.yaml`'s `legacy_records_adapter.
    reader_options` — see this module's header on why they are declared there rather than as
    literals here (the same pattern `finance_seed.facility_via_adapter` uses).

    Returns a list (not a generator) because every step — building the `Principal`, admitting the
    request, reading the rows — has to run inside the `use_purposes` context this function opens
    and closes; a lazily-consumed generator could have that context gone by the time a caller
    actually iterates it (the `with` block would already have exited).

    **The declared purpose is reachable; the pack's own build-time-crawl one is not, and this
    function does not fall back to it.** `roles.yaml`'s dedicated ingest role writes every grant
    conditional on a credential attribute (`:principal.bound_source_id == 'legacy_records'`), and
    `rolemodel.py`'s own loader excludes every conditional grant from `Role.allow`
    unconditionally: "this build cannot evaluate a credential binding... the grant is kept on
    `Grant.condition` and left OUT of `allow`". Confirmed empirically
    (`load_role_model(GOV_ROLES_YAML).roles["adapter_service"].allow == {}`) and pinned by
    `test_adapter_service_ingest_purpose_is_currently_unreachable` in
    `packages/graph/tests/test_adapter_ingest.py` — a real engine gap, not a graph-side workaround.
    The role the map DOES declare grants the estate's audit purpose UNCONDITIONALLY over
    `legacy_records.LEGACY-MASTER` (`row_filters: {}`, listed in `unscoped_objects`), so it is what
    proves the pipe — Core, Policy, `GroundedRows`, `AdapterRow`, graph pointer — actually works
    end to end, using a purpose the estate already grants rather than one invented for the
    occasion.
    """
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
    from databridge_adapter.capture import CaptureStore
    from databridge_adapter.sources.document import DocumentCore

    dataset = next(d for d in load_seed_map(PACK, packs_dir).datasets
                  if d.id == LEGACY_RECORDS_ADAPTER_DATASET)
    options = dataset.reader_options
    purpose = str(options["adapter_purpose"])
    role = str(options["adapter_role"])

    role_model = load_role_model(GOV_ROLES_YAML)
    purpose_binding = load_purpose_bindings(
        role_model.estate, path=GOV_PURPOSE_BINDINGS_YAML,
        known_estate_purposes=role_model.estate_purposes)
    directory = {"graph-ingest@walnut": PrincipalBinding(roles=(role,))}
    policy = PolicyEngine(role_model=role_model, purpose_binding=purpose_binding,
                          directory=directory, default_budget=Budget(max_rows=limit))
    capture_store = CaptureStore(root=Path(capture_root))
    core = DocumentCore(source_id="legacy_records", root=_GOV_FIXTURES, policy=policy,
                        capture_store=capture_store, sources_yaml=GOV_SOURCES_YAML)

    vocab = purpose_vocabulary(("government",), packs_root())
    with use_purposes(vocab):
        principal = Principal(actor="graph-ingest@walnut", on_behalf_of=None, purpose=purpose)
        req = CapabilityRequest(
            source_id="legacy_records", capability="document_read", principal=principal,
            projection=Projection(entity="legacy_master", fields=LEGACY_RECORDS_FIELDS, key=()),
            order_by=("lm_legacy_key",), limit=limit)
        return list(rows_from_core(core, lambda: req))


def legacy_records_adapter_rows(*, capture_root: Path,
                                limit: int = 10_000) -> Callable[[], Iterator[AdapterRow]]:
    """A zero-argument producer for `build_pack_graph`'s `adapter_rows=` — see `estate_seed.py`."""
    def produce() -> Iterator[AdapterRow]:
        return iter(legacy_records_via_adapter(capture_root=capture_root, limit=limit))
    return produce


def build_government_graph(*, max_rows_per_dataset: Optional[int] = None,
                           packs_dir: Optional[Path] = None,
                           only: Optional[Iterable[str]] = None
                           ) -> tuple[list[GraphNode], list[GraphEdge], BuildReport]:
    """Nodes, edges and the build report, without touching a store.

    `max_rows_per_dataset` truncates every dataset to its first N rows in file order. It is
    deterministic — the same bound gives the same graph anywhere — and it exists so a test can
    exercise the whole path in seconds against an estate of tens of thousands of rows.
    """
    return build_pack_graph(PACK, max_rows_per_dataset=max_rows_per_dataset,
                            packs_dir=packs_dir, only=only)


def load_government_seed(store: GraphStore, *, max_rows_per_dataset: Optional[int] = None,
                         packs_dir: Optional[Path] = None,
                         only: Optional[Iterable[str]] = None) -> dict:
    """Build and upsert into `store`. Idempotent."""
    return load_pack_graph(store, PACK, max_rows_per_dataset=max_rows_per_dataset,
                           packs_dir=packs_dir, only=only)


def government_seed_map(packs_dir: Optional[Path] = None):
    """The loaded, validated map — for a UI that wants to show what the build will do."""
    return load_seed_map(PACK, packs_dir)


__all__ = [
    "GOV_PURPOSE_BINDINGS_YAML", "GOV_ROLES_YAML", "GOV_SOURCES_YAML", "LEGACY_RECORDS_FIELDS",
    "LEGACY_RECORDS_ADAPTER_DATASET", "PACK", "build_government_graph", "government_seed_map",
    "legacy_records_adapter_rows", "legacy_records_via_adapter", "load_government_seed",
    "orphan_node_ids",
]
