// `/sources/*` gateway client — connect an MCP server, approve its crawl plan, run it, watch it.
//
// Wraps `apps/gateway/databridge_gateway/sources_routes.py` exactly. Every call returns a
// `SourcesResult<T>`: either the payload, or a failure that carries the HTTP status, the gateway's
// refusal code, its message verbatim, AND a plain-English sentence for the operator. "Could not
// reach the gateway" and "the gateway refused" are different shapes — neither can render as an
// empty list.
//
// Nothing here computes a figure. Counts, stages and statuses are the backend's, passed through.
import { GATEWAY, authHeaders } from "./api";
import type { EvidenceRunSummary } from "./evidence-api";

// ---------------------------------------------------------------------------------------------
// shapes (mirroring sources_routes.py / databridge_mcp_ingest)
// ---------------------------------------------------------------------------------------------

export type RunStatus = "running" | "ok" | "empty" | "failed" | "unverified" | "incomplete" | string;

/** One entry of `list_runs()` — a run directory on disk. */
export interface RunListEntry {
  run_id: string;
  run_dir?: string;
  status: RunStatus;
  message: string;
  counts: RunCounts;
  finished_at: string | null;
}

/** `verify_run().counts` — the run's measured counts. Every field optional: an older or failed
 *  run can lack any of them, and a missing count renders as "—", never as 0. */
export interface RunCounts {
  inventory?: number;
  captured?: number;
  excluded?: number;
  blocked?: number;
  failed?: number;
  failed_listing?: number;
  unchanged?: number;
  extracted?: number;
  extract_skipped?: number;
  extract_skipped_by_reason?: Record<string, number>;
  governance?: Record<string, number>;
  built?: number;
  build_skipped?: number;
  zero_graph_output?: number;
  entities?: number;
  relations?: number;
  refused?: number;
  pointers?: number;
  pointers_resolved?: number;
  partial_coverage?: number;
  [k: string]: unknown;
}

/** A progress event from the runner (`_Progress`): `{stage, elapsed_s, done?, total?, …}`. */
export interface ProgressEvent {
  stage: string;
  elapsed_s?: number;
  done?: number;
  total?: number;
  captured?: number;
  status?: string;
  message?: string;
  [k: string]: unknown;
}

export interface LiveRun {
  run_id: string;
  progress: ProgressEvent | null;
}

export interface SourceSummary {
  id: string;
  title: string;
  via: "direct" | "mcpjungle" | string;
  transport: "stdio" | "http" | string | null;
  plan_status: "proposed" | "approved" | string | null;
  last_run: RunListEntry | null;
  running: LiveRun | null;
}

export interface SourcesList {
  sources: SourceSummary[];
  problems: string[];
}

export interface Scope {
  roots?: string[];
  include?: string[];
  exclude?: string[];
  blocked?: string[];
  file_types?: string[];
  max_bytes?: number;
  max_items?: number;
  max_depth?: number;
  tables_include?: string[];
  tables_exclude?: string[];
  max_rows_per_table?: number;
  deny_tools?: string[];
}

export interface Connection {
  id: string;
  title: string;
  transport:
    | { type: "stdio"; command: string; args: string[]; env: Record<string, string>; env_from: Record<string, string>; cwd?: string | null }
    | { type: "http"; url: string; headers: Record<string, string>; headers_from: Record<string, string> };
  via: "direct" | "mcpjungle";
  jungle_server: string | null;
  scope: Scope;
  auto_approve: boolean;
  document_kind: string | null;
  trust: string;
  purpose: "ingest" | "agent_write" | string;
}

export interface CallSpec {
  tool: string;
  args?: Record<string, unknown>;
  parse?: Record<string, unknown>;
}

export interface StepSpec {
  id: string;
  strategy: "documents" | "resources" | "tabular";
  roots?: CallSpec | null;
  list?: (CallSpec & { recurse?: boolean; root_arg_default?: string | null; page?: { arg: string; max_pages: number } | null }) | null;
  read?: {
    text_tool?: string | null;
    binary_tool?: string | null;
    binary_extensions?: string[];
    resource_uri?: string | null;
  } | null;
  uri_prefix?: string | null;
  sql?: { tool: string; arg?: string; dialect?: string } | null;
}

export interface CrawlPlan {
  source_id: string;
  status: "proposed" | "approved";
  origin: "rules" | "model" | "manual";
  profiles: string[];
  steps: StepSpec[];
  problems: string[];
  notes: string[];
  tools_seen: string[];
  tools_digest: string;
  deny_tools: string[];
  pagination: string[];
  probe: Record<string, unknown>;
  created_at: string;
  approved_at: string | null;
  approved_by: string | null;
  /** The backend's per-tool class, re-derived from the LIVE tool list at proposal and again at
   *  approval: `read:…`, `write:…`, or `unclassified:unknown …` (nothing vouches for it). */
  tool_classes?: Record<string, string>;
  /** The recorded approval (who, how they were authenticated, what they vouched for). `{}` until
   *  approved. */
  approval?: ApprovalRecord | null;
}

