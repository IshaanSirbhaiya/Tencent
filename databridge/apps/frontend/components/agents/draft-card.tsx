"use client";
/**
 * "Here's what I understood" — the preview of one compiled draft, for BOTH kinds the English
 * compiler recognises (an agent, an adapter agent). What is saved is exactly this draft: `POST
 * /agents` saves the draft the gateway kept under `draft_id`, it never re-compiles the words.
 *
 * Every row is read from the draft's spec; every badge ("rules" / "model" / "you") is read from the
 * draft's `derivations`. Activation is offered only when the gateway says `activatable`; when it
 * says no, the button stays visible and disabled with the gateway's own reasons beside it.
 */
import * as React from "react";
import { CheckCircle2, CircleSlash } from "lucide-react";
import {
  ALWAYS_ASKED_DRIFT,
  adaptProblems,
  draftActionState,
  isAdapterAgentSpec,
  isAgentSpec,
  missingAlwaysAsked,
  type AdapterAgentSpec,
  type DraftResult,
  type WorkflowSpec,
} from "@/lib/agents-api";
import {
  Collapsible,
  DerivationBadges,
  DerivationTable,
  OpenQuestions,
  Row,
  TriggerInWords,
  Yaml,
  approvalInWords,
  derivationsFor,
} from "./shared";

/** Why activation is not offered — the gateway's own words, in the order it would refuse. */
export function notActivatableReasons(d: DraftResult): string[] {
  const out: string[] = [];
  if (!d.spec) out.push("there is no spec yet — answer the open questions first");
  for (const q of d.open_questions) out.push(`open question: ${q.text}`);
  if (d.compilation && d.compilation.outcome !== "compiled") {
    const details = d.compilation.refusals
      .map((r) => String((r as { detail?: unknown }).detail ?? ""))
      .filter(Boolean);
    out.push(...(details.length ? details : [d.compilation.reason || `declaration ${d.compilation.outcome}`]));
  }
  if (isAdapterAgentSpec(d.spec)) {
    const spec = d.spec;
    out.push(...adaptProblems(spec));
    for (const c of spec.connections) {
      if (spec.plans[c] !== "approved") {
        out.push(`the crawl plan for ${c} is ${spec.plans[c] ?? "not proposed"} — approve it on Sources (/connectors) first`);
      }
    }
  }
  return out;
}

function AgentRows({ spec, d }: { spec: WorkflowSpec; d: DraftResult }) {
  const dv = d.derivations;
  return (
    <dl data-testid="understood-agent">
      <Row label="Title">
        {spec.title}
        <DerivationBadges derivations={derivationsFor(dv, "title")} testId="deriv-title" />
      </Row>
      <Row label="Runs" testId="understood-trigger">
        <TriggerInWords trigger={spec.trigger} />
        <DerivationBadges derivations={derivationsFor(dv, "trigger")} testId="deriv-trigger" />
      </Row>
      <Row label="Mode" testId="understood-mode">
        <span data-testid="understood-mode-value" className="font-medium">
          {spec.mode === "read_only" ? "read-only" : "read-write"}
        </span>
        <span className="ml-1 text-subtle">
          {spec.mode === "read_only"
            ? "— reads and answers; it cannot change anything"
            : "— may write through the actions below, each under its own approval rule"}
        </span>
        <DerivationBadges derivations={derivationsFor(dv, "mode")} testId="deriv-mode" />
      </Row>
      <Row label="Sources" testId="understood-sources">
        {spec.sources.length ? (
          <span className="flex flex-wrap gap-1">
            {spec.sources.map((s) => (
              <span key={s} className="rounded border border-border px-1.5 font-mono text-2xs">
                {s}
                <DerivationBadges derivations={derivationsFor(dv, `sources[${s}]`)} />
              </span>
            ))}
          </span>
        ) : (
          <span className="text-subtle">none — graph and retrieval only</span>
        )}
      </Row>
      <Row label="Tools" testId="understood-tools">
        <span className="flex flex-wrap gap-1">
          {spec.tools.map((t) => (
            <span key={t} className="rounded bg-muted px-1.5 font-mono text-2xs">
              {t}
              <DerivationBadges derivations={derivationsFor(dv, `tools[${t}]`)} />
            </span>
          ))}
        </span>
      </Row>
      <Row label="Write actions" testId="understood-actions">
        {spec.actions.length === 0 ? (
          <span className="text-subtle">none</span>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {spec.actions.map((a) => (
              <li key={a.name} data-testid={`understood-action-${a.name}`}>
                <span className="font-mono">{a.name}</span>{" "}
                <span className="font-mono text-2xs text-subtle">{a.tool}</span>
                <div data-testid={`understood-action-${a.name}-mode`} className="text-fg">
                  {approvalInWords(a.automation_mode, a.reversal)}
                  <span className="ml-1 font-mono text-2xs text-subtle">({a.automation_mode})</span>
                  <DerivationBadges derivations={derivationsFor(dv, `actions[${a.name}]`)} />
                </div>
                {a.description && <div className="text-2xs text-subtle">{a.description}</div>}
              </li>
            ))}
          </ul>
        )}
      </Row>
      <Row label="Steps">
        <pre className="whitespace-pre-wrap font-sans text-xs text-fg">{spec.instructions}</pre>
        <DerivationBadges derivations={derivationsFor(dv, "instructions")} />
      </Row>
    </dl>
  );
}

