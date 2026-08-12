"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { brainApi } from "@/lib/brain-api";
import type { BrainAnswer, BrainGraph, IngestResult } from "@/lib/brain-types";
import { BrainHeader } from "@/components/brain/brain-header";
import { PurposeBanner } from "@/components/brain/purpose-banner";
import { DropZone } from "@/components/brain/drop-zone";
import { BrainGraph as GraphView } from "@/components/brain/brain-graph";
import { TimeSlider } from "@/components/brain/time-slider";
import { AskPanel } from "@/components/brain/ask-panel";
import { InsightsPanel } from "@/components/brain/insights-panel";
import { StatsPanel } from "@/components/brain/stats-panel";
import { MarkdownPanel } from "@/components/brain/markdown-panel";
import { Toast } from "@/components/brain/toast";
import { ResizeHandle, useBottomPanelHeight } from "@/components/brain/resize-handle";
import { NODE_STYLE } from "@/lib/brain-layout";
import { onDemoAction } from "@/lib/demo";

// The /brain route — the investor-facing Company Brain (T08). Renders backend truth only (D-FE-2):
// graph, grounded answers, server-computed insights, real counters. The "wow" is the live cited
// graph + the bi-temporal time-slider, not decoration.

function Legend() {
  // dark-scoped (the graph canvas is always near-black, theme-independent) — matches the
  // controls/minimap so a future light toggle can't turn this into a white box on black.
  return (
    <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-col gap-1 rounded border border-white/10 bg-black/70 px-2.5 py-2">
      {(Object.keys(NODE_STYLE) as (keyof typeof NODE_STYLE)[]).map((k) => (
        <div key={k} className="flex items-center gap-1.5 text-2xs text-white/60">
          <span
            className="h-2.5 w-2.5 rounded-full"
            style={{ background: NODE_STYLE[k].color, boxShadow: `0 0 5px ${NODE_STYLE[k].color}` }}
          />
          {NODE_STYLE[k].label}
        </div>
      ))}
    </div>
  );
}

// The insurer that issued most of the corpus — a real node in the clinic seed.
const MERIDIAN_NODE_ID = "org:meridian-life-assurance";

