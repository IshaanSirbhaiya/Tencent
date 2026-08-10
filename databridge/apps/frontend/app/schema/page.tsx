"use client";
import * as React from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DataGrid } from "@/components/data-grid";
import { SchemaGraph } from "@/components/schema-graph";
import { ConfidenceBar, ConfidenceBadge } from "@/components/confidence-badge";
import { api } from "@/lib/api";
import type { AnnotatedSchema, ColumnAnnotation, Decision } from "@/lib/types";
import { bandOfColumn } from "@/lib/types";
import { cn, roleBg } from "@/lib/utils";
import { CheckCircle2, Lock, ShieldCheck, X } from "lucide-react";

// ---- session state (useReducer, D-FE-5: the frontend owns it) --------------------
interface State {
  draft: AnnotatedSchema | null;
  decisions: Decision[];
  remaining: number;
  confirmedBy: string | null;
}
type Action =
  | { type: "loaded"; draft: AnnotatedSchema; remaining: number }
  | { type: "confirmed"; draft: AnnotatedSchema; decisions: Decision[]; remaining: number }
  | { type: "finalized"; draft: AnnotatedSchema; by: string };

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "loaded":
      return { draft: a.draft, decisions: [], remaining: a.remaining, confirmedBy: null };
    case "confirmed":
      return { ...s, draft: a.draft, decisions: a.decisions, remaining: a.remaining };
    case "finalized":
      return { ...s, draft: a.draft, confirmedBy: a.by };
  }
}

