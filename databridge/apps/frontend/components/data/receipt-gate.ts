/**
 * Pure decisions the Data page makes about a run's evidence — kept free of React so they can be
 * tested without a page or a gateway (`e2e/data.spec.ts`, `@unit`). R-021 (Codex first review f1).
 *
 * The rule throughout: a download or a claim is allowed only when the evidence for it was
 * received and checks out. Absent evidence is never read as a pass, and an unknown value is never
 * rendered as a negative.
 */
import type { PointerCheck, ReceiptDoc, Refusal, RunSummary } from "../../lib/data-api";
import type { QuarantineView } from "../../lib/types";

/** The authoritative receipt (`GET /materialise/receipts/{plan_hash}`), as the page holds it. */
export type ReceiptState = { state: "loading" } | { state: "ok"; doc: ReceiptDoc } | { state: "refused"; refusal: Refusal };

// --- reproducibility ------------------------------------------------------------------------

export function reproducibility(
  value: boolean | null | undefined,
  shapedBy: string | null | undefined,
): { kind: "yes" | "no" | "unknown"; text: string } {
  if (value === true) {
    return { kind: "yes", text: "Reproducible: re-running this plan against the same pinned sources reproduces content_hash." };
  }
  if (value === false) {
    return shapedBy === "agent"
      ? {
          kind: "no",
          text:
            "Not reproducible as bytes — agent-shaped: the agent chose the columns and order, so plan_hash names the request, not these bytes. Re-running the same request may produce a different content_hash; re-running plan.yaml as a plan is the reproducible path.",
        }
      : { kind: "no", text: "Not reproducible (the receipt says so; see its notes)." };
  }
  return { kind: "unknown", text: "Reproducibility not reported — the receipt carries no value for it, so the page makes no claim either way." };
}

// --- integrity: is this artefact for the run on screen? --------------------------------------

export function receiptIntegrity(run: RunSummary, doc: ReceiptDoc): string[] {
  const problems: string[] = [];
  const r = doc?.receipt;
  if (!r || !doc.platform) return ["the receipt document has no receipt/platform block"];
  if (r.plan_hash !== run.plan_hash) problems.push(`receipt plan_hash ${String(r.plan_hash)} is not this run's ${run.plan_hash}`);
  if (r.content_hash !== run.content_hash) {
    problems.push(`receipt content_hash ${String(r.content_hash)} is not this run's ${run.content_hash}`);
  }
  return problems;
}

/**
 * The receipt the page may TRUST: it loaded and it is for this run. Everything else — a receipt
 * still loading, refused, or for another run — yields `null`, and nothing from it (not its file
 * list, not its file roles, not its shape) is used. R-021 round 2, findings 1 and 4.
 */
export function validatedReceipt(run: RunSummary, receipt: ReceiptState | null | undefined): ReceiptDoc | null {
  if (!receipt || receipt.state !== "ok") return null;
  return receiptIntegrity(run, receipt.doc).length === 0 ? receipt.doc : null;
}

/**
 * plan.yaml is checked against the run by the plan_hash it DECLARES (its `hashes.plan_hash`), and
 * its shape and entity are compared INDEPENDENTLY with the run's and with the receipt's — the
 * receipt never overrides the run. Pass only a validated receipt (`validatedReceipt`). The browser
 * does not recompute the canonical hash — that is a backend job (R-021 finding 5, deferred to FXM).
 */
export function planIntegrity(
  run: RunSummary,
  doc: ReceiptDoc | null,
  plan: { planHash: string | null; shape: string | null; entity: string | null },
): string[] {
  const problems: string[] = [];
  if (!plan.planHash) problems.push("plan.yaml declares no plan_hash, so it cannot be tied to this run");
  else if (plan.planHash !== run.plan_hash) problems.push(`plan.yaml declares plan_hash ${plan.planHash}, not this run's ${run.plan_hash}`);
  const against: [string, string | null | undefined][] = [
    ["the run's", run.shape],
    ["the receipt's", doc?.platform?.shape],
  ];
  for (const [who, shape] of against) {
    if (shape && plan.shape !== shape) problems.push(`plan.yaml shape ${String(plan.shape)} is not ${who} ${shape}`);
  }
  const entities: [string, string | null | undefined][] = [
    ["the run's", run.entity],
    ["the receipt's", doc?.platform?.entity],
  ];
  for (const [who, entity] of entities) {
    if (entity && plan.entity !== entity) problems.push(`plan.yaml entity ${String(plan.entity)} is not ${who} ${entity}`);
  }
  return problems;
}

