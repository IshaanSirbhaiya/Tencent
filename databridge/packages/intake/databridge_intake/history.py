"""The clinic-history port — how the pipeline asks the graph what it already knows.

**Why a Protocol and not an import.** ``packages/graph`` already imports ``packages/intake``
(``clinic_seed`` uses the registry, the resolver and the rules table). Importing back would be a
circular dependency, so the direction is inverted instead: *this* package defines the interface,
the graph package implements it, and composition happens at the gateway and the CLI.
``test_no_graph_import`` asserts ``packages/intake`` never imports ``databridge_graph``.

**Two rules this file exists to carry** (PLAN.md §5 Phase 6):

1. **History never enters the resolver's score.** "Candidate A has been here before" is information
   a human should have and a *score* must not absorb — folding it in would make the machine lean
   toward one identity on a prior, which is identity resolution by the back door. Nothing here is
   reachable from ``resolve.py``, and a test asserts candidate scores are unchanged by history.
2. **The pipeline must work with no graph at all.** ``NoHistory`` is the default and returns empty
   for everything, so an absent, empty or unreachable graph degrades the card (fewer flags) and
   never blocks an intake being read or confirmed. Fail open on enrichment; never on safety.

Every value returned carries the ``SourcePointer``s of the graph nodes and edges it was derived
from, so an enrichment is grounded on exactly the same terms as an extracted field.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from databridge_core import SourcePointer


class _Grounded(BaseModel):
    """Base for anything the history port returns.

    ``sources`` is required to be NON-EMPTY. The module docstring promises every value is grounded,
    and a default of ``[]`` made that promise unenforceable: a non-graph implementation could raise
    the conflict flag with no pointer behind it at all, and the card would show staff a claim with
    nothing to click through to.
    """

    model_config = ConfigDict(frozen=True)

    sources: list[SourcePointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _must_be_grounded(self) -> "_Grounded":
        if not self.sources:
            raise ValueError(
                f"{type(self).__name__} REFUSED: no SourcePointer. A graph-derived enrichment must "
                f"cite the path it was traversed from, exactly like an extracted field.")
        return self


class PriorIntake(_Grounded):
    """A document already on file that names this patient.

    ``candidate_count`` is carried because it qualifies the claim: a prior document that matched
    two patients is weaker evidence of a returning patient than one that matched exactly one, and
    the card must be able to say so.
    """

    document_id: str
    patient_id: str
    issuer_code: Optional[str] = None
    package_code: Optional[str] = None
    seen_at: Optional[dt.datetime] = None
    candidate_count: Optional[int] = None


class PriorTest(_Grounded):
    """A test a document on file already authorised for this patient."""

    canonical: str
    as_printed: str
    document_id: str
    performed_at: Optional[dt.datetime] = None


@runtime_checkable
class ClinicHistory(Protocol):
    """What the pipeline may ask about the past. Read-only, by construction.

    Deliberately narrow: three questions, each answerable by one traversal. A wider port would
    invite the pipeline to reach into the graph for things it should be computing itself, and the
    seam would stop being a seam.
    """

    def prior_intakes_for(self, patient_id: str) -> list[PriorIntake]:
        """Documents on file naming this patient, newest first.

        **Ordering is part of the contract.** The card renders these and joins them into a sentence;
        an implementation returning them in an arbitrary order would make the text differ between
        runs on unchanged facts.
        """
        ...

    def documents_naming(self, patient_id: str, *,
                         exclude_document_id: Optional[str] = None) -> list[PriorIntake]:
        """Same, with the document currently being read excluded.

        Separate from ``prior_intakes_for`` because "other documents about this person" is the
        question that matters for conflict detection, and doing the exclusion at every call site is
        how one call site eventually forgets.
        """
        ...

    def tests_authorised_for(self, patient_id: str, *,
                             since: Optional[dt.datetime] = None) -> list[PriorTest]:
        """Tests already authorised for this patient, optionally bounded by date."""
        ...


class NoHistory:
    """The default. Every answer is empty.

    This is not a stub to be replaced later — it is the honest behaviour of a system with no
    history available, and every test that does not specifically exercise history uses it. If a
    feature only works with a real graph attached, that shows up as a failing test here rather than
    as a surprise in production.
    """

    def prior_intakes_for(self, patient_id: str) -> list[PriorIntake]:
        return []

    def documents_naming(self, patient_id: str, *,
                         exclude_document_id: Optional[str] = None) -> list[PriorIntake]:
        return []

    def tests_authorised_for(self, patient_id: str, *,
                             since: Optional[dt.datetime] = None) -> list[PriorTest]:
        return []
