"use client";
import Link from "next/link";
import { Topbar } from "@/components/topbar";

// REMOVED 2026-10-01 (ROADMAP_KNOWLEDGE_PLATFORM §3 item 17). This page drove `/ask`, which looked
// the question up in a dictionary built at gateway boot from a fixed fintech question list and
// answered anything else with QUERY_NOT_IN_LIBRARY. Asking now goes to the real graph:
// `/brain/query` (the Brain page) and the agents on the Intelligence page.
export default function AskRemovedPage() {
  return (
    <>
      <Topbar title="Ask (removed)" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        <div className="max-w-xl rounded border border-border bg-surface p-4 text-xs text-subtle" data-testid="legacy-removed">
          <p className="text-sm font-medium text-fg">This screen was retired.</p>
          <p className="mt-2">
            It replayed answers to a fixed list of questions prepared when the server started; it
            could not answer anything else.
          </p>
          <p className="mt-3">
            Ask the knowledge graph on{" "}
            <Link href="/intelligence" className="text-accent underline">Intelligence</Link>{" "}
            or the{" "}
            <Link href="/brain" className="text-accent underline">Brain</Link> page — both answer
            from what your connected sources put in the graph, with a pointer on every fact.
          </p>
        </div>
      </div>
    </>
  );
}
