"use client";
/**
 * The evidence chain, rendered as a chain: value → pointer → captured snippet (the cited
 * characters highlighted) → source (uri, retrieved_at, content hash) → re-verify → the ingestion
 * run that brought it in, and what that source excluded. Neutral surface cards, the `2xs`/`tnum`
 * type scale and the app's status palette — the one page whose whole job is to be believed does
 * not get a look of its own.
 *
 * Nothing here decides what may be shown. The gateway releases an entity by pointer provenance or
 * by a committed ledger row, and says which (`authorisation`); this component renders the answer.
 */
import * as React from "react";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  FileStack,
  GitCommitHorizontal,
  HelpCircle,
  Link2,
  ListTree,
  Share2,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import type {
  EvidenceAttribute,
  EvidenceCapture,
  EvidenceIncidentEdge,
  EvidenceIngestion,
  EvidenceLedgerEntry,
  EvidencePointerEvidence,
  EvidenceSource,
  EvidenceTrace,
  EvidenceVerify,
} from "@/lib/evidence-api";
import { cn } from "@/lib/utils";
import { useNouns } from "@/lib/pack-context";
import { PointerJson } from "./pointer-json";
import { RoutePill } from "./route-pill";

function Stage({
  n,
  title,
  icon: Icon,
  last = false,
  children,
}: {
  n: number;
  title: string;
  icon: React.ElementType;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="relative pb-6 pl-9 last:pb-0">
      {!last && <div className="absolute left-[15px] top-8 bottom-0 w-px bg-border" />}
      <div className="absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full border border-border bg-surface">
        <Icon className="h-3.5 w-3.5 text-subtle" />
      </div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-2xs font-semibold uppercase tracking-wide text-subtle">
          Step {n}
        </span>
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function EntityBadge({ trace }: { trace: EvidenceTrace }) {
  if (trace.entity_type === "identity_claim") {
    // Deliberately NOT the accent pill every other entity gets (R-26).
    return (
      <span
        data-testid="evidence-claim-badge"
        className="rounded bg-band-review/15 px-1.5 py-0.5 text-2xs font-medium text-band-review"
      >
        unresolved identity claim · not a relationship
      </span>
    );
  }
  const label =
    trace.entity_type === "node"
      ? `graph node · ${trace.node?.kind}${trace.node?.meta_kind ? ` (${trace.node.meta_kind})` : ""}`
      : trace.entity_type === "edge"
        ? `graph edge · ${trace.edge?.kind}`
        : "committed ledger row";
  return (
    <span className="rounded bg-accent/15 px-1.5 py-0.5 text-2xs font-medium text-accent">
      {label}
    </span>
  );
}

function IdentityCaveat({ caveat }: { caveat: string }) {
  return (
    <p
      data-testid="evidence-identity-caveat"
      className="mb-3 flex items-start gap-2 rounded-lg border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-2xs text-band-review"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{caveat.replace(/\*\*/g, "")}</span>
    </p>
  );
}

// --- re-verify ------------------------------------------------------------------------------

const VERIFY_WORDS: Record<string, string> = {
  verified: "re-verified",
  hash_mismatch: "content changed since cited",
  value_not_in_span: "value not in cited span",
  capture_missing: "capture missing",
  span_out_of_range: "span outside the capture",
  no_text: "no extracted text captured",
  no_bytes: "bytes not captured",
  no_capture_binding: "no captured bytes to re-read",
};

/** The re-verify result, in words. Three tones: verified, failed, and "nothing to check" — the
 *  last is not a failure and must not look like one, nor like a pass. */
export function VerifyBadge({ verify }: { verify: EvidenceVerify }) {
  const failed = ["hash_mismatch", "value_not_in_span", "capture_missing", "span_out_of_range"];
  const tone =
    verify.status === "verified"
      ? "bg-band-auto/15 text-band-auto"
      : failed.includes(verify.status)
        ? "bg-band-review/15 text-band-review"
        : "bg-muted text-subtle";
  const Icon =
    verify.status === "verified" ? CheckCircle2 : failed.includes(verify.status) ? XCircle : HelpCircle;
  return (
    <span
      data-testid="evidence-verify"
      data-verify-status={verify.status}
      title={verify.detail}
      className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium", tone)}
    >
      <Icon className="h-3 w-3" />
      {VERIFY_WORDS[verify.status] ?? verify.status.replace(/_/g, " ")}
    </span>
  );
}

// --- the captured snippet ---------------------------------------------------------------------

function SnippetView({ capture }: { capture: EvidenceCapture }) {
  const s = capture.snippet;
  if (!s) {
    return (
      <p className="rounded border border-dashed border-border px-2 py-1.5 text-2xs text-subtle">
        The captured text could not be read back for this span — see the re-verify result.
      </p>
    );
  }
  return (
    <blockquote
      data-testid="evidence-snippet"
      className="whitespace-pre-wrap break-words rounded border border-border bg-muted/40 px-3 py-2 font-mono text-2xs leading-relaxed text-subtle"
    >
      {s.truncated_before && "…"}
      {s.before}
      <mark
        data-testid="evidence-snippet-match"
        className="rounded-sm bg-accent/25 px-0.5 font-semibold text-fg"
      >
        {s.match}
      </mark>
      {s.after}
      {s.truncated_after && "…"}
    </blockquote>
  );
}

function SourceMeta({ capture }: { capture: EvidenceCapture }) {
  const src = capture.source;
  const pairs: [string, React.ReactNode][] = [];
  if (src.title) pairs.push(["title", src.title]);
  if (src.uri) pairs.push(["uri", <code key="u" className="break-all">{src.uri}</code>]);
  if (src.source) pairs.push(["source", src.source]);
  if (src.retrieved_at) pairs.push(["retrieved", src.retrieved_at]);
  pairs.push(["content hash", <code key="h" className="break-all">{capture.capture_digest}</code>]);
  return <KeyValueGrid pairs={pairs} />;
}

/** One pointer, and what the disk says about it — the middle of the chain. */
function PointerEvidence({ evidence }: { evidence: EvidencePointerEvidence }) {
  return (
    <div className="flex flex-col gap-2">
      {evidence.capture && (
        <>
          <SnippetView capture={evidence.capture} />
          <SourceMeta capture={evidence.capture} />
        </>
      )}
      {evidence.table_cell && (
        <KeyValueGrid
          pairs={[
            ["source", evidence.table_cell.source],
            ["table", evidence.table_cell.table ?? "—"],
            ["column", evidence.table_cell.column ?? "—"],
            ["row", evidence.table_cell.row ?? "—"],
          ]}
        />
      )}
    </div>
  );
}

function KeyValueGrid({ pairs }: { pairs: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
      {pairs.map(([k, v]) => (
        <div className="contents" key={k}>
          <dt className="text-subtle">{k}</dt>
          <dd className="tnum break-all">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function renderValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function AttributeCard({ name, attr }: { name: string; attr: EvidenceAttribute }) {
  return (
    <div
      data-testid="evidence-attribute"
      className="mb-3 rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-subtle">{name}</span>
        <span className="font-medium">{renderValue(attr.value)}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs text-fg">{attr.pointer.kind}</span>
        <VerifyBadge verify={attr.verify} />
      </div>
      <PointerEvidence evidence={attr} />
    </div>
  );
}

function SourceCard({ source, index }: { source: EvidenceSource; index: number }) {
  return (
    <div
      data-testid="evidence-source"
      className="mb-3 rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="text-2xs font-semibold text-subtle">Pointer {index + 1}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs font-medium text-fg">
          {source.pointer.kind}
        </span>
        <RoutePill route={source.ingestion_route} />
        <VerifyBadge verify={source.verify} />
        {source.intake_id && (
          <span className="text-2xs text-subtle">
            intake <code className="text-fg">{source.intake_id}</code>
          </span>
        )}
      </div>
      <PointerEvidence evidence={source} />
      {(source.document || source.document_id) && (
        <div className="mt-2">
          <KeyValueGrid
            pairs={[
              [
                "document",
                source.document ? (
                  <>
                    {source.document.label}
                    <span className="ml-1.5 text-subtle">({source.document.kind})</span>
                  </>
                ) : (
                  <span className="text-subtle">{source.document_id} (not a graph node)</span>
                ),
              ],
            ]}
          />
        </div>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-2xs font-medium uppercase tracking-wide text-subtle">
          The pointer, unedited
        </summary>
        <div className="mt-1">
          <PointerJson pointer={source.pointer} />
        </div>
      </details>
    </div>
  );
}

function RelationCard({ relation }: { relation: EvidenceIncidentEdge }) {
  return (
    <div
      data-testid="evidence-relationship"
      className="mb-2 rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-subtle">{relation.direction === "outgoing" ? "→" : "←"}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs font-medium">{relation.kind}</span>
        <span className="font-medium">
          {relation.other_label ?? (
            <span className="text-subtle">
              {relation.other_kind ?? "entity"} · name withheld until confirmed
            </span>
          )}
        </span>
        <code className="text-2xs text-subtle">{relation.other_id}</code>
      </div>
      {relation.established_by.length > 0 && (
        <p className="mt-1 text-2xs text-band-auto">
          established by committed ledger row(s) {relation.established_by.join(", ")}
        </p>
      )}
    </div>
  );
}

function ClaimCard({ claim }: { claim: EvidenceIncidentEdge }) {
  const nouns = useNouns();
  return (
    <div
      data-testid="evidence-identity-claim"
      className="mb-2 rounded-lg border border-dashed border-band-review/50 bg-band-review/[0.04] p-3 last:mb-0"
    >
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="rounded bg-band-review/15 px-1.5 py-0.5 text-2xs font-medium text-band-review">
          claimed, not verified
        </span>
        <span className="font-medium">{claim.other_label ?? claim.other_id}</span>
        <code className="text-2xs text-subtle">{claim.other_id}</code>
      </div>
      <p className="mt-1 text-2xs text-subtle">
        {claim.candidate_phrase ?? "one of an unrecorded number of candidates"} · no committed
        ledger row names this pairing, so this system does not say it is the same {nouns.subject}
      </p>
    </div>
  );
}

function LedgerRowCard({ entry }: { entry: EvidenceLedgerEntry }) {
  const nouns = useNouns();
  return (
    <div
      data-testid="evidence-ledger-row"
      className="mb-3 rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <span className="rounded bg-band-auto/15 px-1.5 py-0.5 text-2xs font-medium text-band-auto">
          committed
        </span>
        <span className="text-2xs text-subtle">
          row <code className="text-fg">#{entry.rowid}</code>
        </span>
        {entry.supersedes !== null && (
          <span className="rounded bg-band-spot/15 px-1.5 py-0.5 text-2xs text-band-spot">
            supersedes #{entry.supersedes}
          </span>
        )}
      </div>
      <KeyValueGrid
        pairs={[
          ["field", entry.field_path],
          ["value", JSON.stringify(entry.value)],
          ["intake", entry.intake_id],
          ["confirmed by", <span key="c" className="font-medium">{entry.confirmed_by}</span>],
          ["confirmed at", entry.confirmed_at],
          ...(entry.selected_patient_id
            ? ([[`selected ${nouns.subject}`, entry.selected_patient_id]] as [string, React.ReactNode][])
            : []),
        ]}
      />
      <p className="mb-1 mt-2 text-2xs font-medium uppercase tracking-wide text-subtle">
        This row&rsquo;s own pointer
      </p>
      <PointerJson pointer={entry.pointer} />
    </div>
  );
}

function IngestionView({ ingestion }: { ingestion: EvidenceIngestion }) {
  return (
    <div className="flex flex-col gap-2" data-testid="evidence-ingestion">
      {ingestion.runs.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-3 py-3 text-2xs text-subtle">
          {ingestion.note}
        </p>
      ) : (
        <p className="text-xs">
          Brought in by{" "}
          {ingestion.runs.map((r, i) => (
            <React.Fragment key={`${r.source}/${r.run_id}`}>
              {i > 0 && ", "}
              <code>
                {r.source}/{r.run_id}
              </code>
            </React.Fragment>
          ))}
        </p>
      )}
      {ingestion.sources.map((s) => (
        <div
          key={s.source}
          data-testid="evidence-source-exclusions"
          className="rounded-lg border border-border bg-surface p-3 text-xs"
        >
          <p>
            Source <code>{s.source}</code> (latest run <code>{s.latest_run}</code>):{" "}
            <span className="tnum font-medium">{s.excluded}</span>{" "}
            {s.excluded === 1 ? "item" : "items"} excluded
            {s.excluded > 0 ? " — reasons:" : "."}
          </p>
          {s.exclusion_reasons.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-2xs text-subtle">
              {s.exclusion_reasons.map((r) => (
                <li key={r.reason}>
                  <span className="tnum text-fg">{r.n}</span> · {r.reason}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-2xs text-subtle">
            What was excluded is part of the answer: anything built from this source was built
            without those items.
          </p>
        </div>
      ))}
    </div>
  );
}

export function EvidenceTraceView({ trace }: { trace: EvidenceTrace }) {
  const nouns = useNouns();
  const attributes = Object.entries(trace.node?.attribute_evidence ?? trace.edge?.attribute_evidence ?? {});
  const hasNeighbours = trace.relationships.length > 0 || trace.identity_claims.length > 0;
  const showLedger =
    trace.authorisation.basis === "committed_ledger_row" || trace.ledger_entries.length > 0;
  // Step numbers move with the chain rather than being hardcoded, so a stage can be absent.
  const steps: string[] = ["entity"];
  if (attributes.length) steps.push("values");
  steps.push("sources");
  if (trace.ingestion) steps.push("ingestion");
  if (hasNeighbours) steps.push("neighbours");
  if (showLedger) steps.push("ledger");
  const n = (name: string) => steps.indexOf(name) + 1;
  const last = steps[steps.length - 1];

  return (
    <div className="flex flex-col gap-1">
      <Stage n={n("entity")} title="Entity" icon={GitCommitHorizontal} last={last === "entity"}>
        <div className="rounded-lg border border-border bg-surface p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <code className="text-xs font-medium">{trace.entity_id}</code>
            <EntityBadge trace={trace} />
          </div>
          {trace.claim && (
            <>
              {trace.identity_caveat && <IdentityCaveat caveat={trace.identity_caveat} />}
              <KeyValueGrid
                pairs={[
                  ["claimed by", trace.claim.src_id],
                  ["claimed to be", trace.claim.dst_id],
                  ["status", "no committed ledger row — awaiting a human"],
                  ["one of", trace.claim.candidate_phrase ?? "an unrecorded number of candidates"],
                ]}
              />
              <p className="mt-2 text-2xs text-subtle">
                This is what the document says, not what anyone knows. A named person checks{" "}
                {nouns.identifier} against the {nouns.subject}, in person, and selects a candidate;
                until then there is no relationship here to show.
              </p>
            </>
          )}
          {trace.node && (
            <KeyValueGrid
              pairs={[
                ["label", trace.node.label],
                ["canonical_id", trace.node.canonical_id ?? "—"],
                ["valid_from", trace.node.valid_from ?? "—"],
                ["valid_to", trace.node.valid_to ?? "still open"],
                ["system_from", trace.node.system_from ?? "—"],
                ["system_to", trace.node.system_to ?? "still believed"],
              ]}
            />
          )}
          {trace.edge && (
            <KeyValueGrid
              pairs={[
                ["src", trace.edge.src_id],
                ["dst", trace.edge.dst_id],
                ["valid_from", trace.edge.valid_from ?? "—"],
                ["valid_to", trace.edge.valid_to ?? "still open"],
                ...(trace.edge.established_by.length > 0
                  ? ([
                      ["established by", `ledger row(s) ${trace.edge.established_by.join(", ")}`],
                    ] as [string, React.ReactNode][])
                  : []),
              ]}
            />
          )}
          {trace.entity_type === "ledger_entry" && trace.ledger_entries[0] && (
            <p className="text-xs text-subtle">
              A row committed to the evidence ledger — the extracted value a named person attested
              to and this system wrote down.
            </p>
          )}
          <p
            data-testid="evidence-authorisation"
            data-basis={trace.authorisation.basis}
            className="mt-2 text-2xs text-subtle"
          >
            Released because {trace.authorisation.why}.
          </p>
          {trace.node && trace.node.unresolved.length > 0 && (
            <p className="mt-1 text-2xs text-band-spot">
              Not found in any source (named, never guessed): {trace.node.unresolved.join(", ")}
            </p>
          )}
        </div>
      </Stage>

      {attributes.length > 0 && (
        <Stage
          n={n("values")}
          title={`Values (${attributes.length}) — each with its own pointer`}
          icon={ListTree}
          last={last === "values"}
        >
          {attributes.map(([name, attr]) => (
            <AttributeCard key={name} name={name} attr={attr} />
          ))}
        </Stage>
      )}

      <Stage
        n={n("sources")}
        title={`What grounds the entity (${trace.sources.length})`}
        icon={FileStack}
        last={last === "sources"}
      >
        {trace.sources.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-subtle">
            This entity carries no SourcePointer, which should be impossible — the graph refuses
            to construct a node or edge without one (CLAUDE.md hard constraint 2).
          </p>
        ) : (
          trace.sources.map((s, i) => <SourceCard key={i} source={s} index={i} />)
        )}
      </Stage>

      {trace.ingestion && (
        <Stage
          n={n("ingestion")}
          title="Ingestion run & exclusions"
          icon={Archive}
          last={last === "ingestion"}
        >
          <IngestionView ingestion={trace.ingestion} />
        </Stage>
      )}

      {hasNeighbours && (
        <Stage
          n={n("neighbours")}
          title={`Relationships (${trace.relationships.length}) & unresolved claims (${trace.identity_claims.length})`}
          icon={Share2}
          last={last === "neighbours"}
        >
          {trace.identity_claims.length > 0 && (
            <div className="mb-4">
              <p className="mb-1.5 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-band-review">
                <HelpCircle className="h-3 w-3" />
                Claimed identity — not verified
              </p>
              {trace.identity_caveat && <IdentityCaveat caveat={trace.identity_caveat} />}
              {trace.identity_claims.map((c) => (
                <ClaimCard key={c.id} claim={c} />
              ))}
            </div>
          )}
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-subtle">
            Relationships
          </p>
          {trace.relationships.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-subtle">
              None. Nothing above has been settled by a human yet.
            </p>
          ) : (
            trace.relationships.map((r) => <RelationCard key={r.id} relation={r} />)
          )}
        </Stage>
      )}

      {showLedger && (
        <Stage n={n("ledger")} title="Committed ledger rows" icon={ShieldCheck} last>
          {trace.ledger_entries.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-subtle">
              Nothing has been committed for this entity yet. A value only reaches the ledger after
              a named person attests to an in-person identity check and confirms — the gate
              (CLAUDE.md hard constraints 1 &amp; 4) doing its job.
            </p>
          ) : (
            trace.ledger_entries.map((e) => <LedgerRowCard key={e.rowid} entry={e} />)
          )}
        </Stage>
      )}

      <div
        data-testid="evidence-limitation"
        className="mt-2 flex items-start gap-2 rounded-lg border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2 text-2xs text-band-spot"
      >
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {trace.limitation}
      </div>
    </div>
  );
}

export function EvidenceEmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center">
      <Link2 className="h-6 w-6 text-subtle" />
      <p className="text-sm font-medium">Trace a fact to its source</p>
      <p className="max-w-md text-xs text-subtle">
        Paste a graph node id (<code>kind:key</code>), a graph edge id, or a committed ledger row
        id (<code>ledger:12</code>), or pick a recent entity below. You will see each value, the
        pointer it carries, the captured characters it was read from, the source and its content
        hash, and whether it still re-verifies.
      </p>
    </div>
  );
}
