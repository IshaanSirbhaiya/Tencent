"use client";
/**
 * `/graph` — the knowledge graph itself, as nodes and edges a person can look at.
 *
 * Everything else in this app describes the graph without showing it: `/trace` is agent-to-adapter
 * messages, `/ontology` is which kinds may be declared, `/evidence` is one pointer at a time. This
 * is the artefact the whole system exists to produce, made visible — see
 * `components/graph/graph-view.tsx` for the surface itself and
 * `apps/gateway/databridge_gateway/graph_routes.py` for how it stays bounded at estate scale.
 *
 * `?run=<agent>/<run_id>` draws that agent run's graph traversal on the canvas. Reading the query
 * string needs a Suspense boundary under the app router.
 */
import { Suspense } from "react";
import { Topbar } from "@/components/topbar";
import { GraphView } from "@/components/graph/graph-view";

export default function GraphPage() {
  return (
    <>
      <Topbar title="Knowledge graph" />
      <Suspense fallback={<p className="p-6 text-xs text-subtle">Loading…</p>}>
        <GraphView />
      </Suspense>
    </>
  );
}
