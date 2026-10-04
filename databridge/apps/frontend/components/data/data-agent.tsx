"use client";
/**
 * The Data Agent — describe the dataset you need; it plans against what the brain holds, the
 * compiler checks the plan for leakage, the executor builds it, and you get a receipt and files.
 *
 * Every thing drawn here is an answer from the gateway: the shapes from `GET /materialise/plans`,
 * the stages from the job's own progress events, the plan from the `plan.yaml` the run wrote, the
 * receipt from `receipt.json`, a refusal from the job's `refusal`. There is no progress animation
 * that the job did not report, and nothing is shown before the gateway said it.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Bot, Loader2, Send, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import {
  dataApi,
  fetchRunFile,
  type JobEvent,
  type MaterialiseJob,
  type Refusal,
  type RunSummary,
} from "@/lib/data-api";
import { ShapePicker, shapeRows } from "./shape-picker";
import { PlanSummary } from "./plan-summary";
import { ReceiptCard } from "./receipt-card";
import { explain } from "./refusals";
import { flattenPlanYaml, understand } from "./plan-yaml";
import { catalogueExclusions, planAttribution, receiptIntegrity, type ReceiptState } from "./receipt-gate";

const EXAMPLES: { label: string; text: string }[] = [
  // Domain-free (SWEEP #23): this is a platform page under every pack.
  { label: "Past due (point-in-time)", text: "one row per item at creation time, label = past due after 30 days" },
  { label: "RAG corpus of documents", text: "a RAG chunk corpus of documents for retrieval" },
];

const STAGE_WORDS: Record<string, string> = {
  reading_catalogue: "Reading what the brain holds",
  authoring_plan: "Writing a plan",
  materialising: "Checking the plan for leakage and building it",
  checking_pointers: "Re-reading every cited source",
  done: "Done",
};

type Phase = "submitting" | "running" | "succeeded" | "refused" | "failed" | "loaded";

interface Turn {
  id: number;
  sent: string;
  /** Answers to an earlier turn's planner questions, sent with `sent` (E2E run 3, P-10). */
  answers?: Record<string, string>;
  shape: string | null;
  phase: Phase;
  jobId?: string;
  job?: MaterialiseJob;
  refusal?: Refusal;
  error?: string;
  run?: RunSummary;
  /** The authoritative receipt: loading until the gateway answers, then the doc or its refusal. */
  receipt?: ReceiptState;
  yaml?: string | null;
  detailError?: string;
}

function shapeWords(shape: string) {
  return shape.replace(/_/g, " ");
}

function Stages({ events, running }: { events: JobEvent[]; running: boolean }) {
  if (events.length === 0 && running) {
    return <div className="text-2xs text-subtle">Job accepted — waiting for its first progress event.</div>;
  }
  return (
    <ol className="flex flex-col gap-0.5" data-testid="job-stages">
      {events.map((e, i) => (
        <li key={i} className="flex items-baseline gap-2 text-2xs" data-stage={e.stage}>
          <span className="w-16 shrink-0 font-mono text-subtle">{e.at ? e.at.slice(11, 19) : ""}</span>
          <span className="text-fg">{STAGE_WORDS[e.stage] ?? e.stage}</span>
          {e.planner ? <span className="text-subtle">planner: {e.planner}</span> : null}
          {e.shape ? <span className="font-mono text-subtle">{e.shape}</span> : null}
          {e.plan_hash && e.stage !== "done" ? <span className="font-mono text-subtle">plan {e.plan_hash.slice(0, 12)}…</span> : null}
          {running && i === events.length - 1 ? <Loader2 className="h-3 w-3 animate-spin text-subtle" /> : null}
        </li>
      ))}
    </ol>
  );
}

/** The planner's questions, each answerable in place; the answers go back with the same request. */
function QuestionForm({
  refusal,
  onAnswer,
  disabled,
}: {
  refusal: Refusal;
  onAnswer?: (answers: Record<string, string>) => void;
  disabled?: boolean;
}) {
  const items = refusal.question_items ?? [];
  const [draft, setDraft] = React.useState<Record<string, string>>({});
  if (items.length === 0 || !onAnswer) return null;
  const given = Object.fromEntries(Object.entries(draft).filter(([, v]) => v.trim()));
  return (
    <form
      className="mt-2 flex flex-col gap-2"
      data-testid="agent-questions"
      onSubmit={(e) => {
        e.preventDefault();
        if (Object.keys(given).length > 0) onAnswer(given);
      }}
    >
      {items.map((q) => (
        <label key={q.id} className="flex flex-col gap-1" data-testid="agent-question" data-question-id={q.id}>
          <span className="text-fg">{q.text}</span>
          <Textarea
            rows={2}
            data-testid="agent-answer"
            value={draft[q.id] ?? ""}
            onChange={(e) => setDraft((d) => ({ ...d, [q.id]: e.target.value }))}
            placeholder="Your answer"
          />
        </label>
      ))}
      <div>
        <Button type="submit" data-testid="agent-answer-send" disabled={disabled || Object.keys(given).length === 0}>
          <Send className="h-3.5 w-3.5" /> Answer and re-run
        </Button>
      </div>
    </form>
  );
}

