"""Build the `finance` pack's graph, deterministically, from its declared library.

The counterpart to `government_seed.py`, and deliberately the same shape: a thin entry point over
`estate_seed.py`, which executes the binding declared in `packs/finance/seed_map.yaml`. This
module names nothing about the estate at all — the pack's data map lives beside the pack's
ontology, both YAML, both reviewable by somebody who has never opened this repository's Python,
and it is why this module needs no entry in `test_no_domain_branching`'s quarantine list: there is
no domain concept in it to quarantine.

What the build guarantees, all of it enforced in `estate_seed`:

* only kinds the pack's ontology declares — a map naming anything else fails at build time;
* every node and edge grounded by a pointer built from the coordinates the reader actually used;
* no identity determination, ever — identity claims are `about`-shaped or weaker (`references`,
  never `resolved_to`/`same_as`), and the builder refuses a kind bound to an attested meta-kind
  before it constructs one;
* no merge by similarity: two rows that look alike stay two nodes.

`seed_map.yaml` binds all nine of the finance estate's sources at the dataset-catalogue level, but
`WATCHLIST_DATASET` is read only when a caller names it: `only=non_watchlist_dataset_ids()` never
opens it, exactly as when it was absent from the map entirely. `join_watchlist_dataset`, below, is
the incremental JOIN of that ninth source into an already-built graph, once it is loaded — a
one-line call over `estate_seed.resolve_incoming`, which does the actual matching, driven entirely
by the `resolve:` block the map declares for that dataset. See that function's docstring for the
mechanism and the two-signal rule it always applies.
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
    resolve_incoming,
)
from .packs import Vocabulary, packs_root
from .schema import GraphEdge, GraphNode
from .store import GraphStore

PACK = "finance"

# ======================================================================================
# `facility`, read through the adapter fleet — the other half of the proof-of-mechanism (see
# `government_seed.py`'s equivalent for `legacy_records`, and `adapter_ingest.py`'s module
# docstring). Named here for the same reason `WATCHLIST_DATASET` is: this module is where the
# pack's own estate facts belong, not the generic engine in `estate_seed.py`.
#
# This is also where the SECOND half of STATUS.md §7's gap shows up concretely, not just the
# pointer-population one. `cb_facility`'s own dataset (`seed_map.yaml`) does not read `notes` at
# all — `sources.yaml` declares it UNTRUSTED and the direct-file reader grounds a whole DATASET at
# one `trust:` value, so the map's only honest choice for one untrusted column on an otherwise-
# trusted table was to drop it, not mislabel it. Reading `facility` through `PostgresCore` instead
# lifts that ceiling: the Core stamps trust PER FIELD from `sources.yaml`'s own declaration, so
# `notes` can be carried, cited, AND correctly marked `untrusted`, sitting right beside
# `interest_basis`'s `trusted` pointer on the same node.
#
# **Which role and purpose actually admit this read is declared in `seed_map.yaml`'s
# `cb_facility_adapter.reader_options`, not hardcoded here** — a role name and an estate purpose id
# are domain vocabulary the same way an issuer code is (`test_no_domain_branching`'s rule, applied
# to the access model rather than the ontology), so they live in the pack's own data alongside the
# node/edge bindings, and this module only ever reads them out of the map. What is worth recording
# HERE — because it is a finding about the estate, not something the map alone shows — is what was
# RULED OUT first, checked directly against the loaded `RoleModel` rather than assumed:
#
# * The first role tried does NOT demonstrate the untrusted-but-disclosed half of the story:
#   `core_banking.party.notes` is in that role's `deny_columns`, so the read that DOES admit shows
#   every other column `trusted` and `notes` correctly WITHHELD — real, but not what this module
#   needs to show, and why the dataset reads `facility` rather than `party`.
# * The role with the cleanest shape for a build-time-style read — non-subject-scoped, and its
#   purpose-scoped grant for the audit purpose is a genuine wildcard reaching `facility.notes` with
#   nothing denied or masked — is refused anyway: its `egress_ceiling` is `INTERNAL` and
#   `core_banking` is declared `RESTRICTED_INTERNAL`, so `EgressCeilingExceeded` fires before a row
#   is read, for every purpose and every object on this source. A second real, standing refusal on
#   this estate, distinct from `finance.graph_build`'s.
# * The role the map actually declares is what clears the egress ceiling AND leaves `facility.
#   notes` in neither `deny_columns` nor `column_masks` — verified by a live `core.resolve()` call,
#   not inferred from the role file: it returns real rows with `notes` as a `GroundedValue`
#   stamped `Trust.UNTRUSTED` for facilities that carry free text (65 of them in the fixture,
#   checked directly against the SQLite estate).
#
# **`finance.graph_build` is not what admits this read**, and neither is any non-subject-scoped
# purpose — this estate has none that both clears the egress ceiling and discloses the untrusted
# column. The purpose the map declares IS subject-scoped (`packs/finance/purposes.yaml`), so this
# reads on behalf of a named principal with a real grant, the same shape
# `packages/adapter/tests/conftest.py`'s equivalent fixture already uses. Not a build-time crawl in
# name, but a genuine, already-admitted read that proves the MECHANISM (Core -> Policy ->
# GroundedRows -> AdapterRow -> graph pointer, trust and all) end to end.
# ======================================================================================

FACILITY_ADAPTER_DATASET = "cb_facility_adapter"

#: Every column `cb_facility_adapter`'s node spec reads — `cb_facility`'s own list
#: (`seed_map.yaml`) PLUS `notes`, the untrusted column the direct-file dataset cannot carry.
FACILITY_FIELDS: tuple[str, ...] = (
    "facility_id", "facility_type", "limit_amount", "drawn_amount", "currency", "start_date",
    "maturity_date", "interest_basis", "margin_bps", "status", "booking_entity", "review_date",
    "updated_at", "notes",
)


def facility_via_adapter(*, estate_dir: Path, packs_dir: Optional[Path] = None,
                         limit: int = 10_000) -> list[AdapterRow]:
    """Read `core_banking.facility` through `PostgresCore`, behind a REAL `PolicyEngine.admit()`.

    The role, purpose, `on_behalf_of` and grant id are read out of `seed_map.yaml`'s
    `cb_facility_adapter.reader_options` — see this module's header on why they are declared
    there rather than as literals here. `estate_dir` is where `databridge_adapter.testing.estate.
    load_finance_estate` builds its SQLite database (constraint: reuse that loader — do not write
    a second one). Returns a list, not a generator, for the same reason `government_seed.
    legacy_records_via_adapter` does: every step has to run inside the `use_purposes` context this
    function opens and closes.
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
    from databridge_adapter.sources.postgres import PostgresCore
    from databridge_adapter.testing.estate import ROLES_YAML, SOURCES_YAML, load_finance_estate

    dataset = next(d for d in load_seed_map(PACK, packs_dir).datasets
                   if d.id == FACILITY_ADAPTER_DATASET)
    options = dataset.reader_options
    purpose = str(options["adapter_purpose"])
    role = str(options["adapter_role"])
    on_behalf_of = str(options["adapter_on_behalf_of"])
    grant_id = str(options["adapter_grant_id"])

    engine = load_finance_estate(Path(estate_dir))
    try:
        role_model = load_role_model(ROLES_YAML)
        purpose_binding = load_purpose_bindings(role_model.estate,
                                                known_estate_purposes=role_model.estate_purposes)
        directory = {on_behalf_of: PrincipalBinding(roles=(role,))}
        policy = PolicyEngine(role_model=role_model, purpose_binding=purpose_binding,
                              directory=directory, default_budget=Budget(max_rows=limit))
        core = PostgresCore(source_id="core_banking", engine=engine, policy=policy,
                            sources_yaml=SOURCES_YAML)

        vocab = purpose_vocabulary(("finance",), packs_root())
        with use_purposes(vocab):
            principal = Principal(actor="orchestrator@walnut", on_behalf_of=on_behalf_of,
                                  purpose=purpose, grant_id=grant_id)
            req = CapabilityRequest(
                source_id="core_banking", capability="row_read", principal=principal,
                projection=Projection(entity="facility", fields=FACILITY_FIELDS,
                                      key=("facility_id",)),
                order_by=("facility_id",), limit=limit)
            return list(rows_from_core(core, lambda: req))
    finally:
        engine.dispose()


