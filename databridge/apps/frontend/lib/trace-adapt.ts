/**
 * `GET /trace/{request_id}` → the shape `/trace` draws.
 *
 * The gateway's recorder (`apps/gateway/databridge_gateway/trace_recorder.py`) is the authority on
 * what happened: it records an ordered, closed-vocabulary list of steps — principal, budget,
 * capability, policy, pointer, verdict, cell, ledger_append, released, escalation, cost — each with
 * a monotonic `seq`. It does **not** ship a node/edge diagram, and it should not: a diagram is a
 * rendering decision and the recorder's job is the record.
 *
 * So this module is that rendering decision, and it is a projection, never an addition:
 *
 *   * a participant appears because a step names it. No adapter step, no adapter box — an empty
 *     ADAPTER box reads as "the adapter returned nothing" when the truth is "no adapter ran";
 *   * an edge is one KIND of message, ordered by when that kind first flowed. Forty-seven pointer
 *     steps become one arrow labelled "47 pointers", because forty-seven arrows is not more
 *     honest, only unreadable — and the count is exact, with the `seq` span on the arrow;
 *   * a value the recorder left `null` (unmeasured) stays `null`. Zero is a measurement.
 */
import type {
  Tone,
  TraceBranch,
  TraceCell,
  TraceDetail,
  TraceEdge,
  TraceEdgeKind,
  TraceNode,
  TracePointerProvenance,
  TracePreview,
} from "./platform-preview";

export interface GatewayTraceStep {
  seq: number;
  at: string;
  kind: string;
  summary: string;
  detail: Record<string, unknown>;
}

export interface GatewayTraceSummary {
  request_id: string;
  surface: string;
  agent: string;
  version: string;
  started_at: string;
  ended_at: string | null;
  outcome: string | null;
  principal: Record<string, unknown> | null;
  steps: number;
  /** null once steps were dropped (Codex f2 #4): a count of the stored prefix is not a total —
   *  it is in `at_least` instead. */
  grounded: number | null;
  withheld: number | null;
  absent: number | null;
  escalations: number | null;
  at_least?: Record<string, number> | null;
  integrity: {
    complete: boolean;
    defect_count: number;
    defect_codes: string[];
    /** Steps / defects past the per-trace bound: counted, not stored (Codex f1 #6). */
    steps_dropped?: number;
    defects_dropped?: number;
  };
}

export interface GatewayTrace extends GatewayTraceSummary {
  step_list: GatewayTraceStep[];
  defect_list: Record<string, unknown>[];
  view: {
    request: Record<string, unknown>;
    branches: TraceBranch[];
    /** `lower_bound`: steps were dropped, so `count` is "at least" (its detail says so). */
    results: { kind: string; count: number; lower_bound?: boolean; detail: string; tone: Tone }[];
    answer: Record<string, unknown>;
  };
}

const str = (v: unknown): string | null =>
  typeof v === "string" && v.length > 0 ? v : v == null ? null : String(v);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

function blank(summary: string): TraceDetail {
  return { summary, requested: null, decided: null, pulled: null, cost: null, notes: [] };
}

// ---------------------------------------------------------------------------------------------
// Who a step belongs to, and how steps become messages.
// ---------------------------------------------------------------------------------------------

const ADAPTER_PREFIX = "adapter:";
/** Beyond this many distinct sources the rest share one box; the step list keeps every name. */
const MAX_ADAPTERS = 6;

function adapterId(source: string | null): string {
  return `${ADAPTER_PREFIX}${source ?? "unrecorded"}`;
}

/**
 * Which participant a step belongs to.
 *
 * A `source_id` on a step is NOT automatically an adapter: a pointer answered from history carries
 * the id of the source it originally came from, and drawing a box per such id turned a 24-pointer
 * traversal into twenty-four adapter boxes and a hairball of identical arrows. The adapters are
 * the sources a `capability` step was actually issued against — that is what an adapter is — plus
 * any source a pointer had to be read live from. Everything else answered from the graph belongs
 * to the graph.
 */
interface Resolver {
  participantFor: (s: GatewayTraceStep) => string;
  adapters: string[];
  overflow: number;
  usesGraph: boolean;
}