/** How an approval was recorded by the backend's approval gate (`require_authenticated_approver`). */
export interface ApprovalRecord {
  principal_id?: string;
  authenticated_by?: string;
  principal_type?: string;
  /** True when the approving principal came from the public development token (FIN-16). */
  dev_only?: boolean;
  at?: string;
  vouched_read_tools?: string[];
}

/** Was this approval made under the development token? Read from the RECORD, never inferred. */
export function approvedWithDevToken(rec: ApprovalRecord | null | undefined): boolean {
  return !!rec && rec.dev_only === true;
}

export interface TableMapping {
  status: "proposed" | "approved" | "unmapped";
  origin: string;
  node: Record<string, unknown> | null;
  edges: Record<string, unknown>[];
  rationale: string[];
  approval?: ApprovalRecord | null;
  /** FIN-04 per-row kind split ({column, kinds: {value: kind}, default}); reviewed via `splits`. */
  split?: { column: string; kinds: Record<string, string>; default: string } | null;
}

export interface SourceMapping {
  source_id: string;
  tables: Record<string, TableMapping>;
  updated_at: string;
  approved_by: string | null;
}

/** What the captured rows show for one proposed link (LINK7). `samples` is null when the target
 *  kind stands for people (never shown) or the rows are not captured on this gateway; the note
 *  says which. */
export interface LinkEvidence {
  matched: number | null;
  with_value: number | null;
  distinct_matched: number | null;
  target_keys: number | null;
  samples: string[] | null;
  samples_note: string | null;
}

/** One link proposal (C-3): a column no foreign key declares whose values are, by measurement,
 *  the keys of another mapped table. A proposal changes nothing until a person approves it. */
export interface LinkReview {
  table: string;
  column: string;
  /** "source:table" — names the link when a column has several. */
  target: string;
  target_source: string;
  target_table: string;
  target_key: string | null;
  target_kind: string | null;
  status: "proposed" | "not_proposed" | "approved" | "rejected" | string;
  edge_kind: string | null;
  why: string | null;
  basis: string | null;
  alternatives: string[];
  person_target: boolean;
  approvable: boolean;
  decided_by: string | null;
  decided_at: string | null;
  decided_dev_only: boolean | null;
  reject_reason: string | null;
  table_status: string;
  evidence: LinkEvidence;
}

/** A table a person approved before that is no longer approved (re-proposed by the engine, or an
 *  approval that no longer verifies). Read from the append-only approvals log, not from wording. */
export interface Reapproval {
  reasons: string[];
  previously_approved_by: string | null;
  previously_approved_at: string | null;
  dev_only: boolean;
}

/** One discriminator value of a per-row kind split (FIN-04 / ID7), as the gateway measured it on the
 *  rows the last sync captured. `rows` is null when no capture of the table is held yet. */
export interface KindSplitValue {
  /** The NORMALISED value (case-folded, punctuation collapsed) — the form the split binds. */
  value: string;
  kind: string;
  /** Whether the split names this value; an unnamed value takes the default (the subject). */
  bound: boolean;
  rows: number | null;
  stays_subject: boolean;
}

/** A table's PROPOSED (or approved) per-row kind split: which column tells the rows apart, what
 *  each value becomes, and which rows stay the subject. Approved together with the table. */
export interface KindSplitReview {
  column: string;
  /** The table's node kind — a subject; every value the split does not move stays this kind. */
  default: string;
  default_is_subject: boolean;
  default_is_person: boolean;
  kinds: { kind: string; subject: boolean; person: boolean; is_default: boolean }[];
  values: KindSplitValue[];
  rows_total: number | null;
  rows_moved: number | null;
  rows_stay: { bound_to_subject: number; unrecognised: number; null_or_blank: number; masked: number } | null;
  counts_note: string | null;
  status: "proposed" | "approved" | "unmapped" | string;
  approved_with_table: boolean;
}

export interface MappingReview {
  source_id: string;
  links: LinkReview[];
  reapproval: Record<string, Reapproval>;
  /** Tables whose mapping carries a per-row kind split (absent on an older gateway). */
  splits?: Record<string, KindSplitReview>;
}

/** The split's values in the two groups a person reads before approving: the rows that become
 *  another kind, and the rows that stay the subject (never merged). Totals are summed only when
 *  every count is known. */
export function splitGroups(sp: Pick<KindSplitReview, "values" | "default">): {
  moved: KindSplitValue[];
  stays: KindSplitValue[];
  movedRows: number | null;
} {
  // A value is shown as moving only when the gateway says it leaves the subject AND its kind is not
  // the default: fail closed, a disagreement never shows a person row as "becomes an organisation".
  const moved = sp.values.filter((v) => v.stays_subject === false && v.kind !== sp.default);
  const stays = sp.values.filter((v) => !moved.includes(v));
  const known = moved.every((v) => typeof v.rows === "number");
  return { moved, stays, movedRows: known ? moved.reduce((a, v) => a + (v.rows ?? 0), 0) : null };
}

/** One item the latest run's screen HOLDS for a person (review band): never extracted or built
 *  until an owner lifts the hold for exactly this content digest. */
export interface ScreenHold {
  item_id: string;
  uri: string | null;
  item_kind: string | null;
  digest: string;
  rules: { rule: string | null; type: string | null; count: number | null }[];
  rationale: string | null;
  reverse: string | null;
  carried_forward: boolean;
  lifted: boolean;
  lifted_by: string | null;
  lifted_at: string | null;
  lifted_reason: string | null;
  lifted_dev_only: boolean | null;
}

