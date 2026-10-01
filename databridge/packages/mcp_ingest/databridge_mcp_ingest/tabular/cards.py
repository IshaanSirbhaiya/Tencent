"""Schema cards — one captured text document per table, the bridge between the stores (P6, P7).

A card says, in plain text an agent can find by resemblance: what the table holds, its columns
(type, measured distinct/non-null, prose or not), the row count, date ranges, the EXACT facts file
and table to query with an example statement, the extract date (in the text, so the "as at"
caveat travels with any quote), and what is NOT covered (aggregation, history exclusion, a prose
cap, rows beyond the graph's row cap).

Each card is written to the capture store like any other item (`schema-card://<source>/<table>`),
gets a document anchor node in the graph (via the build stage's own `ProseBuilder.anchor`, so its
pointer is a `doc_span` over the card text with the capture digest), and is handed to retrieval's
`index_capture` when `databridge_retrieval` is importable.

Prose columns (measured, `profiling.py`) are also captured as one passages document per table —
newest rows first, capped, the cap stated — so free-text notes are searchable by resemblance while
the numbers stay in SQL.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from .. import capture
from ..crawl import item_identity
from .profiling import SqlRunner, quote_ident
from .pyrepr import to_text

CARD_MIME = "text/markdown"


def card_uri(source_id: str, table_ref: str) -> str:
    return f"schema-card://{source_id}/{table_ref}"


def prose_uri(source_id: str, table_ref: str) -> str:
    return f"table-prose://{source_id}/{table_ref}"


def _fmt_range(r: Optional[dict]) -> str:
    if not r:
        return "—"
    return f"{r.get('lo')} to {r.get('hi')}"


def card_text(source_id: str, profile: dict, facts_entry: Optional[dict], *,
              extracted_at: str, facts_file: Optional[str], mapping: Optional[dict] = None,
              graph_row_cap: Optional[int] = None, prose_note: Optional[str] = None) -> str:
    ref, n = profile["ref"], profile["row_count"]
    m = mapping or {}
    node = m.get("node") or {}
    lines = [f"# Schema card: {ref} (source {source_id})", "",
             f"Extract date: {extracted_at}. Every figure on this card is as at that date.", ""]
    holds = (f"Table {ref} holds {n} row{'s' if n != 1 else ''}" if n is not None else
             f"Table {ref}: row count NOT MEASURED (the count query did not answer)")
    if node.get("kind"):
        holds += f"; each row is a `{node['kind']}` in the knowledge graph"
    lines.append(holds + ".")
    fks = profile.get("foreign_keys") or []
    if fks:
        lines.append("Links: " + "; ".join(
            f"{f['column']} → {f.get('ref_schema') + '.' if f.get('ref_schema') else ''}"
            f"{f.get('ref_table')}.{f.get('ref_column')}" for f in fks) + ".")
    if profile.get("primary_key"):
        lines.append(f"Primary key: {', '.join(profile['primary_key'])}.")
    if node.get("canonical_id"):
        lines.append(f"Identifier shared with other sources: {node['canonical_id']} (documents "
                     f"that cite the same value join to these rows).")
    if node.get("candidate_identifier"):
        # R-022 f2 #1: a subject's identifier never joins anything by itself
        lines.append(f"Declared identifier: {node['candidate_identifier']}. A record elsewhere "
                     f"with the same value is only offered as a CANDIDATE for a person to "
                     f"select; these rows are never merged with any other record.")
    if profile.get("date_ranges"):
        lines.append("Date ranges: " + "; ".join(f"{c} {_fmt_range(r)}"
                                                 for c, r in profile["date_ranges"].items()) + ".")
    lines += ["", "## Columns", ""]
    for c in profile["columns"]:
        bits = [c["type"], c["class"], f"{c['distinct']} distinct", f"{c['non_null']} non-null"]
        if c.get("prose"):
            bits.append("PROSE (searchable as passages)")
        if c["class"] == "numeric" and c.get("range"):
            bits.append(f"range {_fmt_range(c['range'])}")
        lines.append(f"- {c['name']}: " + ", ".join(bits))
    lines += ["", "## How to query", ""]
    fe = facts_entry or {}
    if fe.get("facts_table") and facts_file:
        lines.append(f"Facts store: {facts_file}, table `{fe['facts_table']}` (SQLite, "
                     f"read-only). Counts and lists are answered with SQL there, never from "
                     f"passages.")
        if fe.get("mode") == "rows":
            lines.append(f"Example: SELECT COUNT(*) FROM {quote_ident(fe['facts_table'])};")
        else:
            lines.append(f"Example: SELECT {', '.join(quote_ident(g) for g in fe['group_by'])}, "
                         f"row_count FROM {quote_ident(fe['facts_table'])};")
    else:
        lines.append("Not in the facts store (see coverage below).")
    lines += ["", "## Coverage", ""]
    mode = fe.get("mode")
    if mode == "rows":
        lines.append(f"Facts store: all {fe.get('rows_in_facts')} of {n} rows.")
    elif mode == "aggregated":
        lines.append(f"PARTIAL: facts store holds {fe.get('rows_in_facts')} aggregated groups, "
                     f"not the {n} individual rows. {fe.get('reason')}")
    elif mode in ("excluded", "count_only"):
        lines.append(f"PARTIAL: {fe.get('reason')}")
    if node.get("kind"):
        if n is None:
            # R-022 f2 #14: an unanswered count is unknown — so is the graph's coverage
            lines.append(f"Graph: rows are `{node['kind']}` nodes, but the row count was NOT "
                         f"MEASURED, so whether the graph holds every row is UNKNOWN — an empty "
                         f"graph search is not proof of absence; query the facts store.")
        elif graph_row_cap is not None and n > graph_row_cap:
            lines.append(f"PARTIAL: the graph holds the first {graph_row_cap} of {n} rows "
                         f"(scope.max_rows_per_table); an empty graph search is not proof of "
                         f"absence — query the facts store.")
        else:
            lines.append(f"Graph: all {n} rows as `{node['kind']}` nodes"
                         f"{' (once the mapping is approved)' if m.get('status') != 'approved' else ''}.")
    else:
        lines.append("Graph: this table is not mapped to an ontology kind, so its rows are NOT "
                     "graph nodes (they are in the facts store).")
    if prose_note:
        lines.append(prose_note)
    return "\n".join(lines) + "\n"


def prose_text(runner: SqlRunner, profile: dict, policy: dict) -> tuple[Optional[str], str]:
    """(text, note). One block per row, newest first when a temporal column exists, capped."""
    cols = profile.get("prose_columns") or []
    if not cols:
        return None, ""
    cap = int((policy.get("prose") or {}).get("max_rows", 2000))
    temporal = [c["name"] for c in profile["columns"] if c["class"] == "temporal"]
    key = profile["primary_key"] or [c["name"] for c in profile["columns"]]
    order = (", ".join(f"{runner.quote(t)} DESC" for t in temporal[:1]) + ", " if temporal else "") \
        + ", ".join(runner.quote(k) for k in key)
    rows = runner.rows("select_page", {"schema": profile["schema"], "table": profile["table"],
                                       "order_by": order, "limit": cap, "offset": 0})
    blocks = []
    for r in rows:
        ident = ", ".join(f"{k}={to_text(r.get(k))}" for k in key)
        when = f" ({temporal[0]} {to_text(r.get(temporal[0]))})" if temporal else ""
        for c in cols:
            if r.get(c):
                blocks.append(f"[{profile['ref']} {ident}{when}] {c}: {to_text(r.get(c))}")
    n = profile["row_count"]
    if n is None:
        note = (f"Prose columns {cols}: {len(rows)} row(s) read as passages; the table's row "
                f"count was NOT MEASURED, so coverage is unknown.")
        return "\n\n".join(blocks) + "\n", note
    note = (f"Prose columns {cols}: {min(n, cap)} of {n} rows are searchable as passages"
            f"{' (newest first by ' + temporal[0] + ')' if temporal else ''}"
            f"{'' if n <= cap else ' — PARTIAL: the rest are only in the facts store'}.")
    return "\n\n".join(blocks) + "\n", note


def capture_text_item(source_id: str, uri: str, title: str, text: str, *, retrieved_at: str,
                      mime: str = CARD_MIME) -> dict:
    data = text.encode("utf-8")
    sha = capture.put_bytes(data)
    capture.put_text(sha, text)
    capture.put_meta(sha, {"source": source_id, "uri": uri, "mime": mime, "title": title,
                           "retrieved_at": retrieved_at})
    return {"sha256": sha, "uri": uri, "title": title, "item_id": item_identity(source_id, uri),
            "chars": len(text)}


def anchor_cards(store, vocab, source_id: str, items: list[dict], *,
                 retrieved_at: dt.datetime, document_kind: Optional[str] = None) -> list[dict]:
    """A document node per card, through the build stage's own anchor (no parallel builder)."""
    from ..build import ProseBuilder, document_kind as _doc_kind
    from ..screen import load_screen

    kind, note = _doc_kind(vocab, document_kind)
    out = []
    if kind is None:
        return [{"item_id": i["item_id"], "anchor": None, "note": note} for i in items]
    builder = ProseBuilder(store, None, source_id=source_id, vocab=vocab, doc_kind=kind,
                           screen=load_screen())
    for i in items:
        node_id, _ = builder.anchor(item_id=i["item_id"], sha=i["sha256"], text_len=i["chars"],
                                    title=i["title"], uri=i["uri"], retrieved_at=retrieved_at)
        out.append({"item_id": i["item_id"], "anchor": node_id, "uri": i["uri"],
                    "sha256": i["sha256"]})
    return out


