"use client";
/**
 * LINK7 for GRAPH6 (FIN-04, D-004): the identity rules the bound packs declare for kinds that are
 * NOT people — "records with the same registration number / exact legal name are one record". A
 * rule merges nothing until a named person approves it here. Each rule type shows what approving
 * it would merge now (the engine's own consolidation, run on a copy of the graph) BEFORE the
 * decision. People are never merged: no rule can be declared or approved for a person kind.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  failureOf,
  sourcesApi,
  unwrap,
  type IdentityRuleDecl,
  type IdentityRuleType,
  type SourcesFailure,
  type SourcesResult,
} from "@/lib/sources-api";
import { AuthChip, DevTokenMark } from "@/components/auth-chip";
import { FailureNotice, Section, StatusBadge, fmtTime } from "./common";

const RULE_TEXT: Record<string, string> = {
  business_key: "same value of a declared business key",
  exact_name: "exactly the same name after normalisation (case, punctuation, spacing; the legal form is kept)",
};

function RuleType({ decl, t, onDone }: { decl: IdentityRuleDecl; t: IdentityRuleType; onDone: () => void }) {
  const [failure, setFailure] = React.useState<SourcesFailure | null>(null);
  const decide = useMutation({
    mutationFn: async (decision: "approved" | "revoked"): Promise<SourcesResult<unknown>> =>
      sourcesApi.decideIdentityRule({ pack: decl.pack, kind: decl.kind, rule: t.rule, decision }),
    onSuccess: (r) => {
      if (r.ok) {
        setFailure(null);
        onDone();
      } else setFailure(r);
    },
  });
  const id = `${decl.kind}-${t.rule}`;
  const p = t.preview;
  return (
    <li className="border-b border-border py-2 last:border-0" data-testid={`identity-rule-${id}`} data-state={t.state}>
      <div className="flex flex-wrap items-baseline gap-2 text-xs">
        <span className="font-mono text-2xs">{t.rule}</span>
        <span className="text-2xs text-subtle">
          {RULE_TEXT[t.rule] ?? t.rule}
          {t.rule === "business_key" && decl.business_keys.length > 0 && <> ({decl.business_keys.join(", ")})</>}
        </span>
        <StatusBadge status={t.state} />
      </div>
      {t.decided_by && (
        <p className="mt-1 text-2xs text-subtle" data-testid={`identity-rule-decided-${id}`}>
          {t.state === "approved" ? "Approved" : "Revoked"} by <strong>{t.decided_by}</strong>
          {t.decided_at && <> on {fmtTime(t.decided_at)}</>}
          {t.dev_only === true && <DevTokenMark testId={`identity-rule-dev-${id}`} />}.
        </p>
      )}
      {t.state !== "approved" && (
        <div className="mt-1 text-2xs" data-testid={`identity-rule-preview-${id}`}>
          {p ? (
            <>
              <p>
                Approving it would join <span className="tnum">{p.records_absorbed + p.groups}</span> records into{" "}
                <span className="tnum">{p.groups}</span> {decl.kind} record(s)
                {p.refused > 0 && (
                  <>
                    ; <span className="tnum">{p.refused}</span> group(s) would be left apart
                  </>
                )}
                .
              </p>
              {p.sample_groups.length > 0 && (
                <ul className="list-disc pl-4 text-subtle">
                  {p.sample_groups.map((g) => (
                    <li key={g.survivor}>{g.labels.join(" = ")}</li>
                  ))}
                </ul>
              )}
              {p.refused_samples.slice(0, 2).map((r) => (
                <p key={r.records.join("|")} className="text-subtle">
                  Left apart: {r.reason}
                </p>
              ))}
            </>
          ) : (
            <p className="text-subtle">No preview: {t.preview_note ?? "unavailable"}.</p>
          )}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {t.state !== "approved" ? (
          <Button size="sm" data-testid={`identity-rule-approve-${id}`} disabled={decide.isPending} onClick={() => decide.mutate("approved")}>
            Approve rule
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            data-testid={`identity-rule-revoke-${id}`}
            disabled={decide.isPending}
            onClick={() => decide.mutate("revoked")}
          >
            Revoke (stops future merges)
          </Button>
        )}
        <span className="flex items-center gap-1 text-2xs text-subtle">
          Recorded against your signed-in principal: <AuthChip compact />
        </span>
      </div>
      {failure && <FailureNotice failure={failure} testId={`identity-rule-failure-${id}`} />}
    </li>
  );
}

export function IdentityRulesSection() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["identity-rules"], queryFn: () => unwrap(sourcesApi.identityRules()), retry: false });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["identity-rules"] });
  };
  if (q.isError) {
    const f = failureOf(q.error);
    return f ? <FailureNotice failure={f} testId="identity-rules-failure" /> : null;
  }
  const rules = q.data?.identity_rules ?? [];
  if (rules.length === 0) return null;
  return (
    <Section
      testId="identity-rules"
      title="Identity rules"
      description="When records of an organisation kind are one record. A rule merges nothing until you approve it; people are never merged."
    >
      <div className="space-y-3">
        {rules.map((d) => (
          <div key={`${d.pack}:${d.kind}`} data-testid={`identity-rules-${d.kind}`}>
            <div className="text-xs font-medium">
              <span className="font-mono">{d.kind}</span> <span className="text-subtle">declared by pack {d.pack}</span>
            </div>
            {!d.similarity_merge && (
              <p className="text-2xs text-subtle">Name-similarity merging is off for this kind; only an approved rule joins records.</p>
            )}
            <ul>
              {d.rules.map((t) => (
                <RuleType key={t.rule} decl={d} t={t} onDone={done} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}