const DRIFT_WORDS: Record<string, string> = {
  removed: "a field or tool disappears",
  renamed: "a field or tool is renamed",
  type_changed: "a field changes type",
  tool_signature_changed: "a tool's arguments change",
  new_write_tool: "a new write tool appears",
  subject_touching: "a change touches people-related (subject) data",
};

export function AdaptPolicyInWords({ spec }: { spec: AdapterAgentSpec }) {
  const a = spec.adapt;
  if (!a) {
    return (
      <span data-testid="adapt-policy-missing" className="font-medium text-band-review">
        Invalid spec: it carries no <code>adapt</code> block, so this page cannot establish that a new write tool or a
        people-related change is put to a person. Do not activate it.
      </span>
    );
  }
  const head =
    a.policy === "auto_additive"
      ? "adapts to additive changes on its own"
      : a.policy === "ask_all"
        ? "asks you about every change to a source's shape"
        : "detects and reports changes, but adapts nothing";
  const missing = missingAlwaysAsked(a.always_ask);
  const watch = [a.watch.on_sync && "on every sync", a.watch.on_list_changed && "when a server says its tools changed"]
    .filter(Boolean)
    .join(" and ");
  return (
    <div data-testid="adapt-policy-words" className="flex flex-col gap-0.5">
      <span>
        {head} <span className="font-mono text-2xs text-subtle">({a.policy})</span>; checks {watch || "never"}.
      </span>
      <span>
        Always asks you first when:{" "}
        {a.always_ask.map((k) => DRIFT_WORDS[k] ?? k).join("; ")}.
      </span>
      {missing.length === 0 ? (
        <span data-testid="adapt-always-asks" className="text-2xs text-subtle">
          This policy lists {ALWAYS_ASKED_DRIFT.map((k) => DRIFT_WORDS[k] ?? k).join(" and ")}: the spec schema
          requires both, so they are asked whatever else the policy says.
        </span>
      ) : (
        <span data-testid="adapt-always-asks-missing" className="text-2xs font-medium text-band-review">
          Invalid spec: <code>always_ask</code> is missing {missing.join(", ")}, which the spec schema requires. This page
          does not assume those changes are put to a person; do not activate this spec.
        </span>
      )}
    </div>
  );
}

function AdapterRows({ spec, d }: { spec: AdapterAgentSpec; d: DraftResult }) {
  const dv = d.derivations;
  const screen = Object.entries(spec.screen ?? {});
  const extract = [spec.extract.prose && "prose", spec.extract.tabular && "tables", spec.extract.passages && "passages"]
    .filter(Boolean)
    .join(", ");
  return (
    <dl data-testid="understood-adapter">
      <Row label="Title">
        {spec.title}
        <DerivationBadges derivations={derivationsFor(dv, "title")} />
      </Row>
      <Row label="Connections" testId="understood-connections">
        <ul className="flex flex-col gap-0.5">
          {spec.connections.map((c) => (
            <li key={c} data-testid={`understood-connection-${c}`}>
              <span className="font-mono">{c}</span>{" "}
              <span className={spec.plans[c] === "approved" ? "text-band-auto" : "text-band-spot"}>
                crawl plan {spec.plans[c] ?? "not proposed"}
              </span>
              <DerivationBadges derivations={derivationsFor(dv, `plans[${c}]`)} />
            </li>
          ))}
        </ul>
      </Row>
      <Row label="Syncs" testId="understood-sync">
        <TriggerInWords trigger={spec.sync} />
        <DerivationBadges derivations={derivationsFor(dv, "sync")} />
      </Row>
      <Row label="Extracts">
        {extract || "nothing"} under pack <span className="font-mono">{spec.extract.pack}</span>
      </Row>
      <Row label="Screening">
        {screen.length ? (
          <span className="font-mono text-2xs">{screen.map(([k, v]) => `${k}: ${v}`).join(" · ")}</span>
        ) : (
          <span className="text-subtle">the pack&rsquo;s default screening rules</span>
        )}
      </Row>
      <Row label="Freshness">
        stale after {spec.freshness.max_age_hours}h without a successful sync
        <DerivationBadges derivations={derivationsFor(dv, "freshness")} />
      </Row>
      <Row label="Format changes" testId="understood-adapt">
        <AdaptPolicyInWords spec={spec} />
      </Row>
      <Row label="Ontology">
        {spec.propose_ontology_additions
          ? "proposes new kinds it cannot map — you forward them to the ontology; nothing auto-applies"
          : "does not propose new kinds"}
      </Row>
    </dl>
  );
}

