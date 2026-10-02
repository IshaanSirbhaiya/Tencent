"use client";
/**
 * The delegation tree of one federated run: question -> plan -> one node per sub-task -> combine
 * -> answer with citations. Built only from `delegation_tree` in `GET /activity/runs/{id}`, which
 * is read from the §5.7 run record. A refused or failed sub-task, and reduced coverage, are LOUD:
 * a banner at the top and a red node, never a quiet footnote.
 */
import * as React from "react";
import { AlertTriangle } from "lucide-react";
import {
  coverageWarning,
  envelopeText,
  formatDuration,
  isEnvelope,
  subtaskTone,
  type DelegationTree as Tree,
  type SubtaskNode,
} from "@/lib/activity-api";
import { KV, Maybe, Producer, StatusPill, TONE_BORDER, Verbatim } from "./activity-bits";

function Stage({ label, children, tone = "neutral" }: {
  label: string;
  children: React.ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  return (
    <div className={`rounded-lg border bg-surface px-3 py-2 ${TONE_BORDER[tone]}`}>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">{label}</div>
      {children}
    </div>
  );
}

function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <Maybe value={null} />;
  const text = JSON.stringify(value, null, 1);
  return <Verbatim text={text === "{}" || text === "[]" ? null : text} max={2000} />;
}

