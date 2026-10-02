"use client";
/**
 * UI8: what the screen is HOLDING for a person, and the owner's action to lift a hold.
 *
 * The screen holds an item — a document, or a whole table — in its review band when it fires a
 * quarantine rule (e.g. text addressed to an automated reader) or scores into review. A held item
 * is never extracted or built. Each hold shows the rules that fired and the screen's reason; an
 * owner who has read the item lifts the hold for exactly this content (its digest), with a reason,
 * as the signed-in principal. The next sync builds it. A changed item is held again; a block is
 * never liftable here.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  failureOf,
  holdLiftable,
  sourcesApi,
  unwrap,
  type ScreenHold,
  type SourcesFailure,
  type SourcesResult,
} from "@/lib/sources-api";
import { AuthChip, DevTokenMark } from "@/components/auth-chip";
import { FailureNotice, Section, StatusBadge, Warning, fmtTime } from "./common";

function HoldRow({
  sourceId,
  hold,
  logProblem,
  onDone,
}: {
  sourceId: string;
  hold: ScreenHold;
  logProblem: string | null;
  onDone: () => void;
}) {
  const [reason, setReason] = React.useState("");
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);
  const act = useMutation({
    mutationFn: (action: "approve" | "revoke"): Promise<SourcesResult<unknown>> =>
      sourcesApi.override(sourceId, { action, item_id: hold.item_id, digest: hold.digest, reason }),
    onSuccess: (r) => {
      if (r.ok) {
        setFailure(null);
        setReason("");
        onDone();
      } else setFailure(r);
    },
  });
  const tid = (s: string) => `hold-${s}-${hold.item_id}`;
  return (
    <li className="border-b border-border py-2 last:border-0" data-testid={tid("row")} data-lifted={hold.lifted ? "1" : "0"}>
      <div className="flex flex-wrap items-baseline gap-2 text-xs">
        <code className="font-mono text-2xs">{hold.uri ?? hold.item_id}</code>
        {hold.item_kind && <span className="text-2xs text-subtle">{hold.item_kind}</span>}
        <StatusBadge status={hold.lifted ? "approved" : "proposed"} />
        <span className="text-2xs text-subtle">{hold.lifted ? "hold lifted — built on the next sync" : "held for a person"}</span>
      </div>
      <div className="mt-1 text-2xs" data-testid={tid("reason")}>
        <span className="text-subtle">Why: </span>
        {hold.rules.length > 0 && (
          <>
            {hold.rules.map((r, i) => (
              <span key={`${r.rule}-${i}`}>
                {i > 0 && ", "}
                <code className="font-mono">{r.rule}</code>
                {r.type && <span className="text-subtle"> ({r.type})</span>}
              </span>
            ))}
            {". "}
          </>
        )}
        {hold.rationale}
      </div>
      {hold.reverse && <div className="text-2xs text-subtle">To reverse: {hold.reverse}</div>}
      <div className="text-2xs text-subtle">
        Content digest <code className="font-mono">{hold.digest.slice(0, 12)}…</code> — a lift covers this content only; if it
        changes, it is held again.
      </div>
      {hold.lifted ? (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-subtle" data-testid={tid("lifted")}>
          <span>
            Lifted by <strong className="text-fg">{hold.lifted_by ?? "?"}</strong>
            {hold.lifted_at && <> on {fmtTime(hold.lifted_at)}</>}
            {hold.lifted_dev_only === true && <DevTokenMark testId={tid("lifted-dev")} />}
            {hold.lifted_reason && <> — {hold.lifted_reason}</>}.
          </span>
          <input
            className="h-7 w-56 rounded border border-border bg-background px-2 text-2xs"
            placeholder="Reason for putting the hold back"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid={tid("revoke-reason")}
          />
          <Button
            size="sm"
            variant="outline"
            data-testid={tid("revoke")}
            disabled={act.isPending || !reason.trim() || !!logProblem}
            onClick={() => act.mutate("revoke")}
          >
            Put the hold back
          </Button>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            className="h-7 w-72 rounded border border-border bg-background px-2 text-2xs"
            placeholder="Why it is safe to build (required)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid={tid("lift-reason")}
          />
          <Button
            size="sm"
            data-testid={tid("lift")}
            disabled={act.isPending || !holdLiftable(hold, reason, logProblem)}
            onClick={() => act.mutate("approve")}
          >
            Lift hold
          </Button>
          <span className="flex items-center gap-1 text-2xs text-subtle">
            Recorded against your signed-in principal: <AuthChip compact />
          </span>
        </div>
      )}
      {failure && <FailureNotice failure={failure} testId={tid("failure")} />}
    </li>
  );
}

export function ScreenHoldsSection({ sourceId }: { sourceId: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["source-holds", sourceId],
    queryFn: () => unwrap(sourcesApi.holds(sourceId)),
    retry: false,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["source-holds", sourceId] });
  if (q.isLoading) return null;
  if (q.isError) {
    const f = failureOf(q.error);
    return f ? <FailureNotice failure={f} testId="holds-failure" /> : null;
  }
  const data = q.data;
  if (!data || (data.holds.length === 0 && !data.log_problem)) return null;
  const held = data.holds.filter((h) => !h.lifted);
  return (
    <Section
      testId="source-holds"
      title="Held by the screen"
      description="Items the latest sync's screen held for a person. A held item is not extracted or built until an owner lifts the hold."
    >
      {data.log_problem && <Warning testId="holds-log-problem">{data.log_problem}</Warning>}
      {held.length > 0 && (
        <p className="mb-2 text-2xs text-band-spot" data-testid="holds-summary">
          {held.length} item(s) held{data.run_id && <> by run {data.run_id}</>}. Read each one before lifting it.
        </p>
      )}
      <ul>
        {[...held, ...data.holds.filter((h) => h.lifted)].map((h) => (
          <HoldRow key={`${h.item_id}|${h.digest}`} sourceId={sourceId} hold={h} logProblem={data.log_problem} onDone={refresh} />
        ))}
      </ul>
    </Section>
  );
}
