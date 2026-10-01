"use client";
/**
 * The traversal TRAIL — every step the reader took across `/graph`, as breadcrumbs.
 *
 * A step is recorded for each expand, each search seed / search add, and each hop of a found path.
 * The trail is `node → edge kind → node → …` ONLY where a response named the edge walked (a
 * `/graph/path` hop). Between any other two crumbs no edge is claimed: when the canvas happens to
 * hold edge(s) between them the link reads "adjacent" (with the count), otherwise "jump". Canvas
 * adjacency is not evidence that the reader walked that relation, and with several edges between
 * a pair there is no honest way to pick one.
 *
 * Browser-history semantics: back/forward move a cursor; the CURRENT trail is steps[0..cursor];
 * taking a new step while the cursor is behind the end drops the forward steps.
 *
 * Nothing here is invented: every id comes from a gateway response the reader caused.
 */
import * as React from "react";
import {
  ChevronLeft,
  ChevronRight,
  Copy,
  Footprints,
  Trash2,
} from "lucide-react";
import {
  currentTrail,
  resolveTrailLinks,
  type GraphEdgeSummary,
  type GraphNodeSummary,
  type TrailLink,
  nodeLabelText,
} from "@/lib/graph-api";
import { useKindPalette } from "./kind-palette";

export type TrailAction = "expand" | "seed" | "search-add" | "path" | "focus";

export interface TrailStep {
  nodeId: string;
  action: TrailAction;
  /** Set when the response that produced this step named the edge walked (a path hop). */
  edgeId?: string;
}

export interface TrailState {
  steps: TrailStep[];
  cursor: number;
}

export const EMPTY_TRAIL: TrailState = { steps: [], cursor: -1 };

/** Append steps at the cursor (dropping any forward history). A step that repeats the crumb it
 *  would follow is not recorded twice. */
export function pushTrail(state: TrailState, add: TrailStep[]): TrailState {
  let steps = state.steps.slice(0, state.cursor + 1);
  for (const step of add) {
    const last = steps[steps.length - 1];
    if (last && last.nodeId === step.nodeId) {
      if (step.edgeId && !last.edgeId)
        steps = [...steps.slice(0, -1), { ...last, edgeId: step.edgeId }];
      continue;
    }
    steps = [...steps, step];
  }
  return { steps, cursor: steps.length - 1 };
}

export type { TrailLink };

/** Links between consecutive crumbs — see `resolveTrailLinks` (pure, unit-tested). */
export function resolveLinks(
  steps: TrailStep[],
  edges: Map<string, GraphEdgeSummary>,
): TrailLink[] {
  return resolveTrailLinks(steps, edges);
}

/** What the canvas highlights for a trail: nodes, edges, and the crumb number(s) per node. */
export interface TrailHighlight {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
  numbers: Map<string, number[]>;
}

export function trailHighlight(
  steps: TrailStep[],
  links: TrailLink[],
  upTo: number,
): TrailHighlight {
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  const numbers = new Map<string, number[]>();
  for (let i = 0; i <= upTo && i < steps.length; i += 1) {
    const id = steps[i].nodeId;
    nodeIds.add(id);
    numbers.set(id, [...(numbers.get(id) ?? []), i + 1]);
    if (i > 0 && links[i - 1]?.edgeId)
      edgeIds.add(links[i - 1].edgeId as string);
  }
  return { nodeIds, edgeIds, numbers };
}

/** The trail as plain text: node ids joined by the edge id walked (or `jump`). */
export function trailText(steps: TrailStep[], links: TrailLink[]): string {
  const parts: string[] = [];
  steps.forEach((s, i) => {
    if (i > 0) {
      const l = links[i - 1];
      parts.push(`  --[${l?.edgeId ?? (l?.adjacent ? "adjacent, edge not recorded" : "jump")}]-->`);
    }
    parts.push(s.nodeId);
  });
  return parts.join("\n");
}

const ACTION_LABEL: Record<TrailAction, string> = {
  expand: "expanded",
  seed: "started here",
  "search-add": "added from search",
  path: "path hop",
  focus: "focused",
};