/**
 * Whether plan.yaml may be shown as "the plan that ran": `verified` only against a VALID receipt;
 * `unverified` when it agrees with the run but no valid receipt stands behind it yet; `mismatch`
 * when it disagrees with the run or the valid receipt.
 */
export function planAttribution(
  run: RunSummary,
  receipt: ReceiptState | null | undefined,
  plan: { planHash: string | null; shape: string | null; entity: string | null },
): { status: "verified" | "unverified" | "mismatch"; problems: string[]; note: string | null } {
  const doc = validatedReceipt(run, receipt);
  const problems = planIntegrity(run, doc, plan);
  if (problems.length) return { status: "mismatch", problems, note: null };
  if (doc) return { status: "verified", problems, note: null };
  const why =
    !receipt || receipt.state === "loading"
      ? "the full receipt has not loaded yet"
      : receipt.state === "refused"
        ? `the full receipt could not be loaded (${receipt.refusal.code})`
        : "the receipt the gateway returned is not for this run";
  return { status: "unverified", problems, note: `Unverified — ${why}, so this plan.yaml is not confirmed as the plan that ran.` };
}

// --- pointer verification and the download gate ---------------------------------------------

function samePointerCheck(a: PointerCheck, b: PointerCheck): boolean {
  return (
    a.grounded_cells === b.grounded_cells &&
    a.pointers_resolved === b.pointers_resolved &&
    a.values_match === b.values_match &&
    a.all_resolved === b.all_resolved &&
    a.all_match === b.all_match &&
    (a.failures?.length ?? 0) === (b.failures?.length ?? 0)
  );
}

/** A complete, passing pointer check whose grounded-cell count agrees with the receipt's. */
export function pointerVerdict(pc: PointerCheck | null | undefined, groundedCells: number | null | undefined): { ok: boolean; reason: string } {
  if (!pc) return { ok: false, reason: "the run reports no pointer re-check, so nothing says its values trace to a source" };
  if (typeof pc.grounded_cells !== "number" || typeof pc.pointers_resolved !== "number" || typeof pc.values_match !== "number") {
    return { ok: false, reason: "the pointer re-check is incomplete (a count is missing)" };
  }
  if (!pc.all_resolved || pc.pointers_resolved !== pc.grounded_cells) {
    return { ok: false, reason: `only ${pc.pointers_resolved} of ${pc.grounded_cells} pointers resolve` };
  }
  if (!pc.all_match || pc.values_match !== pc.grounded_cells) {
    return { ok: false, reason: `only ${pc.values_match} of ${pc.grounded_cells} pointers say the value in the cell` };
  }
  if ((pc.failures?.length ?? 0) > 0) return { ok: false, reason: `the pointer re-check lists ${pc.failures.length} failure(s)` };
  if (typeof groundedCells !== "number") {
    return { ok: false, reason: "the receipt reports no grounded-cell count to check the pointer re-check against" };
  }
  if (groundedCells !== pc.grounded_cells) {
    return { ok: false, reason: `the pointer re-check covered ${pc.grounded_cells} cells but the receipt counts ${groundedCells} grounded cells` };
  }
  return { ok: true, reason: `all ${pc.grounded_cells} grounded cells were re-read from their sources and match` };
}

export type PointerDisplay = {
  state: "pass" | "fail" | "disagree" | "absent" | "unverified";
  /** The check whose numbers are drawn (the receipt's when a valid one loaded, else the summary's). */
  pc: PointerCheck | null;
  reason: string;
};

/**
 * What the card may SAY about the pointer re-check. Green (`pass`) needs a valid receipt, the
 * summary's check and the receipt's agreeing (both present or both absent), and the complete
 * `pointerVerdict` passing against the receipt's grounded-cell count. A summary that already
 * fails is shown as failing at once; a summary that passes but has no valid receipt behind it is
 * `unverified`, never green. R-021 round 2, finding 3.
 */
