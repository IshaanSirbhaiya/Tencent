"""`databridge_adapter` — the adapter contract and port (PHASE_PLAN 3A).

**This package contains no I/O.** No driver, no credentials, no network call, no model call, no
filesystem read except the pack YAML that declares the purpose vocabulary. Everything here is a
value object, a Protocol, or a pure function over value objects. A real Postgres Core is
workstream 3B; if an `import psycopg` ever appears under this package, the layering that makes an
adapter's output mechanically verifiable has been broken.

The shape, in one paragraph. An orchestrator sends exactly one kind of message - a
`CapabilityRequest`, frozen and hashable, carrying a `Principal` whose `purpose` comes from a
pack's declared vocabulary. Its `read_hash` is principal-independent and BECOMES
`SnapshotIdentity.query_hash`, which is what makes a citation verifiable by re-execution rather
than by trust; its `cache_key` is principal-shaped, because the withheld set is. An adapter
implements five verbs - `describe`, `snapshot`, `plan`, `resolve`, `verify` - and returns
`GroundedRows` in which every cell is a `GroundedValue`, a `Withheld` or an `Absent`, never a bare
value. `verify` returns an eight-state `Verdict`, never a boolean.

Read `ADAPTER_AGENT_SPEC.md` §2, §3, §4, §4a and §5 alongside this package; the docstrings here
carry the reasoning, not a summary of it.
"""
from __future__ import annotations

from .canonical import (
    SCHEME_VERSION,
    Scalar,
    canonical_ast,
    canonical_json,
    canonical_params,
    canonical_pointer,
    canonical_scalar,
    digest_mapping,
    iso_utc,
    pointer_digest,
    pointer_set_digest,
    sha256_of,
)
from .contract import CapabilityRequest, Entitlement, Principal, Projection, bound_params
from .describe import (
    CursorDescription,
    DeleteDetectability,
    EgressClass,
    EntityDescription,
    FieldDescription,
    PinningTier,
    SourceDescription,
    source_description_from_yaml,
)
from .errors import (
    AdapterError,
    BudgetExceeded,
    CannotPinPast,
    CapabilityNotAdvertised,
    EgressCeilingExceeded,
    EscalationRequired,
    PolicyNotApplied,
    PredicateGrammarNotDefined,
    PurposeNotBound,
    PurposeNotDeclared,
    PurposeNotPermitted,
    RowRuleNotCompilable,
    UngroundedValue,
    UnorderedPointeredRead,
    UntrustedInControlFlow,
)
from .grounding import (
    DERIVED_KINDS,
    EXACT_KINDS,
    GENERATED_KINDS,
    SPAN_KINDS,
    VERBATIM_KINDS,
    DerivationCall,
    DerivationError,
    assert_cell_grounded_in_row,
    assert_cells_grounded,
    assert_derived_from,
    assert_grounded,
    assert_provenance_present,
    exact_match,
    parse_derivation,
    register_derivation,
    registered_derivations,
    replay_derivation,
    verbatim_match,
)
from . import derivations as _derivations           # noqa: F401  registers the built-in rules
from .derivations import PII_NAME_HINTS, PII_PATTERNS, redact_pii
from .policy import (
    Admission,
    CompiledDecisionTable,
    ColumnRule,
    DecisionTable,
    NotYetSpecifiedDecisionTable,
    PolicyDecision,
    PolicyEngine,
    PrincipalBinding,
)
from .port import VERBS, Adapter, NotAnAdapter, assert_conforms
from .rolemodel import (
    EGRESS_RANK,
    GRAMMAR_GRANT_LIST,
    GRAMMAR_PERMISSION_LISTS,
    Grant,
    Mask,
    PurposeBinding,
    Role,
    RoleModel,
    RoleModelError,
    RowRule,
    Subquery,
    applicable_roles,
    compile_row_filter,
    load_purpose_bindings,
    load_role_model,
    resolve_scope_aliases,
)
from .purposes import (
    PURPOSES_FILE,
    PurposeDecl,
    PurposeError,
    PurposeVocabulary,
    active_purposes,
    load_pack_purposes,
    purpose_vocabulary,
    use_purposes,
)
from .results import (
    AdapterResult,
    Budget,
    Cell,
    Cost,
    GroundedRows,
    PointerResolution,
    ReadPlan,
    cell_value,
)
from .taint import (
    ADAPTER_SINKS,
    TRUSTED_SINKS,
    RegistryTaintMap,
    TaintMap,
    screen_sinks,
)
from .verdicts import (
    ADVERTISED_ONLY_VERDICTS,
    UNIVERSAL_VERDICTS,
    Verdict,
    assert_verdict_advertised,
    combine_verdicts,
)

