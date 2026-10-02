"use client";
/**
 * A one-time task (FEAT8): describe a job in English, see the plan, run it once.
 *
 *   describe -> POST /intelligence/one-time/compile   the AGENT compiler, run_once: same grants,
 *                                                     purpose, golden set, questions and refusals
 *            -> the plan, shown BEFORE anything runs: what it reads, what it would write, grants
 *            -> Run once                              the agent runtime; reads run, writes are HELD
 *            -> the cited answer + the outcome block, and each held write with Approve / Reject
 *
 * Nothing here decides anything: every value is a field the gateway sent. The headline is
 * `taskHeadline`, which never says "done" while a write waits. No agent is created; "Save as agent"
 * sends the same words down the Builder's normal path (`POST /agents/compile`, `POST /agents`).
 */
import * as React from "react";
import Link from "next/link";
import { SafeMarkdown } from "./safe-markdown";
import { useMutation } from "@tanstack/react-query";
import { Check, ListChecks, Play, Save, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { agentsApi, citationRefFromLink, resolveCitations, type PointerJson } from "@/lib/agents-api";
import {
  answeredOnly,
  canRetry,
  heldWrites,
  oneTimeApi,
  readsByConnection,
  runBlocker,
  taskHeadline,
  type OneTimeAction,
  type OneTimeDraft,
  type OneTimeTask,
  withheldArgs,
} from "@/lib/one-time-api";
import { ErrorLine } from "@/components/agents/shared";
import { describeValueState } from "@/lib/value-state";
import { evidenceHref, pointerWhere } from "@/components/agents/run-view";

export function OneTimePanel() {
  const [text, setText] = React.useState("");
  const [answers, setAnswers] = React.useState<Record<string, string>>({});
  const [draft, setDraft] = React.useState<OneTimeDraft | null>(null);
  const [task, setTask] = React.useState<OneTimeTask | null>(null);

  const compile = useMutation({
    mutationFn: () => oneTimeApi.compile(text, draft ? answeredOnly(answers, draft.open_questions) : {}),
    onSuccess: (d) => {
      setDraft(d);
      setTask(null);
    },
  });
  const run = useMutation({
    mutationFn: (draftId: string) => oneTimeApi.run(draftId),
    onSuccess: (t) => {
      setTask(t);
      setDraft(null);                     // a draft is consumed by its one run
    },
    onError: async (err) => {
      // A run that failed is still a task: show it (state `failed`, its reference, any write it
      // proposed first) instead of only an error line, so it can be decided or retried.
      const taskId = draft?.plan?.task_id;
      if (!taskId || (err as { code?: string }).code !== "RUN_FAILED") return;
      try {
        setTask(await oneTimeApi.get(taskId));
        setDraft(null);
      } catch {
        /* the error line already says it failed */
      }
    },
  });

  const blocker = draft ? runBlocker(draft) : null;

  return (
    <section data-testid="one-time-panel" className="flex flex-col gap-3 rounded border border-border bg-surface p-4">
      <header>
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
          <ListChecks className="h-4 w-4" aria-hidden /> Run a one-time task
        </h2>
        <p className="text-2xs text-subtle">
          Describe a job once &mdash; read-only, or one that writes. It is compiled exactly as an agent
          would be, you see its plan before anything runs, reads run straight away, and every write waits
          for you to approve it here. No agent is created.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) compile.mutate();
        }}
        className="flex flex-col gap-2"
      >
        <textarea
          data-testid="one-time-text"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setDraft(null);
            setAnswers({});
          }}
          rows={3}
          placeholder="e.g. Find every facility with a breached covenant and draft a follow-up note for each into the follow-ups folder"
          className="w-full rounded border border-border bg-bg px-2.5 py-2 text-xs text-fg"
          aria-label="Describe the one-time task"
        />
        <div className="flex items-center gap-2">
          <button
            type="submit"
            data-testid="one-time-plan"
            disabled={!text.trim() || compile.isPending}
            className="rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
          >
            {compile.isPending ? "Planning…" : draft ? "Plan again" : "Show the plan"}
          </button>
          {task && <SaveAsAgent text={task.created_from ?? text} />}
        </div>
      </form>

      <ErrorLine error={compile.error} testId="one-time-error" />

      {draft && (
        <DraftView
          draft={draft}
          answers={answers}
          setAnswer={(id, v) => setAnswers((a) => ({ ...a, [id]: v }))}
          onReplan={() => compile.mutate()}
          replanning={compile.isPending}
        />
      )}

      {draft && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="one-time-run"
            disabled={!!blocker || run.isPending}
            onClick={() => run.mutate(draft.draft_id)}
            className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:bg-accent/90 disabled:opacity-40"
          >
            <Play className="h-3.5 w-3.5" aria-hidden />
            {run.isPending ? "Running once…" : "Run once"}
          </button>
          {blocker && (
            <span data-testid="one-time-run-blocker" className="text-2xs text-band-review">
              Cannot run: {blocker}.
            </span>
          )}
          <SaveAsAgent text={draft.created_from} />
        </div>
      )}
      <ErrorLine error={run.error} testId="one-time-run-error" />

      {task && <TaskView task={task} onChange={setTask} />}
    </section>
  );
}

