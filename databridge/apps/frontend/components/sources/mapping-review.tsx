"use client";
/**
 * LINK7: the mapping decisions a person still has to make, beside the table mapping.
 *
 *   - Proposed links (C-3): a column no foreign key declares whose values are, by measurement, the
 *     keys of another mapped table (this source or another). Each shows its evidence — the column
 *     pair, how many values matched, a few matched keys — and Approve / Reject as the signed-in
 *     principal. A link into a kind that stands for people is never offered (the gateway refuses
 *     it too), and its keys are never shown.
 *   - Tables that need approval again: shown in the mapping table itself (see `ReapprovalNote`).
 *   - UI8: a table's per-row KIND SPLIT (FIN-04 / ID7), shown in its row before it is approved
 *     (see `KindSplitView`): the discriminator column, each value → kind with its row count, and
 *     the rows that stay the subject. Approving the table approves the split.
 */
import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  linkApprovable,
  linksForReview,
  sourcesApi,
  splitGroups,
  type KindSplitReview,
  type LinkReview,
  type MappingReview,
  type Reapproval,
  type SourcesFailure,
  type SourcesResult,
} from "@/lib/sources-api";
import { AuthChip, DevTokenMark } from "@/components/auth-chip";
import { FailureNotice, StatusBadge, fmtTime } from "./common";

function Evidence({ link }: { link: LinkReview }) {
  const e = link.evidence;
  return (
    <div className="space-y-0.5 text-2xs text-subtle" data-testid={`link-evidence-${link.table}-${link.column}`}>
      <div>
        <span className="tnum">{e.matched ?? "?"}</span> of <span className="tnum">{e.with_value ?? "?"}</span> values of{" "}
        <code className="font-mono">{link.column}</code> are keys of{" "}
        <code className="font-mono">
          {link.target_table}.{link.target_key ?? "?"}
        </code>
        {e.distinct_matched != null && e.target_keys != null && (
          <>
            {" "}
            (<span className="tnum">{e.distinct_matched}</span> distinct, of <span className="tnum">{e.target_keys}</span> keys
            in the target)
          </>
        )}
        .
      </div>
      {e.samples && e.samples.length > 0 ? (
        <div data-testid={`link-samples-${link.table}-${link.column}`}>
          Matched keys, for example:{" "}
          {e.samples.map((s) => (
            <code key={s} className="mr-1 rounded bg-muted px-1 font-mono">
              {s}
            </code>
          ))}
        </div>
      ) : (
        e.samples_note && <div>Matched keys {e.samples_note}</div>
      )}
      {link.alternatives.length > 0 && <div>Equally good other targets: {link.alternatives.join(", ")}.</div>}
    </div>
  );
}

