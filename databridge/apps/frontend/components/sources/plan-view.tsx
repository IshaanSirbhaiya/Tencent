"use client";
/**
 * The proposed crawl plan, in plain English, with the raw plan one click away.
 *
 * Each sentence is a restatement of a field the gateway returned (steps, tools, scope, problems) —
 * nothing is inferred. A plan with recorded problems cannot be approved (the gateway refuses it),
 * so the problems are listed first and the Approve button says why it is disabled.
 */
import * as React from "react";
import type { CatalogueConnection, Connection, CrawlPlan, StepSpec } from "@/lib/sources-api";
import { cn } from "@/lib/utils";

function Code({ children }: { children: React.ReactNode }) {
  return <code className="rounded bg-muted px-1 py-px font-mono text-2xs text-fg">{children}</code>;
}

function list(items: string[] | undefined): React.ReactNode {
  if (!items || items.length === 0) return null;
  return items.map((x, i) => (
    <React.Fragment key={x + i}>
      {i > 0 && ", "}
      <Code>{x}</Code>
    </React.Fragment>
  ));
}

function stepSentences(step: StepSpec, conn: Connection): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const roots = conn.scope?.roots ?? [];
  if (step.strategy === "tabular" && step.sql) {
    out.push(
      <>
        Read database tables by running SQL through <Code>{step.sql.tool}</Code>
        {step.sql.dialect ? <> ({step.sql.dialect})</> : null}
        {conn.scope?.max_rows_per_table ? <>, up to {conn.scope.max_rows_per_table.toLocaleString()} rows per table</> : null}.
      </>,
    );
    if (conn.scope?.tables_include?.length) out.push(<>Only tables matching {list(conn.scope.tables_include)}.</>);
    if (conn.scope?.tables_exclude?.length) out.push(<>Never tables matching {list(conn.scope.tables_exclude)}.</>);
    return out;
  }
  if (step.roots) out.push(<>Ask the server for its starting folders with <Code>{step.roots.tool}</Code>.</>);
  if (step.list) {
    out.push(
      <>
        List items with <Code>{step.list.tool}</Code>
        {roots.length ? <>, starting at {list(roots)}</> : step.roots ? null : <>, starting at the server&apos;s own roots</>}
        {step.list.recurse === false ? ", without descending into sub-folders" : ", descending into sub-folders"}
        {step.list.page ? <>, following pages via <Code>{step.list.page.arg}</Code></> : null}.
      </>,
    );
  }
  if (step.read?.text_tool) out.push(<>Read text files with <Code>{step.read.text_tool}</Code>.</>);
  if (step.read?.binary_tool)
    out.push(
      <>
        Read binary files{step.read.binary_extensions?.length ? <> ({list(step.read.binary_extensions)})</> : null} with{" "}
        <Code>{step.read.binary_tool}</Code>.
      </>,
    );
  if (step.read?.resource_uri) out.push(<>Read each item as an MCP resource at <Code>{step.read.resource_uri}</Code>.</>);
  if (step.strategy === "resources" && !step.list) out.push(<>Read the resources the server lists.</>);
  return out;
}