export function pointerDisplay(run: RunSummary, receipt: ReceiptState | null | undefined): PointerDisplay {
  const doc = validatedReceipt(run, receipt);
  const spc = run.pointer_check ?? null;
  if (!doc) {
    if (!spc) return { state: "absent", pc: null, reason: "no pointer re-check was reported for this run — nothing says its values trace to a source" };
    const v = pointerVerdict(spc, run.cells?.grounded);
    if (!v.ok) return { state: "fail", pc: spc, reason: v.reason };
    return {
      state: "unverified",
      pc: spc,
      reason: "the job summary reports a passing re-check, but no valid full receipt confirms it yet",
    };
  }
  const rpc = doc.platform.pointer_check ?? null;
  if (!!spc !== !!rpc) {
    return {
      state: "disagree",
      pc: rpc ?? spc,
      reason: rpc ? "the receipt reports a pointer re-check but the job summary reports none" : "the job summary reports a pointer re-check but the receipt reports none",
    };
  }
  if (spc && rpc && !samePointerCheck(spc, rpc)) {
    return { state: "disagree", pc: rpc, reason: "the job summary's pointer re-check and the receipt's disagree" };
  }
  if (!rpc) return { state: "absent", pc: null, reason: "no pointer re-check was reported for this run — nothing says its values trace to a source" };
  const grounded = (doc.receipt.cells as { grounded?: number } | null | undefined)?.grounded;
  if (run.cells && typeof grounded === "number" && run.cells.grounded !== grounded) {
    return { state: "disagree", pc: rpc, reason: "the job summary's grounded-cell count and the receipt's disagree" };
  }
  // the receipt's own count only: a count the receipt does not carry is not borrowed from the summary
  const v = pointerVerdict(rpc, grounded);
  return { state: v.ok ? "pass" : "fail", pc: rpc, reason: v.reason };
}

/**
 * The missingness leak check on a VALID receipt. The executor records one on every receipt it
 * issues (`executor.py`, the single `Receipt(...)` constructor, `missingness_leak_check=leak`), so
 * an absent check is not a pass; nor is `passed: true` with an observed delta over its threshold.
 * R-021 round 2, finding 2.
 */
export function leakVerdict(doc: ReceiptDoc | null): {
  state: "pass" | "fail" | "absent" | "inconsistent" | "unverified";
  ok: boolean;
  reason: string;
} {
  if (!doc) return { state: "unverified", ok: false, reason: "the leak check is on the full receipt, which is not loaded or not valid" };
  const l = doc.receipt?.missingness_leak_check as unknown;
  if (l === null || l === undefined) {
    return { state: "absent", ok: false, reason: "the receipt records no missingness leak check, and every build records one — its absence is not a pass" };
  }
  const c = l as { passed?: unknown; max_class_delta_observed?: unknown; threshold?: unknown };
  if (typeof c.passed !== "boolean" || typeof c.max_class_delta_observed !== "number" || typeof c.threshold !== "number") {
    return { state: "inconsistent", ok: false, reason: "the missingness leak check is incomplete (its verdict or a number is missing)" };
  }
  if (!c.passed) {
    return { state: "fail", ok: false, reason: `the missingness leak check failed (worst class delta ${c.max_class_delta_observed} vs threshold ${c.threshold})` };
  }
  if (c.max_class_delta_observed > c.threshold) {
    return {
      state: "inconsistent",
      ok: false,
      reason: `the missingness leak check says passed, but its worst class delta ${c.max_class_delta_observed} is over its threshold ${c.threshold}`,
    };
  }
  return { state: "pass", ok: true, reason: `the missingness leak check passed (worst class delta ${c.max_class_delta_observed} ≤ ${c.threshold})` };
}

/**
 * Dataset downloads need: the full receipt loaded, for THIS run, whose pointer check agrees with
 * the summary's and is complete and passing against the cell counts, AND whose missingness leak
 * check is present, consistent and passed.
 */
export function datasetDownloadGate(run: RunSummary, receipt: ReceiptState): { allowed: boolean; reason: string } {
  if (receipt.state === "loading") return { allowed: false, reason: "waiting for the full receipt" };
  if (receipt.state === "refused") {
    return { allowed: false, reason: `the full receipt could not be loaded (${receipt.refusal.code}: ${receipt.refusal.message})` };
  }
  const integrity = receiptIntegrity(run, receipt.doc);
  if (integrity.length) return { allowed: false, reason: `the receipt is not for this run: ${integrity.join("; ")}` };
  const p = pointerDisplay(run, receipt);
  if (p.state !== "pass") return { allowed: false, reason: p.reason };
  const leak = leakVerdict(receipt.doc);
  if (!leak.ok) return { allowed: false, reason: leak.reason };
  return { allowed: true, reason: `${p.reason}; ${leak.reason}` };
}

