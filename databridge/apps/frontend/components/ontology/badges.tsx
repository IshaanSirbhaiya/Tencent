"use client";
/**
 * Small presentational badges shared across the ontology surface.
 *
 * `MetaKindBadge` is the one that matters most: it is the ONLY place on this screen that decides
 * whether a meta-kind reads as "identity-bearing", and it decides that from the catalogue the
 * gateway serves (`meta_kinds` on `GET /ontology`, computed from `databridge_graph.meta`) — never
 * from a name compared against a literal here. A hardcoded list of "the scary meta-kinds" is
 * exactly the kind of copy that drifts from the engine the day someone adds a second attested kind.
 */
import * as React from "react";
import { Lock, ShieldAlert, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import type { EdgeMetaKindInfo, NodeMetaKindInfo } from "@/lib/ontology-api";

export function MetaKindBadge({
  value,
  info,
}: {
  value: string;
  info?: EdgeMetaKindInfo | NodeMetaKindInfo;
}) {
  const identityBearing =
    info && "attested" in info ? info.attested || info.claim : false;
  const attested = info && "attested" in info ? info.attested : false;
  return (
    <span
      title={
        attested
          ? "Binds to an ATTESTED meta-kind — a determination, written only behind a named human's in-person attestation."
          : identityBearing
            ? "Binds to a CLAIM meta-kind — an artefact's claim about which subject it concerns, never a determination."
            : `Binds to the domain-free meta-kind ${value}.`
      }
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-2xs",
        identityBearing
          ? "border-band-review/40 bg-band-review/[0.07] text-band-review"
          : "border-border bg-muted text-subtle",
      )}
    >
      {identityBearing ? <ShieldAlert className="h-3 w-3 shrink-0" /> : null}
      {value}
    </span>
  );
}

export function SourceBadge({ source }: { source: string }) {
  const predefined = source === "predefined";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs",
        predefined ? "border-border text-subtle" : "border-accent/40 text-accent",
      )}
    >
      {predefined ? null : <Sparkles className="h-3 w-3 shrink-0" />}
      {predefined ? "predefined" : "agent-proposed"}
    </span>
  );
}

export function SubjectBadge() {
  return (
    <span
      title="A SUBJECT (spine D4b): the identity this system may never auto-confirm. Candidates are presented; a named human attests in person. Name similarity never merges two of these."
      className="inline-flex items-center gap-1 rounded border border-band-review/40 bg-band-review/[0.07] px-1.5 py-0.5 text-2xs font-medium text-band-review"
    >
      <ShieldAlert className="h-3 w-3 shrink-0" />
      subject
    </span>
  );
}

export function DeprecatedBadge({ by, at }: { by?: string | null; at?: string | null }) {
  return (
    <span
      title={by ? `Deprecated by ${by}${at ? ` · ${new Date(at).toLocaleString()}` : ""}` : "Deprecated"}
      className="inline-flex items-center gap-1 rounded border border-border bg-muted px-1.5 py-0.5 text-2xs text-subtle line-through decoration-subtle/60"
    >
      deprecated
    </span>
  );
}

export function LockedTag({ reason }: { reason?: string | null }) {
  return (
    <span
      title={reason ?? "Structural — the engine depends on it. Not editable through this screen."}
      className="inline-flex shrink-0 items-center gap-1 text-2xs text-subtle"
    >
      <Lock className="h-3 w-3 shrink-0" />
      fixed
    </span>
  );
}