export interface HoldsListing {
  source_id: string;
  run_id: string | null;
  holds: ScreenHold[];
  blocked: number;
  log_problem: string | null;
}

/** Whether the page may offer "Lift hold": not already lifted, a content digest to bind the lift
 *  to, a non-empty reason, and an override log that verifies. The gateway checks the same. */
export function holdLiftable(h: Pick<ScreenHold, "lifted" | "digest" | "item_id">, reason: string, logProblem: string | null): boolean {
  return !h.lifted && /^[0-9a-f]{64}$/.test(h.digest) && !!h.item_id && reason.trim().length > 0 && !logProblem;
}

export interface LinkDecisionBody {
  table: string;
  column: string;
  target?: string;
  reason?: string;
}

/** Proposed links first (they need a decision), then decided ones, then the ones never offered. */
export function linksForReview(links: readonly LinkReview[]): {
  pending: LinkReview[];
  decided: LinkReview[];
  notOffered: LinkReview[];
} {
  const pending = links.filter((l) => l.status === "proposed");
  const decided = links.filter((l) => l.status === "approved" || l.status === "rejected");
  const notOffered = links.filter((l) => !pending.includes(l) && !decided.includes(l));
  return { pending, decided, notOffered };
}

/** Whether the page may offer Approve for a link. Fails closed: the gateway's own `approvable`
 *  AND a proposal with an edge kind AND never a person-kind target. The gateway refuses the same. */
export function linkApprovable(l: Pick<LinkReview, "approvable" | "status" | "edge_kind" | "person_target">): boolean {
  return l.approvable === true && l.status === "proposed" && !!l.edge_kind && l.person_target !== true;
}

/** What approving one identity rule would merge NOW: the engine's own consolidation, run on a copy
 *  of the graph (it changes nothing). */
export interface IdentityRulePreview {
  groups: number;
  records_absorbed: number;
  sample_groups: { survivor: string; records: string[]; labels: string[]; rules: string[] }[];
  refused: number;
  refused_samples: { records: string[]; reason: string }[];
}

export interface IdentityRuleType {
  rule: "business_key" | "exact_name" | string;
  state: "approved" | "revoked" | "not_approved" | string;
  decided_by: string | null;
  decided_at: string | null;
  dev_only: boolean | null;
  preview: IdentityRulePreview | null;
  preview_note: string | null;
}

/** A pack's declaration of how records of one NON-person kind may be recognised as one record
 *  (D-004). Inert until a named person approves a rule type. */
export interface IdentityRuleDecl {
  pack: string;
  kind: string;
  business_keys: string[];
  exact_name: boolean;
  similarity_merge: boolean;
  rules: IdentityRuleType[];
}

export interface IdentityRulesListing {
  packs: string[];
  identity_rules: IdentityRuleDecl[];
}

export interface SourceDetail {
  connection: Connection;
  tools: string[];
  tools_note: string | null;
  plan: CrawlPlan | null;
  mapping: SourceMapping | null;
  last_run: RunListEntry | null;
  runs: number;
  running: { run_id: string; progress: ProgressEvent | null } | null;
}

export interface SourceHealth {
  source_id: string;
  overall: "running" | "failing" | "awaiting_approval" | "never_synced" | "healthy" | string;
  plan_status: string | null;
  last_run: RunListEntry | null;
  last_success: RunListEntry | null;
  consecutive_failures: number;
  running: LiveRun | null;
  issues: string[];
  generated_at: string;
}

export interface RunsList {
  source_id: string;
  runs: RunListEntry[];
  in_flight: { run_id: string; status: string; progress: ProgressEvent | null; started: string }[];
}

