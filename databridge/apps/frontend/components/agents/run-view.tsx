"use client";
/**
 * One agent run (§5.7 record): the cited answer, what it could not support, what it proposed, and
 * every step with its policy decision and who produced it (§5.8 `produced_by`).
 *
 * Citation markers (`[p1]`) in the answer are the runtime's pointer refs. When the record carries
 * the runtime's explicit ref → pointer map, that map is the ONLY pairing used. Without it, the page
 * falls back to reading the answer's single terminal **Sources:** block against `answer.citations`
 * (`pairCitations`, strict) and labels every chip as text-paired, unverified. If neither holds, the
 * chip says so instead of pairing wrongly.
 *
 * UNSUPPORTED lines are styled only when each one is matched to a structured `unsupported_claims`
 * entry by reason and text (`verifiedFlagLines`); CANDIDATE lines get no special styling, since the
 * record carries no per-claim structure to corroborate them (R-019 round 3, B7).
 */
import * as React from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  agentsApi,
  citationRefFromLink,
  pairCitations,
  producerText,
  resolveCitations,
  verifiedFlagLines,
  type PointerJson,
  type RunAction,
  type RunRecord,
  type RunStep,
} from "@/lib/agents-api";
import { Collapsible, ErrorLine, Section, StatusPill, fmtTime } from "./shared";

// ============================================================================================
// citations
// ============================================================================================
/** ref → pointer pairing lives in `lib/agents-api.ts` (pure, unit-tested); re-exported here for
 *  callers that imported it from this module. */
export { pairCitations };

export function pointerWhere(p: PointerJson): string {
  const parts: string[] = [p.source_id, p.kind];
  const where = p.document_id || p.table || p.file_path || p.message_id;
  if (where) parts.push(String(where));
  if (p.column && p.row_id) parts.push(`${p.column} @ row ${p.row_id}`);
  if (p.char_start != null && p.char_end != null) parts.push(`chars ${p.char_start}–${p.char_end}`);
  if (p.capture_digest) parts.push(`sha256:${String(p.capture_digest).slice(0, 12)}…`);
  return parts.join(" · ");
}

/** `/evidence` is addressed by graph entity id today; a pointer is not one (reported gap). The link
 *  carries the pointer so the page can resolve it once it learns to; until then it opens the
 *  Provenance surface, where the source's register lives. */
export function evidenceHref(p: PointerJson): string {
  const q = new URLSearchParams({ source: p.source_id, pointer: JSON.stringify(p) });
  return `/evidence?${q.toString()}`;
}

function PointerDetail({
  refId,
  pointer,
  basis,
}: {
  refId: string;
  pointer: PointerJson | undefined;
  basis: "structured" | "text" | "none";
}) {
  return (
    <div data-testid={`citation-detail-${refId}`} className="rounded border border-accent/40 bg-accent/[0.05] p-2.5 text-2xs">
      <p className="font-mono text-fg">[{refId}]</p>
      {pointer ? (
        <>
          <p className="mt-1 text-fg">{pointerWhere(pointer)}</p>
          {basis === "text" && (
            <p data-testid={`citation-unverified-${refId}`} className="mt-1 text-band-spot">
              Paired by reading the answer&rsquo;s Sources list, not from a structured map the runtime recorded — treat
              this pairing as unverified.
            </p>
          )}
          <dl className="mt-1 grid grid-cols-[8rem_1fr] gap-x-2 font-mono text-subtle">
            {Object.entries(pointer)
              .filter(([, v]) => v !== null && v !== undefined && v !== "")
              .map(([k, v]) => (
                <React.Fragment key={k}>
                  <dt>{k}</dt>
                  <dd className="break-all text-fg">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                </React.Fragment>
              ))}
          </dl>
          <Link
            data-testid={`citation-evidence-${refId}`}
            href={evidenceHref(pointer)}
            className="mt-1.5 inline-block text-accent hover:underline"
          >
            Open in Provenance →
          </Link>
        </>
      ) : (
        <p className="mt-1 text-band-review">
          This ref could not be paired with a pointer: the answer&rsquo;s Sources list and the recorded
          citations do not line up, so no pointer is shown rather than a possibly wrong one.
        </p>
      )}
    </div>
  );
}

