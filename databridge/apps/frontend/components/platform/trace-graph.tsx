"use client";
/**
 * The communication graph itself — inline SVG, no graph library.
 *
 * What is drawn is the *authorisation path*: the participants in one request, and the messages
 * between them in the order they were sent. Every node and every edge is a button, because the
 * question this surface answers ("what was asked, what did policy decide, what came back, what did
 * it cost") is answered per message, not in aggregate.
 *
 * Deliberately not a force layout. Positions come from the payload via `lib/trace-layout.ts`, so
 * the same request draws the same picture twice and two requests can be compared side by side.
 */
import * as React from "react";
import { NODE_H, NODE_W, type PlacedNode, type RoutedEdge, type TraceLayout } from "@/lib/trace-layout";
import type { TraceEdgeKind } from "@/lib/platform-preview";

export type TraceSelection =
  | { type: "node"; id: string }
  | { type: "edge"; id: string }
  | null;

/** Stroke per message kind. Restrained on purpose (D-FE-3): the accent carries "a request moved",
 *  the confidence scale carries "policy had something to say", everything else is neutral. */
const EDGE_STROKE: Record<TraceEdgeKind, string> = {
  request: "stroke-accent",
  policy: "stroke-band-spot",
  ledger: "stroke-subtle",
  rows: "stroke-fg",
  answer: "stroke-accent",
  human: "stroke-band-spot",
};

/** Markers cannot inherit a parent's stroke, so there is one per colour and this is the mapping. */
const MARKER: Record<string, string> = {
  "stroke-accent": "arrow-accent",
  "stroke-band-spot": "arrow-spot",
  "stroke-band-review": "arrow-review",
  "stroke-subtle": "arrow-subtle",
  "stroke-fg": "arrow-fg",
};

const LABEL_FILL: Record<string, string> = {
  "stroke-accent": "fill-accent",
  "stroke-band-spot": "fill-band-spot",
  "stroke-band-review": "fill-band-review",
  "stroke-subtle": "fill-subtle",
  "stroke-fg": "fill-fg",
};

function strokeFor(edge: RoutedEdge): string {
  const decision = edge.detail?.decided?.decision;
  // A withheld or refused message is drawn in the refusal colour whatever kind it is: the decision
  // is the most important thing about that arrow.
  if (decision === "withheld" || decision === "refused") return "stroke-band-review";
  return EDGE_STROKE[edge.kind] ?? "stroke-subtle";
}

function EdgeShape({
  edge,
  state,
  selected,
  onSelect,
}: {
  edge: RoutedEdge;
  state: "past" | "current" | "future";
  selected: boolean;
  onSelect: () => void;
}) {
  const stroke = strokeFor(edge);
  const opacity = state === "future" ? 0.18 : state === "past" ? 0.75 : 1;
  const width = selected || state === "current" ? 2.4 : 1.4;

  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Step ${edge.step}: ${edge.from} to ${edge.to} — ${edge.label}`}
      className="cursor-pointer outline-none"
      opacity={opacity}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <title>{`${edge.step}. ${edge.label}`}</title>
      {/* A wide invisible copy of the path, so a 1.4px line is still clickable. */}
      <path d={edge.path} fill="none" stroke="transparent" strokeWidth={16} />
      <path
        d={edge.path}
        fill="none"
        className={stroke}
        strokeWidth={width}
        markerEnd={`url(#${MARKER[stroke]})`}
        strokeDasharray={edge.kind === "policy" ? "4 3" : undefined}
      />
      <g transform={`translate(${edge.mid.x}, ${edge.mid.y})`}>
        {state === "current" || selected ? (
          <>
            <rect
              x={-edge.label.length * 3.1 - 6}
              y={12}
              width={edge.label.length * 6.2 + 12}
              height={15}
              rx={3}
              className="fill-surface stroke-border"
              strokeWidth={1}
            />
            <text
              y={22.5}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-fg text-[9px] font-medium"
            >
              {edge.label}
            </text>
          </>
        ) : null}
        <circle
          r={9}
          className={`${selected ? "fill-accent" : "fill-surface"} ${stroke}`}
          strokeWidth={selected ? 2 : 1.2}
        />
        <text
          textAnchor="middle"
          dominantBaseline="central"
          className={`tnum text-[9px] font-semibold ${selected ? "fill-accent-fg" : LABEL_FILL[stroke]}`}
        >
          {edge.step}
        </text>
      </g>
    </g>
  );
}