export function DraftCard({
  draft,
  answers,
  onAnswer,
  onSave,
  busy,
  saving,
  compiledText,
  text,
}: {
  draft: DraftResult;
  answers: Record<string, string>;
  onAnswer: (id: string, value: string) => void;
  onSave: (activate: boolean) => void;
  busy: boolean;
  saving: boolean;
  /** The words this draft was compiled from, and the words in the box now. */
  compiledText: string;
  text: string;
}) {
  // An adapter spec whose adapt block is missing, or lacks a schema-required drift kind, is never
  // offered for activation, whatever `activatable` says: the page will not activate a gate it
  // cannot see (R-019 round 3, B4).
  const invalidAdapt = isAdapterAgentSpec(draft.spec) && adaptProblems(draft.spec).length > 0;
  const canActivate = draft.activatable && !invalidAdapt;
  const reasons = canActivate ? [] : notActivatableReasons(draft);
  // A draft for words since edited, or while a compile/save is in flight, cannot be saved at all.
  const act = draftActionState({
    compiledText,
    text,
    compiling: busy,
    saving,
    hasSpec: !!draft.spec,
    activatable: canActivate,
  });
  const kindLabel = draft.kind === "adapter_agent" ? "adapter agent" : "agent";
  return (
    <div data-testid="draft-card" data-kind={draft.kind} className="flex flex-col gap-3 rounded border border-border bg-surface p-3">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">Here&rsquo;s what I understood</h3>
        <span className="text-2xs text-subtle">
          <span data-testid="draft-kind" className="font-mono text-fg">{kindLabel}</span>
          {draft.spec_id && (
            <>
              {" · id "}
              <span data-testid="draft-spec-id" className="font-mono text-fg">{draft.spec_id}</span>
            </>
          )}
          {" · "}
          {draft.model_used ? "rules + model" : "rules only (no model pass)"}
        </span>
      </header>

      {draft.understood && (
        <p data-testid="draft-understood" className="whitespace-pre-wrap text-xs text-fg">
          {draft.understood}
        </p>
      )}

      {isAgentSpec(draft.spec) && <AgentRows spec={draft.spec} d={draft} />}
      {isAdapterAgentSpec(draft.spec) && <AdapterRows spec={draft.spec} d={draft} />}

      <OpenQuestions questions={draft.open_questions} answers={answers} onAnswer={onAnswer} busy={busy} />

      {draft.assumptions.length > 0 && (
        <div data-testid="draft-assumptions">
          <p className="text-2xs font-semibold uppercase tracking-wide text-subtle">Assumed</p>
          <ul className="mt-1 list-disc pl-4 text-xs text-fg">
            {draft.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      {draft.notes.length > 0 && (
        <ul data-testid="draft-notes" className="list-disc pl-4 text-2xs text-subtle">
          {draft.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-col gap-1.5">
        <Collapsible title={`Where each part came from (${draft.derivations.length})`} testId="draft-derivations">
          <DerivationTable derivations={draft.derivations} />
        </Collapsible>
        {draft.spec_yaml && (
          <Collapsible title="Generated spec (YAML)" testId="draft-spec-yaml">
            <Yaml text={draft.spec_yaml} testId="draft-spec-yaml-text" />
          </Collapsible>
        )}
        {draft.declaration_yaml && (
          <Collapsible title="Generated declaration (YAML)" testId="draft-declaration-yaml">
            <Yaml text={draft.declaration_yaml} />
          </Collapsible>
        )}
      </div>

      <footer className="flex flex-wrap items-start gap-2 border-t border-border pt-3">
        <button
          type="button"
          data-testid="draft-save"
          disabled={!act.canSave}
          onClick={() => act.canSave && onSave(false)}
          className="h-7 rounded border border-border px-3 text-xs text-fg hover:bg-muted disabled:opacity-40"
        >
          Save as draft
        </button>
        <button
          type="button"
          data-testid="draft-activate"
          disabled={!act.canActivate}
          onClick={() => act.canActivate && onSave(true)}
          className="h-7 rounded bg-accent px-3 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-40"
        >
          Activate
        </button>
        <div className="min-w-0 flex-1">
          {act.stale && (
            <p data-testid="draft-stale" className="text-2xs font-medium text-band-spot">
              The words above changed since this draft was compiled. Submit them again; this draft cannot be saved.
            </p>
          )}
          {busy && !act.stale && (
            <p data-testid="draft-recompiling" className="text-2xs text-subtle">
              Recompiling — this draft cannot be saved until it returns.
            </p>
          )}
          {canActivate ? (
            <p className="flex items-center gap-1 text-2xs text-band-auto">
              <CheckCircle2 className="h-3 w-3" /> ready to activate
            </p>
          ) : (
            <div data-testid="draft-not-activatable" className="text-2xs text-subtle">
              <p className="flex items-center gap-1 text-band-spot">
                <CircleSlash className="h-3 w-3" /> cannot be activated yet:
              </p>
              <ul className="list-disc pl-4">
                {reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </footer>
    </div>
  );
}
