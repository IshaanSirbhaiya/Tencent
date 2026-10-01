"""`shaped_by` — the one field the founder's staging decision requires (PHASE_PLAN Phase 6).

> A `Receipt` MUST record which pathway produced the dataset. Because on the agent path
> `plan_hash` no longer identifies the bytes — re-running the same request may produce a different
> dataset — and a dataset that is not reproducible must never be mistaken later for one that is.

The honest failure is a receipt that says "agent-shaped, not reproducible". The dangerous one is a
receipt that looks identical to a compiled dataset. These tests are about keeping the two
distinguishable at every place the fact travels: the field, the derived property, the human
summary, and the Croissant file a consumer might read INSTEAD of the receipt.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from databridge_materialise import (
    CellCounts,
    ExecutorRecord,
    Receipt,
    RowCounts,
    materialise,
    to_croissant,
)

from plans import covenant_plan


def _receipt(**overrides) -> Receipt:
    base = dict(plan_hash="sha256:abc", shaped_by="plan",
                executed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
                executor=ExecutorRecord(version="walnut-mat 0.1.0", backend="duckdb 1.4.2"),
                rows=RowCounts(emitted=10), cells=CellCounts(grounded=50), content_hash="sha256:d")
    base.update(overrides)
    return Receipt(**base)


def test_reproducibility_is_derived_not_asserted():
    """There is no `reproducible` field to set. A boolean somebody can set is a boolean somebody
    will set, and the whole value of this record is that it cannot flatter the dataset."""
    assert _receipt(shaped_by="plan").reproducible is True
    assert _receipt(shaped_by="agent").reproducible is False
    with pytest.raises(Exception):
        Receipt(plan_hash="x", executed_at=datetime.now(timezone.utc),
                executor=ExecutorRecord(version="v", backend="b"))    # shaped_by omitted


def test_an_agent_shaped_receipt_cannot_claim_byte_reproduction():
    """`reproduced_from` is a claim that re-executing the plan produced the same bytes. On the
    agent path the plan does not determine the bytes, so the claim is unavailable rather than
    merely discouraged."""
    with pytest.raises(ValueError, match="cannot claim `reproduced_from`"):
        _receipt(shaped_by="agent", reproduced_from="sha256:prior")
    _receipt(shaped_by="plan", reproduced_from="sha256:prior")        # fine


def test_the_human_summary_states_the_difference_in_words():
    """An operator reading one line has to get the right impression, not a technically-true one."""
    assert "must reproduce content_hash" in _receipt(shaped_by="plan").honest_summary
    agent = _receipt(shaped_by="agent").honest_summary
    assert "NOT reproducible" in agent
    assert "may produce a different dataset" in agent


def test_the_three_missing_classes_are_never_summed(built_receipt):
    """`unresolved`, `policy_suppressed` and `grounding_dropped` are separate counters. Summing
    them would hide exactly the covert channel the missingness leak check looks for."""
    cells = built_receipt.cells
    assert {"grounded", "unresolved", "policy_suppressed", "grounding_dropped"} <= set(
        cells.model_dump())
    assert cells.total == (cells.grounded + cells.unresolved + cells.policy_suppressed
                           + cells.grounding_dropped)


def test_an_empty_verdict_tally_is_not_a_claim_that_everything_verified(built_receipt):
    """A fresh build re-resolves nothing, so `verdicts` is empty — and the receipt says why rather
    than leaving a reader to infer that no news is good news."""
    assert built_receipt.verdicts == {}
    assert any("not a claim that everything verified" in n for n in built_receipt.notes)


def test_a_verdict_outside_the_lattice_is_refused():
    with pytest.raises(ValueError, match="eight-state lattice"):
        _receipt(verdicts={"probably_fine": 3})


def test_a_sampled_verification_is_reported_with_its_verdicts(reader):
    """When pointers ARE re-resolved, the tally is the eight-state lattice and not a boolean."""
    built = materialise(covenant_plan(), reader, verify_sample=5)
    assert sum(built.receipt.verdicts.values()) == 5
    assert set(built.receipt.verdicts) <= {"verified", "verified_as_of_capture", "drift_benign",
                                           "drift_material", "expired", "vanished", "forbidden",
                                           "unverifiable"}


def test_the_croissant_file_carries_shaped_by(built_receipt):
    """A consumer may read the interchange file and never open the receipt. If the pathway lived
    only in the receipt, Croissant would launder a non-reproducible dataset into a reproducible-
    looking one."""
    plan = covenant_plan()
    from databridge_materialise import Dataset

    doc = to_croissant(plan, built_receipt, Dataset(columns=("a",), rows=()))
    assert doc["cr:shapedBy"] == built_receipt.shaped_by
    assert doc["cr:reproducible"] is built_receipt.reproducible
    assert doc["prov:wasGeneratedBy"]["description"] == built_receipt.honest_summary
    assert doc["cr:planHash"] == plan.plan_hash
    # AI Act Art. 10 §2(h) — "data gaps and shortcomings" is a build output, not a prose promise.
    assert "not yet RECORDED" in doc["cr:dataGapsAndShortcomings"]
    assert doc["cr:validated"] is False, (
        "this build does not run mlcroissant's validator, and claiming a validation nobody "
        "performed is the documentation defect the standard exists to fix")


def test_the_croissant_file_is_valid_json_ld_on_disk(reader, tmp_path):
    materialise(covenant_plan(), reader, out_dir=tmp_path)
    # `output_stem`, not `id`: file names use the FULL plan_hash so two plans sharing an `id`
    # prefix (12 hex chars) can never silently overwrite each other's output (R-014 Finding 13).
    doc = json.loads((tmp_path / f"{covenant_plan().output_stem}.croissant.json").read_text("utf-8"))
    assert doc["@context"]["prov"] == "http://www.w3.org/ns/prov#"
    assert doc["conformsTo"].endswith("/1.1")
    assert doc["recordSet"][0]["field"]


@pytest.fixture
def built_receipt(reader):
    return materialise(covenant_plan(), reader).receipt