def facility_adapter_rows(*, estate_dir: Path,
                          limit: int = 10_000) -> Callable[[], Iterator[AdapterRow]]:
    """A zero-argument producer for `build_pack_graph`'s `adapter_rows=` — see `estate_seed.py`."""
    def produce() -> Iterator[AdapterRow]:
        return iter(facility_via_adapter(estate_dir=estate_dir, limit=limit))
    return produce

#: The dataset id that reads the ninth source's transport. Named once so the "eight sources, then
#: the ninth" incremental-join test can build the first eight with `only=non_watchlist_dataset_
#: ids()` and the ninth with `only=[WATCHLIST_DATASET]`, without hard-coding the id twice.
WATCHLIST_DATASET = "wl_watchlist"


def build_finance_graph(*, max_rows_per_dataset: Optional[int] = None,
                        packs_dir: Optional[Path] = None,
                        only: Optional[Iterable[str]] = None
                        ) -> tuple[list[GraphNode], list[GraphEdge], BuildReport]:
    """Nodes, edges and the build report, without touching a store.

    `max_rows_per_dataset` truncates every dataset to its first N rows in file order. It is
    deterministic — the same bound gives the same graph anywhere — and it exists so a test can
    exercise the whole path in seconds against an estate whose largest tables run to thousands of
    rows, without waiting on the full, unbounded build.
    """
    return build_pack_graph(PACK, max_rows_per_dataset=max_rows_per_dataset,
                            packs_dir=packs_dir, only=only)