function NodeShape({
  node,
  selected,
  dim,
  onSelect,
}: {
  node: PlacedNode;
  selected: boolean;
  dim: boolean;
  onSelect: () => void;
}) {
  const withheld = node.detail?.decided?.decision === "withheld";
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${node.label} — ${node.sublabel}`}
      className="cursor-pointer outline-none"
      opacity={dim ? 0.35 : 1}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <title>{node.detail?.summary ?? node.label}</title>
      <rect
        x={node.x}
        y={node.y}
        width={NODE_W}
        height={NODE_H}
        rx={7}
        className={`fill-surface ${
          selected ? "stroke-accent" : withheld ? "stroke-band-review" : "stroke-border"
        }`}
        strokeWidth={selected ? 2 : 1}
      />
      {/* The kind sits on the left rule as a tab, so the participant type is readable at a glance
          without a legend. */}
      <rect
        x={node.x}
        y={node.y}
        width={3}
        height={NODE_H}
        className={selected ? "fill-accent" : withheld ? "fill-band-review" : "fill-border"}
      />
      <text x={node.x + 14} y={node.y + 21} className="fill-fg text-[12px] font-semibold">
        {node.label}
      </text>
      <text x={node.x + 14} y={node.y + 37} className="fill-subtle text-[10px]">
        {node.sublabel}
      </text>
      <text x={node.x + 14} y={node.y + 50} className="fill-subtle text-[9px] uppercase tracking-wider">
        {node.kind}
      </text>
    </g>
  );
}

function Marker({ id, className }: { id: string; className: string }) {
  return (
    <marker
      id={id}
      viewBox="0 0 10 10"
      refX="9"
      refY="5"
      markerUnits="userSpaceOnUse"
      markerWidth="9"
      markerHeight="9"
      orient="auto-start-reverse"
    >
      <path d="M 0 0 L 10 5 L 0 10 z" className={className} />
    </marker>
  );
}

export function TraceGraph({
  layout,
  step,
  selection,
  onSelect,
}: {
  layout: TraceLayout;
  /** Steps up to and including this one are live; later ones are dimmed but never hidden — the
   *  shape of what is still to come is part of what the diagram says. */
  step: number;
  selection: TraceSelection;
  onSelect: (s: TraceSelection) => void;
}) {
  // Which participants have taken part by this step. A node nobody has talked to yet is dimmed.
  const active = React.useMemo(() => {
    const seen = new Set<string>();
    for (const e of layout.edges) {
      if (e.step <= step) {
        seen.add(e.from);
        seen.add(e.to);
      }
    }
    return seen;
  }, [layout.edges, step]);

  return (
    <div className="overflow-x-auto" data-testid="trace-graph">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        style={{ minWidth: 620, maxHeight: layout.height * 1.15 }}
        role="group"
        aria-label="Request authorisation path"
      >
        <defs>
          <Marker id="arrow-accent" className="fill-accent" />
          <Marker id="arrow-spot" className="fill-band-spot" />
          <Marker id="arrow-review" className="fill-band-review" />
          <Marker id="arrow-subtle" className="fill-subtle" />
          <Marker id="arrow-fg" className="fill-fg" />
        </defs>

        {layout.edges.map((e) => (
          <EdgeShape
            key={e.id}
            edge={e}
            state={e.step === step ? "current" : e.step < step ? "past" : "future"}
            selected={selection?.type === "edge" && selection.id === e.id}
            onSelect={() => onSelect({ type: "edge", id: e.id })}
          />
        ))}

        {layout.nodes.map((n) => (
          <NodeShape
            key={n.id}
            node={n}
            dim={!active.has(n.id)}
            selected={selection?.type === "node" && selection.id === n.id}
            onSelect={() => onSelect({ type: "node", id: n.id })}
          />
        ))}
      </svg>
    </div>
  );
}