function buildResolver(steps: GatewayTraceStep[]): Resolver {
  const usesGraph = steps.some((s) => s.kind === "pointer" && s.detail.origin === "graph");
  const named: string[] = [];
  const note = (v: unknown) => {
    const id = str(v);
    if (id && !named.includes(id)) named.push(id);
  };
  for (const s of steps) {
    if (s.kind === "capability") note(s.detail.source_id);
    if (s.kind === "pointer" && s.detail.origin === "live_source") note(s.detail.source_id);
  }
  const kept = named.slice(0, MAX_ADAPTERS);
  const overflow = named.length - kept.length;
  const isAdapter = new Set(kept);
  const adapters = kept.map(adapterId);
  if (overflow > 0) adapters.push(adapterId("other"));

  const participantFor = (s: GatewayTraceStep): string => {
    const src = str(s.detail.source_id);
    if (s.kind === "pointer" && s.detail.origin === "graph") return "graph";
    if (src && isAdapter.has(src)) return adapterId(src);
    if (src && named.includes(src)) return adapterId("other");
    // A step naming no adapter, on a request the graph answered, is the graph's.
    if (usesGraph) return "graph";
    return adapters[0] ?? "orchestrator";
  };

  return { participantFor, adapters, overflow, usesGraph };
}

function groupKey(s: GatewayTraceStep, participant: string): string | null {
  switch (s.kind) {
    case "principal":
      return "principal";
    case "budget":
      return "budget";
    case "capability":
      return `capability:${participant}`;
    case "policy":
      return `policy:${participant}:${s.detail.withheld ? "withheld" : "permitted"}`;
    case "pointer":
      return `pointer:${participant}:${str(s.detail.origin) ?? "unrecorded"}`;
    case "verdict":
      return `verdict:${participant}`;
    case "cell":
      return `cell:${participant}:${str(s.detail.state) ?? "unrecorded"}`;
    case "ledger_append":
      return `ledger:${participant}`;
    case "released":
      return "released";
    case "escalation":
      return "escalation";
    default:
      // cost, note, unrecordable, closed: annotations on the request, not messages between
      // participants. They are read elsewhere rather than drawn as arrows to nowhere.
      return null;
  }
}

interface Group {
  key: string;
  kind: string;
  /** The node this group's steps hang off — an adapter, the graph, or the orchestrator. */
  participant: string;
  steps: GatewayTraceStep[];
}

/**
 * One arrow per *kind of message*, ordered by when that kind first flowed.
 *
 * A real trace interleaves — pointer, cell, pointer, cell — so grouping only consecutive runs
 * leaves eighty arrows on the diagram, which is not a more honest picture, only an unreadable one.
 * Grouping globally gives the ~10-arrow shape `DEMO_AND_FRONTEND` §2.2 specifies, and no
 * information is lost: the count is exact and each edge carries the `seq` span it covers, so the
 * step list remains the authority on exact ordering.
 */
function group(steps: GatewayTraceStep[], resolver: Resolver): Group[] {
  const byKey = new Map<string, Group>();
  for (const s of [...steps].sort((a, b) => a.seq - b.seq)) {
    const participant = resolver.participantFor(s);
    const key = groupKey(s, participant);
    if (key === null) continue;
    const existing = byKey.get(key);
    if (existing) existing.steps.push(s);
    else byKey.set(key, { key, kind: s.kind, participant, steps: [s] });
  }
  return [...byKey.values()].sort((a, b) => a.steps[0].seq - b.steps[0].seq);
}

/** The `seq` window an aggregated arrow covers, so exact ordering stays recoverable. */
function span(g: Group): string {
  const first = g.steps[0].seq;
  const last = g.steps[g.steps.length - 1].seq;
  return g.steps.length === 1
    ? `step ${first} of the recorded trace`
    : `${g.steps.length} recorded steps, seq ${first}–${last}; this arrow is ordered by the first of them`;
}

// ---------------------------------------------------------------------------------------------

function plural(n: number, one: string): string {
  return `${n} ${one}${n === 1 ? "" : "s"}`;
}

