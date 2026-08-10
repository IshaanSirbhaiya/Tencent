"""Confirmation flow (06_PRD §7) — P6 as a designed surface, enforced at the API level.

The load-bearing rule (06_PRD §3): an ``AnnotatedSchema`` MUST NOT receive a
``confirmed_by`` while any mandatory-review item remains. We enforce this in the SERVICE,
not the UI: ``ConfirmationSession.finalize`` is the ONLY path to a confirmed schema and it
RAISES while review-band columns are unresolved — so even a TUI bug cannot emit a usable
schema that skipped review. Downstream consumers refuse an unconfirmed schema anyway
(``AnnotatedSchema.is_confirmed``), giving defence in depth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from databridge_core import AnnotatedSchema, ColumnAnnotation, SemanticType

from .confidence import confidence_band
from .corrections import Correction, CorrectionStore, context_key


class MandatoryReviewPending(Exception):
    """finalize() called while ≥1 mandatory-review column is unresolved (P6)."""


class ConfirmationSession:
    """Owns the review of one draft AnnotatedSchema. Tracks which columns a human has
    resolved (accepted/edited); only when no review-band column remains unresolved may a
    confirmed schema be produced."""

    def __init__(self, draft: AnnotatedSchema, corrections: Optional[CorrectionStore] = None):
        if draft.confirmed_by is not None:
            raise ValueError("ConfirmationSession expects an UNCONFIRMED draft")
        self.draft = draft
        self._corrections = corrections
        self._resolved: set[tuple[str, str]] = set()
        self.n_accepted = 0
        self.n_edited = 0

    # -- inspection --------------------------------------------------------------
    @staticmethod
    def band(ann: ColumnAnnotation) -> str:
        return confidence_band(ann.confidence)

    def queue(self, band: str) -> list[tuple[str, ColumnAnnotation]]:
        return [(t.table, c) for t in self.draft.tables for c in t.columns
                if self.band(c) == band]

    def mandatory_remaining(self) -> list[tuple[str, str]]:
        """Review-band columns not yet resolved by a human (these block confirmation)."""
        return [(table, c.column) for table, c in self.queue("review")
                if (table, c.column) not in self._resolved]

    # -- actions -----------------------------------------------------------------
    def accept(self, table: str, column: str) -> None:
        """Human approves the model's annotation as-is → human-confirmed, resolved."""
        self._set(table, column, update={"provenance": "human", "confidence": 1.0,
                                         "confidence_sources": {"band": "auto", "decided_by": "human"}})
        self._resolved.add((table, column))
        self.n_accepted += 1

    def edit(self, table: str, column: str, new_type: SemanticType,
             description: Optional[str] = None) -> None:
        """Human overrides the type → records a Correction, marks resolved."""
        prev = self._get(table, column)
        upd = {"semantic_type": new_type, "provenance": "human", "confidence": 1.0,
               "confidence_sources": {"band": "auto", "decided_by": "human"}}
        if description is not None:
            upd["description"] = description
        self._set(table, column, update=upd)
        self._resolved.add((table, column))
        self.n_edited += 1
        if self._corrections is not None and prev.semantic_type != new_type:
            corrected = self._get(table, column)
            self._corrections.add(Correction(
                context_embedding_key=context_key(table, column, ""),
                input_context={"table": table, "column": column},
                predicted=prev, corrected=corrected,
                schema_domain=self.draft.domain,
                created_at=datetime.now(timezone.utc),
            ))

    # -- finalisation ------------------------------------------------------------
    def finalize(self, principal: str) -> AnnotatedSchema:
        """Produce a CONFIRMED AnnotatedSchema — or REFUSE if review items remain (P6)."""
        remaining = self.mandatory_remaining()
        if remaining:
            raise MandatoryReviewPending(
                f"{len(remaining)} mandatory-review item(s) unresolved: {remaining[:5]}"
                f"{'…' if len(remaining) > 5 else ''}"
            )
        return self.draft.model_copy(update={"confirmed_by": principal})

    def summary(self) -> dict:
        return {
            "auto": len(self.queue("auto")),
            "spot_check": len(self.queue("spot_check")),
            "review": len(self.queue("review")),
            "accepted": self.n_accepted,
            "edited": self.n_edited,
            "mandatory_remaining": len(self.mandatory_remaining()),
        }

    # -- internals ---------------------------------------------------------------
    def _get(self, table: str, column: str) -> ColumnAnnotation:
        t = next(t for t in self.draft.tables if t.table == table)
        return next(c for c in t.columns if c.column == column)

    def _set(self, table: str, column: str, *, update: dict) -> None:
        new_tables = []
        for t in self.draft.tables:
            if t.table != table:
                new_tables.append(t)
                continue
            new_cols = [c.model_copy(update=update) if c.column == column else c
                        for c in t.columns]
            new_tables.append(t.model_copy(update={"columns": new_cols}))
        self.draft = self.draft.model_copy(update={"tables": new_tables})