function RefusalBlock({
  refusal,
  heading,
  onAnswer,
  answerDisabled,
}: {
  refusal: Refusal;
  heading: string;
  onAnswer?: (answers: Record<string, string>) => void;
  answerDisabled?: boolean;
}) {
  const ex = explain(refusal.code);
  return (
    <div
      data-testid="agent-refusal"
      data-code={refusal.code}
      className="rounded border border-band-review/40 bg-band-review/[0.06] p-3 text-xs"
      role="status"
    >
      <div className="mb-1 flex items-center gap-1.5 font-semibold text-band-review">
        <AlertTriangle className="h-3.5 w-3.5" /> {heading}
        <span className="font-mono text-2xs font-normal">{refusal.code}</span>
      </div>
      {ex ? <p className="text-fg">{ex.headline}</p> : null}
      {ex?.remedy ? <p className="mt-1 text-fg">{ex.remedy}</p> : null}
      <p className="mt-1.5 whitespace-pre-wrap text-2xs text-subtle" data-testid="agent-refusal-message">
        {ex ? "The compiler's words: " : ""}
        {refusal.message}
      </p>
      {refusal.missing && refusal.missing.length > 0 && (
        <ul className="mt-1 list-disc pl-4 text-2xs text-subtle">
          {refusal.missing.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      {refusal.nearest_alternative ? (
        <p className="mt-1 text-2xs text-subtle" data-testid="agent-refusal-alternative">
          Nearest alternative: {refusal.nearest_alternative}
          {refusal.nearest_alternative.includes("/materialise/designs") ? (
            <>
              {" "}
              <a className="underline" href="#raw">
                Open Raw dataset by delegation
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      <QuestionForm refusal={refusal} onAnswer={onAnswer} disabled={answerDisabled} />
    </div>
  );
}

/** Says, prominently, whether the authoritative receipt loaded and is for this run. */
function ReceiptStatus({ run, receipt }: { run: RunSummary; receipt: ReceiptState }) {
  if (receipt.state === "loading") {
    return (
      <div className="text-2xs text-subtle" data-testid="receipt-status" data-state="loading">
        Reading the full receipt… until it arrives, what follows is the job&apos;s summary and dataset downloads are held.
      </div>
    );
  }
  if (receipt.state === "refused") {
    return (
      <div
        role="alert"
        data-testid="receipt-status"
        data-state="refused"
        className="rounded border border-band-review/40 bg-band-review/[0.06] p-2 text-2xs text-band-review"
      >
        The full receipt could not be loaded ({receipt.refusal.code}: {receipt.refusal.message}). What follows is the job&apos;s summary
        only — pinned sources, the leakage check, refused columns and policy are not shown, and dataset downloads are withheld.
      </div>
    );
  }
  const problems = receiptIntegrity(run, receipt.doc);
  if (problems.length === 0) return null;
  return (
    <div
      role="alert"
      data-testid="receipt-status"
      data-state="mismatch"
      className="rounded border border-band-review/40 bg-band-review/[0.06] p-2 text-2xs text-band-review"
    >
      Integrity error — the receipt the gateway returned is not for this run: {problems.join("; ")}. It is not shown as this run&apos;s
      receipt, and dataset downloads are withheld.
    </div>
  );
}

/**
 * plan.yaml, shown as "the plan that ran" only when it declares this run's plan_hash and agrees
 * with the run AND with a receipt that passed integrity. Without a valid receipt it is shown as
 * unverified; an invalid receipt never vouches for it (R-021 round 2, finding 4).
 */
function PlanBlock({ t, run }: { t: Turn; run: RunSummary }) {
  const attribution = React.useMemo(() => {
    if (!t.yaml) return null;
    const u = understand(flattenPlanYaml(t.yaml));
    return planAttribution(run, t.receipt, { planHash: u.planHash, shape: u.shape, entity: u.entity });
  }, [t.yaml, t.receipt, run]);
  if (t.yaml === undefined) return <div className="text-2xs text-subtle">Reading plan.yaml…</div>;
  if (!t.yaml) return <div className="text-2xs text-subtle">plan.yaml could not be read{t.detailError ? `: ${t.detailError}` : ""}.</div>;
  if (attribution && attribution.status === "mismatch") {
    return (
      <div role="alert" data-testid="plan-integrity" className="rounded border border-band-review/40 bg-band-review/[0.06] p-2 text-2xs text-band-review">
        Integrity error — the plan.yaml returned is not attributable to this run: {attribution.problems.join("; ")}. Its contents are not
        shown as the plan that ran.
      </div>
    );
  }
  return <PlanSummary yaml={t.yaml} verified={attribution?.status === "verified"} unverifiedNote={attribution?.note ?? null} />;
}

function AgentTurn({
  t,
  onAnswer,
  busy,
}: {
  t: Turn;
  onAnswer?: (answers: Record<string, string>) => void;
  busy?: boolean;
}) {
  const running = t.phase === "submitting" || t.phase === "running";
  return (
    <div className="flex gap-2" data-testid="agent-turn" data-phase={t.phase} data-job-id={t.jobId ?? ""}>
      <Bot className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {t.phase === "submitting" && <div className="text-2xs text-subtle">Sending to the gateway…</div>}
        {t.job && <Stages events={t.job.events} running={running} />}

        {t.phase === "refused" && t.refusal && (
          <RefusalBlock
            refusal={t.refusal}
            heading={t.jobId ? "Refused — nothing was built" : "The gateway refused the request"}
            onAnswer={onAnswer}
            answerDisabled={busy}
          />
        )}
        {t.phase === "failed" && (
          <div data-testid="agent-failed" className="rounded border border-band-review/40 bg-band-review/[0.06] p-3 text-xs text-band-review">
            The run failed and no receipt was issued: <span className="font-mono">{t.error ?? "no error text"}</span>
          </div>
        )}

        {(t.phase === "succeeded" || t.phase === "loaded") && t.run && (
          <>
            {t.run.shape && t.shape && t.run.shape !== t.shape && (
              <div className="text-2xs text-band-spot" data-testid="shape-mismatch">
                You picked <span className="font-mono">{t.shape}</span>; the agent built <span className="font-mono">{t.run.shape}</span>. The
                receipt below is for what was built.
              </div>
            )}
            <ReceiptStatus run={t.run} receipt={t.receipt ?? { state: "loading" }} />
            <PlanBlock t={t} run={t.run} />
            <ReceiptCard run={t.run} receipt={t.receipt ?? { state: "loading" }} />
          </>
        )}
      </div>
    </div>
  );
}

export function DataAgent() {
  const plansQ = useQuery({ queryKey: ["materialise-plans"], queryFn: dataApi.plans, retry: false });
  const catQ = useQuery({ queryKey: ["materialise-catalogue"], queryFn: dataApi.catalogue, retry: false });
  const receiptsQ = useQuery({ queryKey: ["materialise-receipts"], queryFn: dataApi.receipts, retry: false });

  const [text, setText] = React.useState("");
  const [shape, setShape] = React.useState<string | null>(null);
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const alive = React.useRef(true);
  const nextId = React.useRef(1);
  React.useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const patch = React.useCallback((id: number, p: Partial<Turn>) => {
    if (!alive.current) return;
    setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...p } : t)));
  }, []);

  const loadDetails = React.useCallback(
    async (id: number, planHash: string) => {
      const [doc, yaml] = await Promise.all([
        dataApi.receipt(planHash),
        fetchRunFile(planHash, "plan.yaml")
          .then((b) => b.text())
          .catch((e: unknown) => {
            patch(id, { detailError: e instanceof Error ? e.message : "read failed" });
            return null;
          }),
      ]);
      patch(id, { receipt: doc.ok ? { state: "ok", doc: doc.body } : { state: "refused", refusal: doc.refusal }, yaml });
    },
    [patch],
  );

  const running = turns.some((t) => t.phase === "submitting" || t.phase === "running");
  const available = plansQ.data?.available ?? false;

  async function send(message: string) {
    const body = message.trim();
    if (!body || running) return;
    const sent = shape ? `${body}\nOutput shape: ${shapeWords(shape)}.` : body;
    setText("");
    await submit(sent, shape);
  }

  /** Answer a turn's planner questions: the SAME request goes back with `answers` by id. Earlier
   * answers to that request are carried, so a second question does not lose the first answer. */
  async function answer(t: Turn, given: Record<string, string>) {
    if (running) return;
    await submit(t.sent, t.shape, { ...(t.answers ?? {}), ...given });
  }

  async function submit(sent: string, turnShape: string | null, answers?: Record<string, string>) {
    const id = nextId.current++;
    setTurns((ts) => [...ts, { id, sent, answers, shape: turnShape, phase: "submitting" }]);

    const post = await dataApi.submit(sent, answers);
    if (!post.ok) {
      patch(id, { phase: "refused", refusal: post.refusal });
      return;
    }
    const jobId = post.body.job_id;
    patch(id, { phase: "running", jobId });
    for (;;) {
      if (!alive.current) return;
      const j = await dataApi.job(jobId);
      if (!j.ok) {
        patch(id, { phase: "refused", refusal: j.refusal });
        return;
      }
      const job = j.body;
      if (job.status === "running") {
        patch(id, { job });
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      if (job.status === "succeeded" && job.result) {
        patch(id, { job, phase: "succeeded", run: job.result, receipt: { state: "loading" } });
        void loadDetails(id, job.result.plan_hash);
        void receiptsQ.refetch();
      } else if (job.status === "refused") {
        patch(id, {
          job,
          phase: "refused",
          refusal: job.refusal ?? { code: "REFUSED", message: job.error ?? "the job was refused without a reason" },
        });
      } else {
        patch(id, { job, phase: "failed", error: job.error ?? undefined });
      }
      return;
    }
  }

  function openPrevious(r: RunSummary) {
    const id = nextId.current++;
    setTurns((ts) => [...ts, { id, sent: r.request ?? `(plan ${r.plan_hash.slice(0, 12)}… submitted as a plan)`, shape: null, phase: "loaded", run: r, receipt: { state: "loading" } }]);
    void loadDetails(id, r.plan_hash);
  }

  const cat = catQ.data;
  const entities = cat?.ok ? cat.body.entities : [];
  const excluded = cat?.ok ? catalogueExclusions(cat.body.excluded) : [];
  const byOrigin = entities.reduce<Record<string, number>>((acc, e) => ((acc[e.origin] = (acc[e.origin] ?? 0) + 1), acc), {});

  return (
    <div className="flex flex-col gap-3">
      {/* where the data comes from */}
      <div className="rounded border border-border bg-bg/50 px-3 py-2 text-2xs text-subtle" data-testid="agent-source-note">
        The agent builds from the <span className="text-fg">brain</span> — the sources you have already ingested, read as of one pinned
        instant. {catQ.isLoading && "Reading the catalogue…"}
        {cat?.ok && (
          <span data-testid="agent-catalogue" data-entities={entities.length}>
            {entities.length === 0 ? (
              <> Right now it holds nothing it can read: ingest a source on the Sources page first.</>
            ) : (
              <>
                {" "}
                Right now it can read {entities.length} entit{entities.length === 1 ? "y" : "ies"} (
                {Object.entries(byOrigin)
                  .map(([o, n]) => `${n} ${o}`)
                  .join(", ")}
                ) as of <span className="font-mono">{cat.body.system_as_of}</span>.
              </>
            )}
          </span>
        )}
        {cat && !cat.ok && <span className="text-band-review"> Catalogue unavailable — {cat.refusal.code}: {cat.refusal.message}</span>}{" "}
        <span className="italic">Gathering live from every source (Phase G) is coming; today it reads what the brain has.</span>
        {cat?.ok && (
          <details className="mt-1" data-testid="agent-catalogue-excluded" data-count={excluded.length}>
            <summary className="cursor-pointer">
              {excluded.length === 0
                ? "Screening excluded nothing."
                : `Screening excluded ${excluded.length} entit${excluded.length === 1 ? "y or field" : "ies or fields"} — see why`}
            </summary>
            {excluded.length > 0 && (
              <ul className="mt-1 list-disc pl-4">
                {excluded.map((e, i) => (
                  <li key={i}>
                    <span className="font-mono text-fg">{e.what}</span>
                    {e.origin ? <span> ({e.origin})</span> : null}: {e.reason}
                  </li>
                ))}
              </ul>
            )}
          </details>
        )}
      </div>

      {plansQ.isError && (
        <div className="flex items-start gap-1.5 text-2xs text-band-review" role="alert" data-testid="plans-error">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Could not load the shapes: {(plansQ.error as Error).message}
        </div>
      )}
      {plansQ.data && !plansQ.data.available && (
        <div className="rounded border border-dashed border-border p-3 text-2xs text-subtle" data-testid="materialiser-missing">
          <div className="font-medium text-fg">No materialiser is attached to this gateway.</div>
          {plansQ.data.note ? <p>{plansQ.data.note}</p> : null}
          <ul className="mt-1 list-disc pl-4">
            {plansQ.data.missing.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}

      {/* conversation */}
      <div className="flex flex-col gap-3" data-testid="agent-conversation">
        {turns.length === 0 && (
          <div className="text-xs text-subtle" data-testid="agent-empty">
            Describe the dataset you need — what one row is, what to predict and how long to wait for the answer, or what corpus to
            retrieve over. The agent answers with the plan it ran and a receipt you can check.
          </div>
        )}
        {turns.map((t) => (
          <React.Fragment key={t.id}>
            <div className="flex gap-2" data-testid="user-turn">
              <User className="mt-0.5 h-4 w-4 shrink-0 text-subtle" />
              <div className="flex min-w-0 flex-col gap-1">
                <div className="whitespace-pre-wrap text-xs text-fg">{t.sent}</div>
                {t.answers && Object.keys(t.answers).length > 0 && (
                  <ul className="text-2xs text-subtle" data-testid="user-answers">
                    {Object.entries(t.answers).map(([qid, a]) => (
                      <li key={qid}>
                        Answered: <span className="text-fg">{a}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <AgentTurn t={t} busy={running} onAnswer={(given) => void answer(t, given)} />
          </React.Fragment>
        ))}
      </div>

      {/* composer */}
      <div className="flex flex-col gap-2 rounded border border-border bg-surface p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-2xs text-subtle">Try:</span>
          {EXAMPLES.map((e) => (
            <button
              key={e.label}
              type="button"
              data-testid="agent-example"
              onClick={() => setText(e.text)}
              className="rounded-full border border-border px-2 py-0.5 text-2xs text-subtle hover:bg-muted hover:text-fg"
            >
              {e.label}
            </button>
          ))}
        </div>
        <Textarea
          data-testid="agent-input"
          value={text}
          rows={3}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send(text);
          }}
          placeholder="e.g. one row per item at creation time, label = past due after 30 days"
        />
        <div>
          <div className="mb-1 text-2xs uppercase tracking-wide text-subtle">
            Output shape{" "}
            <span className="normal-case">
              — {plansQ.isLoading ? "loading…" : `${shapeRows(plansQ.data).filter((r) => r.built).length} of ${shapeRows(plansQ.data).length} built`}
            </span>
          </div>
          <ShapePicker plans={plansQ.data} value={shape} onChange={setShape} disabled={running} />
          {shape && (
            <p className="mt-1 text-2xs text-subtle">
              Added to your request as “Output shape: {shapeWords(shape)}.” The agent decides; the receipt reports what it built.
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button data-testid="agent-send" onClick={() => void send(text)} disabled={running || !text.trim() || !available}>
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
            {running ? "Working…" : "Build dataset"}
          </Button>
          <span className="text-2xs text-subtle">Ctrl+Enter to send. One run at a time.</span>
        </div>
      </div>

      {/* earlier runs */}
      <details className="rounded border border-border bg-surface p-3 text-xs" data-testid="agent-previous">
        <summary className="cursor-pointer text-2xs uppercase tracking-wide text-subtle">
          Earlier datasets on this gateway{receiptsQ.data ? ` (${receiptsQ.data.receipts.length})` : ""}
        </summary>
        {receiptsQ.isError && <p className="mt-2 text-2xs text-band-review">{(receiptsQ.error as Error).message}</p>}
        {receiptsQ.data && receiptsQ.data.receipts.length === 0 && <p className="mt-2 text-2xs text-subtle">No dataset has been built yet.</p>}
        <ul className="mt-2 flex flex-col gap-1">
          {receiptsQ.data?.receipts.map((r) => (
            <li key={r.plan_hash} className="flex items-baseline gap-2 text-2xs">
              <button type="button" className="font-mono text-accent hover:underline" onClick={() => openPrevious(r)}>
                {r.plan_hash.slice(0, 12)}…
              </button>
              <span className="font-mono text-subtle">{r.shape}</span>
              <span className="text-subtle">{r.rows?.emitted ?? "—"} rows</span>
              <span className="truncate text-fg">{r.request ?? "(submitted as a plan)"}</span>
              <span className="ml-auto shrink-0 text-subtle">{r.executed_at}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