/** Build the participants, positioned. Only participants some step actually named. */
function buildNodes(t: GatewayTrace, groups: Group[], resolver: Resolver): TraceNode[] {
  const principal = t.principal ?? {};
  const usesGraph = resolver.usesGraph;
  // Only the adapters some group actually landed on. An adapter box nothing was asked of reads as
  // "the adapter returned nothing", which is a different claim from "no adapter ran".
  const touched = new Set(groups.map((g) => g.participant));
  const sources = resolver.adapters.filter((id) => touched.has(id));
  const hasLedger = groups.some((g) => g.kind === "ledger_append");
  const hasHuman = groups.some((g) => g.kind === "escalation");
  const hasAnswer = groups.some((g) => g.kind === "released") || t.outcome !== null;

  // Cost is recorded per request, not per message, so it belongs to the orchestrator box.
  const costSteps = (t.step_list ?? []).filter((s) => s.kind === "cost");
  const tokens = costSteps.reduce<number | null>((acc, s) => {
    const v = num(s.detail.llm_tokens);
    return v === null ? acc : (acc ?? 0) + v;
  }, null);
  const ms = costSteps.reduce<number | null>((acc, s) => {
    const v = num(s.detail.wall_ms);
    return v === null ? acc : (acc ?? 0) + v;
  }, null);

  const nodes: TraceNode[] = [
    {
      id: "agent",
      kind: "agent",
      label: t.agent || t.surface,
      sublabel: [t.version, t.surface].filter(Boolean).join(" · "),
      col: 1,
      row: 0,
      detail: {
        ...blank(`The caller. Surface: ${t.surface}.`),
        requested: {
          capability: str(principal.grant_id) ?? "no grant recorded",
          projection: [],
          predicate: null,
          purpose: str(principal.purpose) ?? "not recorded",
          principal: [
            str(principal.actor) ? `actor=${str(principal.actor)}` : null,
            str(principal.on_behalf_of) ? `on_behalf_of=${str(principal.on_behalf_of)}` : null,
          ]
            .filter(Boolean)
            .join(" · ") || "not recorded",
          as_of: null,
        },
      },
    },
    {
      id: "orchestrator",
      kind: "orchestrator",
      label: "ORCHESTRATOR",
      sublabel: t.outcome ? `outcome: ${t.outcome}` : "in flight",
      col: 1,
      row: 1,
      detail: {
        ...blank(
          "Checks the grant covers the capability and the purpose, resolves what it can from the graph, and asks an adapter only for what is left.",
        ),
        cost: tokens === null && ms === null ? null : { tokens, ms },
        notes: t.integrity.complete
          ? []
          : [
              `This trace is incomplete: ${t.integrity.defect_count} step(s) could not be recorded — ${t.integrity.defect_codes.join(", ")}. The recorder reports the gap rather than hiding it.`,
            ],
      },
    },
  ];

  let row = 2;
  if (usesGraph) {
    nodes.push({
      id: "graph",
      kind: "graph",
      label: "GRAPH",
      sublabel: "pointers, not values",
      col: 0,
      row: 2,
      detail: blank("Stores pointers. A value it already holds a reference for costs no disclosure."),
    });
  }
  for (const id of sources) {
    const name = id.slice(ADAPTER_PREFIX.length);
    const overflowBox = name === "other";
    nodes.push({
      id,
      kind: "adapter",
      label: "ADAPTER",
      sublabel: overflowBox ? `${resolver.overflow} further sources` : name,
      col: 2,
      row,
      detail: blank(
        overflowBox
          ? `${resolver.overflow} further sources share this box so the diagram stays readable. Every one of them is named individually in the recorded step list.`
          : "The adapter agent for one source: policy, then ledger, then core.",
      ),
    });
    row += 1;
  }
  if (hasLedger) {
    nodes.push({
      id: "ledger",
      kind: "ledger",
      label: "LEDGER",
      sublabel: "append-only",
      col: 2,
      row,
      detail: blank("The read entry is written before the value is returned."),
    });
    row += 1;
  }
  let mid = Math.max(row, 3);
  if (hasAnswer) {
    nodes.push({
      id: "answer",
      kind: "answer",
      label: "ANSWER",
      sublabel: t.outcome ?? "not closed",
      col: 1,
      row: mid,
      detail: blank("Per-field citations. The risk certificate is not computed yet and is null, not zero."),
    });
    mid += 1;
  }
  if (hasHuman) {
    nodes.push({
      id: "human",
      kind: "human",
      label: "HUMAN GATE",
      sublabel: "escalated",
      col: 1,
      row: mid,
      detail: blank("Nothing commits until a named human attests."),
    });
  }
  return nodes;
}