// ============================================================================================
// the plan
// ============================================================================================
function DraftView({
  draft,
  answers,
  setAnswer,
  onReplan,
  replanning,
}: {
  draft: OneTimeDraft;
  answers: Record<string, string>;
  setAnswer: (id: string, v: string) => void;
  onReplan: () => void;
  replanning: boolean;
}) {
  const plan = draft.plan;
  return (
    <div data-testid="one-time-draft" className="flex flex-col gap-3 text-xs">
      {draft.understood && <p className="whitespace-pre-wrap text-fg">{draft.understood}</p>}

      {draft.open_questions.length > 0 && (
        <div data-testid="one-time-questions" className="flex flex-col gap-2 rounded border border-band-spot/40 p-2.5">
          <p className="text-2xs font-semibold uppercase tracking-wide text-band-spot">Questions before it can run</p>
          {draft.open_questions.map((q) => (
            <label key={q.id} className="flex flex-col gap-1">
              <span className="text-fg">{q.text}</span>
              {q.options.length > 0 ? (
                <select
                  value={answers[q.id] ?? ""}
                  onChange={(e) => setAnswer(q.id, e.target.value)}
                  className="w-fit rounded border border-border bg-bg px-2 py-1 text-xs"
                >
                  <option value="">choose…</option>
                  {q.options.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={answers[q.id] ?? ""}
                  onChange={(e) => setAnswer(q.id, e.target.value)}
                  className="rounded border border-border bg-bg px-2 py-1 text-xs"
                />
              )}
            </label>
          ))}
          <button
            type="button"
            onClick={onReplan}
            disabled={replanning}
            className="w-fit rounded border border-border px-2.5 py-1 text-xs hover:border-accent/60 disabled:opacity-40"
          >
            Plan again with these answers
          </button>
        </div>
      )}

      {plan && (
        <div data-testid="one-time-plan-view" className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="rounded border border-border p-2.5">
            <p className="text-2xs font-semibold uppercase tracking-wide text-subtle">It will read</p>
            <ul className="mt-1 flex flex-col gap-1">
              {readsByConnection(plan.reads).map((g) => (
                <li key={g.connection ?? "_internal"} data-testid="one-time-read">
                  <span className="font-medium text-fg">{g.connection ?? "the graph and the passage index"}</span>
                  <span className="block font-mono text-2xs text-subtle">{g.tools.join(", ")}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="rounded border border-border p-2.5">
            <p className="text-2xs font-semibold uppercase tracking-wide text-subtle">It would write</p>
            {plan.writes.length === 0 ? (
              <p data-testid="one-time-no-writes" className="mt-1 text-subtle">Nothing &mdash; read-only.</p>
            ) : (
              <ul className="mt-1 flex flex-col gap-1">
                {plan.writes.map((w) => (
                  <li key={w.name} data-testid="one-time-write">
                    <span className="font-medium text-fg">{w.description || w.name}</span>
                    <span className="block font-mono text-2xs text-subtle">
                      {w.tool} · waits for your approval ({w.automation_mode})
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="rounded border border-border p-2.5">
            <p className="text-2xs font-semibold uppercase tracking-wide text-subtle">Granted</p>
            {plan.grants ? (
              <dl data-testid="one-time-grants" className="mt-1 flex flex-col gap-0.5 text-2xs">
                <dt className="text-subtle">purpose</dt>
                <dd className="font-mono text-fg">{plan.grants.purpose}</dd>
                <dt className="text-subtle">capabilities</dt>
                <dd className="font-mono text-fg">
                  {plan.grants.capabilities.map((g) => `${g.source_id}.${g.capability}`).join(", ") || "none"}
                </dd>
                <dt className="text-subtle">golden set</dt>
                <dd className="font-mono text-fg">
                  {plan.grants.golden_set} ({plan.grants.golden_set_records} records)
                </dd>
                <dt className="text-subtle">citation</dt>
                <dd className="font-mono text-fg">{plan.grants.citation}</dd>
              </dl>
            ) : (
              <p className="mt-1 text-band-review">Not granted: the plan did not compile.</p>
            )}
          </div>
        </div>
      )}

      {[...draft.notes, ...draft.assumptions].length > 0 && (
        <ul data-testid="one-time-notes" className="list-disc pl-4 text-2xs text-subtle">
          {[...draft.notes, ...draft.assumptions].map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ============================================================================================
// the result: answer, outcome, held writes
// ============================================================================================
function TaskView({ task, onChange }: { task: OneTimeTask; onChange: (t: OneTimeTask) => void }) {
  const head = taskHeadline(task);
  const run = task.run;
  const retry = useMutation({ mutationFn: () => oneTimeApi.retry(task.task_id), onSuccess: onChange });
  return (
    <div data-testid="one-time-result" className="flex flex-col gap-3 text-xs">
      <p
        data-testid="one-time-headline"
        data-tone={head.tone}
        className={cn(
          "rounded border px-2.5 py-1.5 font-medium",
          head.tone === "waiting" && "border-band-spot/50 text-band-spot",
          head.tone === "done" && "border-band-auto/50 text-band-auto",
          head.tone === "failed" && "border-band-review/50 text-band-review",
        )}
      >
        {head.text}
      </p>
      {canRetry(task) && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="one-time-retry"
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
            className="flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
          >
            <Play className="h-3.5 w-3.5" aria-hidden /> {retry.isPending ? "Retrying…" : "Retry once"}
          </button>
          <ErrorLine error={retry.error} testId="one-time-retry-error" />
        </div>
      )}

      {heldWrites(task).length > 0 || task.actions.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {task.actions.map((a) => (
            <ActionRow key={a.action_id} task={task} action={a} onChange={onChange} />
          ))}
        </ul>
      ) : null}

      {run && <Answer answer={run.answer} />}

      {run?.outcome && (
        <dl data-testid="one-time-outcome" className="grid grid-cols-[10rem_1fr] gap-x-2 gap-y-0.5 rounded border border-border p-2.5 text-2xs">
          <dt className="text-subtle">outcome</dt>
          <dd className="font-mono text-fg">{run.outcome.reason_code}</dd>
          <dt className="text-subtle">coverage</dt>
          <dd className="font-mono text-fg">{run.outcome.coverage_status}</dd>
          <dt className="text-subtle">a person must confirm</dt>
          <dd className="font-mono text-fg">{run.outcome.human_confirmation_required ? "yes" : "no"}</dd>
          {run.outcome.not_connected.length > 0 && (
            <>
              <dt className="text-subtle">not connected</dt>
              <dd className="font-mono text-fg">{run.outcome.not_connected.join(", ")}</dd>
            </>
          )}
          {run.outcome.withheld.length > 0 && (
            <>
              <dt className="text-subtle">withheld by the screen</dt>
              <dd className="font-mono text-fg">{run.outcome.withheld.length}</dd>
            </>
          )}
          {run.outcome.candidates.length > 0 && (
            <>
              <dt className="text-subtle">candidates to confirm</dt>
              <dd className="font-mono text-fg">{run.outcome.candidates.length}</dd>
            </>
          )}
        </dl>
      )}

      <p className="text-2xs text-subtle">
        Recorded in{" "}
        <Link href="/trace?view=timeline" className="text-accent hover:underline">
          Activity
        </Link>{" "}
        as a one-time task (<span className="font-mono">{task.task_id}</span>, run{" "}
        <span className="font-mono">{task.run_id}</span>) &mdash; visible to you only.
      </p>
    </div>
  );
}

function ActionRow({
  task,
  action,
  onChange,
}: {
  task: OneTimeTask;
  action: OneTimeAction;
  onChange: (t: OneTimeTask) => void;
}) {
  const [reason, setReason] = React.useState("");
  const approve = useMutation({
    mutationFn: () => oneTimeApi.approve(task.task_id, action.action_id),
    onSuccess: onChange,
  });
  const reject = useMutation({
    mutationFn: () => oneTimeApi.reject(task.task_id, action.action_id, reason),
    onSuccess: onChange,
  });
  const held = action.status === "proposed" || action.status === "approved";
  const withheld = withheldArgs(action);
  return (
    <li data-testid={`one-time-action-${action.action_id}`} className="rounded border border-border p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-fg">{action.name}</span>
        <span data-testid={`one-time-action-status-${action.action_id}`} className="font-mono text-2xs text-subtle">
          {action.status}
          {action.by ? ` by ${action.by}` : ""}
        </span>
      </div>
      <p className="font-mono text-2xs text-subtle">{action.tool}</p>
      {withheld ? (
        <p data-testid="one-time-args-withheld" className="mt-1 text-2xs text-band-review">
          The values of this write are withheld. {describeValueState(withheld)}
        </p>
      ) : (
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-bg p-2 font-mono text-2xs text-fg">
          {JSON.stringify(action.args, null, 2)}
        </pre>
      )}
      {held && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid={`one-time-approve-${action.action_id}`}
            disabled={approve.isPending || reject.isPending}
            onClick={() => approve.mutate()}
            className="flex items-center gap-1 rounded border border-band-auto/50 px-2.5 py-1 text-xs text-band-auto disabled:opacity-40"
          >
            <Check className="h-3.5 w-3.5" aria-hidden /> {approve.isPending ? "Writing…" : "Approve and write"}
          </button>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="reason (optional)"
            className="rounded border border-border bg-bg px-2 py-1 text-2xs"
            aria-label="Reason for rejecting"
          />
          <button
            type="button"
            data-testid={`one-time-reject-${action.action_id}`}
            disabled={approve.isPending || reject.isPending}
            onClick={() => reject.mutate()}
            className="flex items-center gap-1 rounded border border-band-review/50 px-2.5 py-1 text-xs text-band-review disabled:opacity-40"
          >
            <X className="h-3.5 w-3.5" aria-hidden /> Reject
          </button>
        </div>
      )}
      <ErrorLine error={approve.error ?? reject.error} testId="one-time-decision-error" />
    </li>
  );
}

function Answer({ answer }: { answer: { markdown: string; citations: PointerJson[] } & Record<string, unknown> }) {
  const { byRef, basis } = React.useMemo(() => resolveCitations(answer), [answer]);
  const [open, setOpen] = React.useState<string | null>(null);
  const md = React.useMemo(() => answer.markdown.replace(/\[(p\d+)\](?!\()/g, "[$1](#cite-$1)"), [answer.markdown]);
  const pointer = open ? byRef[open] : undefined;
  return (
    <div className="flex flex-col gap-2">
      <div
        data-testid="one-time-answer"
        className="prose-sm max-w-none text-xs leading-relaxed text-fg [&_li]:ml-4 [&_li]:list-disc [&_p]:my-1.5"
      >
        <SafeMarkdown
          components={{
            a: ({ href, children }) => {
              const label = React.Children.toArray(children).join("");
              const ref = citationRefFromLink(href, label);
              if (href?.startsWith("#cite-") && !ref) return <span>{children}</span>;
              if (ref) {
                return (
                  <button
                    type="button"
                    data-testid={`one-time-citation-${ref}`}
                    onClick={() => setOpen((o) => (o === ref ? null : ref))}
                    className={cn(
                      "mx-0.5 rounded-sm border px-1 font-mono text-[10px] leading-4",
                      ref in byRef ? "border-accent/50 text-accent" : "border-band-review/50 text-band-review",
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
          }}
        >
          {md}
        </SafeMarkdown>
      </div>
      {open && (
        <div data-testid="one-time-citation-detail" className="rounded border border-accent/40 p-2 text-2xs">
          {pointer ? (
            <>
              <p className="text-fg">{pointerWhere(pointer)}</p>
              {basis === "text" && (
                <p className="text-band-spot">Paired from the answer&rsquo;s Sources list, not a recorded map: unverified.</p>
              )}
              <Link href={evidenceHref(pointer)} className="text-accent hover:underline">
                Open in Provenance →
              </Link>
            </>
          ) : (
            <p className="text-band-review">This ref could not be paired with a recorded pointer, so none is shown.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================================
// save as agent — the Builder's normal path, from here
// ============================================================================================
function SaveAsAgent({ text }: { text: string }) {
  const [saved, setSaved] = React.useState<string | null>(null);
  const save = useMutation({
    mutationFn: async () => {
      const d = await agentsApi.compile(text, {});
      if (!d.spec || d.open_questions.length > 0) return { draft: d, id: null as string | null };
      const s = await agentsApi.save(d.draft_id, false);
      return { draft: d, id: s.id };
    },
    onSuccess: (r) => setSaved(r.id),
  });
  if (saved)
    return (
      <span data-testid="one-time-saved" className="text-2xs text-fg">
        Saved as a draft agent &mdash;{" "}
        <Link href={`/builder?agent=${encodeURIComponent(saved)}`} className="text-accent hover:underline">
          review and activate it in the Builder
        </Link>
        .
      </span>
    );
  const needsBuilder = save.data && !save.data.id;
  return (
    <span className="flex items-center gap-2">
      <button
        type="button"
        data-testid="one-time-save-agent"
        disabled={!text.trim() || save.isPending}
        onClick={() => save.mutate()}
        className="flex items-center gap-1 rounded border border-border px-2.5 py-1 text-xs text-fg hover:border-accent/60 disabled:opacity-40"
      >
        <Save className="h-3.5 w-3.5" aria-hidden /> {save.isPending ? "Saving…" : "Save as agent"}
      </button>
      {needsBuilder && (
        <span className="text-2xs text-band-spot">
          As an agent this needs answers first ({save.data!.draft.open_questions.length}):{" "}
          <Link href="/builder" className="text-accent hover:underline">
            open the Builder
          </Link>
          .
        </span>
      )}
      <ErrorLine error={save.error} testId="one-time-save-error" />
    </span>
  );
}
