"use client";
/**
 * The Activity timeline: every agent run, delegated question, adapter-agent sync and set-up step,
 * one-time task, automation firing, approval decision, adapter-agent proposal and human decision
 * (plan, mapping, link, documents, hand-off, design, outbox) the signed-in principal may see,
 * newest first, straight from `GET /activity`. "Load older" follows the gateway's cursor; nothing polls.
 */
import * as React from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { AlertTriangle, Inbox } from "lucide-react";
import {
  ACTIVITY_KINDS,
  KIND_LABEL,
  actorText,
  fetchActivity,
  isPerson,
  formatDuration,
  formatTokens,
  itemTone,
  type ActivityItem,
  type ActivityKind,
} from "@/lib/activity-api";
import { Producer, StatusPill, TONE_BORDER, when } from "./activity-bits";

export function ActivityTimeline({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (item: ActivityItem) => void;
}) {
  const [kinds, setKinds] = React.useState<ActivityKind[]>([]);
  const query = useInfiniteQuery({
    queryKey: ["activity", kinds.join(",")],
    queryFn: ({ pageParam, signal }) =>
      fetchActivity({ limit: 50, before: pageParam, kinds }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
    staleTime: 15_000,
  });
  const pages = query.data?.pages ?? [];
  const items = pages.flatMap((p) => p.items);
  const head = pages[0];

  const toggle = (k: ActivityKind) =>
    setKinds((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]));

  return (
    <div className="flex flex-col gap-3" data-testid="activity-timeline">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-2xs uppercase tracking-wider text-subtle">show</span>
        {ACTIVITY_KINDS.map((k) => {
          const on = kinds.length === 0 || kinds.includes(k);
          return (
            <button
              key={k}
              type="button"
              onClick={() => toggle(k)}
              aria-pressed={kinds.includes(k)}
              data-testid={`activity-kind-${k}`}
              className={`rounded border px-2 py-0.5 text-2xs ${
                on ? "border-accent/50 text-fg" : "border-border text-subtle"
              } ${kinds.includes(k) ? "bg-accent/10" : ""}`}
            >
              {KIND_LABEL[k]}
              {head?.counts?.[k] !== undefined && kinds.length === 0 ? (
                <span className="tnum ml-1 text-subtle">{head.counts[k]}</span>
              ) : null}
            </button>
          );
        })}
        {kinds.length ? (
          <button type="button" className="text-2xs text-accent hover:underline" onClick={() => setKinds([])}>
            all
          </button>
        ) : null}
      </div>

      {query.isLoading ? (
        <p className="text-xs text-subtle" data-testid="activity-loading">
          Reading the run records, firings and approvals…
        </p>
      ) : null}

      {query.isError ? (
        <div
          role="alert"
          data-testid="activity-error"
          className="flex items-start gap-2 rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-xs text-band-review"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Could not load <code>GET /activity</code>: {(query.error as Error).message}. Nothing is
            shown in its place.
          </span>
        </div>
      ) : null}

      {head && head.problems.length ? (
        <p className="text-2xs text-band-spot" data-testid="activity-problems">
          {head.problems.length} record(s) could not be read and are not shown:{" "}
          {head.problems
            .slice(0, 3)
            .map((p) => `${p.source}${p.file ? `/${p.file}` : ""} (${p.error})`)
            .join("; ")}
          {head.problems.length > 3 ? " …" : ""}
        </p>
      ) : null}

      {query.isSuccess && items.length === 0 ? (
        <div
          data-testid="activity-empty"
          className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-surface px-6 py-12 text-center"
        >
          <Inbox className="h-6 w-6 text-subtle" />
          <div className="text-sm font-semibold text-fg">No activity you can see yet</div>
          <p className="max-w-md text-xs text-subtle">
            A row appears when an agent runs, a question is delegated, an automation fires, an
            action or a plan, mapping, design or outbound draft is decided, or an adapter agent
            files a proposal. {head?.visibility}
          </p>
        </div>
      ) : null}

      {items.length ? (
        <ol className="flex flex-col gap-1.5" data-testid="activity-list">
          {items.map((it) => (
            <TimelineRow key={it.id} item={it} active={selected === it.id} onSelect={onSelect} />
          ))}
        </ol>
      ) : null}

      {query.hasNextPage ? (
        <button
          type="button"
          onClick={() => query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
          data-testid="activity-older"
          className="self-start rounded border border-border px-3 py-1 text-2xs text-fg hover:border-accent disabled:opacity-50"
        >
          {query.isFetchingNextPage ? "Loading…" : "Load older"}
        </button>
      ) : null}
      {head ? (
        <p className="text-2xs text-subtle/80">
          {items.length} of{" "}
          {head.total_visible === null || head.total_visible === undefined
            ? "a total that was not counted (the feed read only what these pages need)"
            : head.total_visible}{" "}
          row(s) · signed in as{" "}
          <code>{head.viewer}</code>. {head.visibility}
        </p>
      ) : null}
    </div>
  );
}

function TimelineRow({
  item,
  active,
  onSelect,
}: {
  item: ActivityItem;
  active: boolean;
  onSelect: (item: ActivityItem) => void;
}) {
  const tone = itemTone(item);
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(item)}
        data-testid="activity-row"
        data-kind={item.kind}
        data-loud={item.loud ? "true" : "false"}
        className={`w-full rounded-lg border bg-surface px-3 py-2 text-left hover:border-accent ${
          active ? "border-accent ring-1 ring-accent/40" : TONE_BORDER[tone]
        } ${item.loud ? "border-l-4" : ""}`}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-muted px-1.5 py-px text-[10px] uppercase tracking-wider text-subtle">
            {KIND_LABEL[item.kind]}
          </span>
          <StatusPill status={item.status} tone={tone} />
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">{item.title}</span>
          <span className="text-2xs text-subtle">{when(item.at)}</span>
        </div>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-2xs text-subtle">
          <span>
            by <span className="text-fg">{actorText(item.actor)}</span>
            {item.started_by && isPerson(item.started_by) && !isPerson(item.actor) ? (
              <> · started by <span className="text-fg">{actorText(item.started_by)}</span></>
            ) : null}
          </span>
          <span>
            produced by <Producer pb={item.produced_by} />
          </span>
          <span>{formatDuration(item.duration_ms)}</span>
          <span>{formatTokens(item.tokens)}</span>
          {item.coverage_reduced ? (
            <span className="font-medium text-band-spot">reduced coverage</span>
          ) : null}
          {item.refused_delegations ? (
            <span className="font-medium text-band-review">
              {item.refused_delegations} sub-task(s) refused or failed
            </span>
          ) : null}
        </div>
      </button>
    </li>
  );
}
