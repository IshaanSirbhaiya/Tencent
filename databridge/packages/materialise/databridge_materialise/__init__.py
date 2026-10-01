"""`databridge_materialise` — the Materialisation Plan, its compiler, and the data agent.

**The inversion.** A dataset is normally a pile of bytes with metadata attached. Here the *plan* is
the dataset's identity and the bytes are a cache: `plan_hash` names the dataset, and re-executing
the plan against the pinned snapshots must reproduce `content_hash`. That makes "this training set
= this query + this as-of + this policy" a first-class object, which is what EU AI Act Article 10
actually asks for — origin and original purpose (§2(b)), preparation operations (§2(c)), and "data
gaps and shortcomings" (§2(h)), which is `unresolved` promoted to a regulatory artefact.

**The three moving parts.**

1. `plan.MaterialisationPlan` — the declarative artefact. Selection, the dual-axis temporal
   contract, shape, policy, determinism, provenance, output, and the hashes over each.
2. `compiler.compile_plan` — the gate. It refuses exactly what a model gets wrong when asked for
   "training data": features on the wrong axis, labels with no maturity lag, a policy outside the
   hash, a disabled pointer requirement, an untested abstention budget. Asked for a training set a
   model produces a leaky split *with confidence*, and Feast's own default point-in-time join
   admits values backfilled after the event — so the compiler is not ceremony, it is the reason a
   model may be trusted to draft a plan at all.
3. `agent.DataAgent` — request → plan → output. It reaches a model only through
   `packages/harness`, by `task_class`, and works offline on a deterministic planner.

**`shaped_by`.** PHASE_PLAN Phase 6 stages the data agent ahead of the deterministic materialiser,
so every `Receipt` records which pathway produced the bytes. On the agent path `plan_hash` names
the request's intent and not the dataset, and a receipt that said otherwise would let a
non-reproducible dataset be mistaken later for a reproducible one.

**One thing this package refuses to build, and says so.** Spine §9 **O1b**: there is no cell state
for a *transformed* disclosure. A masked value is neither `GroundedValue`, `Withheld` nor `Absent`,
so a Receipt over a dataset containing masked columns cannot be honest. Rather than inventing a
fourth state locally — which would fragment a vocabulary shared across every pack —
`compile_plan` and `executor.screen_transformed_cells` **refuse** such a projection and name O1b.
"""
from __future__ import annotations

from . import derivations as _derivations                        # noqa: F401  registers the rules
from .agent import (
    PLAN_TASK_CLASS,
    AgentCatalogue,
    AuthoredPlan,
    DataAgent,
    EntityCatalogue,
    PlanDraft,
    gold_harness,
    gold_planner,
)
from .compiler import MEMBERSHIP_SHAPES, CompiledPlan, compile_plan
from .croissant import CROISSANT_VERSION, to_croissant, write_croissant
from .dataset import Dataset, cell_digest, missing_reason, pointer_of, write_jsonl, write_parquet
from .derivations import content_digest, rows_digest
from .durations import DurationError, parse_duration
from .errors import (
    AbstentionNotFeasible,
    AgentShapedPlanViolated,
    AirGapUnpinned,
    ChunkResliceFailed,
    DeterminismIncomplete,
    FeatureAxisRefused,
    ImputationRefused,
    LabelAxisRefused,
    MaterialisationError,
    MaterialisationRefused,
    MaturityLagMissing,
    MissingnessLeaks,
    NoSystemTimeAxis,
    PlanDraftInvalid,
    PlanNeedsInput,
    PlanRefused,
    PointerRequirementDisabled,
    PolicyNotInHash,
    ShapeNotImplemented,
    TransformedDisclosureUnrepresentable,
    UngroundedCell,
)
from .executor import (
    EXECUTOR_VERSION,
    EmissionSink,
    Materialisation,
    assert_no_missingness_leak,
    materialise,
    missingness_leak_check,
    screen_pointer_requirement,
    screen_transformed_cells,
)
from .feasibility import Feasibility, assess, kotte_min_abstention
from .plan import (
    SCHEME_VERSION,
    Abstention,
    AuthoredBy,
    Chunking,
    Cohort,
    Column,
    ColumnSource,
    Consumer,
    Determinism,
    Embedding,
    Executor,
    Hashes,
    Label,
    LabelSource,
    LabelWindow,
    LeakCheck,
    MaterialisationPlan,
    MaterialisationPolicy,
    Output,
    Perturbation,
    PiiPolicy,
    PolicyPrincipal,
    Provenance,
    Selection,
    Shape,
    SourceSnapshot,
    Split,
    TemporalContract,
    TrustPolicy,
)
from .reader import EntityRead, EstateReader
from .receipt import (
    CellCounts,
    ExecutorRecord,
    LeakCheckResult,
    PointerManifest,
    Receipt,
    RowCounts,
    ShapedBy,
)
from .shapes import (
    ChunkBuild,
    TabularBuild,
    build_chunk_corpus,
    build_conversational,
    build_graph_tensor,
    build_tabular,
)
from .shapes.tabular import verify_compressed_aggregate
from .temporal import known_at, label_in_window, label_is_mature, to_instant

