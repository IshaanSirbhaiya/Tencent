"""The data agent: it authors plans, and the compiler stands behind it.

AGENT_PLANE D14's role split, tested from both directions:

* the agent **proposes** a `MaterialisationPlan` and nothing else — the values in the dataset are
  the adapter's cells, and the agent never touches one;
* the compiler **accepts or refuses**, and the tests below include a case where the agent gets it
  wrong for the exact reason the design predicts.

That last test is the important one. A gold planner that always produced a correct plan would make
the compiler look like ceremony. Here, where the catalogue declares no system-time column, the
planner does the *intuitive* thing — cut on the time column that exists — and the compiler refuses
it by name.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from databridge_harness import FakeBackend, Harness
from databridge_materialise import (
    AgentCatalogue,
    AgentShapedPlanViolated,
    DataAgent,
    FeatureAxisRefused,
    NoSystemTimeAxis,
    PLAN_TASK_CLASS,
    compile_plan,
    gold_harness,
)

# NOT `from conftest import BUILD_TIME`. One pytest session collects many packages, several have
# a top-level `conftest.py`, and the bare name resolves to whichever landed on sys.path first —
# `agentdef`'s or `apps/dispatcher`'s, depending on invocation — which aborted collection for the
# whole run the moment `packages/materialise/tests` joined `testpaths`. `plans.py` already defines
# the same instant and is a package-local module name that cannot collide.
from plans import BUILD_TIME, POLICY_BUNDLE_VERSION, SNAPSHOT


@pytest.fixture
def agent(catalogue):
    return DataAgent(gold_harness(), catalogue, build_time=BUILD_TIME, snapshots=(SNAPSHOT,),
                     policy_bundle_version=POLICY_BUNDLE_VERSION)


def test_the_agent_authors_a_plan_that_compiles(agent):
    """The delegation, working: a sentence becomes an entity, a feature set, a dual-axis contract,
    a policy and a shape — and the gate accepts it."""
    authored = agent.author("training data for covenant breach prediction")
    plan = authored.plan
    assert plan.selection.entity == "covenant_test", (
        "the planner picked the entity by relevance over the catalogue it was handed; picking the "
        "dimension table instead would still 'work' and would produce a leak")
    assert plan.temporal.feature_axis == "system_time"
    assert plan.temporal.label_axis == "valid_time"
    assert plan.temporal.label_maturity_lag
    compiled = compile_plan(plan)
    assert compiled.feature_columns
    assert plan.shape.label is not None


def test_the_agent_reaches_a_model_only_through_the_harness_and_by_task_class(catalogue):
    """`CLAUDE.md`: LLM access only through `packages/harness`, and a call site passes a
    `task_class`, never a model name. Swapping to Azure OpenAI for a Copilot Studio story is then
    a config change in `databridge/config/models.yaml` and not an edit here."""
    backend = FakeBackend('{"entity": "covenant_test", "shape": "tabular"}')
    agent = DataAgent(Harness(backend, profile="cloud", cache=None), catalogue,
                      build_time=BUILD_TIME)
    agent.draft("anything")
    assert backend.call_count == 1
    assert PLAN_TASK_CLASS in {"agentic_orchestration"}
    # The routing table resolved the task class to a concrete model; the agent never named one.
    assert backend.calls[0]["model"]


def test_the_agent_proposes_a_leaky_plan_when_the_source_declares_no_system_time(catalogue):
    """**The demonstration that the compiler earns its place.**

    Hand the planner a catalogue in which the entity records only when a fact was true in the world
    — no column saying when the bank learned it — and it does what a model does: it cuts on the
    axis that exists. That is the intuitive answer, it is the Feast default, and it leaks. The
    compiler refuses it by name, before a row is read.
    """
    blind = AgentCatalogue(
        source_id=catalogue.source_id, packs=catalogue.packs, purpose=catalogue.purpose,
        lawful_basis=catalogue.lawful_basis, principal_actor=catalogue.principal_actor,
        principal_on_behalf_of=catalogue.principal_on_behalf_of,
        principal_grant_id=catalogue.principal_grant_id,
        entities=(replace(catalogue.entity("covenant_test"), system_time_field=None),))
    agent = DataAgent(gold_harness(), blind, build_time=BUILD_TIME, snapshots=(SNAPSHOT,))

    authored = agent.author("training data for covenant breach prediction")
    assert authored.plan.temporal.feature_axis == "valid_time", (
        "the planner should reproduce the mistake, not be prevented from making it — a gate that "
        "never sees the mistake is not a gate")
    with pytest.raises(FeatureAxisRefused):
        compile_plan(authored.plan)


def test_the_translation_layer_does_not_quietly_correct_the_draft(agent, catalogue):
    """A `to_plan` that fixed `feature_axis` on the way through would make every refusal in the
    compiler unreachable, and the guarantee decorative."""
    draft = agent.draft("training data for covenant breach prediction")
    broken = draft.model_copy(update={"feature_axis": "valid_time"})
    assert agent.to_plan(broken).temporal.feature_axis == "valid_time"


def test_the_agent_pathway_records_shaped_by_agent(agent, reader):
    """The founder's requirement, end to end: the receipt distinguishes this dataset from a
    compiled one, and says so in words a reader cannot mistake."""
    built = agent.materialise("training data for covenant breach prediction", reader)
    assert built.receipt.shaped_by == "agent"
    assert built.receipt.reproducible is False
    assert "NOT reproducible" in built.receipt.honest_summary
    assert built.receipt.shaping_directives, (
        "the receipt has to record WHAT the agent chose, or 'agent-shaped' is unfalsifiable")
    assert any("plan_hash names the intent" in n for n in built.receipt.notes)
    assert built.dataset.n_rows > 0


def test_the_agent_path_still_produces_grounded_cells_only(agent, reader):
    """`shaped_by: agent` is a statement about REPRODUCIBILITY, not about grounding. The cells are
    still exactly the cells the adapter returned; the agent chose which columns to emit."""
    from databridge_core import Absent, GroundedValue, Withheld

    built = agent.materialise("training data for covenant breach prediction", reader)
    for row in built.dataset.rows:
        for cell in row:
            assert isinstance(cell, (GroundedValue, Withheld, Absent))


def test_agent_shaping_may_drop_a_column_and_may_not_invent_one(agent, reader):
    """The after-the-fact check. The invariants move from compiled to checked; they do not
    become optional."""
    from databridge_materialise import Dataset
    from databridge_materialise.agent import _check_agent_shaping

    built = agent.materialise("training data for covenant breach prediction", reader)
    compiled = built.compiled

    invented = Dataset(columns=built.dataset.columns + ("something.invented",),
                       rows=tuple(row + (row[0],) for row in built.dataset.rows[:2]))
    with pytest.raises(AgentShapedPlanViolated, match="introduced column"):
        _check_agent_shaping(invented, built.dataset, compiled)

    label = compiled.plan.shape.label.path
    keep = [c for c in built.dataset.columns if c != label]
    idx = [built.dataset.index(c) for c in keep]
    without_label = Dataset(columns=tuple(keep),
                            rows=tuple(tuple(row[i] for i in idx)
                                       for row in built.dataset.rows[:2]))
    with pytest.raises(AgentShapedPlanViolated, match="dropped the label"):
        _check_agent_shaping(without_label, built.dataset, compiled)


def test_the_offline_planner_is_a_rule_over_the_catalogue_not_a_recorded_answer(catalogue):
    """`CLAUDE.md` operating rule 4: deterministic pathways are required; input→answer lookup
    tables are forbidden. Change the catalogue and the plan changes with it — which is only true
    of a computation."""
    agent = DataAgent(gold_harness(), catalogue, build_time=BUILD_TIME, snapshots=(SNAPSHOT,))
    base = agent.draft("training data for covenant breach prediction")

    fewer = AgentCatalogue(
        source_id=catalogue.source_id, packs=catalogue.packs, purpose=catalogue.purpose,
        lawful_basis=catalogue.lawful_basis, principal_actor=catalogue.principal_actor,
        principal_on_behalf_of=catalogue.principal_on_behalf_of,
        principal_grant_id=catalogue.principal_grant_id,
        entities=(replace(catalogue.entity("covenant_test"),
                          feature_candidates=("observed_value",)),))
    narrowed = DataAgent(gold_harness(), fewer, build_time=BUILD_TIME).draft(
        "training data for covenant breach prediction")
    assert len(narrowed.features) < len(base.features)
    assert {f.field_name for f in narrowed.features} < {f.field_name for f in base.features}


def test_the_agent_picks_the_retrieval_shape_from_the_request(text_catalogue):
    """The same rule, on the other axis of the decision: what is being ASKED for."""
    agent = DataAgent(gold_harness(), text_catalogue, build_time=BUILD_TIME,
                      snapshots=(SNAPSHOT,))
    tabular = agent.draft("training data for facility status")
    corpus = agent.draft("a RAG corpus over facility notes for retrieval")
    assert tabular.shape == "tabular"
    assert corpus.shape == "chunk_corpus"
    assert corpus.text_field is not None and corpus.text_field.field_name == "notes"


def test_a_shape_this_build_refuses_is_refused_by_the_agent_path_too(catalogue, reader):
    """A request for a graph dataset does not become a tabular one because tabular is what exists."""
    from databridge_materialise import ShapeNotImplemented

    agent = DataAgent(gold_harness(), catalogue, build_time=BUILD_TIME, snapshots=(SNAPSHOT,),
                      policy_bundle_version=POLICY_BUNDLE_VERSION)
    with pytest.raises((ShapeNotImplemented, FeatureAxisRefused, NoSystemTimeAxis)):
        agent.materialise("a temporal graph tensor of covenant edges for a GNN", reader)


def test_an_agent_directive_can_drop_a_column_and_the_bytes_stop_matching_the_plan(agent, reader):
    """What `shaped_by: agent` MEANS, made concrete.

    The agent emits a `drop:` directive; the shaper applies it after the deterministic build; the
    after-the-fact check confirms it only removed a column the compiler had accepted. The emitted
    rectangle is now narrower than the plan describes, which is precisely why `plan_hash` no longer
    identifies these bytes and why the receipt must not read like a compiled dataset's.
    """
    plain = agent.materialise("training data for covenant breach prediction", reader)
    dropped_column = "covenant_test.prior_row_count"

    authored = agent.author("training data for covenant breach prediction")
    directed = authored.draft.model_copy(
        update={"shaping": [*authored.draft.shaping, f"drop:{dropped_column}"]})

    from databridge_materialise.agent import _apply_shaping, _check_agent_shaping

    shaped = _apply_shaping(plain.dataset, directed, plain.compiled)
    _check_agent_shaping(shaped, plain.dataset, plain.compiled)

    assert dropped_column in plain.dataset.columns
    assert dropped_column not in shaped.columns
    assert shaped.content_hash() != plain.dataset.content_hash(), (
        "if the directive did not move the bytes it was not a shaping decision, and "
        "`shaped_by: agent` would be decoration")
