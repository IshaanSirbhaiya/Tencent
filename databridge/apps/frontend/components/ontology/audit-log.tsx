"use client";
/**
 * The append-only record of every change to this pack's vocabulary — who (the authenticated
 * principal, and the name they gave), when, what, and why. Served by `GET /ontology/audit`, which
 * reads `ontology_audit.jsonl`; nothing on this page can edit or remove a line of it.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { History, ShieldAlert } from "lucide-react";
import { fetchAudit, OntologyApiError, type AuditEntry } from "@/lib/ontology-api";

function describe(e: AuditEntry): string {
  const kind = e.kind ? ` ${e.axis === "edge" ? "relation" : "entity kind"} ${e.kind}` : "";
  switch (e.action) {
    case "define":
      return `defined${kind}`;
    case "rename":
      return `renamed${kind} → ${e.new_name}`;
    case "set_endpoints":
      return `changed the endpoints of${kind}`;
    case "deprecate":
      return `deprecated${kind}`;
    case "reactivate":
      return `reactivated${kind}`;
    case "propose":
      return `proposed${kind}`;
    case "approve_proposal":
      return `approved the proposal for${kind}`;
    case "reject_proposal":
      return `rejected the proposal for${kind}`;
    default:
      return `${e.action.replace(/_/g, " ")}${kind}`;
  }
}

export function AuditLog({ pack }: { pack: string }) {
  const q = useQuery({
    queryKey: ["ontology-audit", pack],
    queryFn: ({ signal }) => fetchAudit(pack, signal),
    retry: false,
  });
  const entries = q.data?.entries ?? [];

  return (
    <section data-testid="ontology-audit">
      <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-subtle">
        <History className="h-3.5 w-3.5" /> Change history
      </h2>
      {q.isError ? (
        <p className="text-2xs text-subtle">
          {q.error instanceof OntologyApiError && q.error.status === 401
            ? "The change history needs a signed-in gateway token."
            : "Could not load the change history."}
        </p>
      ) : entries.length === 0 ? (
        <p className="text-2xs text-subtle">No changes recorded for this pack yet.</p>
      ) : (
        <ol className="flex flex-col divide-y divide-border rounded border border-border bg-surface">
          {entries.map((e, i) => (
            <li key={`${e.at}-${i}`} className="flex flex-wrap items-baseline gap-x-2 px-3 py-1.5 text-2xs">
              <span className="font-mono text-subtle">{new Date(e.at).toLocaleString()}</span>
              <span className="text-fg">
                {e.by || e.principal} {describe(e)}
              </span>
              {e.identity_confirmed ? (
                <span className="inline-flex items-center gap-0.5 text-band-review">
                  <ShieldAlert className="h-3 w-3" /> identity confirmed
                </span>
              ) : null}
              {e.reason ? <span className="text-subtle">— {e.reason}</span> : null}
              <span className="ml-auto text-subtle/70">as {e.principal}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
