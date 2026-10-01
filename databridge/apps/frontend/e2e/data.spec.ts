import { test, expect, type Page, type Response } from "@playwright/test";

/**
 * `/data` — the Data Agent (`/materialise/*`) above the direct converter (`/convert`).
 *
 * Runs against the REAL gateway. Every expectation is derived from a response the page itself
 * received (captured with `waitForResponse`) — the shapes the gateway lists, the job's final
 * status and refusal code, the converter's output bytes — never from a number written here.
 *
 * Needs a build with NEXT_PUBLIC_WALNUT_TOKEN set (the launcher does this): every /materialise
 * route requires an authenticated principal.
 *
 * Mutating tests: the refusal test writes nothing (a refused run leaves no directory behind). The
 * one test that may BUILD a dataset (and so writes under var/materialise) runs only when
 * `E2E_DATA_RUN=1`.
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: new Date().toISOString() }),
    );
  });
});

type ShapeRow = { shape: string; built: boolean; reason: string | null; receipt?: boolean };
type Plans = { available: boolean; plans: ShapeRow[]; shapes: string[]; shapes_not_built?: { shape: string; reason: string | null }[] };
type Job = {
  job_id: string;
  status: string;
  refusal?: { code: string; message: string };
  result?: { plan_hash: string; content_hash: string; rows: { emitted: number } | null; pointer_check: { all_resolved: boolean; all_match: boolean } | null };
};

const path = (r: Response) => new URL(r.url()).pathname;
const isGet = (re: RegExp) => (r: Response) => r.request().method() === "GET" && re.test(path(r));

async function openData(page: Page, url = "/data"): Promise<Plans> {
  const plansP = page.waitForResponse(isGet(/^\/materialise\/plans$/));
  await page.goto(url);
  const res = await plansP;
  expect(res.status(), "GET /materialise/plans must succeed for these tests (token set?)").toBe(200);
  return (await res.json()) as Plans;
}

/** Send a request through the page and return the job's FINAL state, as the page received it. */
async function runRequest(page: Page, text: string): Promise<Job> {
  const postP = page.waitForResponse((r) => r.request().method() === "POST" && path(r) === "/materialise");
  await page.getByTestId("agent-input").fill(text);
  await page.getByTestId("agent-send").click();
  const post = await postP;
  const posted = await post.json();
  if (post.status() !== 202) {
    // a route-level refusal is an answer too; hand it back in the job's shape
    return { job_id: "", status: "refused", refusal: posted.refusal };
  }
  const jobId: string = posted.job_id;
  const final = await page.waitForResponse(
    async (r) => {
      if (!isGet(new RegExp(`^/materialise/jobs/${jobId}$`))(r)) return false;
      const body = await r.json().catch(() => ({}));
      return body.status && body.status !== "running";
    },
    { timeout: 55_000 },
  );
  return (await final.json()) as Job;
}

test("the sidebar has one Data entry, and it opens /data", async ({ page }) => {
  await page.goto("/graph");
  const nav = page.locator("aside nav");
  await expect(nav.getByRole("link", { name: "Data", exact: true })).toHaveCount(1);
  await expect(nav.locator('a[href="/materialise"], a[href="/convert"]')).toHaveCount(0);
  await nav.getByRole("link", { name: "Data", exact: true }).click();
  await expect(page).toHaveURL(/\/data$/);
  await expect(page.getByTestId("data-agent-section")).toBeVisible();
  await expect(page.getByTestId("data-convert-section")).toBeVisible();
});