export interface StageRecord {
  done_at?: string;
  seconds?: number;
  breakdown?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface RunReport {
  source_id: string;
  run_id: string;
  status: RunStatus;
  message: string;
  counts: RunCounts;
  durations_s: Record<string, number>;
  verify: { ok?: boolean; problems?: string[]; counts?: RunCounts; checked_at?: string };
  notes: string[];
  started_at: string;
  finished_at: string;
  plan: { origin?: string; profiles?: string[]; approved_by?: string | null; steps?: string[] };
}

export interface RunDetail {
  source_id: string;
  run_id: string;
  status: RunStatus | null;
  report: RunReport | null;
  stages: Record<string, StageRecord> | null;
  checkpoint: Record<string, unknown> | null;
  verify: { ok?: boolean; problems?: string[]; counts?: RunCounts; checked_at?: string } | null;
  progress: ProgressEvent | null;
  events: ProgressEvent[];
  error: string | null;
  graph_counts: Record<string, unknown> | null;
  started_by: string | null;
}

export interface StartedRun {
  run_id: string;
  job_id: string;
  status: "running";
  started_by: string;
}

/** `GET /auth/whoami`: the principal the gateway authenticated this page's credential as. */
export interface WhoAmI {
  principal_id: string;
  dev_only: boolean;
  authenticated_by: string;
}

/** `/platform/catalogue` — the backend's own read/write classification of each tool. */
export interface CatalogueConnection {
  id: string;
  title: string;
  kind: string;
  tools: { name: string; write: boolean; basis: string }[];
  plan_status: string | null;
  purpose: string | null;
}

export interface RegisterBody {
  mode: "command" | "url" | "mcpjungle" | "import";
  id?: string;
  title?: string;
  command?: string;
  args?: string[];
  /** child env var -> HOST env var NAME. Never a value. */
  env_from?: Record<string, string>;
  url?: string;
  /** header -> HOST env var NAME. Never a value. */
  headers_from?: Record<string, string>;
  jungle_server?: string;
  scope?: Scope;
  replace?: boolean;
  route?: "direct" | "mcpjungle";
  only?: string[];
}

// ---------------------------------------------------------------------------------------------
// pure decision helpers — what the page may send, offer and claim (R-019)
// ---------------------------------------------------------------------------------------------

/** A URL query parameter whose name says it carries a credential. */
const SECRET_PARAM = /(^|[_.-])(token|secret|password|passwd|pwd|api[_-]?key|apikey|access[_-]?key|auth|authorization|credentials?|client[_-]?secret|private[_-]?key|sig|signature|session|key)($|[_.-])/i;
/** A command-line flag / NAME=value name that says it carries a credential. Narrower than the URL
 *  rule on purpose (`--auth` is often a mode switch, not a value). */
const SECRET_FLAG = /(^|[_.-])(token|secret|password|passwd|pwd|api[_-]?key|apikey|access[_-]?key|client[_-]?secret|private[_-]?key|credentials?)($|[_.-])/i;
/** Well-known credential shapes, wherever they appear. */
const SECRET_LITERAL =
  /(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})/;
/** The same shapes, global, for REPLACING: every occurrence is redacted, not only the first
 *  (R-019 round 3, S1). Kept separate so `.test()` never inherits a global regex's lastIndex. */
const SECRET_LITERAL_ALL = new RegExp(SECRET_LITERAL.source, "g");
const REDACTED = "REDACTED";
/** A source / server id, as the gateway's `_ID_RE` accepts it. */
const SOURCE_ID_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;

function flagName(arg: string): string {
  return arg.replace(/^-+/, "");
}

/** Every reason the given connection fields would carry a literal secret. Empty means none was
 *  found. The form refuses to send while this is non-empty: secrets reach a source only as HOST
 *  environment-variable NAMES (`env_from`, `headers_from`). */
export function secretProblems(f: { url?: string; command?: string; args?: string[]; other?: string[] }): string[] {
  const out: string[] = [];
  const url = f.url?.trim();
  if (url) {
    let parsed: URL | null = null;
    try {
      parsed = new URL(url);
    } catch {
      /* not a URL — the form's own URL check reports that */
    }
    if (parsed) {
      if (parsed.username || parsed.password) out.push("The URL carries a user name or password (user:pass@). Use a header from a host variable instead.");
      parsed.searchParams.forEach((_v, k) => {
        if (SECRET_PARAM.test(k)) out.push(`The URL's query parameter "${k}" looks like a credential. Pass it as a header from a host variable instead.`);
      });
    } else if (/\/\/[^/@\s]+@/.test(url)) {
      out.push("The URL carries a user name or password (user:pass@).");
    }
    if (SECRET_LITERAL.test(url)) out.push("The URL contains what looks like an API key or token.");
  }
  if (f.command && SECRET_LITERAL.test(f.command)) out.push("The command contains what looks like an API key or token.");
  // A command line typed into the command field is checked exactly like the argument list: a
  // credential flag (`--password=x`, `--token x`, `PASSWORD=x`) embedded there is refused too.
  const cmdTokens = (f.command ?? "").trim().split(/\s+/).filter(Boolean).slice(1);
  for (const problem of argSecretProblems(cmdTokens)) out.push(`In the command: ${problem}`);
  out.push(...argSecretProblems(f.args ?? []));
  for (const o of f.other ?? []) if (SECRET_LITERAL.test(o)) out.push("A field contains what looks like an API key or token.");
  return Array.from(new Set(out));
}

function argSecretProblems(args: string[]): string[] {
  const out: string[] = [];
  args.forEach((a, i) => {
    const eq = a.indexOf("=");
    if (a.startsWith("-") && eq > 0 && SECRET_FLAG.test(flagName(a.slice(0, eq)))) {
      out.push(`Argument "${a.slice(0, eq)}=…" passes a credential literally. Use an environment variable NAME instead.`);
    } else if (a.startsWith("-") && eq < 0 && SECRET_FLAG.test(flagName(a)) && i + 1 < args.length && !args[i + 1].startsWith("-")) {
      out.push(`Argument "${a}" is followed by a literal value that looks like a credential. Use an environment variable NAME instead.`);
    } else if (!a.startsWith("-") && eq > 0 && /^[A-Za-z_][A-Za-z0-9_]*$/.test(a.slice(0, eq)) && SECRET_FLAG.test(a.slice(0, eq))) {
      out.push(`Argument "${a.slice(0, eq)}=…" sets a credential literally. Use an environment variable NAME instead.`);
    } else if (SECRET_LITERAL.test(a)) {
      out.push(`Argument ${i + 1} contains what looks like an API key or token.`);
    }
  });
  return out;
}

