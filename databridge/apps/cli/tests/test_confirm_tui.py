"""The confirmation TUI drives the session and cannot bypass review (injectable input)."""
from __future__ import annotations

import io

from rich.console import Console

from databridge_cli.confirm_tui import render_overview, run_confirmation
from databridge_core import (
    AnnotatedSchema,
    ColumnAnnotation,
    SemanticType,
    TableAnnotation,
    TableRole,
)
from databridge_semantic import ConfirmationSession

_QUIET = Console(file=io.StringIO())


def _draft() -> AnnotatedSchema:
    auto = ColumnAnnotation(column="id", semantic_type=SemanticType.ENTITY_ID,
                            description="pk", confidence=1.0, provenance="deterministic")
    review = ColumnAnnotation(column="weird", semantic_type=SemanticType.FREE_TEXT,
                              description="?", confidence=0.5, provenance="llm")
    t = TableAnnotation(table="t", concept="c", role=TableRole.ENTITY, columns=[auto, review])
    return AnnotatedSchema(source_id="s", version=1, tables=[t])


def test_enter_accepts_and_confirms():
    s = ConfirmationSession(_draft())
    confirmed = run_confirmation(s, "alice", input_fn=lambda _p: "", console=_QUIET)
    assert confirmed.confirmed_by == "alice" and confirmed.is_confirmed


def test_typing_a_type_edits():
    s = ConfirmationSession(_draft())
    confirmed = run_confirmation(s, "bob", input_fn=lambda _p: "status_categorical",
                                 console=_QUIET)
    weird = next(c for tbl in confirmed.tables for c in tbl.columns if c.column == "weird")
    assert weird.semantic_type is SemanticType.STATUS_CATEGORICAL
    assert weird.provenance == "human"


def test_render_overview_smoke():
    assert render_overview(_draft()) is not None