def load_finance_seed(store: GraphStore, *, max_rows_per_dataset: Optional[int] = None,
                      packs_dir: Optional[Path] = None,
                      only: Optional[Iterable[str]] = None) -> dict:
    """Build and upsert into `store`. Idempotent."""
    return load_pack_graph(store, PACK, max_rows_per_dataset=max_rows_per_dataset,
                           packs_dir=packs_dir, only=only)


def finance_seed_map(packs_dir: Optional[Path] = None):
    """The loaded, validated map — for a UI that wants to show what the build will do."""
    return load_seed_map(PACK, packs_dir)


def non_watchlist_dataset_ids(packs_dir: Optional[Path] = None) -> tuple[str, ...]:
    """Every dataset id `seed_map.yaml` declares, less `WATCHLIST_DATASET` and every
    `reader: adapter` dataset (`cb_facility_adapter`).

    COMPUTED from the map, never hand-maintained as a parallel list: a dataset added or removed
    above is reflected here with no second edit, which is what keeps "the eight sources" an
    accurate description of the build rather than a comment somebody forgot to update.

    The `reader: adapter` exclusion is the same rule for the same reason: an adapter-routed
    dataset is opt-in by construction (`estate_seed.build_pack_graph`'s `adapter_rows=`) and has
    no file to fall back to, so naming it in an `only=` list without also supplying a producer is
    refused loudly — which `non_watchlist_dataset_ids()`'s whole reason to exist (an ordinary,
    no-producer build of "the eight sources") must never trigger.
    """
    seed = load_seed_map(PACK, packs_dir)
    return tuple(d.id for d in seed.datasets
                if d.id != WATCHLIST_DATASET and d.reader != "adapter")


def join_watchlist_dataset(store: GraphStore, *, vocab: Optional[Vocabulary] = None,
                           packs_dir: Optional[Path] = None) -> dict:
    """Resolve `WATCHLIST_DATASET`'s nodes (already loaded into `store`) against the store, per
    the `resolve:` block `seed_map.yaml` declares for that dataset. Delegates entirely to
    `estate_seed.resolve_incoming` — this function names nothing about what the dataset holds or
    what it is matched against; that is the map's declaration to make, not this module's."""
    return resolve_incoming(store, PACK, dataset_id=WATCHLIST_DATASET, vocab=vocab,
                            packs_dir=packs_dir)


__all__ = [
    "FACILITY_ADAPTER_DATASET", "FACILITY_FIELDS", "PACK", "WATCHLIST_DATASET",
    "build_finance_graph", "facility_adapter_rows", "facility_via_adapter", "finance_seed_map",
    "join_watchlist_dataset", "load_finance_seed", "non_watchlist_dataset_ids", "orphan_node_ids",
]