test("/materialise and /convert redirect to the Data page's sections, keeping the query", async ({ page }) => {
  await page.goto("/materialise?from=old-link");
  await expect(page).toHaveURL(/\/data\?from=old-link#agent$/);
  await expect(page.getByTestId("data-agent-section")).toBeVisible();

  await page.goto("/convert");
  await expect(page).toHaveURL(/\/data#convert$/);
  await expect(page.getByTestId("data-convert-section")).toBeVisible();
});

test("the shape picker shows every shape the gateway lists: built ones selectable, the rest disabled with their reason", async ({ page }) => {
  const plans = await openData(page);
  if (!plans.available) {
    await expect(page.getByTestId("materialiser-missing")).toBeVisible();
    await expect(page.getByTestId("agent-send")).toBeDisabled();
    return;
  }
  const rows = plans.plans.filter((p) => !p.receipt);
  expect(rows.length, "non-vacuous: an attached materialiser lists its shapes").toBeGreaterThan(0);
  await expect(page.getByTestId("shape-picker").locator('[data-testid^="shape-option-"]:not([data-testid="shape-option-auto"])')).toHaveCount(rows.length);
  for (const r of rows) {
    const opt = page.getByTestId(`shape-option-${r.shape}`);
    await expect(opt).toHaveAttribute("data-built", String(r.built));
    if (r.built) {
      await expect(opt).toBeEnabled();
      expect(plans.shapes).toContain(r.shape);
    } else {
      await expect(opt).toBeDisabled();
      if (r.reason) await expect(page.getByTestId(`shape-reason-${r.shape}`)).toContainText(r.reason);
    }
  }
});

test("a request for a shape that is not built is refused, and the page shows the gateway's refusal", async ({ page }) => {
  const plans = await openData(page);
  test.skip(!plans.available, "no materialiser attached");
  const notBuilt = plans.plans.find((p) => !p.receipt && p.shape === "graph_tensor" && !p.built);
  test.skip(!notBuilt, "graph_tensor is built on this gateway — no not-built shape to ask for");

  const job = await runRequest(page, "a graph tensor of the nodes and edges for a GNN");
  expect(job.status, "an unbuilt shape (or an empty brain) must be refused, never built").toBe("refused");
  expect(job.refusal?.code).toBeTruthy();

  const turn = page.getByTestId("agent-turn").last();
  await expect(turn).toHaveAttribute("data-phase", "refused");
  const refusal = turn.getByTestId("agent-refusal");
  await expect(refusal).toHaveAttribute("data-code", job.refusal!.code);
  await expect(refusal.getByTestId("agent-refusal-message")).toContainText(job.refusal!.message.slice(0, 60));
  await expect(turn.getByTestId("receipt-card")).toHaveCount(0);
});

test("the fraud-style example ends as the job ended: a receipt that matches the API, or its refusal", async ({ page }) => {
  test.skip(process.env.E2E_DATA_RUN !== "1", "set E2E_DATA_RUN=1 to build a dataset (writes under var/materialise)");
  const plans = await openData(page);
  test.skip(!plans.available, "no materialiser attached");

  await page.getByTestId("agent-example").first().click();
  const text = await page.getByTestId("agent-input").inputValue();
  expect(text.trim().length).toBeGreaterThan(0);
  const job = await runRequest(page, text);
  const turn = page.getByTestId("agent-turn").last();

  if (job.status === "succeeded") {
    const r = job.result!;
    await expect(turn).toHaveAttribute("data-phase", "succeeded");
    await expect(turn.getByTestId("receipt-plan-hash")).toHaveText(r.plan_hash);
    await expect(turn.getByTestId("receipt-content-hash")).toHaveText(r.content_hash);
    if (r.pointer_check) {
      // green only on the COMPLETE verdict (counts, failures, grounded cells), not the two flags (R-021 round 2)
      const full = r as unknown as RunSummary;
      await expect(turn.getByTestId("receipt-pointer-check")).toHaveAttribute(
        "data-ok",
        String(pointerVerdict(full.pointer_check, full.cells?.grounded).ok),
      );
    }
    await expect(turn.getByTestId("plan-summary")).toBeVisible();
  } else if (job.status === "refused") {
    await expect(turn.getByTestId("agent-refusal")).toHaveAttribute("data-code", job.refusal!.code);
  } else {
    await expect(turn.getByTestId("agent-failed")).toBeVisible();
  }
});

test("the converter round-trips your own records through POST /convert and previews exactly what came back", async ({ page }) => {
  await page.goto("/data#convert");
  // synthetic test input, typed by the test — the page itself ships no sample
  const records = [
    { id: "r1", amount: 12.5, note: "plain" },
    { id: "r2", amount: 7, note: "has, a comma" },
  ];
  await page.getByTestId("convert-source-format").selectOption("json");
  await page.getByTestId("convert-target-format").selectOption("csv");
  await page.getByTestId("convert-source").fill(JSON.stringify(records));
  await page.getByTestId("convert-identity").click();

  const respP = page.waitForResponse((r) => r.request().method() === "POST" && path(r) === "/convert");
  await page.getByTestId("convert-run").click();
  const body = await (await respP).json();

  if (body.refusal) {
    await expect(page.getByTestId("convert-error")).toContainText(body.refusal.code);
    return;
  }
  const csv = Buffer.from(body.result_b64, "base64").toString("utf-8").trim();
  expect(csv.length, "non-vacuous: /convert returned output").toBeGreaterThan(0);
  const header = csv.split(/\r?\n/)[0].split(",");
  const preview = page.getByTestId("convert-preview");
  await expect(preview.locator("thead th")).toHaveText(header);
  await expect(page.getByTestId("convert-quarantine")).toHaveAttribute("data-quarantined", String(body.quarantine.quarantined));
  const dataLines = csv.split(/\r?\n/).length - 1;
  await expect(preview.locator("tbody tr")).toHaveCount(Math.min(20, dataLines));
});

// =============================================================================================
// @unit — pure decision helpers (no gateway, no page). Codex first review f1, R-021 data page.
// Run alone with a config that has no webServer and `grep: /@unit/`.
// =============================================================================================
import {
  catalogueExclusions,
  convertFingerprint,
  datasetDownloadGate,
  decodeConvertResponse,
  fileDecision,
  fileRole,
  latestOnly,
  leakVerdict,
  planAttribution,
  pointerDisplay,
  validatedReceipt,
  payloadPreview,
  planIntegrity,
  pointerVerdict,
  quarantineThresholdText,
  receiptIntegrity,
  receiptNotes,
  reproducibility,
  type ReceiptState,
} from "../components/data/receipt-gate";
import type { PointerCheck, ReceiptDoc, RunSummary } from "../lib/data-api";

const H1 = "a".repeat(64);
const H2 = "b".repeat(64);

function pcOf(grounded: number, over: Partial<PointerCheck> = {}): PointerCheck {
  return { grounded_cells: grounded, pointers_resolved: grounded, values_match: grounded, all_resolved: true, all_match: true, failures: [], ...over };
}

function runOf(over: Partial<RunSummary> = {}): RunSummary {
  const grounded = 7;
  return {
    plan_hash: H1, content_hash: H2, shaped_by: "plan", reproducible: true, executed_at: "2026-10-01T00:00:00+00:00",
    shape: "tabular_supervised", entity: "invoice", request: null, run_by: "u",
    rows: { emitted: 3, rejected_by_policy: 0, rejected_by_maturity: 0, rejected_incomplete: 0 },
    cells: { grounded, unresolved: 0, policy_suppressed: 0, grounding_dropped: 0 },
    verdicts: null, unresolved: null, pointer_check: pcOf(grounded), produced_by: null, honest_summary: null, notes: [],
    files: ["receipt.json", "plan.yaml", `${H1}.parquet`], ...over,
  };
}

// the executor records a leak check on every receipt it issues (executor.py: Receipt(..., missingness_leak_check=leak))
const LEAK_PASS = { passed: true, max_class_delta_observed: 0, threshold: 0.02, detail: "d", worst_column: null };

function docOf(run: RunSummary, over: { receipt?: Record<string, unknown>; platform?: Record<string, unknown> } = {}): ReceiptDoc {
  return {
    receipt: {
      plan_hash: run.plan_hash, content_hash: run.content_hash, shaped_by: run.shaped_by, executed_at: run.executed_at,
      columns_fully_absent: [], columns_refused: [], missingness_leak_check: LEAK_PASS, shaping_directives: [], notes: [],
      output_uri: `${H1}.parquet`, croissant_uri: null, purpose: "p", policy_applied: "default",
      cells: run.cells, rows: run.rows, ...(over.receipt ?? {}),
    },
    platform: {
      shape: run.shape ?? "", entity: run.entity ?? "", request: null, planner: null, run_by: "u",
      produced_by: null, reproducible: true, honest_summary: "", system_as_of: "t", identifiers: "suppress",
      sources: [], pointer_check: run.pointer_check, unresolved: { cells: 0, columns_fully_absent: [] }, columns: [],
      ...(over.platform ?? {}),
    },
    files: run.files,
  } as unknown as ReceiptDoc;
}

const okState = (doc: ReceiptDoc): ReceiptState => ({ state: "ok", doc });

test.describe("@unit data page helpers", () => {
  test("Critical 1: dataset downloads fail closed unless a complete, passing pointer check agrees with the receipt", () => {
    const run = runOf();
    const good = datasetDownloadGate(run, okState(docOf(run)));
    expect(good.allowed, good.reason).toBe(true);

    const cases: [string, RunSummary][] = [
      ["pointer_check absent", runOf({ pointer_check: null })],
      ["not all resolved", runOf({ pointer_check: pcOf(7, { all_resolved: false, pointers_resolved: 6 }) })],
      ["not all match", runOf({ pointer_check: pcOf(7, { all_match: false, values_match: 6 }) })],
      ["flags true but counts short", runOf({ pointer_check: pcOf(7, { values_match: 6 }) })],
      ["failures listed", runOf({ pointer_check: pcOf(7, { failures: [{ row: 0, column: "c", pointer_kind: "k", resolves: true, matches: false }] }) })],
      ["grounded count disagrees with receipt", runOf({ pointer_check: pcOf(5) })],
      ["receipt cells missing", runOf({ cells: null })],
    ];
    for (const [why, r] of cases) {
      const g = datasetDownloadGate(r, okState(docOf(r)));
      expect(g.allowed, why).toBe(false);
      expect(g.reason.length, why).toBeGreaterThan(0);
    }
    // the receipt itself not loaded / refused / for another run: blocked
    expect(datasetDownloadGate(run, { state: "loading" }).allowed).toBe(false);
    expect(datasetDownloadGate(run, { state: "refused", refusal: { code: "HTTP_401", message: "x" } }).allowed).toBe(false);
    expect(datasetDownloadGate(run, okState(docOf(run, { receipt: { content_hash: "c".repeat(64) } }))).allowed).toBe(false);
    // the summary's check and the receipt's check disagree: blocked
    expect(
      datasetDownloadGate(run, okState(docOf(run, { platform: { pointer_check: pcOf(7, { all_match: false, values_match: 1 }) } }))).allowed,
    ).toBe(false);
  });

  test("Critical 1: evidence files stay downloadable, everything else is a dataset artefact (unknown role → dataset)", () => {
    const run = runOf();
    const doc = docOf(run);
    expect(fileRole("receipt.json", doc)).toBe("evidence");
    expect(fileRole("plan.yaml", doc)).toBe("evidence");
    expect(fileRole(`${H1}.parquet`, doc)).toBe("dataset");
    expect(fileRole("anything-else.bin", doc)).toBe("dataset");
    expect(fileRole("receipt.json", null)).toBe("evidence");
    // a gateway-declared role is used when present; an unknown declared role fails closed
    const withRoles = { ...doc, file_roles: { "receipt.json": "evidence", "x.csv": "mystery" } } as unknown as ReceiptDoc;
    expect(fileRole("x.csv", withRoles)).toBe("dataset");
    expect(fileRole("receipt.json", withRoles)).toBe("evidence");
  });

  test("High 2: reproducible true / false / null are three different statements", () => {
    const t = reproducibility(true, "plan");
    const fPlan = reproducibility(false, "plan");
    const fAgent = reproducibility(false, "agent");
    const n = reproducibility(null, "agent");
    expect([t.kind, fPlan.kind, fAgent.kind, n.kind]).toEqual(["yes", "no", "no", "unknown"]);
    expect(n.text).toMatch(/not reported/i);
    expect(n.text).not.toMatch(/agent-shaped|not reproducible/i);
    expect(reproducibility(undefined, "plan").kind).toBe("unknown");
  });

  test("High 3: a receipt that is loading or refused holds dataset downloads and carries the gateway's own refusal", () => {
    const run = runOf();
    const loading = datasetDownloadGate(run, { state: "loading" });
    expect(loading.allowed).toBe(false);
    expect(loading.reason).toMatch(/waiting/i);
    const refusal = { code: "RECEIPT_NOT_FOUND", message: "no receipt for that plan_hash" };
    const refused = datasetDownloadGate(run, { state: "refused", refusal });
    expect(refused.allowed).toBe(false);
    expect(refused.reason).toContain(refusal.code);
    expect(refused.reason).toContain(refusal.message);
  });

  test("High 5: a receipt or plan.yaml for another run is an integrity error, not attributed to this run", () => {
    const run = runOf();
    expect(receiptIntegrity(run, docOf(run))).toEqual([]);
    expect(receiptIntegrity(run, docOf(run, { receipt: { plan_hash: H2 } })).length).toBeGreaterThan(0);
    expect(receiptIntegrity(run, docOf(run, { receipt: { content_hash: H1 } })).length).toBeGreaterThan(0);
    const doc = docOf(run);
    expect(planIntegrity(run, doc, { planHash: H1, shape: run.shape, entity: run.entity })).toEqual([]);
    expect(planIntegrity(run, doc, { planHash: H2, shape: run.shape, entity: run.entity }).length).toBeGreaterThan(0);
    expect(planIntegrity(run, doc, { planHash: null, shape: run.shape, entity: run.entity }).length).toBeGreaterThan(0);
    expect(planIntegrity(run, doc, { planHash: H1, shape: "rag_corpus", entity: run.entity }).length).toBeGreaterThan(0);
    expect(planIntegrity(run, doc, { planHash: H1, shape: run.shape, entity: "other" }).length).toBeGreaterThan(0);
  });

  test("High 4: receipt notes join the run's notes, labelled by origin, none dropped", () => {
    const run = runOf({ notes: ["a", "b"] });
    const doc = docOf(run, { receipt: { notes: ["b", "c"] } });
    const notes = receiptNotes(run, doc);
    expect(notes.map((n) => n.text).sort()).toEqual(["a", "b", "c"]);
    expect(notes.find((n) => n.text === "c")!.origin).toBe("receipt");
    expect(receiptNotes(runOf({ notes: null }), null)).toEqual([]);
  });

  test("Medium 6: the threshold is the gateway's number, or explicitly not reported", () => {
    const without = quarantineThresholdText({ quarantined: 1, by_reason: {}, rate: 0.5, over_threshold: true, records: [] });
    expect(without).not.toMatch(/\d+%/);
    expect(without).toMatch(/not report/i);
    const withT = quarantineThresholdText({ quarantined: 1, by_reason: {}, rate: 0.5, over_threshold: false, records: [], threshold: 0.05 });
    expect(withT).toContain("5%");
    expect(withT).toMatch(/under/);
  });

  test("Medium 7: any change to source, formats or spec changes the input fingerprint", () => {
    const base = { source: "[1]", sourceFormat: "json", targetFormat: "csv", spec: "{}" };
    const fp = convertFingerprint(base);
    expect(convertFingerprint({ ...base })).toBe(fp);
    for (const k of Object.keys(base) as (keyof typeof base)[]) {
      expect(convertFingerprint({ ...base, [k]: base[k] + "x" }), k).not.toBe(fp);
    }
  });

  test("Medium 8: every catalogue exclusion is listed with its reason; an unknown shape is shown whole", () => {
    const rows = catalogueExclusions([
      { entity: "facts.x", origin: "facts", reason: "unreadable" },
      { entity: "k", origin: "graph", field: "e_id", reason: "many-valued" },
      { odd: 1 },
    ]);
    expect(rows.length).toBe(3);
    expect(rows[0]).toMatchObject({ what: "facts.x", origin: "facts", reason: "unreadable" });
    expect(rows[1].what).toBe("k.e_id");
    expect(rows[2].reason).toContain('"odd"');
  });

  test("Low 9: a long quarantined payload is marked as truncated and the full text kept", () => {
    const small = payloadPreview({ a: 1 }, 80);
    expect(small.truncated).toBe(false);
    expect(small.text).toBe(small.full);
    const big = payloadPreview({ a: "x".repeat(200) }, 80);
    expect(big.truncated).toBe(true);
    expect(big.full.length).toBeGreaterThan(80);
    expect(big.text).toMatch(/…/);
  });

  // ------------------------------------------------------------------------------------------
  // Round 2 (Codex f2) — FXD2
  // ------------------------------------------------------------------------------------------

  test("R2 Critical 1: nothing from an invalid receipt is trusted — its file_roles cannot unlock a dataset", () => {
    const run = runOf({ files: ["receipt.json", "plan.yaml", "dataset.csv"] });
    const foreign = docOf(run, { receipt: { plan_hash: H2 } }) as ReceiptDoc & { file_roles?: Record<string, string> };
    foreign.file_roles = { "dataset.csv": "evidence" };
    const state = okState(foreign);
    expect(validatedReceipt(run, state)).toBeNull();
    const d = fileDecision("dataset.csv", run, state);
    expect(d.role).toBe("dataset");
    expect(d.blocked, "an invalid receipt's role must not unblock a dataset").not.toBeNull();
    // the fixed evidence allowlist still downloads
    for (const f of ["receipt.json", "plan.yaml"]) expect(fileDecision(f, run, state).blocked, f).toBeNull();

    // a VALID receipt: a file the run lists but the receipt does not is withheld, even evidence
    const valid = docOf(run) as ReceiptDoc & { file_roles?: Record<string, string> };
    valid.files = ["receipt.json", "dataset.csv"];
    expect(fileDecision("plan.yaml", run, okState(valid)).blocked).not.toBeNull();
    expect(fileDecision("dataset.csv", run, okState(valid)).blocked).toBeNull();
    // a valid receipt's own role is honoured, and an unknown role fails closed
    const extra = runOf({ files: [...run.files, "notes.md"], pointer_check: null });
    const vRoles = docOf(extra) as ReceiptDoc & { file_roles?: Record<string, string> };
    vRoles.file_roles = { "notes.md": "evidence", "dataset.csv": "mystery" };
    expect(fileDecision("notes.md", extra, okState(vRoles)).blocked).toBeNull();
    expect(fileDecision("dataset.csv", extra, okState(vRoles)).role).toBe("dataset");
    expect(fileDecision("dataset.csv", extra, okState(vRoles)).blocked).not.toBeNull();
    // the gateway's own vocabulary (receipt | plan | dataset | metadata | other): receipt and plan are
    // evidence; metadata and other are gated like a dataset, as the gateway's file_withheld gates them
    const gw = docOf(extra) as ReceiptDoc & { file_roles?: Record<string, string> };
    gw.files = [...extra.files, "croissant.json"];
    gw.file_roles = { "receipt.json": "receipt", "plan.yaml": "plan", "dataset.csv": "dataset", "croissant.json": "metadata", "notes.md": "other" };
    const withMeta = { ...extra, files: gw.files };
    expect(fileDecision("receipt.json", withMeta, okState(gw)).blocked).toBeNull();
    expect(fileDecision("plan.yaml", withMeta, okState(gw)).blocked).toBeNull();
    for (const f of ["dataset.csv", "croissant.json", "notes.md"]) expect(fileDecision(f, withMeta, okState(gw)).blocked, f).not.toBeNull();
  });

  test("R2 Critical 2: a failed, absent or inconsistent leakage check blocks dataset downloads", () => {
    const run = runOf();
    expect(datasetDownloadGate(run, okState(docOf(run))).allowed).toBe(true);
    const cases: [string, unknown][] = [
      ["failed", { ...LEAK_PASS, passed: false, max_class_delta_observed: 0.5 }],
      ["absent", null],
      ["passed but over threshold", { ...LEAK_PASS, passed: true, max_class_delta_observed: 0.5 }],
      ["passed not a boolean", { ...LEAK_PASS, passed: "yes" }],
    ];
    for (const [why, leak] of cases) {
      const doc = docOf(run, { receipt: { missingness_leak_check: leak } });
      const g = datasetDownloadGate(run, okState(doc));
      expect(g.allowed, why).toBe(false);
      expect(g.reason, why).toMatch(/leak/i);
      expect(leakVerdict(doc).ok, why).toBe(false);
    }
    expect(leakVerdict(docOf(run)).ok).toBe(true);
    expect(leakVerdict(null).state).toBe("unverified");
  });

  test("R2 High 3: green only when the whole pointer verdict passes and the summary and receipt agree", () => {
    const run = runOf();
    expect(pointerDisplay(run, okState(docOf(run))).state).toBe("pass");
    // flags true, counts short
    const short = runOf({
      pointer_check: pcOf(10, { pointers_resolved: 9 }),
      cells: { grounded: 10, unresolved: 0, policy_suppressed: 0, grounding_dropped: 0 },
    });
    expect(pointerDisplay(short, okState(docOf(short))).state).toBe("fail");
    // flags true, failures listed
    const fl = runOf({ pointer_check: pcOf(7, { failures: [{ row: 1, column: "c", pointer_kind: "k", resolves: false, matches: false }] }) });
    expect(pointerDisplay(fl, okState(docOf(fl))).state).toBe("fail");
    // summary says pass, receipt says fail → disagreement shown, not the summary's pass
    const dis = pointerDisplay(run, okState(docOf(run, { platform: { pointer_check: pcOf(7, { all_match: false, values_match: 3 }) } })));
    expect(dis.state).toBe("disagree");
    // one side has a check and the other has none → disagreement, never a pass
    expect(pointerDisplay(run, okState(docOf(run, { platform: { pointer_check: null } }))).state).toBe("disagree");
    expect(datasetDownloadGate(run, okState(docOf(run, { platform: { pointer_check: null } }))).allowed).toBe(false);
    // receipt not loaded / invalid → the summary's pass is "unverified", never green
    expect(pointerDisplay(run, { state: "loading" }).state).toBe("unverified");
    expect(pointerDisplay(run, okState(docOf(run, { receipt: { plan_hash: H2 } }))).state).toBe("unverified");
    expect(pointerDisplay(runOf({ pointer_check: null }), { state: "loading" }).state).toBe("absent");
    // a summary that already fails is shown as failing even before the receipt arrives
    expect(pointerDisplay(short, { state: "loading" }).state).toBe("fail");
  });

  test("R2 High 4: a plan is 'the plan that ran' only when verified against a valid receipt", () => {
    const run = runOf({ shape: "tabular", entity: "invoice" });
    const plan = { planHash: H1, shape: "chunk_corpus", entity: "invoice" };
    // Codex's scenario: an invalid receipt whose shape is chunk_corpus must not vouch for a chunk_corpus plan
    const foreign = docOf(run, { receipt: { plan_hash: H2 }, platform: { shape: "chunk_corpus" } });
    const a = planAttribution(run, okState(foreign), plan);
    expect(a.status).toBe("mismatch");
    expect(a.problems.join(" ")).toMatch(/tabular/);
    // a plan that matches the run but has no valid receipt behind it is unverified
    const matching = { planHash: H1, shape: "tabular", entity: "invoice" };
    expect(planAttribution(run, okState(foreign), matching).status).toBe("unverified");
    expect(planAttribution(run, { state: "loading" }, matching).status).toBe("unverified");
    expect(planAttribution(run, { state: "refused", refusal: { code: "X", message: "y" } }, matching).status).toBe("unverified");
    expect(planAttribution(run, okState(docOf(run)), matching).status).toBe("verified");
    // the run and a valid receipt are each compared independently: a receipt that disagrees with the run fails
    const odd = docOf(run, { platform: { shape: "chunk_corpus" } });
    expect(planAttribution(run, okState(odd), matching).status).toBe("mismatch");
  });

  test("R2 Medium 5: only the latest file read can win", async () => {
    const gate = latestOnly();
    const applied: string[] = [];
    const read = async (name: string, ms: number) => {
      const t = gate.begin();
      await new Promise((r) => setTimeout(r, ms));
      if (gate.current(t)) applied.push(name);
    };
    await Promise.all([read("big.json", 60), read("small.csv", 5)]);
    expect(applied).toEqual(["small.csv"]);
    // typing into the source cancels a pending read too (it begins a newer generation)
    const t = gate.begin();
    gate.begin();
    expect(gate.current(t)).toBe(false);
  });

  test("R2 Medium 6: malformed converter output is an error, not a crash", () => {
    const q = { quarantined: 0, by_reason: {}, rate: 0, over_threshold: false, records: [] };
    const good = decodeConvertResponse({ result_b64: Buffer.from("a,b\n1,2\n", "utf-8").toString("base64"), quarantine: q, artifact_id: "x" });
    expect(good.ok).toBe(true);
    if (good.ok) expect(good.text).toBe("a,b\n1,2\n");
    const bad: [string, unknown][] = [
      ["invalid base64", { result_b64: "%%%not base64%%%", quarantine: q, artifact_id: "x" }],
      ["not utf-8", { result_b64: Buffer.from([0xff, 0xfe, 0xfd]).toString("base64"), quarantine: q, artifact_id: "x" }],
      ["no quarantine", { result_b64: "", artifact_id: "x" }],
      ["quarantine records not a list", { result_b64: "", quarantine: { ...q, records: null }, artifact_id: "x" }],
      ["quarantine rate not a number", { result_b64: "", quarantine: { ...q, rate: "0" }, artifact_id: "x" }],
      ["record without a reason_code", { result_b64: "", quarantine: { ...q, quarantined: 1, records: [{ detail: "d", payload: {} }] }, artifact_id: "x" }],
      ["refusal", { refusal: { code: "C", message: "m" } }],
      ["not an object", null],
    ];
    for (const [why, body] of bad) {
      const r = decodeConvertResponse(body);
      expect(r.ok, why).toBe(false);
      if (!r.ok) expect(r.error.length, why).toBeGreaterThan(0);
    }
  });
});