/** The free-text checks the add-source form applies in EVERY mode (R-019 round 3, S1): in import
 *  mode the server-id list is free text too, so each id must be an id and no field may carry a
 *  literal secret. (The form adds its own per-mode checks — id present, URL scheme, the launch
 *  acknowledgement — on top of these.) */
export function registrationProblems(f: {
  mode: RegisterBody["mode"];
  only?: string;
  url?: string;
  command?: string;
  args?: string[];
  other?: string[];
}): string[] {
  const out: string[] = [];
  if (f.mode === "import") {
    const ids = (f.only ?? "").split(/[\s,]+/).filter(Boolean);
    const bad = ids.filter((i) => !SOURCE_ID_RE.test(i));
    if (bad.length) out.push(`Not a server id (letters, digits, '.', '_' and '-' only): ${bad.join(", ")}`);
    out.push(...secretProblems({ other: [...ids, ...(f.other ?? [])] }));
    return Array.from(new Set(out));
  }
  out.push(
    ...secretProblems({
      url: f.mode === "url" ? f.url : undefined,
      command: f.mode === "command" ? f.command : undefined,
      args: f.mode === "command" ? f.args : undefined,
      other: f.other,
    }),
  );
  return Array.from(new Set(out));
}

/** A stored URL with any userinfo and credential-named query values replaced, for display. */
export function redactUrl(url: string): string {
  try {
    const u = new URL(url);
    if (u.username) u.username = REDACTED;
    if (u.password) u.password = REDACTED;
    const keys: string[] = [];
    u.searchParams.forEach((_v, k) => keys.push(k));
    for (const k of keys) if (SECRET_PARAM.test(k)) u.searchParams.set(k, REDACTED);
    return u.toString().replace(SECRET_LITERAL_ALL, REDACTED);
  } catch {
    return url.replace(/\/\/[^/@\s]+@/, `//${REDACTED}@`).replace(SECRET_LITERAL_ALL, REDACTED);
  }
}

/** A stored argument list with credential-looking values replaced, for display. */
export function redactArgs(args: string[]): string[] {
  return args.map((a, i) => {
    const eq = a.indexOf("=");
    const name = eq > 0 ? a.slice(0, eq) : "";
    if (eq > 0 && SECRET_FLAG.test(flagName(name))) return `${name}=${REDACTED}`;
    const prev = i > 0 ? args[i - 1] : "";
    if (prev.startsWith("-") && !prev.includes("=") && SECRET_FLAG.test(flagName(prev)) && !a.startsWith("-")) return REDACTED;
    return a.replace(SECRET_LITERAL_ALL, REDACTED);
  });
}

/** A stored command line for display: its first word (the program) kept, every later word run
 *  through `redactArgs`, and every well-known credential shape redacted anywhere (R-019 round 3, S1). */
export function redactCommand(command: string): string {
  const words = command.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  const [program, ...rest] = words;
  return [program.replace(SECRET_LITERAL_ALL, REDACTED), ...redactArgs(rest)].join(" ");
}

/** The marker `plans.py` / `mapping.py` record when the connection's `auto_approve` flag approved
 *  on proposal. That is not a person's decision. */
export function isAutoApproval(approvedBy: string | null | undefined): boolean {
  return !!approvedBy && /^auto_approve\b/i.test(approvedBy.trim());
}

/** Approved BY A PERSON: approved, attributed, and not by the auto_approve flag. */
export function humanApproved(plan: Pick<CrawlPlan, "status" | "approved_by"> | null | undefined): boolean {
  return !!plan && plan.status === "approved" && !!plan.approved_by?.trim() && !isAutoApproval(plan.approved_by);
}

/** Only an exact `ingest` connection may propose, approve or run a crawl. Anything else — a write
 *  connection, an unknown or missing purpose — fails closed. */
export function crawlAllowed(purpose: string | null | undefined): boolean {
  return purpose === "ingest";
}

/** The tools a plan's steps call, checked against the backend's own read/write catalogue. */
export function planToolAudit(
  plan: Pick<CrawlPlan, "steps"> | null,
  catalogue: Pick<CatalogueConnection, "tools"> | null,
): { used: string[]; writes: string[]; unclassified: string[] } {
  const used = Array.from(
    new Set(
      (plan?.steps ?? [])
        .flatMap((s) => [s.roots?.tool, s.list?.tool, s.read?.text_tool, s.read?.binary_tool, s.sql?.tool])
        .filter((t): t is string => !!t),
    ),
  );
  const byName = new Map((catalogue?.tools ?? []).map((t) => [t.name, t]));
  return {
    used,
    writes: used.filter((t) => byName.get(t)?.write === true),
    unclassified: used.filter((t) => !byName.has(t)),
  };
}

/** A plan tool a PERSON may vouch for as a read (FIN-02), and why nothing else could.
 *
 *  Only a tool that NOTHING classifies is vouchable: the catalogue calls it a write solely because
 *  nothing vouches for it (`basis: "unknown"`) AND the plan's own live classification says
 *  `unclassified:unknown`. A tool whose name or annotation says it writes, a denied tool, or a
 *  write-only connection's tool is never offered: the backend refuses those whatever is sent, and
 *  this page does not invite the attempt. */