def index_items(items: list[dict], source_id: str) -> dict[str, Any]:
    """Hand captured cards/passages to retrieval (G) when it is importable; record either way.

    `indexed_ids` lists EXACTLY the item ids retrieval accepted (R-022 f2 #12): a caller records
    only those as indexed. Retrieval being unavailable is `status: "unavailable"` with no ids —
    never a success."""
    try:
        from databridge_retrieval import index_capture          # type: ignore
    except Exception as exc:                                     # noqa: BLE001
        return {"indexed": 0, "indexed_ids": [], "errors": [
            f"retrieval not importable ({type(exc).__name__})"], "status": "unavailable"}
    done, errors = [], []
    for i in items:
        try:
            index_capture(i["sha256"], {"source_id": source_id, "uri": i["uri"],
                                        "item_id": i["item_id"], "title": i["title"],
                                        "mime": i.get("mime", CARD_MIME)})
            done.append(i["item_id"])
        except Exception as exc:                                 # noqa: BLE001
            errors.append(f"{i['uri']}: {type(exc).__name__}: {str(exc)[:200]}")
    return {"indexed": len(done), "indexed_ids": done, "errors": errors,
            "status": "ok" if not errors else "partial"}


__all__ = ["card_text", "prose_text", "capture_text_item", "anchor_cards", "index_items",
           "card_uri", "prose_uri"]
