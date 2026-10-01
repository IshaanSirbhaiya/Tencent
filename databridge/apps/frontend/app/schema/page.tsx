"use client";
import Link from "next/link";
import { Topbar } from "@/components/topbar";

// REMOVED 2026-10-01 (ROADMAP_KNOWLEDGE_PLATFORM §3 item 17). This page drove `/annotate`, which
// returned a draft built once at gateway boot from a hard-coded fintech fixture, whatever you
// asked it, and "finalized" it as `demo@databridge.io`. The real capability is a source's mapping
// approval: connect a source in the Builder's Adapter tab, and approve the crawl plan and its
// mapping there (gateway `/sources`), attributed to the signed-in principal.
export default function SchemaRemovedPage() {
  return (
    <>
      <Topbar title="Schema annotation (removed)" />
      <div className="min-w-0 flex-1 overflow-auto p-5">
        <div className="max-w-xl rounded border border-border bg-surface p-4 text-xs text-subtle" data-testid="legacy-removed">
          <p className="text-sm font-medium text-fg">This screen was retired.</p>
          <p className="mt-2">
            It annotated one canned schema that the gateway built at startup, regardless of input.
            Schemas are now mapped from the sources you actually connect.
          </p>
          <p className="mt-3">
            Go to{" "}
            <Link href="/builder" className="text-accent underline">Builder → Adapter</Link>{" "}
            to connect a source and approve its mapping, or{" "}
            <Link href="/ontology" className="text-accent underline">Ontology</Link>{" "}
            to review the node and edge kinds it proposes.
          </p>
        </div>
      </div>
    </>
  );
}