function LinkRow({
  sourceId,
  link,
  onDone,
}: {
  sourceId: string;
  link: LinkReview;
  onDone: () => void;
}) {
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);
  const [reason, setReason] = React.useState("");
  const body = { table: link.table, column: link.column, target: link.target };
  const decide = useMutation({
    mutationFn: async (kind: "approve" | "reject"): Promise<SourcesResult<unknown>> =>
      kind === "approve" ? sourcesApi.approveLink(sourceId, body) : sourcesApi.rejectLink(sourceId, { ...body, reason }),
    onSuccess: (r) => {
      if (r.ok) {
        setFailure(null);
        onDone();
      } else setFailure(r);
    },
  });
  const canApprove = linkApprovable(link);
  const pending = link.status === "proposed";
  return (
    <li className="border-b border-border py-2 last:border-0" data-testid={`link-${link.table}-${link.column}`} data-status={link.status}>
      <div className="flex flex-wrap items-baseline gap-2 text-xs">
        <code className="font-mono text-2xs">
          {link.table}.{link.column}
        </code>
        <span className="text-subtle">→</span>
        <code className="font-mono text-2xs">
          {link.target_source}:{link.target_table}
          {link.target_key ? `.${link.target_key}` : ""}
        </code>
        {link.edge_kind && (
          <span className="text-2xs text-subtle">
            as <span className="font-mono">{link.edge_kind}</span>
          </span>
        )}
        <StatusBadge status={link.status} />
      </div>
      <div className="mt-1">
        <Evidence link={link} />
      </div>
      {link.person_target && (
        <p className="mt-1 text-2xs text-band-spot" data-testid={`link-person-${link.table}-${link.column}`}>
          Not offered: <span className="font-mono">{link.target_kind}</span> records stand for people, and an inferred
          link never points at a person record.
        </p>
      )}
      {!link.person_target && link.status === "not_proposed" && link.why && (
        <p className="mt-1 text-2xs text-subtle">Not offered: {link.why}</p>
      )}
      {(link.status === "approved" || link.status === "rejected") && (
        <p className="mt-1 text-2xs text-subtle" data-testid={`link-decided-${link.table}-${link.column}`}>
          {link.status === "approved" ? "Approved" : "Rejected"} by <strong>{link.decided_by ?? "?"}</strong>
          {link.decided_at && <> on {fmtTime(link.decided_at)}</>}
          {link.decided_dev_only === true && <DevTokenMark testId={`link-decided-dev-${link.table}-${link.column}`} />}
          {link.reject_reason && <> — {link.reject_reason}</>}.
          {link.status === "approved" && link.table_status !== "approved" && (
            <> The link is now an edge of the table&apos;s mapping; approve the table to build it.</>
          )}
        </p>
      )}
      {pending && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            data-testid={`link-approve-${link.table}-${link.column}`}
            disabled={decide.isPending || !canApprove}
            onClick={() => decide.mutate("approve")}
          >
            Approve link
          </Button>
          <input
            className="h-7 w-56 rounded border border-border bg-background px-2 text-2xs"
            placeholder="Reason for rejecting (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid={`link-reason-${link.table}-${link.column}`}
          />
          <Button
            size="sm"
            variant="outline"
            data-testid={`link-reject-${link.table}-${link.column}`}
            disabled={decide.isPending}
            onClick={() => decide.mutate("reject")}
          >
            Reject
          </Button>
          <span className="flex items-center gap-1 text-2xs text-subtle">
            Recorded against your signed-in principal: <AuthChip compact />
          </span>
        </div>
      )}
      {failure && <FailureNotice failure={failure} testId={`link-failure-${link.table}-${link.column}`} />}
    </li>
  );
}

export function LinksPanel({
  sourceId,
  review,
  onDone,
}: {
  sourceId: string;
  review: MappingReview;
  onDone: () => void;
}) {
  const { pending, decided, notOffered } = linksForReview(review.links);
  if (review.links.length === 0) return null;
  return (
    <div className="mt-4 border-t border-border pt-3" data-testid="mapping-links">
      <div className="mb-1 text-xs font-medium">
        Proposed links{pending.length > 0 && <span className="text-band-spot"> — {pending.length} need your decision</span>}
      </div>
      <p className="mb-2 text-2xs text-subtle">
        A column no foreign key declares, whose values were measured to be keys of another table. Nothing is linked until
        you approve; approving adds an edge to that table&apos;s mapping. A link never merges records.
      </p>
      <ul>
        {[...pending, ...decided, ...notOffered].map((l) => (
          <LinkRow key={`${l.table}|${l.column}|${l.target}`} sourceId={sourceId} link={l} onDone={onDone} />
        ))}
      </ul>
    </div>
  );
}