/** The run's own evidence — downloadable whatever the dataset gate says. */
export const EVIDENCE_FILES: readonly string[] = ["receipt.json", "plan.yaml"];

/** Declared roles that mean the run's own evidence. The gateway's vocabulary (FXM,
 * `platform.py` `file_roles`) is receipt | plan | dataset | pointers | metadata | other (`pointers` is the per-cell
 * pointer sidecar, E2E run 2 N-08); "evidence" is kept for
 * the round-1 contract. Every other role — including metadata and anything unknown — is gated like
 * a dataset, which is also how the gateway's own `file_withheld` treats it. */
export const EVIDENCE_ROLES: readonly string[] = ["evidence", "receipt", "plan"];

/** A role declared by the given (already validated) receipt wins, and an unknown one fails closed;
 * otherwise only the fixed evidence files are evidence and everything else is a dataset artefact.
 * Pass only a validated receipt — `fileDecision` does. */
export function fileRole(name: string, doc: ReceiptDoc | null): "evidence" | "dataset" {
  const declared = (doc as unknown as { file_roles?: Record<string, string> } | null)?.file_roles;
  if (declared && typeof declared === "object" && Object.prototype.hasOwnProperty.call(declared, name)) {
    return EVIDENCE_ROLES.includes(declared[name]) ? "evidence" : "dataset";
  }
  return EVIDENCE_FILES.includes(name) ? "evidence" : "dataset";
}

/**
 * Whether one file of `run.files` may be downloaded. Roles come only from a VALID receipt; with
 * none, only the fixed evidence allowlist is downloadable. With a valid receipt, a file it does not
 * list is withheld (it is not part of the run the receipt describes), and a dataset needs the gate.
 * R-021 round 2, finding 1.
 */
export function fileDecision(
  name: string,
  run: RunSummary,
  receipt: ReceiptState,
): { role: "evidence" | "dataset"; blocked: string | null } {
  const doc = validatedReceipt(run, receipt);
  if (!doc) {
    const role = EVIDENCE_FILES.includes(name) ? "evidence" : "dataset";
    return { role, blocked: role === "evidence" ? null : `Withheld: ${datasetDownloadGate(run, receipt).reason}` };
  }
  const role = fileRole(name, doc);
  const listed = Array.isArray(doc.files) && doc.files.includes(name);
  if (!listed) return { role, blocked: "Withheld: the receipt's file list does not include this file" };
  if (role === "evidence") return { role, blocked: null };
  const gate = datasetDownloadGate(run, receipt);
  return { role, blocked: gate.allowed ? null : `Withheld: ${gate.reason}` };
}

// --- notes and exclusions -------------------------------------------------------------------

export function receiptNotes(run: RunSummary, doc: ReceiptDoc | null): { text: string; origin: "run" | "receipt" | "both" }[] {
  const out = new Map<string, "run" | "receipt" | "both">();
  for (const n of run.notes ?? []) out.set(n, "run");
  for (const n of (doc?.receipt?.notes as string[] | undefined) ?? []) out.set(n, out.has(n) ? "both" : "receipt");
  return Array.from(out, ([text, origin]) => ({ text, origin }));
}

export function catalogueExclusions(excluded: Record<string, unknown>[] | null | undefined): { what: string; origin: string | null; reason: string }[] {
  return (excluded ?? []).map((e) => {
    const entity = typeof e.entity === "string" ? e.entity : null;
    const field = typeof e.field === "string" ? e.field : null;
    const reason = typeof e.reason === "string" ? e.reason : null;
    return {
      what: entity ? (field ? `${entity}.${field}` : entity) : "(unnamed)",
      origin: typeof e.origin === "string" ? e.origin : null,
      // an entry the page does not recognise is shown whole rather than dropped
      reason: reason ?? JSON.stringify(e),
    };
  });
}

// --- converter ------------------------------------------------------------------------------

export type QuarantineWithPolicy = QuarantineView & { threshold?: number | null; threshold_rule?: string | null };

function pct(x: number): string {
  return `${Number((x * 100).toFixed(4))}%`;
}