export interface VouchableTool {
  tool: string;
  /** The plan's own class for it, verbatim (e.g. `unclassified:unknown (shared classifier)`). */
  plan_class: string;
  /** Plain-English reason it is unclassified. */
  reason: string;
}

export function vouchableTools(
  plan: Pick<CrawlPlan, "steps" | "tool_classes"> | null,
  catalogue: Pick<CatalogueConnection, "tools"> | null,
): VouchableTool[] {
  if (!plan || !catalogue) return [];
  const byName = new Map(catalogue.tools.map((t) => [t.name, t]));
  const classes = plan.tool_classes ?? {};
  return planToolAudit(plan, catalogue)
    .writes.filter((t) => byName.get(t)?.basis === "unknown" && (classes[t] ?? "").startsWith("unclassified:unknown"))
    .map((t) => ({
      tool: t,
      plan_class: classes[t],
      reason:
        "Its name carries no read verb, the server publishes no read-only annotation for it, and no approved plan " +
        "uses it yet, so nothing establishes that it only reads. It is treated as a write until a person vouches for it.",
    }));
}

/** Why a crawl plan cannot be approved from this page. Empty means it may be. Fails closed: a
 *  non-ingest connection, a missing catalogue, or ANY tool the catalogue does not positively
 *  classify as read-only blocks approval (R-019 round 3, S3), UNLESS it is a `vouchableTools`
 *  tool the signed-in person has explicitly vouched for (`vouched`, FIN-02). */
export function planApprovalBlockers(
  plan: Pick<CrawlPlan, "steps" | "problems" | "tool_classes"> | null,
  catalogue: Pick<CatalogueConnection, "tools"> | null,
  purpose: string | null | undefined,
  vouched: readonly string[] = [],
): string[] {
  const out: string[] = [];
  if (!crawlAllowed(purpose)) out.push("This connection's purpose is not \"ingest\", so its plan cannot be approved here.");
  if (!plan) return out;
  if ((plan.problems ?? []).length) out.push("Resolve the plan's problems first (adjust the connection or scope, then propose again).");
  if (!catalogue) {
    out.push("The tool catalogue is unavailable, so this page cannot establish that the plan's tools only read.");
    return out;
  }
  const audit = planToolAudit(plan, catalogue);
  const vouchable = new Set(vouchableTools(plan, catalogue).map((v) => v.tool));
  const writes = audit.writes.filter((t) => !vouchable.has(t));
  const unvouched = audit.writes.filter((t) => vouchable.has(t) && !vouched.includes(t));
  if (writes.length) out.push(`The plan calls ${writes.join(", ")}, which the tool catalogue classifies as writing.`);
  if (unvouched.length) {
    out.push(
      `Nothing classifies ${unvouched.join(", ")} as a read. Vouch for it below if you know it only reads; until then it is treated as writing.`,
    );
  }
  if (audit.unclassified.length) {
    out.push(`The tool catalogue does not classify ${audit.unclassified.join(", ")}, so it cannot be shown to only read.`);
  }
  return out;
}

/** Whether the table mapping still needs a PERSON before a run may build from it (R-019 round 3,
 *  S2). Any approved table in a mapping whose approver is missing or the auto_approve flag means
 *  the run would build from a machine-approved mapping. A mapping with nothing approved builds
 *  nothing from it, so it does not block a run. */
export function mappingGate(mapping: Pick<SourceMapping, "tables" | "approved_by"> | null | undefined): {
  needsHuman: boolean;
  approvedTables: string[];
} {
  const approvedTables = Object.entries(mapping?.tables ?? {})
    .filter(([, t]) => t.status === "approved")
    .map(([name]) => name);
  const byPerson = !!mapping?.approved_by?.trim() && !isAutoApproval(mapping?.approved_by);
  return { needsHuman: approvedTables.length > 0 && !byPerson, approvedTables };
}

/** The stage ids the timeline shows: the runner's known order, then — in first-seen order — every
 *  other stage named by a stage record, an event, or the live progress (R-019 round 3, S7). */
