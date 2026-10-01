"""**databridge_federated** — delegated cross-source querying (FED).

"The main agent sees and delegates … each adapter agent gets values and results from its source
and the main agent just understands all of it." This package is that loop:

* `contracts` — the typed `SubTask` / `SubResult` / `CombineSpec` / `DelegationPlan`;
* `catalogue` — what may be named, measured from the adapter-agent registry, facts stores and the
  ontology (never from the plan);
* `planner`   — rules first; otherwise the harness (``task_class="agent"``) proposes a plan that is
  validated against the catalogue — unknown names become questions, never guesses;
* `delegates` — each adapter agent answers its sub-task with the fastest CORRECT method (exact SQL
  with aggregate pushdown / graph + passages / live only on request), read-only, under the
  caller's admission;
* `combine`   — the deterministic engine (links by exact identifier or declared edges, filters,
  periods, group, Decimal aggregates, comparison, top-k); every value carries a ``derived``
  pointer listing exactly its inputs;
* `service`   — parallel dispatch, narration of the computed table only, the answer contract, and
  the §5.7 run record with §5.8 ``produced_by`` on every step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .catalogue import (
    AdapterAgentEntry,
    Catalogue,
    ConnectionEntry,
    FactsTable,
    SteadyFacts,
    build_catalogue,
    registry_specs,
)
from .combine import CombinedTable, CombineError, TableRow, combine, table_from_result
from .contracts import (
    PERIODS,
    Aggregate,
    CombineSpec,
    CompareSpec,
    DelegateCoverage,
    DelegationPlan,
    EntityRef,
    Filter,
    LinkSpec,
    ResultRow,
    SkippedRead,
    SubResult,
    SubTask,
    TimeWindow,
    resolve_period,
)
from .delegates import DelegateError, run_documents, run_live, run_tabular
from .planner import PlanOutcome, plan_question, rules_plan, validate_plan
from .pointers import derived_pointer, expand, rows_digest
from .service import (CatalogueAdmission, FederatedAnswer, FederatedService, name_resolvable,
                      scope_literals)


def build_service(*, store: Any = None, harness: Any = None, facts: Any = None,
                  retriever: Any = None, gateway_factory: Any = None,
                  adapter_agents_root: Optional[Path] = None, vocabulary: Any = None,
                  runs_dir: Optional[Path] = None) -> FederatedService:
    """A service over the live registry, facts stores and vocabulary (each optional; what is
    missing is reported by the catalogue and by coverage)."""
    specs: list = []
    problems: list[str] = []
    try:
        specs, bad = registry_specs(adapter_agents_root)
        problems += [f"{k}: {v}" for k, v in bad.items()]
    except Exception as e:                                   # noqa: BLE001 - reported
        problems.append(f"adapter-agent registry unavailable: {type(e).__name__}: {e}")
    if facts is None:
        from databridge_agent_runtime import default_facts   # noqa: PLC0415
        facts = default_facts()
    if facts is not None and not isinstance(facts, SteadyFacts):
        facts = SteadyFacts(facts)                           # E2E4 Q-01: contention is re-read
    if vocabulary is None:
        try:
            from databridge_graph.packs import active_vocabulary  # noqa: PLC0415
            vocabulary = active_vocabulary()
        except Exception as e:                               # noqa: BLE001 - reported
            problems.append(f"vocabulary unavailable: {e}")
    cat = build_catalogue(specs, facts=facts, vocabulary=vocabulary,
                          documents=store is not None or retriever is not None)
    cat.problems[:0] = problems
    return FederatedService(catalogue=cat, facts=facts, store=store, retriever=retriever,
                            gateway_factory=gateway_factory, harness=harness, runs_dir=runs_dir)


def answer(question: str, *, service: Optional[FederatedService] = None,
           admission: Any = None, principal: str = "system", **build: Any) -> FederatedAnswer:
    """``federated.answer(question)`` — the path /brain or the orchestrator calls."""
    svc = service or build_service(**build)
    return svc.answer(question, admission=(admission if admission is not None
                                          else CatalogueAdmission(principal)))


__all__ = [
    "AdapterAgentEntry", "Catalogue", "ConnectionEntry", "FactsTable", "SteadyFacts",
    "build_catalogue",
    "registry_specs", "CombinedTable", "CombineError", "TableRow", "combine",
    "table_from_result", "PERIODS", "Aggregate", "CombineSpec", "CompareSpec",
    "DelegateCoverage", "DelegationPlan", "EntityRef", "Filter", "LinkSpec", "ResultRow",
    "SkippedRead", "SubResult", "SubTask", "TimeWindow", "resolve_period", "DelegateError",
    "run_documents", "run_live", "run_tabular", "PlanOutcome", "plan_question", "rules_plan",
    "validate_plan", "derived_pointer", "expand", "rows_digest", "CatalogueAdmission",
    "FederatedAnswer", "FederatedService", "build_service", "answer", "name_resolvable",
    "scope_literals",
]
