"""``GET /brain/export/sheets`` — the evidence sheets as a downloadable zip (one .md per node).

`/brain/node/{id}/sheet` already hands back one entity's evidence sheet, and the panel renders it.
This is the same document for every entity at once, as files: a folder someone can open in an
editor or an Obsidian vault, keep, diff against next week's, or send to a reviewer who will never
run this application. The claim "every value traces to a source" is checkable in a text editor, or
it is a slide.

**It exports the existing surface, and only it.** Every byte comes from
``databridge_graph.node_sheet.node_markdown`` — no route-side rendering, no extra fields, and no
node the renderer declines to produce a sheet for. Adding a bulk endpoint must not widen what the
system will disclose; it changes the *shape* of a disclosure that already exists, nothing else.

**Streamed, not assembled.** The archive is drained entry by entry
(``databridge_graph.sheet_export.iter_zip_chunks``), so the gateway holds one sheet at a time
rather than a whole graph's worth of markdown plus a whole zip of it.

**``?as_of=`` rewinds the export**, exactly as it rewinds a single sheet: the files are what that
instant would have produced, and an entity that did not exist then is absent from the folder
rather than present and empty. That is what makes the download match the screen when the /brain
time slider has been moved.

Not wired into ``create_app()`` — same convention as ``evidence_routes.py``. See
``register_export_routes`` for the one line the gateway owner adds.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from databridge_graph.sheet_export import DEFAULT_ROOT, SheetExport, iter_zip_chunks

log = logging.getLogger("databridge.gateway")


def _refusal(code: str, message: str, status: int = 422) -> JSONResponse:
    # The gateway's one refusal shape (`main.py`, `evidence_routes.py`): a stable machine-readable
    # `code`, a human message, and the alternative if there is one.
    return JSONResponse(status_code=status, content={"refusal": {
        "code": code, "message": message, "nearest_alternative": None}})


def _archive_name(as_of: Optional[dt.datetime], generated_at: dt.datetime) -> str:
    """The download's filename. Built from timestamps ONLY.

    Nothing entity-derived goes in a `Content-Disposition` header: a label or an id is document
    text, and a header is a place where a stray quote or newline is a header-splitting bug rather
    than a rendering one. Timestamps cannot carry either.
    """
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    if as_of is not None:
        return f"clinic-evidence-sheets-as-of-{as_of.strftime('%Y%m%d')}-{stamp}.zip"
    return f"clinic-evidence-sheets-{stamp}.zip"


def register_export_routes(app: FastAPI) -> None:
    """Attach ``GET /brain/export/sheets``.

    NOT called by ``create_app()``. Wiring it into the running gateway is a one-line addition to
    ``main.py``: ``register_export_routes(app)`` alongside ``register_evidence_routes(app)``.
    """

    @app.get("/brain/export/sheets")
    def export_sheets(as_of: Optional[str] = None):
        brain = getattr(app.state, "brain", None)
        store = getattr(brain, "store", None)
        if store is None:
            return _refusal("EXPORT_UNAVAILABLE",
                            "the clinic graph is not initialised; nothing to export", status=503)

        when: Optional[dt.datetime] = None
        if as_of:
            try:
                when = dt.datetime.fromisoformat(as_of)
            except ValueError:
                return _refusal("BAD_AS_OF", f"'{as_of}' is not an ISO-8601 timestamp")

        # `SheetExport` normalises a naive instant to UTC (the store holds nothing else), so the
        # header below names the same moment the sheets were rendered at.
        export = SheetExport(store, as_of=when, root=DEFAULT_ROOT)
        filename = _archive_name(export.as_of, export.generated_at)
        log.info("exporting evidence sheets (as_of=%s) as %s", export.as_of, filename)
        return StreamingResponse(
            iter_zip_chunks(export),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                # The browser reads Content-Disposition through fetch() only if it is exposed;
                # without this the client cannot name the saved file after the server's choice.
                "Access-Control-Expose-Headers": "Content-Disposition",
                # A rewound export is a snapshot of a moment, but "that moment" is still assembled
                # from today's beliefs — never cache it as though it were immutable.
                "Cache-Control": "no-store",
            },
        )