export function TrailBar({
  trail,
  links,
  nodes,
  onFocus,
  onBack,
  onForward,
  onClear,
}: {
  trail: TrailState;
  links: TrailLink[];
  nodes: Map<string, GraphNodeSummary>;
  onFocus: (index: number) => void;
  onBack: () => void;
  onForward: () => void;
  onClear: () => void;
}) {
  const palette = useKindPalette();
  const [copied, setCopied] = React.useState<string | null>(null);
  if (trail.steps.length === 0) return null;

  const copy = async () => {
    // Only the CURRENT trail (up to the cursor) is copied — never forward history (R-019 round 3, G6).
    const cur = currentTrail(trail.steps, links, trail.cursor);
    const text = trailText(cur.steps, cur.links);
    try {
      await navigator.clipboard.writeText(text);
      setCopied("copied");
    } catch {
      setCopied("clipboard unavailable");
    }
    window.setTimeout(() => setCopied(null), 1500);
  };

  const btn =
    "flex items-center gap-1 rounded px-1.5 py-0.5 text-subtle hover:bg-muted hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent";

  return (
    <div
      className="flex flex-col gap-1.5 rounded border border-accent/40 bg-accent/[0.04] px-3 py-2 text-2xs"
      data-testid="graph-trail"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Footprints className="h-3.5 w-3.5 text-accent" />
        <span className="font-medium text-fg">Trail</span>
        <span className="text-subtle" data-testid="graph-trail-count">
          {trail.cursor + 1} of {trail.steps.length} step
          {trail.steps.length === 1 ? "" : "s"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            className={btn}
            onClick={onBack}
            disabled={trail.cursor <= 0}
            data-testid="graph-trail-back"
          >
            <ChevronLeft className="h-3 w-3" /> back
          </button>
          <button
            type="button"
            className={btn}
            onClick={onForward}
            disabled={trail.cursor >= trail.steps.length - 1}
            data-testid="graph-trail-forward"
          >
            forward <ChevronRight className="h-3 w-3" />
          </button>
          <button
            type="button"
            className={btn}
            onClick={() => void copy()}
            data-testid="graph-trail-copy"
          >
            <Copy className="h-3 w-3" /> {copied ?? "copy trail"}
          </button>
          <button
            type="button"
            className={btn}
            onClick={onClear}
            data-testid="graph-trail-clear"
          >
            <Trash2 className="h-3 w-3" /> clear
          </button>
        </div>
      </div>
      <ol
        className="flex flex-wrap items-center gap-1"
        aria-label="Traversal trail"
      >
        {trail.steps.map((s, i) => {
          const node = nodes.get(s.nodeId);
          const link = i > 0 ? links[i - 1] : null;
          const active = i <= trail.cursor;
          return (
            <li key={`${i}-${s.nodeId}`} className="flex items-center gap-1">
              {link && (
                <span
                  className={
                    active
                      ? "font-mono text-accent"
                      : "font-mono text-subtle/60"
                  }
                  data-testid="graph-trail-link"
                  data-link={link.edgeId ? "edge" : link.adjacent ? "adjacent" : "jump"}
                  title={
                    link.edgeId ??
                    (link.adjacent
                      ? `${link.adjacent} edge(s) join these two on the canvas, but no response named the one walked`
                      : "no edge between these two on the canvas — reached by search or seed")
                  }
                >
                  {link.edge
                    ? `→ ${palette.edgeLabel(link.edge.kind)} →`
                    : link.edgeId
                      ? "→ (edge not drawn) →"
                      : link.adjacent
                        ? "⇢ adjacent ⇢"
                        : "⇢ jump ⇢"}
                </span>
              )}
              <button
                type="button"
                onClick={() => onFocus(i)}
                data-testid="graph-trail-crumb"
                data-node-id={s.nodeId}
                title={`${s.nodeId} — ${ACTION_LABEL[s.action]}`}
                className={
                  i === trail.cursor
                    ? "max-w-[12rem] truncate rounded border border-accent bg-accent/10 px-1.5 py-0.5 text-fg"
                    : active
                      ? "max-w-[12rem] truncate rounded border border-accent/40 px-1.5 py-0.5 text-fg hover:border-accent"
                      : "max-w-[12rem] truncate rounded border border-dashed border-border px-1.5 py-0.5 text-subtle hover:text-fg"
                }
              >
                <span className="tnum mr-1 text-subtle">{i + 1}</span>
                {node ? nodeLabelText(node.label) : s.nodeId}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
