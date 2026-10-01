"""`plan_hash` names the dataset. What is inside it, and what deliberately is not.

SYSTEM_DESIGN §6.1: the plan is the dataset's identity and the bytes are a cache. That claim is
only worth something if the hash actually covers everything that changes the bytes and nothing
that does not — so each test below is one line of §6.2's exclusion list or §6.3's rule 5.
"""
from __future__ import annotations

from datetime import datetime, timezone

from databridge_materialise import MaterialisationPlan

from plans import covenant_plan


def test_the_same_query_under_a_different_policy_is_a_different_dataset():
    """§6.3 rule 5. This is the one an operator gets wrong: 'it's the same query, so it's the same
    training set' — and it is not, because a different purpose reaches different rows and carries a
    different lawful basis."""
    plan = covenant_plan()
    other = covenant_plan(policy=plan.policy.model_copy(
        update={"purpose": "lending.portfolio_reporting"}))
    assert plan.query_hash == other.query_hash, (
        "the selection and shape are identical, so the query hash must be too — otherwise the "
        "policy test below proves nothing")
    assert plan.policy_hash != other.policy_hash
    assert plan.plan_hash != other.plan_hash


def test_a_pack_bump_is_a_different_dataset():
    """The column paths and category vocabularies are named in the packs' closed vocabulary, so a
    pack version change means the names mean something else."""
    plan = covenant_plan()
    bumped = covenant_plan(packs=("finance@3",))
    assert plan.pack_hash != bumped.pack_hash
    assert plan.plan_hash != bumped.plan_hash


def test_a_human_label_and_an_authoring_TIME_are_not_identity():
    """§6.2's exclusion list. Renaming a plan does not produce a new dataset, and neither does
    writing the same plan again on Tuesday."""
    plan = covenant_plan()
    renamed = covenant_plan(label="the same thing, renamed")
    later = covenant_plan(authored_by=plan.authored_by.model_copy(
        update={"at": datetime(2027, 1, 1, tzinfo=timezone.utc)}))
    assert renamed.plan_hash == plan.plan_hash
    assert later.plan_hash == plan.plan_hash


def test_who_asked_and_why_ARE_identity():
    """The counterpart to the test above, and the reason `authored_by` is not simply excluded
    wholesale: *when* a plan was written is not a property of the dataset, but *who* wrote it and
    *for what purpose* travel with derived data (AI Act Art. 10 §2(b))."""
    plan = covenant_plan()
    other = covenant_plan(authored_by=plan.authored_by.model_copy(
        update={"on_behalf_of": "someone-else"}))
    assert other.plan_hash != plan.plan_hash


def test_the_temporal_pins_are_inside_the_hash():
    """§6.2: `temporal_hash` is over the temporal block INCLUDING `source_snapshots`. The same
    query against a different snapshot is a different dataset, however identical the SQL."""
    plan = covenant_plan()
    moved = covenant_plan(temporal=plan.temporal.model_copy(update={
        "source_snapshots": (plan.temporal.source_snapshots[0].model_copy(
            update={"content_hash": "sha256:something-else"}),)}))
    assert moved.temporal_hash != plan.temporal_hash
    assert moved.plan_hash != plan.plan_hash


def test_the_maturity_lag_changes_the_dataset():
    """A different right-censoring guard admits a different set of rows. If the hash did not move,
    two datasets with different row counts would share a name."""
    plan = covenant_plan()
    lagged = covenant_plan(temporal=plan.temporal.model_copy(
        update={"label_maturity_lag": "P30D"}))
    assert lagged.plan_hash != plan.plan_hash


def test_the_hash_is_stable_across_construction_paths():
    """Round-tripping through JSON must not move the identity: a plan read back from a receipt has
    to hash to what the receipt says it hashes to."""
    plan = covenant_plan()
    revived = MaterialisationPlan.model_validate(plan.model_dump(mode="json"))
    assert revived.plan_hash == plan.plan_hash
    assert revived.id == plan.id


def test_the_plan_hash_is_pinned():
    """A GOLDEN. `plan_hash` is computed over a Pydantic `model_dump`, which is a deliberate trade
    (see `plan.py`'s module docstring): the hand-built payload `databridge_adapter.canonical` uses
    would be its own bug farm over a fifteen-model tree. The trade is only safe with a pin — a
    Pydantic release that changed how a `datetime` or a `date` renders would otherwise silently
    re-identify every dataset ever built. When this fails, the question is not 'what is the new
    hash' but 'what moved, and does every stored plan_hash now mean something else'.

    Re-pinned for SCHEME_VERSION 2 (R-014): `MaterialisationPolicy.bundle_version` and
    `Executor.materialiser_version` are now part of the hashed payload (Findings 3 and 11), and
    `covenant_plan()`'s fixture now declares both — a deliberate, scheme-versioned identity change,
    not a silent one."""
    assert covenant_plan().plan_hash == (
        "3893fb97fb2fd8601b2497a1c07993d160bd15b150382378ee59cb6a7ed233ce")


def test_a_plan_renders_to_yaml_with_its_hashes():
    """The artefact a human reviews carries its own identity block; recomputing it by hand is how
    a reviewer ends up approving a plan that is not the one that ran."""
    text = covenant_plan().to_yaml()
    plan = covenant_plan()
    assert plan.plan_hash in text
    assert plan.id in text
