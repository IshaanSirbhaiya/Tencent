"""The **data agent**: a natural-language request in, a dataset and a receipt out.

AGENT_PLANE D14 sets the role split, and it is the whole design:

```
main agent ──delegates──▶ DATA AGENT
                            proposes a MaterialisationPlan            (LLM: propose)
                                     ↓
                          deterministic plan compiler                 (accept or refuse)
                                     ↓
                          materialiser executes                       (no model)
                                     ↓
                          dataset + Receipt + plan_hash
```

Authoring a plan is genuinely semantic — *"training data for default prediction"* has to become an
entity, a feature set, a dual-axis temporal contract, a policy and a shape — so a model does it.
Executing one is not, so no model does that.

**The founder's staging decision (PHASE_PLAN Phase 6) and how it is honoured here.** For now the
data agent may produce the formats directly. `DataAgent.materialise(...)` therefore accepts the
agent's own *shaping directives* — which admissible columns to emit, in what order, and where the
split boundaries fall — and applies them **after** the deterministic build. Those directives are
NOT inside `plan_hash`, which is precisely why the receipt records `shaped_by: agent` and reports
the dataset as not reproducible: re-running the same request may produce a different plan and
different directives, so the plan hash names the request's intent rather than these bytes.

The invariants do not become optional on that path. They move from *compiled* to *checked after
the fact*: `_check_agent_shaping` re-verifies that the agent only ever dropped columns the
compiler had already accepted, never added one, never touched the label, and never produced a cell
without a pointer. A dataset failing any of those is refused rather than shipped with a warning.

**Model access is through the harness and a `task_class`, never a model name.** The class used is
`agentic_orchestration`, which is what plan authoring is. It deserves a row of its own
(`materialisation_planning`) in `databridge/config/models.yaml`; adding one is a config change
outside this package's scope and would not touch a line of code here, which is the point of
routing by task class in the first place.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from databridge_harness import Harness

from .compiler import CompiledPlan, compile_plan
from .dataset import Dataset
from .errors import (
    AgentShapedPlanViolated,
    PlanDraftInvalid,
    PlanNeedsInput,
    PlanRefused,
    ShapeNotImplemented,
)
from .durations import is_duration
from .executor import EXECUTOR_VERSION, Materialisation, materialise, screen_pointer_requirement
from .questions import answers_block, split_answers
from .plan import (
    AuthoredBy,
    Cohort,
    Column,
    ColumnSource,
    ColumnType,
    Consumer,
    Determinism,
    Executor,
    Label,
    LabelSource,
    LabelWindow,
    MaterialisationPlan,
    MaterialisationPolicy,
    Output,
    PolicyPrincipal,
    Provenance,
    Relation,
    Selection,
    Shape,
    ShapeKind,
    SourceSnapshot,
    Split,
    TemporalContract,
)
from .reader import EstateReader

#: The task class the plan-authoring call routes through. Never a model name.
PLAN_TASK_CLASS = "agentic_orchestration"


# ---------------------------------------------------------------------------------------
# What the agent is shown, and what it returns
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class EntityCatalogue:
    """One entity as the data agent sees it, INCLUDING which columns are which axis.

    **`system_time_field` has no default and is frequently `None`, on purpose.** It records when
    the organisation LEARNED a fact, and a great many real tables do not have one — they keep
    current state and overwrite. The catalogue says so rather than nominating the nearest date
    column, because that nomination is exactly the substitution that produces a leaky split.

    Where this belongs eventually: the source contract (`fixtures/packs/<estate>/sources.yaml`),
    beside `cursor` and `deletes_detectable`, so an adapter's `describe()` carries it. It is a
    constructor argument today because that file is another workstream's scope.
    """

    name: str
    fields: tuple[str, ...]
    key: tuple[str, ...]
    valid_time_field: Optional[str] = None
    system_time_field: Optional[str] = None
    #: Boolean/outcome fields that could serve as a label.
    label_candidates: tuple[str, ...] = ()
    #: Fields that may serve as prior-value features (numeric or boolean).
    feature_candidates: tuple[str, ...] = ()
    #: A free-text field, for a chunk corpus.
    text_field: Optional[str] = None


@dataclass(frozen=True)
class AgentCatalogue:
    source_id: str
    entities: tuple[EntityCatalogue, ...]
    packs: tuple[str, ...]
    purpose: str
    lawful_basis: str
    principal_actor: str
    principal_on_behalf_of: Optional[str] = None
    principal_grant_id: Optional[str] = None

    def entity(self, name: str) -> EntityCatalogue:
        for e in self.entities:
            if e.name == name:
                return e
        raise KeyError(name)

    def as_prompt(self) -> str:
        body = {
            "source_id": self.source_id,
            "purpose": self.purpose,
            # The shapes this build PRODUCES (E2E run 2, N-07): a planner shown only the
            # vocabulary picked `graph_tensor` for a request that said "the graph", and was
            # refused for a shape nobody asked for.
            "shapes_built": list(built_shapes()),
            "entities": [
                {"name": e.name, "key": list(e.key), "fields": list(e.fields),
                 "valid_time_field": e.valid_time_field,
                 "system_time_field": e.system_time_field,
                 "label_candidates": list(e.label_candidates),
                 "feature_candidates": list(e.feature_candidates),
                 "text_field": e.text_field}
                for e in self.entities
            ],
        }
        return json.dumps(body, indent=2, sort_keys=True)


def built_shapes() -> tuple[str, ...]:
    """The shapes this build PRODUCES, derived from the executor's own builder table (a builder in
    the ``unimplemented`` module refuses by design). Nothing listed by hand."""
    from .executor import _BUILDERS

    return tuple(k for k in get_args(ShapeKind)
                 if k in _BUILDERS and not _BUILDERS[k].__module__.endswith(".unimplemented"))


def _literal_values(model: type[BaseModel], name: str) -> tuple[str, ...]:
    """The accepted values of a `Literal` field, read off the plan model itself — so the schema the
    model is SHOWN cannot drift from the schema the plan VALIDATES against (E2E run 2, N-07)."""
    annotation = model.model_fields[name].annotation
    out: list[str] = []
    for arg in get_args(annotation) or ():
        if isinstance(arg, str):
            out.append(arg)
        else:                                                    # Optional[Literal[...]]
            out.extend(a for a in get_args(arg) if isinstance(a, str))
    return tuple(out)


#: What each relation MEANS, shown to the model beside the closed set. Keyed by every member of
#: `plan.Relation` — `test_the_prompt_schema_is_the_validated_schema` fails if the two drift.
RELATION_MEANINGS: dict[str, str] = {
    "cohort_key": "the cohort row's own identity (its key field). Not a feature.",
    "decision_time": "the cohort row's decision instant T_d. Not a feature.",
    "cohort_row": ("a field of the cohort row ITSELF (\"its own\" value). Admitted only where the "
                   "row was recorded (system time) by T_d. Use this for a field of the same row."),
    "as_of_prior": ("the most recent EARLIER row sharing `join_key`, recorded by T_d. Needs "
                    "join_key, system_time_field and valid_time_field."),
    "aggregate_count": ("a count of rows sharing `join_key` recorded by T_d. Needs join_key and "
                        "system_time_field; set pointer_mode to derived_compressed."),
}


def plan_schema_prompt() -> str:
    """The draft schema as the compiler will judge it, generated from the plan's own `Literal`s.

    The run-2 failure was a model answering ``relation: "self"`` because the only thing it was
    shown was a JSON schema in which `relation` is a free string. Every closed set below is read
    from `plan.py`; none is restated by hand. (Lower-case "catalogue"/"request" only: the offline
    planner splits the prompt on the upper-case section headers.)
    """
    relations = "\n".join(f"      - {r}: {RELATION_MEANINGS.get(r, '')}"
                          for r in get_args(Relation))
    built = ", ".join(built_shapes())
    unbuilt = ", ".join(k for k in get_args(ShapeKind) if k not in built_shapes())

    def one_of(model: type[BaseModel], name: str) -> str:
        return " | ".join(_literal_values(model, name))

    return f"""\
