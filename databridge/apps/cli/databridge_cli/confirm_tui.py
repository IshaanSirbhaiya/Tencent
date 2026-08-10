"""Confirmation TUI (06_PRD §7) — schema overview → review queues → per-column cards.

Ordered for human efficiency: the human calibrates on the forest (schema overview with
confidence colouring) before the trees (per-column cards), mandatory-review items first.
The TUI cannot bypass review: it can only call ``session.finalize``, which REFUSES while
mandatory items remain. ``input_fn`` is injectable so the loop is testable without a TTY.
"""
from __future__ import annotations

from typing import Callable, Optional

from rich.console import Console
from rich.table import Table

from databridge_core import AnnotatedSchema, SemanticType
from databridge_semantic.confirmation import ConfirmationSession

_BAND_STYLE = {"auto": "green", "spot_check": "yellow", "review": "red"}


def render_overview(schema: AnnotatedSchema) -> Table:
    t = Table(title=f"Schema {schema.source_id} v{schema.version} ({schema.domain})")
    t.add_column("table"); t.add_column("role"); t.add_column("concept")
    t.add_column("cols", justify="right"); t.add_column("review", justify="right")
    for tbl in schema.tables:
        review = sum(1 for c in tbl.columns if ConfirmationSession.band(c) == "review")
        t.add_row(tbl.table, tbl.role.value, tbl.concept, str(len(tbl.columns)),
                  f"[red]{review}[/red]" if review else "0")
    return t


def render_column_card(table: str, ann) -> Table:
    band = ConfirmationSession.band(ann)
    t = Table(title=f"{table}.{ann.column}", title_style=_BAND_STYLE[band])
    t.add_column("field"); t.add_column("value")
    t.add_row("semantic_type", ann.semantic_type.value)
    t.add_row("description", ann.description)
    t.add_row("confidence", f"{ann.confidence:.2f} [{band}]")
    t.add_row("provenance", ann.provenance)
    if ann.is_pii:
        t.add_row("PII", f"yes ({ann.pii_kind})")
    return t


def run_confirmation(
    session: ConfirmationSession,
    principal: str,
    *,
    input_fn: Callable[[str], str] = input,
    console: Optional[Console] = None,
) -> AnnotatedSchema:
    """Drive the review loop and return a CONFIRMED schema.

    For every mandatory-review column: show the card and accept (Enter) or edit
    (type a semantic_type from the closed vocab). Raises via ``finalize`` if — through a
    bug — a mandatory item is left unresolved.
    """
    console = console or Console()
    console.print(render_overview(session.draft))

    # Spot-check queue (06_PRD §7): all 0.80–0.95 items + a 10% sample of the auto band,
    # shown for the human to "check the checker" (non-blocking — only review-band blocks).
    auto = session.queue("auto")
    sample_n = max(1, round(len(auto) * 0.10)) if auto else 0
    spot = session.queue("spot_check") + auto[:sample_n]
    if spot:
        console.print(f"[yellow]Spot-check ({len(spot)} item(s))[/yellow]")
        for table, ann in spot:
            console.print(render_column_card(table, ann))

    for table, ann in list(session.queue("review")):
        console.print(render_column_card(table, ann))
        choice = input_fn(
            f"[{table}.{ann.column}] Enter=accept, or type a semantic_type to correct: "
        ).strip()
        if not choice:
            session.accept(table, ann.column)
        else:
            try:
                session.edit(table, ann.column, SemanticType(choice))
            except ValueError:
                console.print(f"  unknown type {choice!r}; accepting model's value")
                session.accept(table, ann.column)

    confirmed = session.finalize(principal)        # REFUSES if anything remains unresolved
    console.print(f"Session: {session.summary()}")
    console.print(f"[green]Confirmed[/green] {confirmed.source_id} v{confirmed.version} "
                  f"by {confirmed.confirmed_by}")
    return confirmed