__all__ = [
    # plan
    "SCHEME_VERSION", "MaterialisationPlan", "AuthoredBy", "Selection", "Cohort",
    "TemporalContract", "SourceSnapshot", "LabelWindow", "Shape", "Column", "ColumnSource",
    "Label", "LabelSource", "Consumer", "Chunking", "Embedding", "MaterialisationPolicy",
    "PolicyPrincipal", "PiiPolicy", "Perturbation", "TrustPolicy", "Abstention", "LeakCheck",
    "Determinism", "Split", "Executor", "Provenance", "Output", "Hashes",
    # compiler
    "compile_plan", "CompiledPlan", "MEMBERSHIP_SHAPES",
    # feasibility
    "assess", "kotte_min_abstention", "Feasibility",
    # reading + shaping
    "EstateReader", "EntityRead", "Dataset", "cell_digest", "missing_reason", "pointer_of",
    "write_jsonl", "write_parquet", "build_tabular", "TabularBuild", "build_chunk_corpus",
    "ChunkBuild", "build_graph_tensor", "build_conversational", "verify_compressed_aggregate",
    # executing
    "materialise", "Materialisation", "EXECUTOR_VERSION", "EmissionSink",
    "screen_transformed_cells",
    "assert_no_missingness_leak",
    "screen_pointer_requirement", "missingness_leak_check",
    # receipt + metadata
    "Receipt", "RowCounts", "CellCounts", "LeakCheckResult", "PointerManifest",
    "ExecutorRecord", "ShapedBy", "to_croissant", "write_croissant", "CROISSANT_VERSION",
    # the agent
    "DataAgent", "AgentCatalogue", "EntityCatalogue", "PlanDraft", "AuthoredPlan",
    "gold_harness", "gold_planner", "PLAN_TASK_CLASS",
    # temporal helpers
    "to_instant", "known_at", "label_in_window", "label_is_mature", "parse_duration",
    "DurationError", "rows_digest", "content_digest",
    # errors
    "MaterialisationError", "PlanRefused", "FeatureAxisRefused", "LabelAxisRefused",
    "MaturityLagMissing", "PolicyNotInHash", "PointerRequirementDisabled",
    "AbstentionNotFeasible", "NoSystemTimeAxis", "ImputationRefused", "DeterminismIncomplete",
    "AirGapUnpinned", "ShapeNotImplemented", "TransformedDisclosureUnrepresentable",
    "MaterialisationRefused", "MissingnessLeaks", "UngroundedCell", "ChunkResliceFailed",
    "AgentShapedPlanViolated", "PlanNeedsInput", "PlanDraftInvalid",
]