export function quarantineThresholdText(q: QuarantineWithPolicy): string {
  const side = q.over_threshold ? "over" : "under";
  if (typeof q.threshold === "number") {
    return `${side} the ${pct(q.threshold)} threshold${q.threshold_rule ? ` (${q.threshold_rule})` : ""}`;
  }
  return `${side} the configured threshold — the gateway did not report its value`;
}

export function convertFingerprint(i: { source: string; sourceFormat: string; targetFormat: string; spec: string }): string {
  return JSON.stringify([i.source, i.sourceFormat, i.targetFormat, i.spec]);
}

export function payloadPreview(payload: unknown, max: number): { text: string; full: string; truncated: boolean } {
  let full: string;
  try {
    full = JSON.stringify(payload) ?? String(payload);
  } catch {
    full = String(payload);
  }
  if (full.length <= max) return { text: full, full, truncated: false };
  return { text: `${full.slice(0, max)}… (${full.length - max} more characters)`, full, truncated: true };
}

/**
 * A generation counter: `begin()` starts a new read and makes every earlier one stale. Only the
 * read whose token is still `current` may write to the page — so a slow, older file read can
 * never overwrite a newer selection or anything typed since. R-021 round 2, finding 5.
 */
export function latestOnly(): { begin: () => number; current: (token: number) => boolean } {
  let generation = 0;
  return { begin: () => ++generation, current: (token) => token === generation };
}

/** Base64 → UTF-8 text; throws on invalid base64 or on bytes that are not UTF-8. */
export function decodeB64Utf8(b64: string): string {
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function quarantineProblem(q: unknown): string | null {
  if (!q || typeof q !== "object") return "the response has no quarantine report";
  const v = q as Record<string, unknown>;
  if (typeof v.quarantined !== "number" || !Number.isFinite(v.quarantined)) return "quarantine.quarantined is not a number";
  if (typeof v.rate !== "number" || !Number.isFinite(v.rate)) return "quarantine.rate is not a number";
  if (typeof v.over_threshold !== "boolean") return "quarantine.over_threshold is not true/false";
  if (!v.by_reason || typeof v.by_reason !== "object" || Array.isArray(v.by_reason)) return "quarantine.by_reason is not a map";
  if (Object.values(v.by_reason as Record<string, unknown>).some((n) => typeof n !== "number")) return "quarantine.by_reason has a non-numeric count";
  if (!Array.isArray(v.records)) return "quarantine.records is not a list";
  for (const [i, r] of (v.records as unknown[]).entries()) {
    if (!r || typeof r !== "object") return `quarantine.records[${i}] is not a record`;
    const rec = r as Record<string, unknown>;
    if (typeof rec.reason_code !== "string") return `quarantine.records[${i}] has no reason_code`;
    if (typeof rec.detail !== "string") return `quarantine.records[${i}] has no detail`;
  }
  return null;
}

/**
 * Validate and decode a `POST /convert` answer BEFORE the page commits it. Anything malformed —
 * a refusal, invalid base64, bytes that are not UTF-8, a quarantine report of the wrong shape —
 * becomes an error to show, never an exception during render. R-021 round 2, finding 6.
 */
export function decodeConvertResponse(
  body: unknown,
): { ok: true; text: string; quarantine: QuarantineWithPolicy; artifactId: string } | { ok: false; error: string } {
  if (!body || typeof body !== "object") return { ok: false, error: "the gateway's answer is not a JSON object" };
  const b = body as Record<string, unknown>;
  const refusal = b.refusal as { code?: unknown; message?: unknown } | undefined;
  if (refusal && typeof refusal === "object") return { ok: false, error: `${String(refusal.code ?? "REFUSED")}: ${String(refusal.message ?? "")}` };
  if (typeof b.result_b64 !== "string") return { ok: false, error: "the gateway returned no output" };
  const qp = quarantineProblem(b.quarantine);
  if (qp) return { ok: false, error: `the gateway's answer is malformed: ${qp}` };
  if (typeof b.artifact_id !== "string") return { ok: false, error: "the gateway's answer is malformed: artifact_id is missing" };
  let text: string;
  try {
    text = decodeB64Utf8(b.result_b64);
  } catch (e) {
    return { ok: false, error: `the output could not be decoded (${e instanceof Error ? e.message : "invalid base64 or not UTF-8"})` };
  }
  return { ok: true, text, quarantine: b.quarantine as QuarantineWithPolicy, artifactId: b.artifact_id };
}
