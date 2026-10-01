"use client";
/**
 * A `/brain/query` answer that was DELEGATED across sources (E2E run 2, N-14).
 *
 * The gateway sent the question to the federated service: adapter agents read their own sources
 * under the caller's grant, a deterministic engine combined the rows, and every value in the table
 * is a `derived` pointer that expands to exactly the input cells it was computed from. This panel
 * renders that and nothing else — it computes nothing, re-orders nothing, and invents no row.
 *
 * Shown, in order: coverage (a reduced-coverage warning comes FIRST — a total over part of the
 * evidence is the thing a reader must not miss), candidate flags (rows that concern people are
 * candidates; a human selects), the narrated claims, the table with each cell's citations
 * expandable, and a link to the run on the Activity page. All text is rendered as React text
 * nodes, so nothing from the payload is interpreted as markup.
 */
import * as React from "react";
import Link from "next/link";
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, Users } from "lucide-react";
import type {
  BrainRoute,
  DelegatedAnswer,
  DelegatedCell,
  DelegatedPointer,
  DelegationAttempt,
} from "@/lib/brain-types";
import { Grounded } from "@/components/grounded";
import { cn } from "@/lib/utils";

/** Only the app's own Activity page is linked; anything else the payload carried is not a link. */
function safeActivityHref(url: string | null | undefined): string | null {
  return url && url.startsWith("/trace?") ? url : null;
}

function fmt(v: DelegatedCell["value"]): string {
  return v === null || v === undefined ? "—" : String(v);
}

/** One pointer, and — for a derived value — the pointers it was computed from, one level at a
 *  time. Bounded: a total over 2,000 cells does not render 2,000 rows in a side panel. */