export function stageOrder(
  known: string[],
  run: { stages: Record<string, unknown> | null; events: { stage?: string }[] | null; progress: { stage?: string } | null },
): string[] {
  const out = [...known];
  const seen = new Set(out);
  const names = [...Object.keys(run.stages ?? {}), ...(run.events ?? []).map((e) => e.stage), run.progress?.stage];
  for (const name of names) {
    if (typeof name === "string" && name && !seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

/** Run statuses the runner ends in. `running`, `null` and anything unknown are NOT finished. */
export const RUN_TERMINAL_STATUSES = ["ok", "empty", "failed", "unverified", "incomplete", "refused", "completed", "stopped"] as const;
export function isTerminalRunStatus(status: string | null | undefined): boolean {
  return !!status && (RUN_TERMINAL_STATUSES as readonly string[]).includes(status);
}

/** A stage finished only when its record says so (`done_at`), not because a record exists. */
export function stageCompleted(rec: StageRecord | null | undefined): boolean {
  return typeof rec?.done_at === "string" && rec.done_at.length > 0;
}

/** What the ledger summary lets the page say about exclusions. A truncated read never supports an
 *  exhaustive "nothing excluded"; a counted exclusion with no reasons is not "none" either. */
export function exclusionsVerdict(
  s: { exclusion_reasons: unknown[]; truncated: boolean; excluded?: number },
): "some" | "none" | "none-in-examined" | "counted-without-reasons" {
  if (s.exclusion_reasons.length > 0) return "some";
  if (typeof s.excluded === "number" && s.excluded > 0) return "counted-without-reasons";
  return s.truncated ? "none-in-examined" : "none";
}

/** Which counts to show: the latest verification's when it has any, else the report's snapshot. */
export function pickCounts(
  run: Pick<RunDetail, "report" | "verify">,
): { counts: RunCounts | null; from: "verify" | "report-verify" | "report" | null } {
  const v = run.verify?.counts;
  if (v && Object.keys(v).length) return { counts: v, from: "verify" };
  // The report's own embedded verification outranks its pre-verification snapshot (R-019 round 3, S5).
  const rv = run.report?.verify?.counts;
  if (rv && Object.keys(rv).length) return { counts: rv, from: "report-verify" };
  const r = run.report?.counts;
  if (r && Object.keys(r).length) return { counts: r, from: "report" };
  return { counts: null, from: null };
}

// ---------------------------------------------------------------------------------------------
// transport
// ---------------------------------------------------------------------------------------------

export interface SourcesFailure {
  ok: false;
  /** 0 when the gateway could not be reached at all. */
  status: number;
  code: string;
  /** The gateway's message, verbatim. */
  message: string;
  /** What it means for the operator, in plain English. */
  plain: string;
}

export type SourcesResult<T> = { ok: true; status: number; data: T } | SourcesFailure;

/** Plain-English meaning of a refusal. The gateway's own message is always shown beside it. */
export function plainMessage(status: number, code: string, message: string): string {
  if (status === 0) {
    return `The gateway at ${GATEWAY} could not be reached. Check that it is running.`;
  }
  switch (code) {
    case "PLAN_NOT_APPROVED":
      return "This source cannot run yet: its crawl plan has not been approved. Review the plan and approve it first.";
    case "RUN_IN_PROGRESS":
      return "A run is already in progress for this source. Open it from the run history to watch it.";
    case "SOURCE_EXISTS":
      return "A source with this id is already registered. Choose another id, or tick “Replace the existing source”.";
    case "BAD_CONNECTION":
      return "The gateway refused this connection declaration.";
    case "PLAN_FAILED":
      return "Walnut could not connect to the server to list its tools, so no plan was proposed.";
    case "PLAN_NOT_APPROVABLE":
      return "The plan could not be approved. A plan with recorded problems, or no plan at all, cannot be approved.";
    case "MAPPING_NOT_APPROVABLE":
      return "The table mapping could not be approved.";
    case "LINK_TARGETS_PERSON":
      return "This link points at records of people. An inferred link never points at a person record, so it cannot be approved.";
    case "LINK_NOT_APPROVABLE":
      return "The link could not be approved. Only a proposed link with an edge kind can be approved.";
    case "LINK_NOT_REJECTABLE":
      return "The link could not be rejected. Only a proposed link can be rejected.";
    case "IDENTITY_RULE_REFUSED":
      return "That identity rule cannot be decided: only a rule a bound pack declares, on a kind that is not a person, can be approved.";
    case "UNAUTHENTICATED_APPROVER":
      return "The signed-in principal could not be established, so nothing was approved.";
    case "MAPPING_UNREADABLE":
      return "The table mapping could not be read for review.";
    case "INGEST_UNAVAILABLE":
      return "Source ingestion is not available in this gateway (the ingest package failed to load).";
    case "UNKNOWN_SOURCE":
      return "No source is registered under this id.";
    case "UNKNOWN_RUN":
      return "No run with this id exists for this source.";
    case "BAD_SOURCE_ID":
    case "BAD_RUN_ID":
      return "That id is not valid (letters, digits, '.', '_' and '-' only).";
    case "VERIFY_FAILED":
      return "Re-verifying the run failed.";
    case "CATALOGUE_UNAVAILABLE":
      return "The tool catalogue is unavailable, so tools cannot be marked read or write.";
  }
  if (status === 401) {
    return "The gateway did not accept this terminal's credentials. This build may lack its access token (NEXT_PUBLIC_WALNUT_TOKEN), or the token was rejected.";
  }
  if (status === 403) return "The signed-in principal is not allowed to do this.";
  if (status === 404) return "Not found on the gateway. It may be an older gateway without the /sources routes.";
  if (status === 409) return "The request conflicts with the source's current state.";
  if (status === 422) return "The gateway refused the request as invalid.";
  if (status >= 500) return "The gateway failed while handling this request.";
  return "The request did not succeed.";
}

function describeDetail(detail: unknown): string {
  // FastAPI request-validation 422: {detail: [{loc, msg}]}
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${d?.msg ?? ""}` : String(d?.msg ?? "");
      })
      .join("; ");
  }
  return typeof detail === "string" ? detail : "";
}

async function request<T>(path: string, method = "GET", payload?: unknown): Promise<SourcesResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}${path}`, {
      method,
      headers: { ...(payload !== undefined ? { "Content-Type": "application/json" } : {}), ...authHeaders() },
      body: payload !== undefined ? JSON.stringify(payload) : undefined,
      cache: "no-store",
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return { ok: false, status: 0, code: "UNREACHABLE", message, plain: plainMessage(0, "UNREACHABLE", message) };
  }
  const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (res.ok) return { ok: true, status: res.status, data: body as T };
  const refusal = body.refusal as { code?: string; message?: string } | undefined;
  const code = refusal?.code ?? (res.status === 401 ? "UNAUTHENTICATED" : `HTTP_${res.status}`);
  const message = refusal?.message ?? (describeDetail(body.detail) || `${path} → ${res.status}`);
  return { ok: false, status: res.status, code, message, plain: plainMessage(res.status, code, message) };
}

const enc = encodeURIComponent;

export const sourcesApi = {
  list: () => request<SourcesList>("/sources"),
  get: (id: string) => request<SourceDetail>(`/sources/${enc(id)}`),
  health: (id: string) => request<SourceHealth>(`/sources/${enc(id)}/health`),
  register: (body: RegisterBody) =>
    request<{ registered: Connection[]; by: string }>("/sources", "POST", body),
  proposePlan: (id: string) =>
    request<{ plan: CrawlPlan; proposed_by: string }>(`/sources/${enc(id)}/plan`, "POST"),
  /** `vouchReadTools` is the signed-in person's explicit, per-tool statement that an UNCLASSIFIED
   *  tool only reads (FIN-02). Sent only when non-empty; the backend accepts it for unknowns only. */
  approvePlan: (id: string, vouchReadTools: readonly string[] = []) =>
    request<{ plan: CrawlPlan }>(
      `/sources/${enc(id)}/plan/approve`,
      "POST",
      vouchReadTools.length ? { vouch_read_tools: [...vouchReadTools] } : undefined,
    ),
  approveMapping: (id: string, tables?: string[]) =>
    request<{ mapping: SourceMapping }>(`/sources/${enc(id)}/mapping/approve`, "POST", tables ? { tables } : {}),
  /** Proposed links with their evidence, and the tables that need approval again (LINK7). */
  mappingReview: (id: string) => request<MappingReview>(`/sources/${enc(id)}/mapping/review`),
  /** Approve ONE proposed link as the signed-in principal (never into a person kind). */
  approveLink: (id: string, body: LinkDecisionBody) =>
    request<{ mapping: SourceMapping; approved_by: string }>(`/sources/${enc(id)}/links/approve`, "POST", body),
  identityRules: () => request<IdentityRulesListing>("/ontology/identity-rules"),
  decideIdentityRule: (body: { pack: string; kind: string; rule: string; decision: "approved" | "revoked" }) =>
    request<{ decision: Record<string, unknown>; consolidation: { merged: unknown[]; refused: unknown[] } | null }>(
      "/ontology/identity-rules/decide",
      "POST",
      body,
    ),
  rejectLink: (id: string, body: LinkDecisionBody) =>
    request<{ mapping: SourceMapping; rejected_by: string }>(`/sources/${enc(id)}/links/reject`, "POST", body),
  /** Items the latest run's screen holds for a person, with the rules and reason (UI8). */
  holds: (id: string) => request<HoldsListing>(`/sources/${enc(id)}/holds`),
  /** Lift (or revoke the lift of) ONE hold for ONE content digest, as the signed-in principal. */
  override: (id: string, body: { action: "approve" | "revoke"; item_id: string; digest: string; reason: string }) =>
    request<{ override: Record<string, unknown> }>(`/sources/${enc(id)}/overrides`, "POST", body),
  startRun: (id: string) => request<StartedRun>(`/sources/${enc(id)}/runs`, "POST"),
  runs: (id: string) => request<RunsList>(`/sources/${enc(id)}/runs`),
  run: (id: string, runId: string) => request<RunDetail>(`/sources/${enc(id)}/runs/${enc(runId)}`),
  verifyRun: (id: string, runId: string) =>
    request<{ ok: boolean; problems: string[]; counts: RunCounts; checked_at: string }>(
      `/sources/${enc(id)}/runs/${enc(runId)}/verify`,
      "POST",
    ),
  catalogue: () => request<{ connections: CatalogueConnection[] }>("/platform/catalogue"),
  /** Who this page is signed in as, and whether that is the public development token (FIN-16). */
  whoami: () => request<WhoAmI>("/auth/whoami"),
  /** Per-run exclusion reasons (rule + count), read from the run ledgers. */
  ledgerRuns: (id: string) =>
    request<{ source: string; runs: EvidenceRunSummary[] }>(`/evidence/sources/${enc(id)}`),
};

/** Unwrap for react-query: a failure is thrown as an Error carrying the failure. */
export class SourcesError extends Error {
  constructor(public failure: SourcesFailure) {
    super(failure.plain);
  }
}

export async function unwrap<T>(p: Promise<SourcesResult<T>>): Promise<T> {
  const r = await p;
  if (!r.ok) throw new SourcesError(r);
  return r.data;
}

export function failureOf(e: unknown): SourcesFailure | null {
  if (e instanceof SourcesError) return e.failure;
  if (e instanceof Error) return { ok: false, status: 0, code: "ERROR", message: e.message, plain: e.message };
  return null;
}