/** "Needs your approval again" for a table a person approved before, with the engine's reason. */
export function ReapprovalNote({ table, r }: { table: string; r: Reapproval }) {
  return (
    <div className="mt-1 text-2xs text-band-spot" data-testid={`mapping-reapprove-${table}`}>
      <strong>Needs your approval again.</strong>{" "}
      {r.previously_approved_by && (
        <>
          Approved before by {r.previously_approved_by}
          {r.previously_approved_at && <> on {fmtTime(r.previously_approved_at)}</>}
          {r.dev_only && <DevTokenMark />}.{" "}
        </>
      )}
      <ul className="list-disc pl-4">
        {r.reasons.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    </div>
  );
}

function rowsText(rows: number | null): string {
  return rows == null ? "?" : rows.toLocaleString();
}

/**
 * UI8: what a person approves when a table's rows are of more than one declared kind. Shown in the
 * table's mapping row, BEFORE approval. Nothing is split until the table is approved, and the
 * approval (the existing "Approve" for the table, recorded against the signed-in principal) covers
 * the split exactly as shown.
 */
export function KindSplitView({ table, split }: { table: string; split: KindSplitReview }) {
  const { moved, stays, movedRows } = splitGroups(split);
  const stay = split.rows_stay;
  const tid = (s: string) => `split-${s}-${table}`;
  return (
    <div className="mt-1 rounded border border-border p-2 text-2xs" data-testid={tid("view")} data-status={split.status}>
      <div className="mb-1 font-medium text-fg">
        Rows split by <code className="font-mono">{split.column}</code>
        {split.status === "approved" ? (
          <span className="text-subtle"> — approved with the table</span>
        ) : (
          <span className="text-band-spot"> — proposed; approving this table approves the split</span>
        )}
      </div>
      {moved.length > 0 && (
        <table className="mb-1 w-full text-left" data-testid={tid("moved")}>
          <thead className="text-subtle">
            <tr>
              <th className="pr-2 font-medium">Value</th>
              <th className="pr-2 font-medium">Becomes</th>
              <th className="text-right font-medium">Rows</th>
            </tr>
          </thead>
          <tbody>
            {moved.map((v) => (
              <tr key={v.value} data-testid={`${tid("value")}-${v.value}`}>
                <td className="pr-2 font-mono">{v.value}</td>
                <td className="pr-2 font-mono">{v.kind}</td>
                <td className="tnum text-right">{rowsText(v.rows)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div data-testid={tid("stays")}>
        Stay <span className="font-mono">{split.default}</span>
        {stays.length > 0 && (
          <>
            :{" "}
            {stays.map((v, i) => (
              <span key={v.value} data-testid={`${tid("value")}-${v.value}`}>
                {i > 0 && ", "}
                <code className="font-mono">{v.value}</code> (<span className="tnum">{rowsText(v.rows)}</span>
                {v.bound ? "" : ", not recognised"})
              </span>
            ))}
          </>
        )}
        {stay && (
          <>
            {stays.length > 0 ? "; " : ": "}
            <span data-testid={tid("null")}>
              no value <span className="tnum">{stay.null_or_blank.toLocaleString()}</span>
            </span>
            ; masked by the screen <span className="tnum" data-testid={tid("masked")}>{stay.masked.toLocaleString()}</span>
          </>
        )}
        . Any value not listed above — including a new spelling, an empty value or a masked one — also stays{" "}
        <span className="font-mono">{split.default}</span>.
      </div>
      {split.rows_total != null && (
        <div className="text-subtle" data-testid={tid("totals")}>
          <span className="tnum">{movedRows == null ? "?" : movedRows.toLocaleString()}</span> of{" "}
          <span className="tnum">{split.rows_total.toLocaleString()}</span> captured rows change kind.
        </div>
      )}
      {split.counts_note && <div className="text-subtle">{split.counts_note}</div>}
      <p className="mt-1 text-subtle" data-testid={tid("persons")}>
        {split.default_is_person ? (
          <>
            <span className="font-mono">{split.default}</span> records stand for people.{" "}
            <strong className="text-fg">People are never merged</strong>: each row stays its own record, and identity is
            only ever chosen by a person.
          </>
        ) : (
          <>
            <span className="font-mono">{split.default}</span> is a subject kind: its records are never merged.
          </>
        )}{" "}
        Rows that become another kind merge only under an identity rule a named person approves.
      </p>
    </div>
  );
}
