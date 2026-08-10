"use client";
import * as React from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { SourcePointer } from "@/lib/types";
import { cn } from "@/lib/utils";

// Provenance is the aesthetic (D-FE-1). Every data value renders through this affordance:
// a dotted underline → a popover showing its SourcePointer. The popover dispatches on
// `pointer.kind` (REVIEW_2 build-note 4) — `derived` renders the SQL/rule + snapshot digest;
// `table_cell` renders table/column/row. The frontend NEVER computes — it shows the receipt.

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex gap-2 py-0.5">
      <span className="w-24 shrink-0 text-subtle">{k}</span>
      <span className="break-all font-mono text-fg">{v}</span>
    </div>
  );
}

function PointerBody({ p }: { p: SourcePointer }) {
  if (p.kind === "derived") {
    const snap = (p.snapshot ?? {}) as Record<string, unknown>;
    return (
      <div className="space-y-1">
        <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-accent">
          SQL-as-provenance · derived
        </div>
        {(p.derivation || p.predicate) && (
          <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-bg p-2 font-mono text-2xs text-fg">
            {p.derivation || p.predicate}
          </pre>
        )}
        <Row k="source" v={p.source_id} />
        {p.row_count != null && <Row k="rows" v={String(p.row_count)} />}
        {p.rows_digest && <Row k="rows_digest" v={String(p.rows_digest).slice(0, 20) + "…"} />}
        {snap["as_of"] != null && <Row k="as_of" v={String(snap["as_of"])} />}
        {snap["content_hash"] != null && (
          <Row k="content_hash" v={String(snap["content_hash"]).slice(0, 20) + "…"} />
        )}
      </div>
    );
  }
  // every other closed kind: show the SourcePointer's actual locator so the receipt resolves to
  // a specific place — a cell, a char span, a page bbox, an email part (review L3).
  return (
    <div className="space-y-1">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-accent">
        {p.kind}
      </div>
      <Row k="source" v={p.source_id} />
      {/* table_cell / query_cell */}
      {p.table && <Row k="table" v={p.table} />}
      {p.column && <Row k="column" v={p.column} />}
      {p.row_id && <Row k="row_id" v={p.row_id} />}
      {/* workbook_cell (C16) — Sheet!A1 / a range */}
      {p.sheet && <Row k="sheet" v={p.sheet} />}
      {(p.cell || p.cell_range) && <Row k="cell" v={p.cell ?? p.cell_range} />}
      {/* doc_span — character range into the source IR */}
      {p.char_start != null && p.char_end != null && (
        <Row k="chars" v={`${p.char_start}–${p.char_end}`} />
      )}
      {/* ocr_bbox — page + recognition confidence */}
      {p.media_id && <Row k="media" v={p.media_id} />}
      {p.page != null && <Row k="page" v={String(p.page)} />}
      {p.ocr_conf != null && <Row k="ocr_conf" v={Number(p.ocr_conf).toFixed(2)} />}
      {/* email_part — message + part */}
      {p.message_id && <Row k="message" v={String(p.message_id).slice(0, 28)} />}
      {p.part && <Row k="part" v={p.part} />}
    </div>
  );
}

export function Grounded({
  children,
  pointer,
  className,
}: {
  children: React.ReactNode;
  pointer?: SourcePointer | null;
  className?: string;
}) {
  if (!pointer) return <span className={className}>{children}</span>;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <span data-grounded className={cn("grounded", className)}>
          {children}
        </span>
      </PopoverTrigger>
      <PopoverContent>
        <PointerBody p={pointer} />
      </PopoverContent>
    </Popover>
  );
}