export default function SchemaPage() {
  const [state, dispatch] = React.useReducer(reducer, {
    draft: null, decisions: [], remaining: 0, confirmedBy: null,
  });
  const [selectedTable, setSelectedTable] = React.useState<string | null>(null);
  const [drawer, setDrawer] = React.useState<{ table: string; col: ColumnAnnotation } | null>(null);
  const [finalizeMsg, setFinalizeMsg] = React.useState<string | null>(null);

  React.useEffect(() => {
    api.annotate().then((r) => {
      dispatch({ type: "loaded", draft: r.draft, remaining: r.queue_counts.review });
      setSelectedTable(r.draft.tables.find((t) => t.table === "transactions")?.table ?? r.draft.tables[0]?.table ?? null);
    });
  }, []);

  const draft = state.draft;
  const table = draft?.tables.find((t) => t.table === selectedTable) ?? draft?.tables[0];

  async function confirmColumn(tableName: string, col: ColumnAnnotation) {
    if (!draft) return;
    const decisions = [
      ...state.decisions.filter((d) => !(d.table === tableName && d.column === col.column)),
      { table: tableName, column: col.column, accepted_type: col.semantic_type },
    ];
    const r = await api.confirm(draft, decisions);
    dispatch({ type: "confirmed", draft: r.draft, decisions, remaining: r.remaining_mandatory });
  }

  async function finalize() {
    if (!draft) return;
    try {
      const r = await api.finalize(draft, "demo@databridge.io", state.decisions);
      if (r.schema) {
        dispatch({ type: "finalized", draft: r.schema, by: r.schema.confirmed_by ?? "demo@databridge.io" });
        setFinalizeMsg(null);
      } else if (r.refusal) {
        setFinalizeMsg(r.refusal.message);
      }
    } catch (e) {
      // a non-refusal error (e.g. a 500) is thrown by the api client — surface it, don't no-op.
      setFinalizeMsg(e instanceof Error ? `finalize failed (${e.message})` : "finalize failed");
    }
  }

  const columns: ColumnDef<ColumnAnnotation, unknown>[] = [
    {
      header: "Column",
      accessorKey: "column",
      cell: ({ row }) => (
        <button
          className="font-mono text-fg hover:text-accent"
          onClick={() => setDrawer({ table: table!.table, col: row.original })}
        >
          {row.original.column}
          {row.original.is_pii && <Badge className="ml-2 border-band-spot/40 text-band-spot">PII</Badge>}
        </button>
      ),
    },
    { header: "Semantic Type", accessorKey: "semantic_type",
      cell: ({ getValue }) => <span className="font-mono text-2xs text-subtle">{getValue() as string}</span> },
    { header: "Confidence", accessorKey: "confidence",
      cell: ({ row }) => <ConfidenceBar confidence={row.original.confidence} /> },
    { header: "Provenance", accessorKey: "provenance",
      cell: ({ getValue }) => <Badge>{getValue() as string}</Badge> },
    {
      header: "Status",
      cell: ({ row }) => {
        const c = row.original;
        const resolved = state.decisions.some((d) => d.table === table!.table && d.column === c.column) || c.provenance === "human";
        if (bandOfColumn(c) === "review" && !resolved)
          return <Button size="sm" variant="outline" onClick={() => confirmColumn(table!.table, c)}>Confirm</Button>;
        return (
          <span className="inline-flex items-center gap-1 text-2xs text-band-auto">
            <CheckCircle2 className="h-3 w-3" /> {resolved ? "confirmed" : "ok"}
          </span>
        );
      },
    },
  ];

  if (!draft || !table) {
    return (<><Topbar title="Schema Explorer" /><div className="p-6 text-sm text-subtle">Loading annotated schema…</div></>);
  }

  return (
    <>
      <Topbar title="Schema Explorer" />
      <div className="flex min-h-0 flex-1">
        {/* left: table list */}
        <div className="w-60 shrink-0 overflow-auto border-r border-border p-2">
          {draft.tables.map((t) => {
            const review = t.columns.filter((c) => bandOfColumn(c) === "review").length;
            return (
              <button
                key={t.table}
                onClick={() => setSelectedTable(t.table)}
                className={cn(
                  "mb-0.5 flex w-full items-center justify-between gap-2 rounded px-2.5 py-2 text-left text-sm",
                  selectedTable === t.table ? "bg-muted" : "hover:bg-muted",
                )}
              >
                <span className="min-w-0 truncate">
                  <span className="font-mono text-xs text-fg">{t.table}</span>
                  <span className={cn("ml-2 rounded border px-1 text-2xs", roleBg(t.role))}>{t.role}</span>
                </span>
                {review > 0 && <span className="h-2 w-2 shrink-0 rounded-full bg-band-review" />}
              </button>
            );
          })}
        </div>

        {/* main */}
        <div className="min-w-0 flex-1 overflow-auto p-5">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="font-mono text-base font-semibold">{table.table}</h2>
              <span className="text-xs text-subtle">{table.concept}</span>
            </div>
            <div className="flex items-center gap-3">
              {state.confirmedBy ? (
                <Badge className="border-band-auto/40 text-band-auto">
                  <ShieldCheck className="h-3 w-3" /> confirmed by {state.confirmedBy}
                </Badge>
              ) : (
                <>
                  {state.remaining > 0 && (
                    <Badge className="border-band-review/40 bg-band-review/10 text-band-review">
                      {state.remaining} item{state.remaining > 1 ? "s" : ""} need review
                    </Badge>
                  )}
                  <Button onClick={finalize} disabled={state.remaining > 0}>
                    {state.remaining > 0 ? <Lock className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                    Finalize schema
                  </Button>
                </>
              )}
            </div>
          </div>

          {finalizeMsg && (
            <div className="mb-3 rounded border border-band-review/40 bg-band-review/10 p-2 text-xs text-band-review">
              {finalizeMsg}
            </div>
          )}

          <SchemaGraph schema={draft} onSelect={setSelectedTable} />

          <div className="mt-5">
            <DataGrid data={table.columns} columns={columns} />
          </div>
        </div>

        {/* right drawer: provenance chain */}
        {drawer && (
          <div className="w-80 shrink-0 overflow-auto border-l border-border bg-surface p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Provenance</h3>
              <button onClick={() => setDrawer(null)} className="text-subtle hover:text-fg"><X className="h-4 w-4" /></button>
            </div>
            <div className="space-y-3 text-xs">
              <div className="font-mono text-fg">{drawer.table}.{drawer.col.column}</div>
              <Field k="semantic_type" v={drawer.col.semantic_type} />
              <Field k="description" v={drawer.col.description} />
              <div className="flex items-center gap-2">
                <span className="w-28 text-subtle">confidence</span>
                <ConfidenceBadge confidence={drawer.col.confidence} band={bandOfColumn(drawer.col)} />
              </div>
              <Field k="provenance" v={drawer.col.provenance} />
              <Field k="pii" v={drawer.col.is_pii ? drawer.col.pii_kind : "none"} />
              <Field k="ml_role" v={drawer.col.ml_role} />
              <div>
                <div className="mb-1 text-subtle">confidence_sources</div>
                <pre className="overflow-x-auto rounded bg-bg p-2 font-mono text-2xs">
                  {JSON.stringify(drawer.col.confidence_sources, null, 2)}
                </pre>
              </div>
              <div className="text-2xs text-subtle">source: {draft.source_id} · kind: annotation</div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <span className="w-28 shrink-0 text-subtle">{k}</span>
      <span className="font-mono text-fg">{v}</span>
    </div>
  );
}