function AnswerMarkdown({ record, flagLines }: { record: RunRecord; flagLines: Set<number> }) {
  const { byRef, basis } = React.useMemo(() => resolveCitations(record.answer), [record.answer]);
  const aligned = basis !== "none";
  const [open, setOpen] = React.useState<string | null>(null);
  // `[p1]` → a link the renderer turns into a chip. `(?!\()` leaves real markdown links alone.
  const md = React.useMemo(
    () => record.answer.markdown.replace(/\[(p\d+)\](?!\()/g, "[$1](#cite-$1)"),
    [record.answer.markdown],
  );
  return (
    <div className="flex flex-col gap-2">
      <div data-testid="run-answer" className="prose-sm max-w-none text-xs leading-relaxed text-fg [&_blockquote]:border-l-2 [&_blockquote]:border-band-spot [&_blockquote]:pl-2 [&_li]:ml-4 [&_li]:list-disc [&_p]:my-1.5">
        <ReactMarkdown
          components={{
            a: ({ href, children }) => {
              const text = React.Children.toArray(children).join("");
              const ref = citationRefFromLink(href, text);
              if (href?.startsWith("#cite-") && !ref) {
                // `#cite-*` whose text is not exactly its ref: answer text trying to look like a
                // citation. Rendered as plain text — it is not a pointer.
                return <span data-testid="citation-forged">{children}</span>;
              }
              if (ref) {
                const known = ref in byRef;
                return (
                  <button
                    type="button"
                    data-testid={`citation-${ref}`}
                    onClick={() => setOpen((o) => (o === ref ? null : ref))}
                    className={cn(
                      "mx-0.5 rounded-sm border px-1 font-mono text-[10px] leading-4",
                      known ? "border-accent/50 text-accent hover:bg-accent/10" : "border-band-review/50 text-band-review",
                      open === ref && "bg-accent/15",
                    )}
                  >
                    {children}
                  </button>
                );
              }
              return (
                <a href={href} className="text-accent hover:underline">
                  {children}
                </a>
              );
            },
            // A line is styled as a flag ONLY when `verifiedFlagLines` matched it, by reason and
            // text, to a structured unsupported claim. Bold text alone — `**UNSUPPORTED**` or
            // `**CANDIDATE…**` written anywhere — is ordinary answer text.
            li: ({ node, children }) => {
              const line = node?.position?.start.line;
              if (line !== undefined && flagLines.has(line)) {
                return (
                  <li data-testid="flag-unsupported" className="rounded-sm bg-band-review/10 text-band-review">
                    {children}
                  </li>
                );
              }
              return <li>{children}</li>;
            },
          }}
        >
          {md}
        </ReactMarkdown>
      </div>
      {open && <PointerDetail refId={open} pointer={byRef[open]} basis={basis} />}
      {basis === "text" && (
        <p data-testid="citations-text-paired" className="text-2xs text-band-spot">
          This run record carries no structured citation map; chips are paired by reading the answer&rsquo;s own
          Sources list and are unverified.
        </p>
      )}
      {!aligned && record.answer.citations.length > 0 && (
        <p data-testid="citations-unaligned" className="text-2xs text-band-review">
          The recorded citations ({record.answer.citations.length}) and the answer&rsquo;s Sources list do not
          line up; citation chips will not show pointers.
        </p>
      )}
    </div>
  );
}

// ============================================================================================
// actions of this run (reverse lives here: only an EXECUTED action can be reversed)
// ============================================================================================
function RunActions({ agentId, record }: { agentId: string; record: RunRecord }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = React.useState<string | null>(null);
  const [note, setNote] = React.useState("");
  const reverse = useMutation({
    mutationFn: (aid: string) => agentsApi.reverse(record.run_id, aid, note),
    onSuccess: () => {
      setConfirming(null);
      setNote("");
      qc.invalidateQueries({ queryKey: ["agent-run", agentId, record.run_id] });
      qc.invalidateQueries({ queryKey: ["agent-runs", agentId] });
      qc.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
  if (!record.actions.length) return null;
  return (
    <Section title={`Actions (${record.actions.length})`} testId="run-actions">
      <ul className="flex flex-col gap-2">
        {record.actions.map((a: RunAction, i) => {
          // action ids are positional in the runtime's contract: a1 is actions[0] (runs.py).
          const aid = `a${i + 1}`;
          return (
            <li key={aid} data-testid={`run-action-${aid}`} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono text-subtle">{aid}</span>
              <span className="font-mono">{a.name}</span>
              <span className="font-mono text-2xs text-subtle">{a.automation_mode}</span>
              <StatusPill status={a.status} testId={`run-action-${aid}-status`} />
              {a.by && <span className="text-2xs text-subtle">by {a.by}</span>}
              {a.status === "executed" &&
                (confirming === aid ? (
                  <span className="flex items-center gap-1">
                    <input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="why (recorded)"
                      className="h-6 w-44 rounded border border-border bg-bg px-1.5 text-2xs"
                    />
                    <button
                      type="button"
                      data-testid={`reverse-confirm-${aid}`}
                      disabled={reverse.isPending}
                      onClick={() => reverse.mutate(aid)}
                      className="h-6 rounded border border-band-review/40 bg-band-review/15 px-2 text-2xs text-band-review"
                    >
                      Confirm reverse
                    </button>
                    <button type="button" onClick={() => setConfirming(null)} className="h-6 px-1 text-2xs text-subtle">
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    data-testid={`reverse-${aid}`}
                    onClick={() => setConfirming(aid)}
                    className="h-6 rounded border border-border px-2 text-2xs text-subtle hover:text-fg"
                  >
                    Reverse…
                  </button>
                ))}
            </li>
          );
        })}
      </ul>
      <div className="mt-2">
        <ErrorLine error={reverse.error} />
      </div>
    </Section>
  );
}

// ============================================================================================
// steps timeline
// ============================================================================================
function StepRow({ step, index }: { step: RunStep; index: number }) {
  const d = step.policy_decision;
  const decision = d.decision ?? "—";
  return (
    <li data-testid={`run-step-${index}`} className="relative border-l border-border pb-3 pl-4 last:pb-0">
      <span
        className={cn(
          "absolute -left-[4.5px] top-1 h-2 w-2 rounded-full",
          decision === "permit" ? "bg-band-auto" : decision === "refuse" ? "bg-band-review" : "bg-band-spot",
        )}
      />
      <div className="flex flex-wrap items-baseline gap-2 text-xs">
        <span className="font-mono text-subtle">{index + 1}</span>
        <span className="font-mono text-fg">{step.tool}</span>
        <span
          data-testid={`run-step-${index}-decision`}
          className={cn(
            "font-mono text-2xs",
            decision === "permit" ? "text-band-auto" : decision === "refuse" ? "text-band-review" : "text-band-spot",
          )}
        >
          {decision}
          {d.reason_code ? ` · ${d.reason_code}` : ""}
        </span>
        <span className="text-2xs tabular-nums text-subtle">{typeof step.ms === "number" ? `${step.ms} ms` : "— ms"}</span>
        <span className="text-2xs text-subtle">
          {Array.isArray(step.result_pointers) ? `${step.result_pointers.length} pointer(s)` : "pointers not recorded"}
        </span>
      </div>
      {d.detail && <p className="text-2xs text-subtle">{d.detail}</p>}
      <p data-testid={`run-step-${index}-produced-by`} className="text-2xs text-subtle">
        {step.produced_by ? <>by {producerText(step.produced_by)}</> : producerText(null)}
      </p>
      {Object.keys(step.args).length > 0 && (
        <details className="mt-0.5 text-2xs">
          <summary className="cursor-pointer text-subtle">arguments</summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded bg-muted p-1.5 font-mono">
            {JSON.stringify(step.args, null, 2)}
          </pre>
        </details>
      )}
    </li>
  );
}

// ============================================================================================
// the run
// ============================================================================================
export function RunView({ agentId, runId }: { agentId: string; runId: string }) {
  const q = useQuery({
    queryKey: ["agent-run", agentId, runId],
    queryFn: ({ signal }) => agentsApi.runRecord(agentId, runId, signal),
  });
  if (q.isLoading) return <p className="text-xs text-subtle">Loading run {runId}…</p>;
  if (q.error) return <ErrorLine error={q.error} testId="run-error" />;
  const record = q.data!.record;
  const reduced = record.coverage.skipped.length > 0;
  const unsupported = record.answer.unsupported_claims;
  // Correspondence per claim (reason + text), never by count (R-019 round 3, B7).
  const flags = verifiedFlagLines(record.answer.markdown, unsupported);
  const marksMatch = flags.verified;
  const flagLines = new Set(flags.lines);
  return (
    <div data-testid="run-view" data-run-id={record.run_id} className="flex flex-col gap-3">
      <header className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-fg">{record.run_id}</span>
        <StatusPill status={record.status} testId="run-status" />
        <span className="text-subtle">
          {fmtTime(record.started)} → {fmtTime(record.ended)}
        </span>
        <span className="text-subtle">
          trigger <span className="font-mono">{String(record.trigger.type ?? "?")}</span>
          {record.trigger.by ? <> by {String(record.trigger.by)}</> : null}
        </span>
        <Link
          data-testid="run-view-on-graph"
          href={`/graph?run=${encodeURIComponent(agentId)}/${encodeURIComponent(record.run_id)}`}
          className="text-accent hover:underline"
        >
          View on graph →
        </Link>
        {record.models_used.length > 0 && (
          <span data-testid="run-models" className="text-subtle">
            models <span className="font-mono">{record.models_used.join(", ")}</span>
          </span>
        )}
      </header>

      {reduced && (
        <div data-testid="run-reduced-coverage" className="flex gap-2 rounded border border-band-spot/40 bg-band-spot/[0.06] p-2.5 text-xs">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
          <div>
            <p className="font-medium text-band-spot">Reduced coverage — this answer may be incomplete.</p>
            <ul className="mt-1 list-disc pl-4 text-subtle">
              {(record.coverage.reasons.length ? record.coverage.reasons : record.coverage.skipped.map((s) => JSON.stringify(s))).map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {unsupported.length > 0 && (
        <div data-testid="run-unsupported" className="rounded border border-band-review/40 bg-band-review/[0.06] p-2.5 text-xs">
          <p className="font-medium text-band-review">
            {unsupported.length} claim{unsupported.length === 1 ? "" : "s"} not supported by the cited evidence
            {marksMatch ? " (kept in the answer, each marked UNSUPPORTED):" : " — listed from the run record:"}
          </p>
          {!marksMatch && (
            <p data-testid="run-unsupported-mismatch" className="mt-1 text-2xs text-band-review">
              The answer&rsquo;s inline UNSUPPORTED markers could not each be matched to a claim in the record, so they
              are not highlighted and are unverified — this list is the record.
            </p>
          )}
          <ul className="mt-1 list-disc pl-4 text-fg">
            {unsupported.map((c, i) => (
              <li key={i}>
                {c.text} <span className="font-mono text-2xs text-band-review">({c.reason})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Section
        title="Answer"
        testId="run-answer-section"
        aside={
          <span data-testid="answer-produced-by" className="text-2xs text-subtle">
            {record.answer.produced_by ? <>by {producerText(record.answer.produced_by)}</> : producerText(null)}
          </span>
        }
      >
        <AnswerMarkdown record={record} flagLines={flagLines} />
      </Section>

      <RunActions agentId={agentId} record={record} />

      <Section title={`Steps (${record.steps.length})`} testId="run-steps">
        {record.steps.length ? (
          <ol className="flex flex-col">
            {record.steps.map((s, i) => (
              <StepRow key={i} step={s} index={i} />
            ))}
          </ol>
        ) : (
          <p className="text-2xs text-subtle">This run recorded no steps.</p>
        )}
      </Section>

      <Collapsible title="Coverage (what was searched)">
        <p className="p-2.5 font-mono text-2xs text-subtle">
          {record.coverage.searched.length ? record.coverage.searched.join(", ") : "nothing recorded"}
        </p>
      </Collapsible>
    </div>
  );
}