export function PlanView({
  plan,
  connection,
  audit,
}: {
  plan: CrawlPlan;
  connection: Connection;
  /** `planToolAudit(plan, catalogue)` — the plan's tools against the backend's read/write catalogue. */
  audit?: { used: string[]; writes: string[]; unclassified: string[] };
}) {
  const scope = connection.scope ?? {};
  return (
    <div className="space-y-3 text-xs" data-testid="plan-view">
      <p className="text-subtle">
        Proposed by{" "}
        <strong className="text-fg">
          {plan.origin === "rules" ? "matching rules" : plan.origin === "model" ? "the model (validated against the real tool list)" : "hand"}
        </strong>
        {plan.profiles.length > 0 && <> · profiles {list(plan.profiles)}</>}
        {plan.created_at && <> · {new Date(plan.created_at).toLocaleString()}</>}
      </p>

      {plan.problems.length > 0 && (
        <div className="rounded border border-band-review/40 bg-band-review/10 p-3" data-testid="plan-problems">
          <div className="mb-1 font-medium text-band-review">This plan cannot be approved while it has problems:</div>
          <ul className="list-disc space-y-0.5 pl-5">
            {plan.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      {plan.steps.length === 0 ? (
        <p className="text-subtle" data-testid="plan-no-steps">
          The plan has no steps: nothing on this server matched a known way to crawl it.
        </p>
      ) : (
        <ol className="space-y-2" data-testid="plan-steps">
          {plan.steps.map((s, i) => (
            <li key={s.id} className="rounded border border-border p-3">
              <div className="mb-1 text-2xs uppercase tracking-wider text-subtle">
                Step {i + 1} · {s.strategy} · <span className="font-mono normal-case">{s.id}</span>
              </div>
              <ul className="list-disc space-y-0.5 pl-5">
                {stepSentences(s, connection).map((node, j) => (
                  <li key={j}>{node}</li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}

      <ul className="list-disc space-y-0.5 pl-5 text-subtle">
        {scope.file_types?.length ? <li>Only these file types: {list(scope.file_types)}.</li> : <li>Every file type an extractor exists for.</li>}
        {scope.include?.length ? <li>Only paths matching {list(scope.include)}.</li> : null}
        {scope.exclude?.length ? <li>Paths matching {list(scope.exclude)} are recorded as excluded.</li> : null}
        {scope.blocked?.length ? <li>Paths matching {list(scope.blocked)} are never collected and are recorded as blocked.</li> : null}
        {typeof scope.max_items === "number" && (
          <li>
            At most {scope.max_items.toLocaleString()} items
            {typeof scope.max_bytes === "number" && <>, each up to {(scope.max_bytes / 1024 / 1024).toFixed(0)} MB</>}.
          </li>
        )}
        {plan.deny_tools.length > 0 && <li>Never called: {list(plan.deny_tools)}.</li>}
        {plan.pagination.length > 0 && <li>Paged listings: {list(plan.pagination)}.</li>}
        {!audit ? null : audit.writes.length > 0 ? (
          <li className="font-medium text-band-review" data-testid="plan-write-tools">
            This plan calls {list(audit.writes)}, which the tool catalogue classifies as WRITING. It must not be approved.
          </li>
        ) : audit.unclassified.length > 0 ? (
          <li className="text-band-spot" data-testid="plan-tools-unverified">
            {list(audit.unclassified)} {audit.unclassified.length === 1 ? "is" : "are"} not classified by the tool
            catalogue, so this page cannot confirm the plan only reads.
          </li>
        ) : audit.used.length > 0 ? (
          <li data-testid="plan-tools-read-only">
            Every tool this plan calls is classified read-only by the tool catalogue.
          </li>
        ) : null}
      </ul>

      {plan.notes.length > 0 && (
        <div>
          <div className="mb-1 font-medium">Notes</div>
          <ul className="list-disc space-y-0.5 pl-5 text-subtle">
            {plan.notes.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}

      <details className="rounded border border-border" data-testid="plan-raw">
        <summary className="cursor-pointer px-3 py-2 font-medium">Raw plan</summary>
        <pre className="max-h-80 overflow-auto border-t border-border p-3 font-mono text-2xs">{JSON.stringify(plan, null, 2)}</pre>
      </details>
    </div>
  );
}

/** The tools the server offered, marked read or write by the backend's own classification. */
export function ToolsView({
  tools,
  plan,
  catalogue,
  note,
}: {
  tools: string[];
  plan: CrawlPlan | null;
  catalogue: CatalogueConnection | null;
  note: string | null;
}) {
  if (tools.length === 0) {
    return (
      <p className="text-xs text-subtle" data-testid="tools-empty">
        {note ?? "The server listed no tools."}
      </p>
    );
  }
  const byName = new Map((catalogue?.tools ?? []).map((t) => [t.name, t]));
  const used = new Set((plan?.steps ?? []).flatMap((s) => [s.roots?.tool, s.list?.tool, s.read?.text_tool, s.read?.binary_tool, s.sql?.tool]).filter(Boolean) as string[]);
  const denied = new Set(plan?.deny_tools ?? []);
  return (
    <div className="space-y-2">
      {!catalogue && (
        <p className="text-2xs text-subtle" data-testid="tools-unclassified">
          The tool catalogue does not list this source, so read/write is not marked.
        </p>
      )}
      <ul className="divide-y divide-border rounded border border-border text-xs" data-testid="tools-list">
        {tools.map((name) => {
          const t = byName.get(name);
          return (
            <li key={name} className="flex items-center justify-between gap-2 px-3 py-1.5" data-testid={`tool-${name}`}>
              <span className="font-mono text-2xs">{name}</span>
              <span className="flex items-center gap-1.5">
                {used.has(name) && <span className="text-2xs text-accent">used by the plan</span>}
                {denied.has(name) && <span className="text-2xs text-band-review">never called</span>}
                {t && (
                  <span
                    title={`classified by ${t.basis}`}
                    data-testid={`tool-kind-${name}`}
                    data-write={t.write ? "true" : "false"}
                    className={cn(
                      "rounded border px-1.5 py-px text-2xs font-medium",
                      t.write ? "border-band-review/30 bg-band-review/10 text-band-review" : "border-border bg-muted text-subtle",
                    )}
                  >
                    {t.write ? "write" : "read"}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