export function DelegationTree({ tree }: { tree: Tree }) {
  const warning = coverageWarning(tree);
  return (
    <div className="flex flex-col gap-2" data-testid="delegation-tree">
      {warning ? (
        <div
          role="alert"
          data-testid="delegation-reduced"
          className="flex items-start gap-2 rounded border border-band-review/50 bg-band-review/[0.08] px-3 py-2 text-xs font-medium text-band-review"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{warning}</span>
        </div>
      ) : null}

      <Stage label="question">
        <p className="whitespace-pre-wrap text-xs text-fg">{tree.question ?? <Maybe value={null} />}</p>
        <dl>
          <KV label="asked by"><Maybe value={tree.asked_by} /></KV>
          <KV label="duration">{formatDuration(tree.duration_ms)}</KV>
          <KV label="models used">
            <Maybe value={tree.models_used && tree.models_used.length ? tree.models_used.join(", ") : null} />
          </KV>
        </dl>
      </Stage>

      <Stage label="plan">
        {tree.plan ? (
          <dl>
            <KV label="planned by"><Producer pb={tree.plan.produced_by} /></KV>
            <KV label="method"><Maybe value={tree.plan.method} /></KV>
            <KV label="took">{formatDuration(tree.plan.ms)}</KV>
            <KV label="questions">
              {tree.plan.questions && tree.plan.questions.length ? (
                <ul className="list-disc pl-4" data-testid="plan-questions">
                  {tree.plan.questions.map((q, i) => <li key={i}>{q}</li>)}
                </ul>
              ) : (
                <span className="italic text-subtle/80">
                  {tree.plan.questions_note ?? "not recorded"}
                </span>
              )}
            </KV>
            <KV label="assumptions">
              {tree.plan.assumptions && tree.plan.assumptions.length ? (
                <ul className="list-disc pl-4" data-testid="plan-assumptions">
                  {tree.plan.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                </ul>
              ) : (
                <span className="italic text-subtle/80">{tree.plan.assumptions_note ?? "not recorded"}</span>
              )}
            </KV>
          </dl>
        ) : (
          <span className="italic text-subtle/80">no plan step recorded</span>
        )}
      </Stage>

      <div className="ml-3 flex flex-col gap-2 border-l-2 border-border pl-3" data-testid="delegation-subtasks">
        {tree.subtasks.length === 0 ? (
          <span className="text-2xs italic text-subtle/80">no sub-task was delegated</span>
        ) : (
          tree.subtasks.map((t, i) => <Subtask key={`${t.id ?? i}`} t={t} />)
        )}
      </div>

      <Stage label="combine" tone={tree.combine?.failed ? "bad" : "neutral"}>
        {tree.combine ? (
          <dl>
            <KV label="spec"><Json value={tree.combine.spec} /></KV>
            <KV label="steps done"><Json value={tree.combine.steps} /></KV>
            {tree.combine.failed ? (
              <KV label="failed">
                <span className="font-medium text-band-review">{tree.combine.failed}</span>
              </KV>
            ) : null}
            <KV label="pointers"><Maybe value={tree.combine.pointers_recorded} /></KV>
            <KV label="took">{formatDuration(tree.combine.ms)}</KV>
            <KV label="by"><Producer pb={tree.combine.produced_by} /></KV>
          </dl>
        ) : (
          <span className="italic text-subtle/80">no combine step recorded</span>
        )}
      </Stage>

      <Stage label="answer">
        <dl>
          <KV label="written by"><Producer pb={tree.answer.produced_by} /></KV>
          {tree.narrate ? <KV label="narration">{formatDuration(tree.narrate.ms)}</KV> : null}
          {/* Codex f1 #3: a list the record did not carry is "not recorded", never "0". */}
          <KV label="citations"><Maybe value={tree.answer.citations ? tree.answer.citations.length : null} /></KV>
          {tree.answer.unsupported_claims === null ? (
            <KV label="unsupported"><Maybe value={null} /></KV>
          ) : tree.answer.unsupported_claims.length ? (
            <KV label="unsupported">
              <span className="font-medium text-band-review">
                {tree.answer.unsupported_claims.length} claim(s) had no supporting pointer
              </span>
            </KV>
          ) : null}
        </dl>
        <div className="mt-1">
          <Verbatim text={tree.answer.markdown} />
        </div>
        {tree.answer.citations && tree.answer.citations.length ? (
          <details className="mt-1 text-2xs">
            <summary className="cursor-pointer text-subtle">citations</summary>
            <Json value={tree.answer.citations} />
          </details>
        ) : null}
      </Stage>

      <Stage label="coverage" tone={tree.coverage.reduced ? "warn" : "neutral"}>
        <dl>
          <KV label="searched"><Maybe value={tree.coverage.searched ? tree.coverage.searched.join(", ") || "none" : null} /></KV>
          <KV label="skipped">
            {tree.coverage.skipped === null ? (
              <Maybe value={null} />
            ) : tree.coverage.skipped.length ? (
              <ul className="list-disc pl-4">
                {tree.coverage.skipped.map((s, i) => (
                  <li key={i} className="text-band-spot">
                    {s.connection ?? s.tool ?? "?"}: {s.reason ?? s.reason_code ?? "reason not recorded"}
                  </li>
                ))}
              </ul>
            ) : (
              "none"
            )}
          </KV>
          {tree.coverage.reasons && tree.coverage.reasons.length ? (
            <KV label="reasons">{tree.coverage.reasons.join("; ")}</KV>
          ) : null}
        </dl>
      </Stage>
    </div>
  );
}

function Subtask({ t }: { t: SubtaskNode }) {
  const tone = subtaskTone(t.status);
  return (
    <div
      className={`rounded-lg border bg-surface px-3 py-2 ${TONE_BORDER[tone]} ${tone === "bad" ? "border-l-4" : ""}`}
      data-testid="delegation-subtask"
      data-status={t.status}
    >
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-subtle">sub-task</span>
        <span className="font-mono text-xs text-fg">{t.id ?? "unnamed"}</span>
        <StatusPill status={t.status} tone={tone} />
        <span className="text-2xs text-subtle">{formatDuration(t.ms)}</span>
      </div>
      <dl>
        <KV label="adapter agent"><Maybe value={t.adapter_agent} /></KV>
        <KV label="connection">
          <Maybe value={t.connection} /> <span className="text-subtle/70">({t.connection_basis})</span>
        </KV>
        <KV label="decision"><Maybe value={[t.decision, t.method].filter(Boolean).join(" · ") || null} /></KV>
        <KV label="pushed down">
          {t.pushed_down === null ? <Maybe value={null} /> : t.pushed_down ? "yes" : "no"}
        </KV>
        {typeof t.query === "string" && t.query ? (
          <KV label="query"><Verbatim text={t.query} max={2000} /></KV>
        ) : isEnvelope(t.query) ? (
          <KV label="query"><span className="italic text-subtle/80">{envelopeText(t.query)}</span></KV>
        ) : null}
        <KV label="rows read">
          <Maybe value={t.rows_returned} /> <span className="text-subtle/70">({t.rows_basis})</span>
        </KV>
        <KV label="pointers"><Maybe value={t.pointers_recorded} /></KV>
        <KV label="request"><Json value={t.request} /></KV>
        <KV label="by"><Producer pb={t.produced_by} /></KV>
        {t.skipped === null ? (
          <KV label="skipped"><Maybe value={null} /></KV>
        ) : t.skipped.length ? (
          <KV label="skipped">
            <ul className="list-disc pl-4" data-testid="delegation-skipped">
              {t.skipped.map((s, i) => (
                <li key={i} className="font-medium text-band-review">
                  {s.connection ?? s.channel ?? "?"}: {s.reason ?? s.reason_code ?? "reason not recorded"}
                </li>
              ))}
            </ul>
          </KV>
        ) : null}
        {typeof t.delegate_note === "string" && t.delegate_note && typeof t.query !== "string" ? (
          <KV label="note"><Verbatim text={t.delegate_note} max={1500} /></KV>
        ) : isEnvelope(t.delegate_note) ? (
          <KV label="note"><span className="italic text-subtle/80">{envelopeText(t.delegate_note)}</span></KV>
        ) : null}
      </dl>
    </div>
  );
}