function PointerTree({ p, depth = 0 }: { p: DelegatedPointer; depth?: number }) {
  const [open, setOpen] = React.useState(false);
  const kids = p.derived_from ?? [];
  const SHOW = 12;
  return (
    <li className="text-2xs">
      <div className="flex items-center gap-1">
        {kids.length > 0 && depth < 4 ? (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="text-subtle hover:text-fg"
            aria-label={open ? "Hide inputs" : "Show inputs"}
          >
            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          </button>
        ) : (
          <span className="inline-block w-3" />
        )}
        <Grounded pointer={p}>
          <span className="font-mono text-accent">{p.kind}</span>
        </Grounded>
        <span className="text-subtle">{p.source_id}</span>
        {p.derivation ? <span className="font-mono text-fg">{String(p.derivation)}</span> : null}
        {p.agg_window ? <span className="text-subtle">· {String(p.agg_window)}</span> : null}
        {p.row_count != null ? <span className="text-subtle">· {String(p.row_count)} row(s)</span> : null}
        {p.table && p.column ? (
          <span className="font-mono text-subtle">
            {String(p.table)}.{String(p.column)}
            {p.row_id ? `[${String(p.row_id)}]` : ""}
          </span>
        ) : null}
      </div>
      {open && kids.length > 0 ? (
        <ul className="ml-4 mt-0.5 space-y-0.5 border-l border-border pl-2">
          {kids.slice(0, SHOW).map((k, i) => (
            <PointerTree key={i} p={k} depth={depth + 1} />
          ))}
          {kids.length > SHOW ? (
            <li className="text-2xs text-subtle">
              +{kids.length - SHOW} more input(s) — the full list is in the run on the Activity page
            </li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}

function CellView({ cell }: { cell: DelegatedCell | null | undefined }) {
  const [open, setOpen] = React.useState(false);
  if (!cell) return <span className="text-subtle">—</span>;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 font-mono text-fg hover:text-accent"
        title="Show the citations this value was computed from"
      >
        {fmt(cell.value)}
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
      </button>
      {open ? (
        <ul className="mt-1 space-y-0.5">
          <PointerTree p={cell.pointer} />
        </ul>
      ) : null}
    </div>
  );
}

export function RouteNote({ route }: { route?: BrainRoute }) {
  if (!route) return null;
  return (
    <p className="text-2xs text-subtle" data-testid="brain-route">
      Routed to <span className="font-semibold text-fg">{route.route}</span>
      {route.decided_by ? ` by ${route.decided_by}` : ""}
      {route.rule ? ` (${route.rule})` : ""}
      {route.reason ? ` — ${route.reason}` : ""}
    </p>
  );
}

/** Delegation was tried and read nothing, or was not permitted: say so beside the graph answer. */
export function DelegationNote({
  route,
  attempt,
}: {
  route?: BrainRoute;
  attempt?: DelegationAttempt;
}) {
  const refused = route?.delegation_refused;
  const href = safeActivityHref(attempt?.activity_url);
  if (!refused && !attempt && !route?.delegation_unavailable && !route?.delegation_failed) return null;
  return (
    <div className="space-y-1 rounded border border-border bg-surface px-3 py-2 text-2xs text-fg">
      {refused ? <p>Not delegated: {refused.message}</p> : null}
      {route?.delegation_unavailable ? (
        <p>Delegation unavailable: {route.delegation_unavailable}</p>
      ) : null}
      {route?.delegation_failed ? <p>Delegation failed: {route.delegation_failed}</p> : null}
      {attempt ? (
        <>
          <p>
            Delegation read nothing — the planner asked instead of guessing, so the graph answered
            what it could:
          </p>
          <ul className="list-disc pl-4">
            {attempt.questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
          {href ? (
            <Link href={href} className="inline-flex items-center gap-1 text-accent hover:underline">
              The attempt on the Activity page <ExternalLink className="h-3 w-3" />
            </Link>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function DelegatedAnswerView({ answer }: { answer: DelegatedAnswer }) {
  const columns = answer.table?.columns ?? [];
  const rows = answer.table?.rows ?? [];
  const cov = answer.coverage;
  const href = safeActivityHref(answer.activity_url);
  const rowFlags = new Set(
    answer.candidate_flags.filter((f) => f.where === "table_row").map((f) => f.row),
  );
  const claimFlags = answer.candidate_flags.filter((f) => f.where === "claim");

  return (
    <div className="space-y-3" data-testid="brain-delegated-answer">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-accent/30 bg-accent/[0.08] px-2 py-0.5 text-2xs text-accent">
          Delegated across sources
        </span>
        {href ? (
          <Link
            href={href}
            className="inline-flex items-center gap-1 text-2xs text-accent hover:underline"
            data-testid="brain-activity-link"
          >
            Run {answer.run_id} on the Activity page <ExternalLink className="h-3 w-3" />
          </Link>
        ) : null}
      </div>
      <RouteNote route={answer.route} />

      {cov?.reduced ? (
        <div
          className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2"
          data-testid="brain-reduced-coverage"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
          <div className="space-y-1 text-xs text-fg">
            <p className="font-semibold">Reduced coverage — this answer did not read everything.</p>
            <ul className="list-disc pl-4">
              {cov.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
      {cov?.searched?.length ? (
        <p className="text-2xs text-subtle">Read: {cov.searched.join(", ")}</p>
      ) : null}

      {answer.requires_human_selection ? (
        <div
          className="flex items-start gap-2 rounded border border-band-review/30 bg-band-review/[0.06] px-3 py-2"
          data-testid="brain-candidate-flags"
        >
          <Users className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
          <div className="space-y-1 text-xs text-fg">
            <p className="font-semibold">
              Candidates — {rowFlags.size} row(s) and {claimFlags.length} claim(s) concern people
              found in the data. They are candidates, not identifications: a human selects.
            </p>
          </div>
        </div>
      ) : null}

      {answer.questions.length > 0 ? (
        <div className="space-y-1 text-xs text-fg">
          <p className="font-semibold">Before reading anything, the planner needs to know:</p>
          <ul className="list-disc pl-4">
            {answer.questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {answer.claims.length > 0 ? (
        <ul className="space-y-1">
          {answer.claims.map((c, i) => (
            <li key={i} className="text-sm leading-relaxed text-fg">
              {c.text}
              {c.citations.length ? (
                <span className="ml-1 font-mono text-2xs text-subtle">[{c.citations.join(", ")}]</span>
              ) : null}
              {!c.supported ? (
                <span className="ml-1 text-2xs text-band-review">(unsupported{c.reason ? `: ${c.reason}` : ""})</span>
              ) : null}
              {c.requires_human_selection ? (
                <span className="ml-1 text-2xs text-band-review">(candidate — a human selects)</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : !answer.grounded ? (
        <p className="text-xs text-fg">{answer.refusal || "No table could be computed."}</p>
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-left text-xs" data-testid="brain-delegated-table">
            <thead className="bg-surface text-2xs uppercase tracking-wide text-subtle">
              <tr>
                {columns.map((c) => (
                  <th key={c} className="px-2 py-1 font-medium">
                    {c}
                  </th>
                ))}
                <th className="px-2 py-1" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={i}
                  className={cn("border-t border-border align-top", rowFlags.has(i) && "bg-band-review/[0.06]")}
                >
                  {columns.map((c) => (
                    <td key={c} className="px-2 py-1">
                      <CellView cell={r.cells[c]} />
                    </td>
                  ))}
                  <td className="px-2 py-1 text-2xs text-band-review">
                    {r.candidate ? "candidate" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {(answer.table?.excluded ?? []).length > 0 ? (
        <div className="text-2xs text-subtle">
          Not counted:
          <ul className="list-disc pl-4">
            {(answer.table.excluded ?? []).map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