__all__ = [
    # contract
    "CapabilityRequest", "Entitlement", "Principal", "Projection", "bound_params",
    # canonicalisation
    "SCHEME_VERSION", "Scalar", "canonical_ast", "canonical_json", "canonical_params",
    "canonical_pointer", "canonical_scalar", "digest_mapping", "iso_utc", "pointer_digest",
    "pointer_set_digest", "sha256_of",
    # port
    "Adapter", "VERBS", "assert_conforms", "NotAnAdapter",
    # describe
    "SourceDescription", "EntityDescription", "FieldDescription", "CursorDescription",
    "PinningTier", "DeleteDetectability", "EgressClass", "source_description_from_yaml",
    # results
    "AdapterResult", "GroundedRows", "ReadPlan", "Cost", "Budget", "PointerResolution",
    "Cell", "cell_value",
    # verdicts
    "Verdict", "UNIVERSAL_VERDICTS", "ADVERTISED_ONLY_VERDICTS", "assert_verdict_advertised",
    "combine_verdicts",
    # grounding
    "assert_grounded", "assert_cell_grounded_in_row", "assert_cells_grounded",
    "assert_derived_from", "assert_provenance_present", "exact_match", "verbatim_match",
    "VERBATIM_KINDS", "EXACT_KINDS", "SPAN_KINDS", "DERIVED_KINDS", "GENERATED_KINDS",
    # replayable derivations (§4a)
    "DerivationCall", "DerivationError", "parse_derivation", "register_derivation",
    "registered_derivations", "replay_derivation", "redact_pii", "PII_PATTERNS",
    "PII_NAME_HINTS",
    # purpose vocabulary
    "PurposeVocabulary", "PurposeDecl", "PurposeError", "PURPOSES_FILE", "active_purposes",
    "purpose_vocabulary", "load_pack_purposes", "use_purposes",
    # taint (see taint.py on what is honestly built)
    "TaintMap", "RegistryTaintMap", "screen_sinks", "TRUSTED_SINKS", "ADAPTER_SINKS",
    # policy (workstream 3C) — the stub is kept beside the real engine, see policy.py
    "PolicyDecision", "DecisionTable", "NotYetSpecifiedDecisionTable", "PolicyEngine",
    "CompiledDecisionTable", "ColumnRule", "Admission", "PrincipalBinding",
    # the estate's role model (workstream 3C)
    "RoleModel", "Role", "Grant", "Mask", "RowRule", "Subquery", "PurposeBinding",
    "RoleModelError", "EGRESS_RANK", "applicable_roles", "compile_row_filter", "load_role_model",
    "load_purpose_bindings", "resolve_scope_aliases",
    "GRAMMAR_PERMISSION_LISTS", "GRAMMAR_GRANT_LIST",
    # errors
    "AdapterError", "BudgetExceeded", "CannotPinPast", "CapabilityNotAdvertised",
    "EgressCeilingExceeded", "EscalationRequired", "PolicyNotApplied",
    "PredicateGrammarNotDefined", "PurposeNotBound", "PurposeNotDeclared", "PurposeNotPermitted",
    "RowRuleNotCompilable", "UngroundedValue", "UnorderedPointeredRead",
    "UntrustedInControlFlow",
]
