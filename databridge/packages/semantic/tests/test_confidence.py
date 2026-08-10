"""blended_confidence implements 06_PRD §4 exactly."""
from __future__ import annotations

import pytest

from databridge_core import SemanticType as ST
from databridge_semantic import blended_confidence, confidence_band, dtype_compatible
from databridge_semantic.confidence import second_model_vote


def test_structural_agreement_and_dtype_bonus():
    # 0.80 + 0.07 (agreement) + 0.03 (dtype compatible) = 0.90
    c = blended_confidence(0.80, ST.AMOUNT_MONEY, structural_prelabel=ST.AMOUNT_MONEY,
                           dtype="numeric", null_fraction=0.0)
    assert c == pytest.approx(0.90)


def test_dtype_incompatibility_penalty():
    # 0.80 + 0 (no agreement) - 0.15 (varchar can't be money) = 0.65 → review
    c = blended_confidence(0.80, ST.AMOUNT_MONEY, structural_prelabel=None,
                           dtype="varchar", null_fraction=0.0)
    assert c == pytest.approx(0.65)
    assert confidence_band(c) == "review"


def test_sparsity_penalty_on_density_type():
    # 0.90 +0.07 +0.03 (caps at 1.0) -0.10 sparsity = 0.90
    c = blended_confidence(0.90, ST.ENTITY_ID, structural_prelabel=ST.ENTITY_ID,
                           dtype="integer", null_fraction=0.7)
    assert c == pytest.approx(0.90)


def test_unknown_always_zero():
    c = blended_confidence(0.99, ST.UNKNOWN, structural_prelabel=None,
                           dtype="varchar", null_fraction=0.0)
    assert c == 0.0
    assert confidence_band(c) == "review"


def test_bands():
    assert confidence_band(0.96) == "auto"
    assert confidence_band(0.95) == "auto"
    assert confidence_band(0.85) == "spot_check"
    assert confidence_band(0.79) == "review"


def test_dtype_compatible_table():
    assert dtype_compatible(ST.AMOUNT_MONEY, "numeric")
    assert not dtype_compatible(ST.AMOUNT_MONEY, "varchar")
    assert dtype_compatible(ST.BOOLEAN_FLAG, "boolean")
    assert dtype_compatible(ST.ENTITY_ID, "integer")
    assert dtype_compatible(ST.UNKNOWN, "anything")          # vacuously compatible


def test_second_model_vote():
    # a <0.80 (review) item: agreement promotes to spot-check, disagreement stays review
    assert second_model_vote(0.70, ST.AMOUNT_MONEY, ST.AMOUNT_MONEY) == "spot_check"
    assert second_model_vote(0.70, ST.AMOUNT_MONEY, ST.AMOUNT_COUNT) == "review"
    assert second_model_vote(0.70, ST.AMOUNT_MONEY, None) == "review"
    assert second_model_vote(0.96, ST.AMOUNT_MONEY, ST.AMOUNT_COUNT) == "auto"  # not in review band