function edgeFor(g: Group, index: number): TraceEdge | null {
  const first = g.steps[0];
  const n = g.steps.length;
  const src = str(first.detail.source_id);
  // Where this message came from or went to. Resolved once, when the group was formed.
  const adapter = g.participant;
  const base = { id: `g${index}`, step: index + 1 };

  const make = (
    from: string,
    to: string,
    kind: TraceEdgeKind,
    label: string,
    detail: TraceDetail,
  ): TraceEdge => ({ ...base, from, to, kind, label, detail });

  switch (g.kind) {
    case "principal":
      return make("agent", "orchestrator", "request", "task + Principal", {
        ...blank(first.summary),
        requested: {
          capability: str(first.detail.grant_id) ?? "no grant recorded",
          projection: [],
          predicate: null,
          purpose: str(first.detail.purpose) ?? "not recorded",
          principal: [
            str(first.detail.actor) ? `actor=${str(first.detail.actor)}` : null,
            str(first.detail.on_behalf_of)
              ? `on_behalf_of=${str(first.detail.on_behalf_of)}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ") || "not recorded",
          as_of: null,
        },
      });

    case "budget":
      return make("orchestrator", "orchestrator", "policy", "budget admission", {
        ...blank(first.summary),
        decided: {
          decision: "allowed",
          reason_code: null,
          note: `max_llm_tokens ${str(first.detail.max_llm_tokens) ?? "not recorded"}`,
        },
      });

    case "capability": {
      const fields = Array.isArray(first.detail.fields) ? (first.detail.fields as string[]) : [];
      return make(
        "orchestrator",
        adapter,
        "request",
        n > 1 ? plural(n, "CapabilityRequest") : `CapabilityRequest ${str(first.detail.capability) ?? ""}`.trim(),
        {
          ...blank(first.summary),
          requested: {
            capability: str(first.detail.capability) ?? "not recorded",
            projection: fields,
            predicate: str(first.detail.entity) ? `entity = ${str(first.detail.entity)}` : null,
            purpose: "see the Principal step",
            principal: "see the Principal step",
            as_of: str(first.detail.as_of),
          },
        },
      );
    }

    case "policy": {
      const withheld = Boolean(first.detail.withheld);
      const codes = Array.from(
        new Set(g.steps.map((s) => str(s.detail.reason_code)).filter(Boolean) as string[]),
      );
      const fields = g.steps.map((s) => str(s.detail.field_path)).filter(Boolean) as string[];
      return make(
        adapter,
        adapter,
        "policy",
        withheld ? `POLICY withheld ×${n}` : `POLICY permitted ×${n}`,
        {
          ...blank(
            "The policy checkpoint: grant, purpose, budget and egress, evaluated per field.",
          ),
          decided: {
            decision: withheld ? "withheld" : "allowed",
            reason_code: codes.join(", ") || null,
            note: fields.length ? fields.join(", ") : first.summary,
          },
        },
      );
    }

    // Codex f1 #3: pointer, verdict and cell groups carry NO `pulled` block. The recorder counts
    // pointer / verdict / cell STEPS; it does not record rows read, a tier, or capture reads, and
    // a `pulled` block would have to fill those with zeros nobody measured. The counts it does
    // record are stated in the notes, in the recorder's own terms.
    case "pointer": {
      const origin = str(first.detail.origin);
      const fromGraph = origin === "graph";
      return make(
        adapter,
        "orchestrator",
        "rows",
        `${plural(n, "pointer")} · ${
          fromGraph ? "from history" : origin === "live_source" ? "from the source" : "origin not recorded"
        }`,
        {
          ...blank(
            fromGraph
              ? "Answered from what the graph already held: no adapter call, no new disclosure."
              : origin === "live_source"
                ? "History could not answer these, so the source was read."
                : "The recorder did not capture where these pointers were answered from.",
          ),
          notes: [
            `${plural(n, "pointer step")} recorded, origin ${origin ?? "not recorded"}. Rows read, tier and capture reads are not recorded per pointer.`,
          ],
        },
      );
    }

    case "verdict": {
      const counts = new Map<string, number>();
      for (const s of g.steps) {
        const v = str(s.detail.verdict) ?? "not recorded";
        counts.set(v, (counts.get(v) ?? 0) + 1);
      }
      return make(adapter, "orchestrator", "rows", `verify → ${plural(n, "verdict")}`, {
        ...blank("Re-reading each cited value under its snapshot identity."),
        notes: [...counts].map(([verdict, count]) => `${verdict}: ${count}`),
      });
    }

    case "cell": {
      const state = str(first.detail.state) ?? "unrecorded";
      const codes = Array.from(
        new Set(g.steps.map((s) => str(s.detail.reason_code)).filter(Boolean) as string[]),
      );
      return make(adapter, "orchestrator", "rows", `${n} ${state}`, {
        ...blank(
          state === "withheld"
            ? "Withheld: the field exists and policy did not release it. Not the same state as absent."
            : state === "absent"
              ? "Absent: no source held it. Named, never guessed."
              : "Returned as grounded cells, each carrying its pointer.",
        ),
        decided:
          state === "withheld"
            ? {
                decision: "withheld",
                reason_code: codes.join(", ") || null,
                note: g.steps.map((s) => str(s.detail.field_path)).filter(Boolean).join(", "),
              }
            : null,
        notes: [`${plural(n, "cell step")} recorded in state ${state}.`],
      });
    }

    case "ledger_append":
      return make(src ? adapter : "orchestrator", "ledger", "ledger", `${plural(n, "append")}`, {
        ...blank(
          "Written before the value is returned, so the audit cannot lag the disclosure (AGENT_PLANE §7).",
        ),
        // An empty `covers` authorises nothing (Codex f1 #4) — it is said so, never read as "all".
        notes: g.steps.map((s) => `${str(s.detail.entry_id) ?? "entry id not recorded"} (source ${
          str(s.detail.source_id) ?? "not recorded"
        }) covers ${
          Array.isArray(s.detail.covers) && (s.detail.covers as string[]).length
            ? (s.detail.covers as string[]).join(", ")
            : "no field — an append that names no field authorises no release"
        }`),
      });

    case "released": {
      // Certified ONLY where the recorder says `ledger_covered: true` and names the entry. A
      // missing flag is not coverage (Codex f1 #4).
      const certified = (s: GatewayTraceStep) =>
        s.detail.ledger_covered === true && str(s.detail.covered_by) !== null;
      const uncovered = g.steps.filter((s) => !certified(s)).length;
      return make("orchestrator", "answer", "answer", `${plural(n, "value")} released`, {
        ...blank("Disclosed to the caller. Each note names the ledger entry that authorised it, or says none did."),
        decided: uncovered
          ? {
              decision: "refused",
              reason_code: "LEDGER_APPEND_NOT_RECORDED",
              note: `${uncovered} of ${n} released with no ledger append recorded that explicitly covers that field of that source. The trace records the gap rather than hiding it.`,
            }
          : null,
        notes: g.steps.map(
          (s) =>
            `${str(s.detail.field_path) ?? "field not recorded"} — ${
              certified(s) ? `covered by ledger entry ${str(s.detail.covered_by)}` : "no covering ledger entry"
            }`,
        ),
      });
    }

    case "escalation":
      return make("orchestrator", "human", "human", `escalated ×${n}`, {
        ...blank("Handed to a named human."),
        notes: g.steps.map(
          (s) => `${str(s.detail.to) ?? "human"} — ${str(s.detail.reason) ?? "no reason recorded"}`,
        ),
      });

    default:
      return null;
  }
}

const CELL_STATES: readonly string[] = ["grounded", "withheld", "absent"];

/** Cell steps whose state is not one of the three recorded states. They are NOT drawn as
 *  "absent" (Codex f1 #3 — that would assert a fact the recorder did not record); the page says
 *  how many were left out, and the recorder has already raised a defect for each. */
export function unstatedCells(steps: GatewayTraceStep[]): number {
  return steps.filter((s) => s.kind === "cell" && !CELL_STATES.includes(str(s.detail.state) ?? "")).length;
}

function buildCells(steps: GatewayTraceStep[]): TraceCell[] {
  return steps
    .filter((s) => s.kind === "cell" && CELL_STATES.includes(str(s.detail.state) ?? ""))
    .map((s) => ({
      path: str(s.detail.field_path) ?? "field not recorded",
      state: str(s.detail.state) as TraceCell["state"],
      trust: (str(s.detail.trust) as TraceCell["trust"]) ?? null,
      source: str(s.detail.source_id),
      tier: null,
      verdict: null,
      reason_code: str(s.detail.reason_code),
      note: null,
    }));
}

function buildProvenance(steps: GatewayTraceStep[]): TracePointerProvenance | null {
  const pointers = steps.filter((s) => s.kind === "pointer");
  if (!pointers.length) return null;
  const cells = steps.filter((s) => s.kind === "cell");
  return {
    total: pointers.length,
    from_history: pointers.filter((s) => s.detail.origin === "graph").length,
    needed_source: pointers.filter((s) => s.detail.origin === "live_source").length,
    withheld: cells.filter((s) => s.detail.state === "withheld").length,
    absent: cells.filter((s) => s.detail.state === "absent").length,
  };
}

/** The whole projection. `endpoint` is passed in so the page can name what served it. */
export function adaptGatewayTrace(t: GatewayTrace, endpoint: string): TracePreview {
  const resolver = buildResolver(t.step_list ?? []);
  const groups = group(t.step_list ?? [], resolver);
  const nodes = buildNodes(t, groups, resolver);
  const present = new Set(nodes.map((n) => n.id));
  const edges = groups
    .map((g, i) => {
      const e = edgeFor(g, i);
      // Every aggregated arrow says how many steps it stands for and where they sat in the trace.
      return e && { ...e, detail: { ...e.detail, notes: [...e.detail.notes, span(g)] } };
    })
    .filter((e): e is TraceEdge => Boolean(e))
    // An edge whose participant was never created is dropped rather than drawn to a phantom box.
    .filter((e) => present.has(e.from) && present.has(e.to))
    // Renumber after the drop so the timeline has no holes in it.
    .map((e, i) => ({ ...e, step: i + 1 }));

  const view = t.view ?? { request: {}, branches: [], results: [], answer: {} };
  const req = view.request ?? {};
  const ans = view.answer ?? {};
  const skippedCells = unstatedCells(t.step_list ?? []);

  return {
    endpoint,
    request_id: t.request_id,
    request: {
      agent: str(req.agent) ?? t.agent,
      version: str(req.version) ?? t.version,
      purpose: str(req.purpose),
      on_behalf_of: str(req.on_behalf_of),
      grant: str(req.grant),
      budget_used: num(req.budget_used),
      budget_total: num(req.budget_total),
    },
    pointer_provenance: buildProvenance(t.step_list ?? []) ?? undefined,
    nodes,
    edges,
    cells: buildCells(t.step_list ?? []),
    cells_note:
      "Every cell the recorder captured for this request. Withheld and absent are different facts and stay different rows." +
      (skippedCells
        ? ` ${plural(skippedCells, "cell")} recorded with no valid state ${skippedCells === 1 ? "is" : "are"} not shown as any state (the trace lists ${skippedCells === 1 ? "it" : "them"} as a defect).`
        : ""),
    branches: view.branches ?? [],
    results: view.results ?? [],
    answer: {
      // The recorder's count, or the same derivation from the recorded `released` steps. When
      // neither exists the count is unknown: NaN, which the page renders as "not recorded"
      // (`citationCount`). Never 0 — Codex f1 #3. (`TracePreview.answer.citations` is typed
      // `number` in platform-preview.ts, which this module does not own.)
      citations:
        num(ans.citations) ??
        (Array.isArray(t.step_list) ? t.step_list.filter((s) => s.kind === "released").length : Number.NaN),
      certificate_risk: num(ans.certificate_risk),
      risk_budget: num(ans.risk_budget),
      admitted: typeof ans.admitted === "boolean" ? ans.admitted : null,
      note: str(ans.note),
    },
    integrity: t.integrity,
    defects: t.defect_list ?? [],
  };
}