export default function BrainPage() {
  const graphQ = useQuery({ queryKey: ["brain-graph"], queryFn: brainApi.graph });
  const insightsQ = useQuery({ queryKey: ["brain-insights"], queryFn: () => brainApi.insights() });
  const healthQ = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 5000 });

  const graph: BrainGraph | undefined = graphQ.data;
  const [selectedNode, setSelectedNode] = React.useState<string | null>(null);
  const [focusNode, setFocusNode] = React.useState<string | null>(null);
  const [highlightNodes, setHighlightNodes] = React.useState<Set<string>>(new Set());
  const [highlightEdges, setHighlightEdges] = React.useState<Set<string>>(new Set());
  const [asOf, setAsOf] = React.useState<Date | null>(null);
  const [seeding, setSeeding] = React.useState(false);
  const [toast, setToast] = React.useState<string | null>(null);
  const [autoAsk, setAutoAsk] = React.useState<{ q: string; n: number } | undefined>(undefined);
  const [bottomHeight, setBottomHeight] = useBottomPanelHeight();

  // highlight a set of node ids + the edges that connect any two of them (backend edges only).
  const highlightFromNodes = React.useCallback(
    (ids: string[]) => {
      const set = new Set(ids);
      setHighlightNodes(set);
      const eids = new Set(
        (graph?.edges ?? []).filter((e) => set.has(e.source) && set.has(e.target)).map((e) => e.id),
      );
      setHighlightEdges(eids);
      setFocusNode(ids[0] ?? null);
    },
    [graph?.edges],
  );

  const onAnswer = React.useCallback(
    (a: BrainAnswer | null) => {
      if (!a) {
        setHighlightNodes(new Set());
        setHighlightEdges(new Set());
        return;
      }
      const ids = Array.from(new Set([...a.matched_entities, ...a.citations.map((c) => c.entity_id)]));
      highlightFromNodes(ids);
    },
    [highlightFromNodes],
  );

  const focusOne = React.useCallback(
    (id: string) => {
      highlightFromNodes([id]);
    },
    [highlightFromNodes],
  );

  // a dropped/ingested fixture → refetch the graph (new nodes animate in), highlight them, toast.
  const onIngested = React.useCallback(
    async (r: IngestResult) => {
      const { data } = await graphQ.refetch();
      await insightsQ.refetch();
      const g = data as BrainGraph | undefined;
      if (g && r.new_entities.length) {
        const ids = g.nodes.filter((n) => r.new_entities.includes(n.label)).map((n) => n.id);
        highlightFromNodes(ids);
      }
      setToast(
        r.new_entities.length
          ? `Found ${r.new_entities.length} new ${r.new_entities.length === 1 ? "entity" : "entities"}: ${r.new_entities.join(", ")}`
          : `${r.source_path} already in the brain — provenance corroborated`,
      );
    },
    [graphQ, insightsQ, highlightFromNodes],
  );

  const onLoadDemo = React.useCallback(async () => {
    setSeeding(true);
    try {
      await brainApi.seed();
      const { data } = await graphQ.refetch();
      await insightsQ.refetch();
      const g = data as BrainGraph | undefined;
      if (g) {
        setToast(
          `Clinic graph loaded — ${g.stats.nodes} nodes, ${g.stats.edges} edges from ${Object.keys(g.provenance_by_source).length} sources`,
        );
      }
    } finally {
      setSeeding(false);
    }
  }, [graphQ, insightsQ]);

  // automated /demo autoplay: perform the step the global DemoOverlay emits.
  React.useEffect(() => {
    return onDemoAction((action) => {
      if (action === "load") onLoadDemo();
      else if (action === "ask")
        setAutoAsk((prev) => ({ q: "What does the WELL2 package include?", n: (prev?.n ?? 0) + 1 }));
      else if (action === "slider") {
        // May 2026: the coverage rules and the test catalogue are already in force, but none of
        // the referrals have arrived yet — a genuinely meaningful "before" view. The old date
        // (Jan 2024) predated the entire clinic seed, so the slider rewound to an EMPTY graph.
        setAsOf(new Date("2026-05-01T00:00:00Z"));
        setTimeout(() => setAsOf(null), 4000);
      } else if (action === "node") {
        // A node that exists. This used to focus `person:sarah@acmecorp.com`, deleted with the
        // Acme seed in Phase 2 — so the step silently selected nothing.
        setSelectedNode(MERIDIAN_NODE_ID);
        setFocusNode(MERIDIAN_NODE_ID);
      }
    });
  }, [onLoadDemo]);

  const sourceCount = graph ? Object.keys(graph.provenance_by_source).length : 0;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <BrainHeader
        connected={healthQ.data?.status === "ok"}
        sourceCount={sourceCount}
        seeding={seeding}
        onLoadDemo={onLoadDemo}
      />
      <PurposeBanner />
      <DropZone onIngested={onIngested} />

      {/* HERO: the knowledge graph. Takes whatever height is left once the bottom row below claims
          its resizable share — `flex-1` rather than a fixed ratio, so the drag handle has something
          to actually trade against instead of two ratios that both stay fixed. */}
      <div className="relative min-h-0 flex-1 border-b border-border bg-bg">
        <Legend />
        {graph ? (
          <GraphView
            graph={graph}
            highlightNodes={highlightNodes}
            highlightEdges={highlightEdges}
            asOf={asOf}
            focusNode={focusNode}
            onNodeClick={(id) => {
              setSelectedNode(id);
              setFocusNode(id);
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-subtle">
            {graphQ.isError ? "Gateway offline — start apps/gateway." : "Loading the brain…"}
          </div>
        )}
      </div>

      {/* The slider's domain is derived from this graph's own valid_from/valid_to. It used to be
          hardcoded to Jan–Jun 2024, two years before the clinic seed, so any drag blanked the
          canvas. */}
      <TimeSlider graph={graph} onChange={setAsOf} />

      {/* T-32: draggable/keyboard-resizable boundary — see components/brain/resize-handle.tsx. */}
      <ResizeHandle height={bottomHeight} onResize={setBottomHeight} />

      {/* BOTTOM: height is the user's own drag choice (persisted), not a ratio.
          The ask panel gets TWO of four columns. It was one of three, so a grounded answer —
          which is prose, not a stat — was rendered into a 319×54px box: the reply arrived, was
          correct, and was invisible without scrolling a two-line window. It read as "the LLM does
          not respond", and it is the single most important thing on this screen. */}
      <div
        className="grid shrink-0 grid-cols-4 gap-px overflow-hidden bg-border"
        style={{ height: bottomHeight }}
      >
        <div className="col-span-2 overflow-hidden bg-bg p-4">
          <AskPanel onAnswer={onAnswer} autoAsk={autoAsk} graph={graph} />
        </div>
        <div className="overflow-hidden bg-bg p-4">
          <InsightsPanel insights={insightsQ.data?.insights} onFocus={(id) => focusOne(id)} />
        </div>
        <div className="overflow-hidden bg-bg p-4">
          <StatsPanel graph={graph} />
        </div>
      </div>

      <MarkdownPanel
        nodeId={selectedNode}
        onClose={() => setSelectedNode(null)}
        onNavigate={(id) => {
          // clicking a [[wikilink]] navigates the graph to that node AND opens its note (Obsidian)
          setSelectedNode(id);
          setFocusNode(id);
          focusOne(id);
        }}
      />
      {toast ? <Toast message={toast} onClose={() => setToast(null)} /> : null}
    </div>
  );
}