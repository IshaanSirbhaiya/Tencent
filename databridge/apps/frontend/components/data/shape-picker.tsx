"use client";
import * as React from "react";
import { cn } from "@/lib/utils";
import type { PlansResponse, ShapeRow } from "@/lib/data-api";

/** One sentence per shape, for the picker — what a consumer gets. Unknown shapes show their name. */
const WHAT: Record<string, string> = {
  tabular: "one row per entity, features cut at decision time, optional label",
  chunk_corpus: "retrieval chunks with their source span, for RAG",
  graph_tensor: "nodes, edges and a negative sample set, for a GNN",
  conversational: "dialogue turns, for fine-tuning",
  relational: "linked tables with keys",
  preference: "chosen / rejected pairs",
  olap: "a cube for analytics",
};

export function shapeRows(plans: PlansResponse | undefined): ShapeRow[] {
  return (plans?.plans ?? []).filter((p) => !p.receipt && typeof p.shape === "string") as ShapeRow[];
}

/**
 * Every shape the gateway lists — built ones selectable, the rest disabled with the gateway's own
 * reason. `null` = let the agent decide from the request.
 */
export function ShapePicker({
  plans,
  value,
  onChange,
  disabled,
}: {
  plans: PlansResponse | undefined;
  value: string | null;
  onChange: (shape: string | null) => void;
  disabled?: boolean;
}) {
  const rows = shapeRows(plans);
  return (
    <div data-testid="shape-picker" className="flex flex-wrap gap-1.5">
      <button
        type="button"
        data-testid="shape-option-auto"
        aria-pressed={value === null}
        disabled={disabled}
        onClick={() => onChange(null)}
        className={cn(
          "rounded border px-2 py-1 text-left text-2xs transition-colors",
          value === null ? "border-accent bg-accent/10 text-fg" : "border-border text-subtle hover:bg-muted",
        )}
      >
        <span className="font-medium">let the agent decide</span>
      </button>
      {rows.map((r) => (
        <button
          key={r.shape}
          type="button"
          data-testid={`shape-option-${r.shape}`}
          data-built={r.built ? "true" : "false"}
          aria-pressed={value === r.shape}
          disabled={disabled || !r.built}
          title={r.built ? WHAT[r.shape] ?? r.shape : `Not built: ${r.reason ?? "no reason given"}`}
          onClick={() => onChange(r.shape)}
          className={cn(
            "max-w-[260px] rounded border px-2 py-1 text-left text-2xs transition-colors",
            !r.built && "cursor-not-allowed border-dashed opacity-60",
            r.built && value === r.shape ? "border-accent bg-accent/10 text-fg" : "border-border text-subtle",
            r.built && value !== r.shape && "hover:bg-muted",
          )}
        >
          <span className="font-mono font-medium text-fg">{r.shape}</span>
          {r.built ? (
            WHAT[r.shape] ? <span className="block text-subtle">{WHAT[r.shape]}</span> : null
          ) : (
            <span className="block text-subtle" data-testid={`shape-reason-${r.shape}`}>
              not built — {r.reason ?? "no reason given"}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