THE DRAFT SCHEMA (values outside these closed sets are refused):
  shape: one of {built}. ({unbuilt} are named by the vocabulary but NOT built here.)
  entity: an entity name from the catalogue, exactly as written.
  decision_time_field: REQUIRED for tabular — a date/time field of that entity: the instant each
    row's decision is taken (T_d). Prefer the instant the request names; else valid_time_field.
  feature_axis: {one_of(TemporalContract, "feature_axis")} (only system_time compiles).
  feature_as_of: {one_of(TemporalContract, "feature_as_of")} (tabular uses per_row).
  features: list of {{path, entity, field_name, relation, type, join_key, system_time_field,
    valid_time_field, window, pointer_mode}} where
    relation is one of:
{relations}
    type is one of: {" | ".join(get_args(ColumnType))}
    pointer_mode is one of: {one_of(Column, "pointer_mode")}
    system_time_field / valid_time_field: the entity's own, from the catalogue.
  label: REQUIRED for tabular (a tabular dataset is a supervised training set) —
    {{path, entity, field_name, join_key, valid_time_field, system_time_field, type}}: the outcome
    field, recorded AFTER the decision. Both time fields are the label entity's own.
  label_axis: {one_of(TemporalContract, "label_axis")} (a label uses valid_time).
  label_window: an ISO duration in days or weeks, e.g. P30D (no months or years).
  label_read_at_system_time: {one_of(TemporalContract, "label_read_at_system_time")}
    (a label uses build_time).
  label_maturity_lag: an ISO duration in days, e.g. P14D (P0D means "no lag", stated).
  text_field (chunk_corpus only): {{path, entity, field_name, relation: cohort_row, type: text,
    system_time_field}} where field_name is the entity's text_field; max_chars: an integer.
  row_order: column paths; include the key column ("<entity>.<key>").
  null_policy: keep.
  shaping: optional "drop:<column path>" directives.
  question: if the request cannot be expressed under these rules — no outcome field for a
    tabular dataset, no decision instant, a shape that is not built — set `question` to what the
    requester must name (with the catalogue's candidates) instead of guessing.
"""


class FeatureDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    entity: str
    field_name: str
    relation: str = Field(description="one of: " + ", ".join(get_args(Relation)))
    type: str = Field(default="float64",
                      description="one of: " + ", ".join(get_args(ColumnType)))
    join_key: Optional[str] = None
    system_time_field: Optional[str] = None
    valid_time_field: Optional[str] = None
    window: Optional[str] = None
    pointer_mode: str = Field(default="full", description="full or derived_compressed")


class LabelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    entity: str
    field_name: str
    join_key: str
    valid_time_field: str
    system_time_field: str
    type: str = Field(default="bool", description="one of: " + ", ".join(get_args(ColumnType)))


class PlanDraft(BaseModel):
    """What the model returns. Deliberately LOOSER than `MaterialisationPlan`.

    A draft can name `feature_axis: "valid_time"`, omit a maturity lag, or ask for imputation —
    because a model will, and the compiler's refusals are worth nothing if the schema quietly
    prevents the mistake from being expressible. Every field here is the shape of an answer, not a
    guarantee about it.
    """

    model_config = ConfigDict(extra="forbid")

    shape: str = Field(default="tabular", description="one of the built shapes")
    entity: str = ""
    decision_time_field: Optional[str] = None
    feature_axis: str = "system_time"
    feature_as_of: str = "per_row"
    features: list[FeatureDraft] = Field(default_factory=list)
    label: Optional[LabelDraft] = None
    label_axis: str = "none"
    label_window: Optional[str] = None
    label_read_at_system_time: str = "none"
    label_maturity_lag: Optional[str] = None
    text_field: Optional[FeatureDraft] = None
    max_chars: Optional[int] = None
    row_order: list[str] = Field(default_factory=list)
    null_policy: str = "keep"
    rationale: str = ""
    #: The agent's own shaping choices. NOT part of `plan_hash` — see the module docstring.
    shaping: list[str] = Field(default_factory=list)
    #: Set INSTEAD of a plan when the request cannot be expressed under the rules (E2E run 2,
    #: N-07): the planner asks rather than guessing. Raised as `PlanNeedsInput`.
    question: Optional[str] = None


_PROMPT = """\
You are the Walnut data agent. Turn the request below into a MaterialisationPlan draft.

The rules the plan compiler will enforce, so you may as well satisfy them:
  * FEATURES are cut on SYSTEM time — when the organisation RECORDED a fact — at each row's own
    decision instant. Never on valid time: that admits values backfilled after the decision.
  * Every feature must name the `system_time_field` of its entity. If the catalogue shows the
    entity has none, the feature is not admissible and the plan will be refused.
  * LABELS are cut on VALID time strictly after the decision, read at the latest system time, and
    must declare a maturity lag so outcomes not yet recorded are rejected rather than counted as
    negatives.
  * Never impute. `null_policy` is "keep".

{schema}{feedback}
CATALOGUE
{catalogue}

REQUEST
{request}

Return JSON only.
"""


# ---------------------------------------------------------------------------------------
# Draft normalisation — closed, recorded, never widening (E2E run 2, N-07)
# ---------------------------------------------------------------------------------------
#: Spellings a model produces for each relation. CLOSED, and deliberately one-directional: every
#: synonym maps to a FEATURE relation, all of which are cut on system time at T_d. Nothing maps
#: onto `cohort_key` or `decision_time` — those are axis relations exempt from the cut, so a
#: repair landing there would widen what a feature may see.
_RELATION_SYNONYMS: dict[str, str] = {
    **{k: "cohort_row" for k in (
        "self", "row", "same_row", "this_row", "own", "own_row", "cohort", "cohort_field",
        "cohort_attribute", "cohort_column", "attribute", "field", "column", "direct", "current",
        "current_row", "row_field", "same_entity", "local")},
    **{k: "as_of_prior" for k in (
        "prior", "previous", "last", "last_known", "last_value", "lag", "lagged", "as_of",
        "asof", "point_in_time", "pit", "prior_value", "prior_row", "previous_value")},
    **{k: "aggregate_count" for k in (
        "count", "aggregate", "prior_count", "count_prior", "aggregate_prior", "n_prior",
        "history_count", "count_rows", "row_count")},
}
_SAME_ROW_TARGET = "cohort_row"

_SHAPE_SYNONYMS: dict[str, str] = {
    **{k: "tabular" for k in ("table", "tabular_dataset", "rectangle", "dataframe",
                              "training_set", "feature_table", "features", "supervised",
                              "rows", "tabular_data")},
    **{k: "chunk_corpus" for k in ("corpus", "chunks", "chunked", "chunk", "rag", "rag_corpus",
                                   "retrieval", "retrieval_corpus", "text_corpus",
                                   "chunked_corpus")},
}

_TYPE_SYNONYMS: dict[str, str] = {
    **{k: "float64" for k in ("float", "double", "number", "numeric", "decimal", "real",
                              "money", "currency", "float32")},
    **{k: "int64" for k in ("int", "integer", "long", "bigint")},
    **{k: "int32" for k in ("smallint",)},
    **{k: "string" for k in ("str", "varchar", "char", "keyword", "identifier", "id")},
    **{k: "bool" for k in ("boolean",)},
    **{k: "timestamp" for k in ("datetime", "instant", "time", "timestamptz")},
    **{k: "category" for k in ("categorical", "enum", "class", "label")},
}

#: Spelling variants of the temporal-contract literals. Each maps to the value of the SAME
#: meaning — `valid`/`event_time` stay `valid_time`, so the compiler still refuses them by name.
_AXIS_SYNONYMS: dict[str, dict[str, str]] = {
    "feature_axis": {"system": "system_time", "recorded": "system_time",
                     "recorded_time": "system_time", "transaction_time": "system_time",
                     "knowledge_time": "system_time", "valid": "valid_time",
                     "event_time": "valid_time", "effective_time": "valid_time",
                     "source": "source_as_of"},
    "feature_as_of": {"row": "per_row", "per_decision": "per_row", "each_row": "per_row",
                      "decision": "per_row"},
    "label_axis": {"valid": "valid_time", "event_time": "valid_time", "system": "system_time"},
    "label_read_at_system_time": {"build": "build_time", "latest": "build_time",
                                  "now": "build_time", "decision": "decision_time"},
}


def _canon(value: Any) -> str:
    return re.sub(r"[\s\-./]+", "_", str(value).strip().lower())


def normalise_draft(draft: PlanDraft, catalogue: AgentCatalogue
                    ) -> tuple[PlanDraft, tuple[str, ...]]:
    """Map a draft's near-miss spellings onto the plan vocabulary, and say so.

    **Every repair is recorded** (returned, and carried into the receipt's notes) and **none
    widens what a plan may see**: synonyms land only on feature relations (all cut at T_d), axis
    spellings keep their meaning (``valid`` stays ``valid_time`` and is refused by the compiler),
    and a missing time field is filled only with the field the catalogue DECLARES for that entity
    — never a nearby date column. A value no closed table maps raises `PlanDraftInvalid` naming the
    field and the accepted set. `to_plan` stays verbatim; this is a separate, visible step.
    """
    repairs: list[str] = []
    names = {e.name for e in catalogue.entities}
    lowered: dict[str, list[str]] = {}
    for n in names:
        lowered.setdefault(n.lower(), []).append(n)

    def entity_name(value: str, where: str) -> str:
        if value in names or not value:
            return value
        hit = lowered.get(value.strip().lower(), [])
        if len(hit) == 1:
            repairs.append(f"{where}: entity {value!r} -> {hit[0]!r} (case)")
            return hit[0]
        return value

    def closed(field_name: str, value: str, accepted: Sequence[str],
               synonyms: dict[str, str], where: str) -> str:
        if value in accepted:
            return value
        c = _canon(value)
        target = c if c in accepted else synonyms.get(c)
        if target is None or target not in accepted:
            raise PlanDraftInvalid(
                f"{where}: {field_name} {value!r} is not in the plan vocabulary and no recorded "
                f"normalisation maps it. Accepted: {', '.join(accepted)}")
        repairs.append(f"{where}: {field_name} {value!r} -> {target!r}")
        return target

    def catalogue_entity(name: str) -> Optional[EntityCatalogue]:
        try:
            return catalogue.entity(name)
        except KeyError:
            return None

    updates: dict[str, Any] = {}
    entity = entity_name(draft.entity, "draft")
    updates["entity"] = entity
    shapes = tuple(get_args(ShapeKind))
    updates["shape"] = closed("shape", draft.shape, shapes, _SHAPE_SYNONYMS, "draft")
    for name, syn in _AXIS_SYNONYMS.items():
        updates[name] = closed(name, getattr(draft, name),
                               _literal_values(TemporalContract, name), syn, "draft")
    updates["null_policy"] = closed("null_policy", draft.null_policy,
                                    _literal_values(Column, "null_policy"), {}, "draft")

    def duration(name: str, value: Optional[str], where: str) -> Optional[str]:
        # "30 days" -> P30D. Days and weeks only; a calendar unit stays as written and is refused
        # by the plan's own duration rule, with its reason.
        if value is None or is_duration(value):
            return value
        iso = _window_from(value) if re.fullmatch(r"\s*\d+\s*(days?|weeks?)\s*", value,
                                                  re.I) else None
        if iso is None:
            return value
        repairs.append(f"{where}: {name} {value!r} -> {iso!r}")
        return iso

    updates["label_window"] = duration("label_window", draft.label_window, "draft")
    updates["label_maturity_lag"] = duration("label_maturity_lag", draft.label_maturity_lag,
                                             "draft")

    relations = tuple(get_args(Relation))
    types = tuple(get_args(ColumnType))
    modes = _literal_values(Column, "pointer_mode")
    cohort = catalogue_entity(entity)
    key_field = cohort.key[0] if cohort is not None and cohort.key else None

    features: list[FeatureDraft] = []
    for f in draft.features:
        where = f"feature {f.path!r}"
        f_entity = entity_name(f.entity, where)
        relation = f.relation
        if relation not in relations:
            c = _canon(relation)
            target = c if c in relations else _RELATION_SYNONYMS.get(c)
            if target is None:
                raise PlanDraftInvalid(
                    f"{where}: relation {relation!r} is not in the plan vocabulary and no "
                    f"recorded normalisation maps it. Accepted: {', '.join(relations)}")
            if target == _SAME_ROW_TARGET and f_entity != entity:
                raise PlanDraftInvalid(
                    f"{where}: relation {relation!r} reads as 'a field of this row', but the "
                    f"feature reads entity {f_entity!r} and the cohort is {entity!r}. A field of "
                    f"another entity needs as_of_prior or aggregate_count with a join_key")
            repairs.append(f"{where}: relation {relation!r} -> {target!r}")
            relation = target
        if relation in ("cohort_key", "decision_time") and (
                f.field_name == key_field or f.field_name == draft.decision_time_field):
            # `to_plan` emits the key and the decision instant itself; a second copy would be a
            # duplicate column. Dropping it removes nothing the plan does not already carry.
            repairs.append(f"{where}: dropped — duplicates the {relation} column the plan "
                           f"already emits")
            continue
        ftype = closed("type", f.type, types, _TYPE_SYNONYMS, where)
        mode = closed("pointer_mode", f.pointer_mode, modes,
                      {"compressed": "derived_compressed", "derived": "derived_compressed"},
                      where)
        system, valid = f.system_time_field, f.valid_time_field
        source = catalogue_entity(f_entity)
        if source is not None and not system and source.system_time_field:
            system = source.system_time_field
            repairs.append(f"{where}: system_time_field filled with {system!r}, the field the "
                           f"catalogue declares for {f_entity!r}")
        if (source is not None and not valid and source.valid_time_field
                and relation == "as_of_prior"):
            valid = source.valid_time_field
            repairs.append(f"{where}: valid_time_field filled with {valid!r}, the field the "
                           f"catalogue declares for {f_entity!r}")
        features.append(f.model_copy(update={
            "entity": f_entity, "relation": relation, "type": ftype, "pointer_mode": mode,
            "system_time_field": system, "valid_time_field": valid,
            "window": duration("window", f.window, where)}))
    updates["features"] = features

    if draft.label is not None:
        lab = draft.label
        where = f"label {lab.path!r}"
        l_entity = entity_name(lab.entity, where)
        source = catalogue_entity(l_entity)
        lup: dict[str, Any] = {"entity": l_entity,
                               "type": closed("type", lab.type, types, _TYPE_SYNONYMS, where)}
        for attr in ("valid_time_field", "system_time_field"):
            declared = getattr(source, attr, None) if source is not None else None
            if not getattr(lab, attr) and declared:
                lup[attr] = declared
                repairs.append(f"{where}: {attr} filled with {declared!r}, the field the "
                               f"catalogue declares for {l_entity!r}")
        updates["label"] = lab.model_copy(update=lup)

    if draft.text_field is not None:
        tf = draft.text_field
        where = "text_field"
        t_entity = entity_name(tf.entity, where)
        source = catalogue_entity(t_entity)
        tup: dict[str, Any] = {"entity": t_entity}
        if source is not None and not tf.system_time_field and source.system_time_field:
            tup["system_time_field"] = source.system_time_field
            repairs.append(f"{where}: system_time_field filled with "
                           f"{source.system_time_field!r}, the field the catalogue declares")
        updates["text_field"] = tf.model_copy(update=tup)

    if updates["shape"] != "chunk_corpus" and key_field:
        key_path = f"{entity}.{key_field}"
        if key_path not in draft.row_order:
            # A TOTAL order needs the whole key; appending it changes only tie-breaking, never
            # which cells a row may hold.
            updates["row_order"] = [*draft.row_order, key_path]
            if draft.row_order:
                repairs.append(f"draft: row_order {draft.row_order} + key {key_path!r} (a total "
                               f"order needs the cohort key)")
    return draft.model_copy(update=updates), tuple(repairs)


# ---------------------------------------------------------------------------------------
# The offline planner
# ---------------------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "were", "what", "which", "who", "how", "many", "much",
    "this", "that", "these", "those", "from", "with", "has", "have", "had", "our", "your", "not",
    "any", "all", "into", "over", "about", "please", "can", "you", "does", "did", "there", "give",
    "build", "make", "want", "need", "data", "set", "dataset", "training", "please",
})

#: Words that say the request NAMES its outcome. Removed before field matching, so "the label is
#: …" never matches a field called `label`.
_LABEL_CUES = frozenset({"label", "labels", "labelled", "labeled", "predict", "predicting",
                         "prediction", "predictions", "outcome", "target", "whether",
                         "classify", "classification"})

#: Request words that ask for a shape EXPLICITLY. A bare "graph" is not one: run 2's plain
#: request said "as the graph and facts hold them now" and was planned as a `graph_tensor`.
_CHUNK_SIGNALS = frozenset({"rag", "retrieval", "chunk", "chunks", "corpus", "embedding",
                            "embeddings", "search"})
_EXPLICIT_SHAPE_SIGNALS: dict[str, frozenset[str]] = {
    "graph_tensor": frozenset({"gnn", "tensor", "graph_tensor"}),
    "conversational": frozenset({"dialogue", "sft", "conversation", "conversational", "chat",
                                 "turns"}),
}

_INSTANT_SUFFIXES = ("_on", "_at", "_date", "_time", "_from")


def _tokens(text: str) -> set[str]:
    return set(_ordered_tokens(text))


def _ordered_tokens(text: str) -> list[str]:
    out: list[str] = []
    word = ""
    for ch in (text or "").lower() + " ":
        if ch.isalnum() or ch == "_":
            word += ch
            continue
        if len(word) >= 3 and word not in _STOPWORDS and word not in out:
            out.append(word)
        word = ""
    return out


def _field_tokens(name: str) -> set[str]:
    """A field name's tokens, whole AND split on underscores: `net_gbp` answers "net"."""
    return _tokens(name) | _tokens(name.replace("_", " "))


def _matches(q: str, t: str) -> bool:
    return q == t or (len(q) >= 4 and len(t) >= 3 and (t.startswith(q) or q.startswith(t)))


def _named_at(request_tokens: Sequence[str], name: str) -> Optional[int]:
    """The position of the first request word naming field ``name``, or None."""
    toks = _field_tokens(name)
    for i, q in enumerate(request_tokens):
        if any(_matches(q, t) for t in toks):
            return i
    return None


def _shape_for(question: set[str]) -> str:
    if question & _CHUNK_SIGNALS:
        return "chunk_corpus"
    for shape, signals in _EXPLICIT_SHAPE_SIGNALS.items():
        if question & signals:
            return shape
    return "tabular"


def _is_instant(entity: dict, name: str) -> bool:
    if name == entity.get("valid_time_field"):
        return True
    if name == entity.get("system_time_field") or name.startswith("_system") \
            or name.endswith("_to"):
        return False
    return name.endswith(_INSTANT_SUFFIXES) or name in ("date", "time")


def _decision_field(ordered: Sequence[str], entity: dict) -> Optional[str]:
    """T_d: the instant field the request names FIRST, else the entity's valid-time field.

    A rule over the catalogue: a field is an instant by its declared axis or by its name's suffix
    (`_on`, `_at`, `_date`, `_time`, `_from`); the system-time field is never a decision instant.
    """
    named = sorted(((pos, i, f) for i, f in enumerate(entity.get("fields") or ())
                    if _is_instant(entity, f)
                    and (pos := _named_at(ordered, f)) is not None))
    if named:
        return named[0][2]
    return entity.get("valid_time_field") or None


def _label_and_feature_words(request: str) -> tuple[list[str], list[str]]:
    """Split the request into the words that name its OUTCOME and the words that name features.

    Clause by clause (``;``, ``.``, ``?``, ``!``, newline): in a clause that carries a label cue
    ("the label is …", "predict …", "whether …"), the words AFTER the cue name the outcome; every
    clause without a cue names features. So "with net and VAT as features; the label is the
    status" makes `status` the outcome and never a feature — and "the label is whether it is
    paid" names no field at all, so the planner asks rather than promoting a feature to label.
    """
    label_words: list[str] = []
    feature_words: list[str] = []
    for clause in re.split(r"[;.!?\n]+", request or ""):
        words = _ordered_tokens(clause)
        cue = next((i for i, w in enumerate(words) if w in _LABEL_CUES), None)
        if cue is None:
            feature_words += [w for w in words if w not in feature_words]
        else:
            label_words += [w for w in words[cue + 1:]
                            if w not in _LABEL_CUES and w not in label_words]
    return label_words, feature_words


def _window_from(request: str) -> Optional[str]:
    """"after 30 days" -> P30D. Days and weeks only: a month is not a fixed length."""
    m = re.search(r"(\d+)\s*(day|days|week|weeks)\b", request or "", re.I)
    if not m:
        return None
    n = int(m.group(1))
    return f"P{n * 7 if m.group(2).lower().startswith('week') else n}D"


def gold_planner(*, model: str = "", messages: Optional[list[dict]] = None,
                 guided_json: Any = None, **_: Any) -> str:
    """The deterministic offline planner. A RULE over the catalogue, not a recorded answer.

    `CLAUDE.md`: a real model when a key is set, otherwise a deterministic backend so tests and an
    offline demo always pass. What this replaces is the model's judgement, and it replaces it with
    a computation over whatever catalogue it is handed — delete an entity from the catalogue and
    the plan changes; add a field and it changes again. There is no request→plan table anywhere.

    **It is a competent planner, not an infallible one, and the difference is deliberate.** Where
    the catalogue declares a `system_time_field`, this picks the correct axis. Where it does NOT,
    this does the *intuitive* thing — cut on the time column that exists — because that is exactly
    what a model does, and the compiler refusing it is the demonstration that the compiler earns
    its place. See `test_the_agent_proposes_a_leaky_plan_when_the_source_declares_no_system_time`.

    **What it will not do (E2E run 2, N-07) is author a plan it can SEE cannot compile** for want
    of something the request did not say — a tabular dataset with no outcome, or no decision
    instant. It asks instead (`question`), naming the catalogue's candidates.
    """
    messages = messages or []
    body = (messages[-1].get("content") if messages else "") or ""
    catalogue, request = _split_prompt(body)
    ordered = _ordered_tokens(request)
    question = set(ordered)
    label_words, feature_words = _label_and_feature_words(request)

    shape = _shape_for(question)
    entities = catalogue.get("entities") or []
    if not entities:
        return json.dumps({"entity": "", "shape": shape, "rationale": "empty catalogue"})

    def rank(e: dict) -> tuple:
        return (_relevance(question, e), _declares_both_axes(e), len(e["name"]))

    pool = entities
    if shape == "chunk_corpus":
        texty = [e for e in entities if e.get("text_field")]
        pool = texty or entities
    elif shape == "tabular":
        # A supervised point-in-time rectangle needs an entity that records BOTH when a fact
        # was true and when it was learned; among those the request ranks. Choosing one that
        # cannot carry a label is a guaranteed refusal, not a plan.
        able = [e for e in entities if _declares_both_axes(e) and _relevance(question, e) > 0]
        pool = able or entities
    entity = max(pool, key=rank)
    system_field = entity.get("system_time_field")
    valid_field = entity.get("valid_time_field")

    if shape == "chunk_corpus":
        draft = {
            "shape": shape, "entity": entity["name"],
            "feature_axis": "system_time" if system_field else "valid_time",
            "text_field": {
                "path": f"{entity['name']}.text", "entity": entity["name"],
                "field_name": entity.get("text_field") or "", "relation": "cohort_row",
                "type": "text", "system_time_field": system_field,
                "valid_time_field": valid_field},
            "max_chars": 240,
            "row_order": ["chunk_id"],
            "rationale": ("membership on system time: the corpus replays what was on file at the "
                          "corpus instant"),
            "shaping": ["chunk order follows document order, then offset"],
        }
        return json.dumps(draft)

    name = entity["name"]
    key = (entity.get("key") or [""])[0]
    decision = _decision_field(ordered, entity)
    group_key = entity.get("group_key") or _group_key(entity)

    # The label. A declared label candidate (a boolean outcome) the request names — or, failing
    # that, the first declared one. Otherwise, when the request SAYS it names an outcome ("the
    # label is …", "predict …", "whether …"), the non-axis field it names; that label is the
    # row's own outcome, so it joins on the row key.
    candidates = list(entity.get("label_candidates") or ())
    label_field = max(candidates, key=lambda f: (len(question & _field_tokens(f)), -len(f)),
                      default=None)
    label_type, label_join = "bool", group_key
    if label_field is None and label_words:
        outcome_words = label_words
        excluded = {key, decision, system_field, valid_field, group_key}
        named = sorted((pos, i, f) for i, f in enumerate(entity.get("fields") or ())
                       if f not in excluded and not f.startswith("_") and not f.endswith("_id")
                       and not _is_instant(entity, f)
                       and (pos := _named_at(outcome_words, f)) is not None)
        if named:
            label_field, label_type, label_join = named[0][2], "category", key

    def ask(why: str) -> str:
        outcome = [f for f in (entity.get("fields") or ())
                   if not f.startswith("_") and not f.endswith("_id") and f not in (key, group_key)
                   and not _is_instant(entity, f)]
        instants = [f for f in (entity.get("fields") or ()) if _is_instant(entity, f)]
        able = [e["name"] for e in entities if _declares_both_axes(e)]
        return json.dumps({
            "shape": "tabular", "entity": name,
            "question": (
                f"{why} A tabular dataset here is a supervised training set: one row per {name} "
                f"at a decision instant, with an OUTCOME (label) recorded after it. Please name "
                f"(1) the outcome field — candidates on {name}: {outcome or 'none'}; "
                f"(2) the decision instant — candidates: {instants or 'none'}; and (3) the "
                f"window, e.g. 'the label is <field> within 30 days of <instant>'. Entities that "
                f"record both valid and system time (so an outcome can be placed after a "
                f"decision): {able or 'none'}. This build has no unlabelled tabular shape; "
                f"built shapes: {', '.join(catalogue.get('shapes_built') or ())}."),
            "rationale": why})

    if shape == "tabular":
        if not decision:
            return ask(f"{name} declares no instant at which each row's decision is taken.")
        if not label_field:
            return ask(f"The request names no outcome I can find on {name}.")
        if not valid_field:
            # No valid time: an outcome cannot be placed AFTER a decision at all. (A missing
            # SYSTEM time is not asked about: the planner then does the intuitive, leaky thing —
            # cut on the axis that exists — and the compiler refuses it by name. That refusal is
            # the demonstration the gate earns its place, so it is not pre-empted here.)
            return ask(f"{name} records no valid time, so an outcome cannot be placed after a "
                       f"decision.")

    # Features: prior values of the entity's own feature candidates and a count of prior rows,
    # plus each non-axis field of the row the request names (the row's own value, cut at T_d).
    features = [
        {"path": f"{name}.prior_{f}", "entity": name, "field_name": f,
         "relation": "as_of_prior", "type": "float64", "join_key": group_key,
         "system_time_field": system_field, "valid_time_field": valid_field}
        for f in (entity.get("feature_candidates") or ())
    ]
    features.append({
        "path": f"{name}.prior_row_count", "entity": name,
        "field_name": key, "relation": "aggregate_count",
        "type": "int32", "join_key": group_key, "system_time_field": system_field,
        "valid_time_field": valid_field, "pointer_mode": "derived_compressed"})
    # The label field is NOT excluded: a request that names its own outcome as a feature gets
    # that plan, and the compiler refuses it by name (the label's own field, off this row). A
    # planner that silently dropped it would hide the very mistake the gate exists to catch.
    taken = {key, decision, group_key, system_field, valid_field}
    for f in entity.get("fields") or ():
        if f in taken or f.startswith("_") or _named_at(feature_words, f) is None:
            continue
        features.append({
            "path": f"{name}.{f}", "entity": name, "field_name": f, "relation": "cohort_row",
            "type": ("float64" if f in (entity.get("feature_candidates") or ()) else "string"),
            "system_time_field": system_field, "valid_time_field": valid_field})

    draft = {
        "shape": shape,
        "entity": name,
        "decision_time_field": decision,
        # The judgement the compiler exists to check. With a declared system-time column this is
        # right; without one, cutting on the column that exists is the intuitive answer and it
        # leaks — which is what the refusal is for.
        "feature_axis": "system_time" if system_field else "valid_time",
        "feature_as_of": "per_row",
        "features": features,
        "label": ({"path": f"{name}.{label_field}", "entity": name,
                   "field_name": label_field, "join_key": label_join,
                   "valid_time_field": valid_field or "",
                   "system_time_field": system_field or "", "type": label_type}
                  if label_field and valid_field and system_field else None),
        "label_axis": "valid_time" if label_field else "none",
        "label_window": (_window_from(request) or "P365D") if label_field else None,
        "label_read_at_system_time": "build_time" if label_field else "none",
        "label_maturity_lag": "P14D" if label_field else None,
        "row_order": [f"{name}.{key or 'id'}"],
        "null_policy": "keep",
        "rationale": (f"cohort {name}, decision instant {decision}; features cut on "
                      f"{'system time ' + str(system_field) if system_field else 'the only time '
                                                                                'column present'}"),
        "shaping": [
            "columns emitted in catalogue order, keys first",
            "row order: decision instant then primary key",
        ],
    }
    return json.dumps(draft)


def _relevance(question: set[str], entity: dict) -> int:
    """How well one catalogue entity answers the request. A rule over the catalogue in front of it.

    Prefix-tolerant with a four-character floor, because a request says "breach" and a column is
    named `breached`. Requiring an exact match would make the planner's entity choice depend on
    English morphology; matching two-character prefixes would make everything match everything.
    Field names are split on underscores too, so "net" names `net_gbp`.
    """
    pool = _field_tokens(entity["name"])
    for group in ("fields", "label_candidates", "feature_candidates"):
        for name in entity.get(group) or ():
            pool |= _field_tokens(name)
    score = 0
    for q in question:
        if any(_matches(q, t) for t in pool):
            score += 1
    return score


def _declares_both_axes(entity: dict) -> int:
    """A tie-break, and a principled one: an entity that records BOTH when a fact was true and
    when it was learned can answer a point-in-time question, and one that records neither cannot.
    Where the request does not distinguish two entities, the usable one wins."""
    return int(bool(entity.get("valid_time_field")) and bool(entity.get("system_time_field")))


def _group_key(entity: dict) -> str:
    """The column rows are grouped by for a prior-value join.

    The primary key identifies a row and therefore never has a prior; the join needs the column
    that identifies the SERIES the row belongs to. Chosen as the first non-key field whose name
    ends in `_id`, which is a property of the catalogue in front of it, not of any domain.
    """
    key = set(entity.get("key") or ())
    for name in entity.get("fields") or ():
        if name not in key and name.endswith("_id"):
            return name
    return (entity.get("key") or [""])[0]


def _split_prompt(body: str) -> tuple[dict, str]:
    catalogue: dict = {}
    request = body
    if "CATALOGUE" in body and "REQUEST" in body:
        _, rest = body.split("CATALOGUE", 1)
        cat_text, request = rest.split("REQUEST", 1)
        cat_text = cat_text.strip()
        request = request.replace("Return JSON only.", "").strip()
        # E2E run 3, P-10: the requester's answers complete the request. The offline planner
        # reads their WORDS (as if the request had said them); the questions they answer, which
        # list every candidate field, are context for a model, never tokens for this rule.
        request, answered = split_answers(request)
        if answered:
            request = request + "\n" + "\n".join(answered)
        try:
            catalogue = json.loads(cat_text)
        except json.JSONDecodeError:                             # pragma: no cover - defensive
            catalogue = {}
    return catalogue, request


def gold_harness() -> Harness:
    """A `Harness` over the deterministic planner, on the real task-class route."""
    from databridge_harness import FakeBackend

    return Harness(FakeBackend(gold_planner), profile="cloud", cache=None)


# ---------------------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------------------
@dataclass
class AuthoredPlan:
    plan: MaterialisationPlan
    draft: PlanDraft
    compiled: Optional[CompiledPlan] = None
    #: What `normalise_draft` changed, and any re-draft, in words — carried into the receipt's
    #: notes so a repaired draft is never a silent one (E2E run 2, N-07).
    repairs: tuple[str, ...] = ()


class DataAgent:
    """Request → plan → compiled plan → dataset + receipt."""

    def __init__(self, harness: Harness, catalogue: AgentCatalogue, *,
                 build_time: datetime, snapshots: Sequence[SourceSnapshot] = (),
                 seed: int = 20260816, backend: str = "sqlite/adapter",
                 policy_bundle_version: str = "") -> None:
        self._harness = harness
        self._catalogue = catalogue
        self._build_time = build_time
        self._snapshots = tuple(snapshots)
        self._seed = seed
        self._backend = backend
        # Mirrors `snapshots`: the agent authors a plan before any reader/PolicyEngine is bound, so
        # whoever wires up the orchestrator resolves the CURRENT compiled policy bundle version
        # (`RoleModel.bundle_version`) the same way it resolves source snapshots, and hands it in
        # here (R-014 Finding 3). Left blank by default so a caller that has not been updated for
        # this fails the compiler's OWN check (`policy.bundle_version` pin) rather than this
        # constructor guessing a value that might not match the estate actually being read.
        self._policy_bundle_version = policy_bundle_version
        self.last_event = None

    # --- author -----------------------------------------------------------------------
    def draft(self, request: str, *, feedback: str = "",
              answers: Optional[dict[str, str]] = None) -> PlanDraft:
        """The one model call. Everything downstream is deterministic.

        ``feedback``, given, is the deterministic gate's refusal of a previous draft, shown so the
        model can correct it (see `author`). It sits BEFORE the catalogue, so the offline planner
        (which reads only the catalogue and the request) is unaffected by it."""
        block = ""
        if feedback:
            # The section headers are upper-case words the offline planner splits on; a refusal
            # quoting either must not move the split.
            safe = feedback.replace("CATALOGUE", "catalogue").replace("REQUEST", "request")
            block = (f"\nYOUR PREVIOUS DRAFT WAS REFUSED by the deterministic gate:\n  {safe}\n"
                     f"Return a corrected draft under the schema above, or set `question`.\n")
        prompt = _PROMPT.format(schema=plan_schema_prompt(), feedback=block,
                                catalogue=self._catalogue.as_prompt(),
                                request=request + answers_block(answers))
        result = self._harness.complete_structured(
            PLAN_TASK_CLASS, [{"role": "user", "content": prompt}], PlanDraft, strict=False)
        # The harness event (task_class, model, tokens) of the call that authored the draft, so a
        # caller can record `produced_by` (BUILD_PLAN §5.8) without a second model call.
        self.last_event = getattr(result, "event", None)
        return result.parsed

    def author(self, request: str, *, answers: Optional[dict[str, str]] = None) -> AuthoredPlan:
        """Draft a plan and translate it into the hashed artefact.

        E2E run 2 (N-07) found every English request for a tabular dataset refused, three runs
        out of three: a model wrote ``relation: "self"`` (outside the closed set it had never been
        shown), and the fallback planner wrote a per-row plan with no decision instant. So:

        1. the model is shown the schema it is judged by (`plan_schema_prompt`);
        2. a draft that ASKS (`question`) is raised as `PlanNeedsInput` — never guessed past;
        3. near-miss spellings are normalised by closed, recorded tables (`normalise_draft`);
        4. the plan is dry-compiled, and a draft the gate refuses is re-drafted ONCE with the
           refusal shown. A second refusal is not hidden: an out-of-vocabulary draft raises
           `PlanDraftInvalid`, and a plan the compiler refuses is returned so that
           `materialise` refuses it by name — the gate still sees the mistake.

        A shape this build does not produce is refused here, by name, with the built shapes.

        ``answers`` (E2E run 3, P-10) — ``{mat:<sha1>: text}`` answering questions an earlier
        round asked — are shown to the planner after the request, on every draft of this round.
        """
        draft = self.draft(request, answers=answers)
        try:
            return self._checked(draft, request)
        except (PlanNeedsInput, ShapeNotImplemented):
            raise
        except (ValueError, PlanRefused) as first:            # ValidationError is a ValueError
            failure = f"{type(first).__name__}: {first}"
        retry = self.draft(request, feedback=failure, answers=answers)
        note = (f"the first draft was refused by the gate ({failure[:300]}); the planner "
                f"re-drafted once with the refusal shown")
        try:
            authored = self._checked(retry, request)
        except (PlanNeedsInput, ShapeNotImplemented):
            raise
        except (ValueError, PlanDraftInvalid) as second:
            raise PlanDraftInvalid(
                f"the planner's draft is not a MaterialisationPlan, twice. First: {failure}. "
                f"Second: {type(second).__name__}: {second}") from second
        except PlanRefused:
            # The COMPILER refused the re-draft too. Hand it on uncompiled so `materialise`
            # raises the compiler's own refusal by name — the gate is never routed around.
            normal, repairs = normalise_draft(retry, self._catalogue)
            return AuthoredPlan(plan=self.to_plan(normal, name=request[:80]), draft=normal,
                                repairs=(note, *repairs))
        return AuthoredPlan(plan=authored.plan, draft=authored.draft, compiled=authored.compiled,
                            repairs=(note, *authored.repairs))

    def _checked(self, draft: PlanDraft, request: str) -> AuthoredPlan:
        """Question → normalise → translate → built-shape check → dry compile."""
        if (draft.question or "").strip():
            raise PlanNeedsInput(
                f"the planner needs more from the request before it can author a plan: "
                f"{draft.question.strip()}", questions=(draft.question.strip(),))
        normal, repairs = normalise_draft(draft, self._catalogue)
        plan = self.to_plan(normal, name=request[:80])
        built = built_shapes()
        if plan.shape.kind not in built:
            raise ShapeNotImplemented(
                f"the request asks for shape {plan.shape.kind!r}, which this build does not "
                f"produce. Built shapes: {', '.join(built)}. Re-phrase the request as one of "
                f"those, or name what is missing for {plan.shape.kind!r} (see GET "
                f"/materialise/plans for the reason it is not built)")
        compiled = compile_plan(plan)
        return AuthoredPlan(plan=plan, draft=normal, compiled=compiled, repairs=repairs)

    def to_plan(self, draft: PlanDraft, *, name: str = "") -> MaterialisationPlan:
        """Draft → `MaterialisationPlan`, verbatim. **Nothing is corrected on the way through.**

        A translation layer that quietly fixed `feature_axis` would make the compiler's refusals
        unreachable and the guarantee decorative: the whole argument for delegating plan authoring
        to a model is that a deterministic gate stands behind it, and a gate that never sees the
        mistake is not a gate.
        """
        cat = self._catalogue
        columns: list[Column] = []
        entity = draft.entity
        try:
            entity_cat = cat.entity(entity)
            key_field = entity_cat.key[0] if entity_cat.key else ""
        except KeyError:
            entity_cat, key_field = None, ""

        if key_field:
            columns.append(Column(path=f"{entity}.{key_field}", type="string",
                                  source=ColumnSource(entity=entity, field=key_field,
                                                      relation="cohort_key")))
        if draft.decision_time_field:
            columns.append(Column(
                path=f"{entity}.{draft.decision_time_field}", type="date",
                source=ColumnSource(entity=entity, field=draft.decision_time_field,
                                    relation="decision_time")))
        for f in draft.features:
            columns.append(Column(
                path=f.path, type=f.type,                        # type: ignore[arg-type]
                null_policy=draft.null_policy,                   # type: ignore[arg-type]
                pointer_mode=f.pointer_mode,                     # type: ignore[arg-type]
                source=ColumnSource(entity=f.entity, field=f.field_name,
                                    relation=f.relation,         # type: ignore[arg-type]
                                    join_key=f.join_key,
                                    system_time_field=f.system_time_field,
                                    valid_time_field=f.valid_time_field,
                                    window=f.window)))

        label_block: Optional[Label] = None
        if draft.label is not None:
            label_block = Label(path=draft.label.path, type=draft.label.type,   # type: ignore[arg-type]
                          source=LabelSource(entity=draft.label.entity,
                                             field=draft.label.field_name,
                                             join_key=draft.label.join_key,
                                             valid_time_field=draft.label.valid_time_field,
                                             system_time_field=draft.label.system_time_field))

        text_field = None
        chunking = None
        if draft.shape == "chunk_corpus" and draft.text_field is not None:
            from .plan import Chunking

            text_field = ColumnSource(
                entity=draft.text_field.entity, field=draft.text_field.field_name,
                relation="cohort_row",
                system_time_field=draft.text_field.system_time_field,
                valid_time_field=draft.text_field.valid_time_field)
            chunking = Chunking(max_chars=draft.max_chars or 240, overlap_chars=0)

        row_order = tuple(o for o in draft.row_order if any(c.path == o for c in columns))
        if not row_order and columns:
            row_order = (columns[0].path,)
        if draft.shape == "chunk_corpus":
            row_order = ("chunk_id",)                # the corpus's own fixed identity column

        return MaterialisationPlan(
            label=name,
            authored_by=AuthoredBy(agent="data_agent", on_behalf_of=cat.principal_on_behalf_of,
                                   purpose=cat.purpose, at=datetime.now(timezone.utc)),
            selection=Selection(entity=entity,
                                cohort=Cohort(decision_time_field=draft.decision_time_field)),
            temporal=TemporalContract(
                feature_axis=draft.feature_axis,                 # type: ignore[arg-type]
                feature_as_of=draft.feature_as_of,               # type: ignore[arg-type]
                label_axis=draft.label_axis,                     # type: ignore[arg-type]
                label_window=(LabelWindow(within=draft.label_window)
                              if draft.label_window else None),
                label_read_at_system_time=draft.label_read_at_system_time,  # type: ignore
                label_maturity_lag=draft.label_maturity_lag,
                build_time=self._build_time,
                corpus_as_of=(self._build_time if draft.shape == "chunk_corpus" else None),
                source_snapshots=self._snapshots,
                pin_strength="observational"),
            shape=Shape(kind=draft.shape,                        # type: ignore[arg-type]
                        consumer=Consumer(framework=("xgboost" if draft.shape == "tabular"
                                                     else "retriever"), version="3.4.0"),
                        columns=tuple(columns), label=label_block,
                        text_field=text_field, chunking=chunking),
            policy=MaterialisationPolicy(
                purpose=cat.purpose,
                principal=PolicyPrincipal(actor=cat.principal_actor,
                                          on_behalf_of=cat.principal_on_behalf_of,
                                          grant_id=cat.principal_grant_id),
                lawful_basis=cat.lawful_basis,
                bundle_version=self._policy_bundle_version),
            determinism=Determinism(seed=self._seed, row_order=row_order,
                                    executor=Executor(backend=self._backend,
                                                      materialiser_version=EXECUTOR_VERSION),
                                    split=Split(strategy="none")),
            provenance=Provenance(mode="compressed", require_pointer_for_every_cell=True),
            output=Output(format="jsonl", metadata="croissant/1.1"),
            packs=cat.packs,
        )

    # --- shape ------------------------------------------------------------------------
    def materialise(self, request: str, reader: EstateReader, *,
                    out_dir: Optional[Path] = None,
                    authored: Optional[AuthoredPlan] = None,
                    verify_sample: int = 0) -> Materialisation:
        """The full agent pathway. `shaped_by: agent`, and the receipt says so.

        The plan is authored and COMPILED first — that does not become optional on this path — and
        the agent's directives are then applied to the built rectangle and re-checked.

        ``authored``, given, is a plan this agent already authored for ``request`` (so a caller
        that inspected the draft first does not pay for, or get a different answer from, a second
        model call). It is compiled exactly as a fresh one would be.
        """
        authored = authored if authored is not None else self.author(request)
        built = materialise(authored.plan, reader, shaped_by="agent", out_dir=None,
                            verify_sample=verify_sample,
                            shaping_directives=tuple(authored.draft.shaping))
        shaped = _apply_shaping(built.dataset, authored.draft, built.compiled)
        _check_agent_shaping(shaped, built.dataset, built.compiled)

        # R-021 Finding 6: every measurement on the receipt is re-taken on the SHAPED rectangle —
        # the bytes that ship — not inherited from the pre-shaping build. Counts, pointer
        # manifest, fully-absent columns, the missingness leak check and the verification sample
        # all describe a dataset; after a `drop:` they would describe a different one.
        from databridge_adapter import pointer_set_digest

        from .executor import _verify_sample, absent_exempt_columns, assert_no_missingness_leak
        from .receipt import CellCounts, PointerManifest

        plan = built.compiled.plan
        pointers = shaped.pointers()
        measured = {
            "cells": CellCounts(**shaped.cell_counts()),
            "pointer_manifest": PointerManifest(entries=len(pointers),
                                                digest=pointer_set_digest(pointers)),
            "columns_fully_absent": shaped.fully_absent_columns(),
            "missingness_leak_check": assert_no_missingness_leak(
                shaped, label_column=plan.shape.label.path if plan.shape.label else None,
                max_class_delta=plan.policy.missingness_leak_check.max_class_delta,
                absent_exempt=absent_exempt_columns(plan)),
            "verdicts": _verify_sample(reader, pointers, verify_sample),
        }

        receipt = built.receipt.model_copy(update={
            **measured,
            "content_hash": shaped.content_hash(),
            "notes": built.receipt.notes + (
                "shaped_by=agent: the plan was authored by a model from a natural-language "
                "request and the column selection and order below were the agent's, not the "
                "plan's. plan_hash names the intent; it does not identify these bytes. Re-running "
                "the same request may produce a different dataset.",) + tuple(
                f"draft normalised before translation (recorded, never widening): {r}"
                for r in authored.repairs),
        })
        if out_dir is not None:
            from .executor import _write

            receipt = receipt.model_copy(
                update={"output_uri": str(_write(shaped, authored.plan, Path(out_dir)))})
        return Materialisation(compiled=built.compiled, dataset=shaped, receipt=receipt)


def _apply_shaping(dataset: Dataset, draft: PlanDraft, compiled: CompiledPlan) -> Dataset:
    """Apply the agent's directives: a column SUBSET and an order. Never an addition.

    Deliberately narrow. The agent may choose what to emit out of what the compiler accepted, and
    it may choose the order; it may not introduce a column, alter a value, or drop the label. That
    boundary is what keeps `shaped_by: agent` a statement about REPRODUCIBILITY rather than about
    grounding — the cells are still exactly the cells the adapter returned.
    """
    keep = [name for name in dataset.columns
            if not any(d.startswith(f"drop:{name}") for d in draft.shaping)]
    if keep == list(dataset.columns):
        return dataset
    idx = [dataset.index(name) for name in keep]
    rows = tuple(tuple(row[i] for i in idx) for row in dataset.rows)
    return Dataset(columns=tuple(keep), rows=rows, splits=dataset.splits,
                   float_scale=dataset.float_scale)


def _check_agent_shaping(shaped: Dataset, built: Dataset, compiled: CompiledPlan) -> None:
    """The after-the-fact checks. A dataset failing one is refused, never warned about."""
    added = [c for c in shaped.columns if c not in built.columns]
    if added:
        raise AgentShapedPlanViolated(
            f"the agent's shaping introduced column(s) {added}, which the compiled plan does not "
            f"emit. An agent may choose among the columns the compiler accepted; a column that "
            f"came from somewhere else has no compiled provenance and no place in the receipt")
    label = compiled.plan.shape.label
    if label is not None and label.path not in shaped.columns:
        raise AgentShapedPlanViolated(
            f"the agent's shaping dropped the label column {label.path!r}. The label is the "
            f"reason the temporal contract exists; a supervised dataset without it is not the "
            f"dataset the plan compiled to")
    screen_pointer_requirement(shaped)


__all__ = ["AgentCatalogue", "AuthoredPlan", "DataAgent", "EntityCatalogue", "FeatureDraft",
           "LabelDraft", "PLAN_TASK_CLASS", "PlanDraft", "RELATION_MEANINGS", "built_shapes",
           "gold_harness", "gold_planner", "normalise_draft", "plan_schema_prompt"]
