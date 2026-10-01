import { test, expect, type Page, type Response } from "@playwright/test";

/**
 * `/connectors` — Sources: connect an MCP server, approve its plan, run it, watch it.
 *
 * Runs against the REAL gateway. Every expectation is derived from the API response the page itself
 * received (captured with `waitForResponse`), never from a hard-coded count — the registered sources
 * differ per deployment and grow over time.
 *
 * Non-mutating by default. The one mutating test (start a run and watch it to the end) runs only
 * when `E2E_SOURCES_RUN=<source id>` names a source whose plan is approved.
 *
 * Needs a build with NEXT_PUBLIC_WALNUT_TOKEN set (the launcher does this): every /sources route
 * requires an authenticated principal.
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: new Date().toISOString() }),
    );
  });
});

// Source and run ids are [A-Za-z0-9_.-] (the gateway's _ID_RE); only "." is special in a RegExp.
const esc = (s: string) => s.replace(/\./g, "\\.");
const isGet = (path: RegExp) => (r: Response) => r.request().method() === "GET" && path.test(new URL(r.url()).pathname);

async function openSources(page: Page, url = "/connectors") {
  const listP = page.waitForResponse(isGet(/^\/sources$/));
  await page.goto(url);
  const res = await listP;
  expect(res.status(), "GET /sources must succeed for these tests (token set?)").toBe(200);
  return (await res.json()) as {
    sources: { id: string; title: string; via: string; plan_status: string | null; last_run: unknown; running: unknown }[];
    problems: string[];
  };
}

test("the sidebar's Sources entry opens the sources page, not a redirect", async ({ page }) => {
  await page.goto("/graph");
  await page.getByRole("link", { name: "Sources" }).click();
  await expect(page).toHaveURL(/\/connectors/);
  await expect(page.getByTestId("sources-list")).toBeVisible();
});

test("the list shows exactly the sources the gateway returned, each with its plan status", async ({ page }) => {
  const body = await openSources(page);
  if (body.sources.length === 0) {
    await expect(page.getByTestId("sources-empty")).toBeVisible();
    return;
  }
  await expect(page.locator('[data-testid^="source-row-"]')).toHaveCount(body.sources.length);
  for (const s of body.sources) {
    await expect(page.getByTestId(`source-row-${s.id}`)).toContainText(s.title);
    await expect(page.getByTestId(`source-plan-${s.id}`)).toHaveAttribute("data-status", s.plan_status ?? "none");
  }
});

test("a source's detail shows its tools, plan and run history as the API reports them", async ({ page }) => {
  const body = await openSources(page);
  test.skip(body.sources.length === 0, "no source is registered on this gateway");
  const id = body.sources[0].id;

  const detailP = page.waitForResponse(isGet(new RegExp(`^/sources/${esc(id)}$`)));
  const runsP = page.waitForResponse(isGet(new RegExp(`^/sources/${esc(id)}/runs$`)));
  await page.getByTestId(`source-row-${id}`).click();
  const detail = await (await detailP).json();
  const runs = await (await runsP).json();

  await expect(page.getByTestId("source-detail")).toHaveAttribute("data-source-id", id);

  // tools: one row per tool the gateway listed (or the honest "not listed yet" note)
  if (detail.tools.length > 0) {
    await expect(page.getByTestId("tools-list").locator("li")).toHaveCount(detail.tools.length);
  } else {
    await expect(page.getByTestId("tools-empty")).toBeVisible();
  }

  // plan: status badge = the plan's status; Run is only enabled for an approved plan
  if (detail.plan) {
    await expect(page.getByTestId("plan-status")).toHaveAttribute("data-status", detail.plan.status);
    await expect(page.getByTestId("plan-steps").or(page.getByTestId("plan-no-steps"))).toBeVisible();
    if (detail.plan.problems.length > 0) await expect(page.getByTestId("plan-problems")).toBeVisible();
  }
  // Run is offered only for a plan approved BY A PERSON (an auto_approve marker does not count).
  const approved =
    detail.plan?.status === "approved" && !!detail.plan.approved_by && !/^auto_approve/i.test(detail.plan.approved_by);
  if (!approved) {
    await expect(page.getByTestId("run-start")).toBeDisabled();
    await expect(page.getByTestId("run-needs-approval")).toBeVisible();
  }

  // history: one row per run on disk plus any in flight
  const expected = runs.runs.length + runs.in_flight.length;
  if (expected === 0) {
    await expect(page.getByTestId("history-empty")).toBeVisible();
  } else {
    await expect(page.locator('[data-testid^="history-run-"]')).toHaveCount(expected);
  }
});

test("opening a finished run shows its stages and counts from the run report", async ({ page }) => {
  const body = await openSources(page);
  const withRun = body.sources.find((s) => s.last_run && !s.running);
  test.skip(!withRun, "no source on this gateway has a finished run");
  const id = withRun!.id;

  const runsP = page.waitForResponse(isGet(new RegExp(`^/sources/${esc(id)}/runs$`)));
  await page.getByTestId(`source-row-${id}`).click();
  const runs = await (await runsP).json();
  const last = runs.runs[runs.runs.length - 1];

  const runP = page.waitForResponse(isGet(new RegExp(`^/sources/${esc(id)}/runs/${esc(last.run_id)}$`)));
  const ledgerP = page.waitForResponse(isGet(new RegExp(`^/evidence/sources/${esc(id)}$`)));
  await page.getByTestId(`history-run-${last.run_id}`).click();
  const run = await (await runP).json();

  const view = page.getByTestId("run-view");
  await expect(view).toHaveAttribute("data-run-id", last.run_id);
  await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", run.status ?? "none");

  // every stage stages.json records as finished (done_at) is marked done
  for (const [stage, rec] of Object.entries((run.stages ?? {}) as Record<string, { done_at?: string }>)) {
    if (rec.done_at) await expect(page.getByTestId(`run-stage-${stage}`)).toHaveAttribute("data-state", "done");
  }
  // counts: the latest verification's when it has any, else the report's
  const vc = run.verify?.counts;
  const counts = vc && Object.keys(vc).length ? vc : run.report?.counts;
  if (counts && typeof counts.inventory === "number") {
    await expect(page.getByTestId("run-count-inventory")).toHaveText(counts.inventory.toLocaleString());
  }
  if (counts?.governance) {
    for (const [decision, n] of Object.entries(counts.governance as Record<string, number>)) {
      await expect(page.getByTestId(`run-screen-${decision}`)).toContainText(String(n));
    }
  }
  // exclusions: one row per reason the ledgers record, or the explicit "nothing excluded"
  const ledgerRes = await ledgerP;
  if (ledgerRes.ok()) {
    const ledgers = await ledgerRes.json();
    const summary = ledgers.runs.find((r: { run_id: string }) => r.run_id === last.run_id);
    if (summary && summary.exclusion_reasons.length > 0) {
      await expect(page.getByTestId("run-exclusion-row")).toHaveCount(summary.exclusion_reasons.length);
    } else if (summary && !summary.truncated && !(summary.excluded > 0)) {
      await expect(page.getByTestId("run-exclusions-none")).toBeVisible();
    } else if (summary) {
      await expect(page.getByTestId("run-exclusions-none")).toHaveCount(0);
    }
  }
  if (run.status !== "failed") {
    await expect(page.getByTestId("run-graph-link")).toHaveAttribute("href", `/graph?source=${encodeURIComponent(id)}`);
  }
});

test("a command source warns that it runs a program, and asks for names, never secret values", async ({ page }) => {
  await openSources(page);
  await page.getByTestId("add-source").click();
  await page.getByTestId("add-mode-command").click();
  await expect(page.getByTestId("command-warning")).toContainText("runs a program");
  await page.getByTestId("add-id").fill("e2e_never_registered");
  await page.getByTestId("add-command").fill("npx");
  // not submittable until the operator acknowledges the program will run on the host
  await expect(page.getByTestId("add-submit")).toBeDisabled();
  await page.getByTestId("add-ack").check();
  await expect(page.getByTestId("add-submit")).toBeEnabled();
  // environment is name → host variable name; a value-looking entry is refused client-side
  await page.getByTestId("add-env-from").getByRole("button", { name: "Add" }).click();
  await page.getByTestId("add-env-from").getByPlaceholder("SERVER_VAR_NAME").fill("API_KEY");
  await page.getByTestId("add-env-from").getByPlaceholder("HOST_VAR_NAME").fill("sk-live 123/abc");
  await expect(page.getByTestId("add-submit")).toBeDisabled();
  await expect(page.getByTestId("add-problems")).toContainText("variable NAMES");
});

test("registering over an existing id is refused in plain English", async ({ page }) => {
  const body = await openSources(page);
  test.skip(body.sources.length === 0, "no source is registered on this gateway");
  await page.getByTestId("add-source").click();
  // Registered via MCPJungle, NOT via a URL. E2E run 4 (Q-10) typed `http://127.0.0.1:9/mcp`.
  // The gateway's registration policy runs BEFORE the existence check and refuses a loopback
  // destination (403 SOURCE_DESTINATION_NOT_ALLOWED) unless the operator lists the host in
  // WALNUT_SOURCE_ALLOWED_HOSTS, so the test never reached the duplicate id. The MCPJungle route
  // sends no URL, and the aggregator's own default address is exempt from that policy
  // (`registration_refusal`). This body therefore reaches the existence check under every
  // deployment's destination policy. The ordering, policy before existence, is correct and is
  // not what this test is about.
  await page.getByTestId("add-mode-mcpjungle").click();
  await page.getByTestId("add-id").fill(body.sources[0].id);
  // A name the connection model accepts whatever the existing id looks like (`__` is reserved).
  await page.getByTestId("add-jungle").fill("e2e_never_registered");
  const regP = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/sources");
  await page.getByTestId("add-submit").click();
  const reg = await regP;
  // the gateway refuses (409 SOURCE_EXISTS); nothing is written
  expect(reg.status()).toBe(409);
  const refusal = (await reg.json()).refusal;
  await expect(page.getByTestId("add-failure")).toHaveAttribute("data-code", refusal.code);
  await expect(page.getByTestId("add-failure")).toContainText("already registered");
});

test("a 401 from the gateway is explained, not rendered as an empty list", async ({ page }) => {
  await page.route(/\/sources$/, (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not authenticated" }) }),
  );
  await page.goto("/connectors");
  await expect(page.getByTestId("sources-list-failure")).toHaveAttribute("data-status", "401");
  await expect(page.getByTestId("sources-list-failure")).toContainText("credentials");
  await expect(page.getByTestId("sources-empty")).toHaveCount(0);
});

test("starting a run streams real progress to the end (mutating; opt-in)", async ({ page }) => {
  const id = process.env.E2E_SOURCES_RUN;
  test.skip(!id, "set E2E_SOURCES_RUN=<source id with an approved plan> to run");
  test.setTimeout(15 * 60_000);
  await openSources(page, `/connectors?source=${encodeURIComponent(id!)}`);
  const startP = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === `/sources/${id}/runs`);
  await page.getByTestId("run-start").click();
  const start = await startP;
  expect(start.status()).toBe(202);
  const { run_id } = await start.json();

  await expect(page.getByTestId("run-view")).toHaveAttribute("data-run-id", run_id);
  // the page polls until the gateway says the run is no longer running
  await expect(page.getByTestId("run-view")).not.toHaveAttribute("data-status", "running", { timeout: 14 * 60_000 });

  const final = await page.request.get(`${process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000"}/sources/${id}/runs/${run_id}`, {
    headers: process.env.NEXT_PUBLIC_WALNUT_TOKEN ? { Authorization: `Bearer ${process.env.NEXT_PUBLIC_WALNUT_TOKEN}` } : {},
  });
  if (final.ok()) {
    const run = await final.json();
    await expect(page.getByTestId("run-status")).toHaveAttribute("data-status", run.status);
    for (const stage of Object.keys(run.stages ?? {})) {
      await expect(page.getByTestId(`run-stage-${stage}`)).toHaveAttribute("data-state", "done");
    }
  }
});

// =============================================================================================
// @unit — pure decision helpers (no gateway, no page). Codex batch 2, R-019 ui-builder-sources-graph.
// =============================================================================================
import {
  crawlAllowed,
  exclusionsVerdict,
  humanApproved,
  isTerminalRunStatus,
  pickCounts,
  planToolAudit,
  redactArgs,
  redactUrl,
  secretProblems,
  stageCompleted,
  type CatalogueConnection,
  type CrawlPlan,
  type RunDetail,
} from "../lib/sources-api";

test.describe("@unit sources helpers", () => {
  test("a literal secret in the URL, command or arguments is refused before it is sent", () => {
    const bad = [
      { url: "https://user:hunter2@mcp.example.test/mcp" },
      { url: "https://mcp.example.test/mcp?token=abc123" },
      { url: "https://mcp.example.test/mcp?x=1&API_KEY=abc" },
      { args: ["--api-key", "abc123"] },
      { args: ["--token=abc123"] },
      { args: ["PASSWORD=abc123"] },
      { args: ["sk-live-0123456789abcdefghij"] },
      { command: "ghp_0123456789abcdefghijABCDEFGHIJ012345" },
    ];
    for (const b of bad) expect(secretProblems(b).length, JSON.stringify(b)).toBeGreaterThan(0);
    const good = [
      { url: "https://mcp.example.test/mcp?page=2" },
      { args: ["-y", "@modelcontextprotocol/server-filesystem", "C:/data"] },
      { command: "npx", args: ["--port", "8080"] },
    ];
    for (const g of good) expect(secretProblems(g), JSON.stringify(g)).toEqual([]);
  });

  test("a stored URL or argument list is redacted before display", () => {
    const u = redactUrl("https://user:hunter2@mcp.example.test/mcp?token=abc123&page=2");
    expect(u).not.toContain("hunter2");
    expect(u).not.toContain("abc123");
    expect(u).toContain("page=2");
    const a = redactArgs(["--api-key", "abc123", "--token=xyz", "-y"]);
    expect(a.join(" ")).not.toContain("abc123");
    expect(a.join(" ")).not.toContain("xyz");
    expect(a).toContain("-y");
  });

  test("an auto-approved plan is not a human approval", () => {
    const plan = { status: "approved", approved_by: "auto_approve (connection flag)" } as CrawlPlan;
    expect(humanApproved(plan)).toBe(false);
    expect(humanApproved({ ...plan, approved_by: "PS-FD-014" })).toBe(true);
    expect(humanApproved({ ...plan, approved_by: null })).toBe(false);
    expect(humanApproved({ ...plan, status: "proposed", approved_by: "PS-FD-014" })).toBe(false);
    expect(humanApproved(null)).toBe(false);
  });

  test("only an exact ingest purpose may crawl", () => {
    expect(crawlAllowed("ingest")).toBe(true);
    for (const p of ["agent_write", "write", "admin_write", "", null, undefined]) expect(crawlAllowed(p)).toBe(false);
  });

  test("a plan that uses a write-classified tool is flagged; unclassified tools are reported", () => {
    const plan = {
      steps: [{ id: "s", strategy: "documents", list: { tool: "list_docs" }, read: { text_tool: "delete_document" } }],
    } as unknown as CrawlPlan;
    const cat = {
      tools: [
        { name: "list_docs", write: false, basis: "name" },
        { name: "delete_document", write: true, basis: "name" },
      ],
    } as CatalogueConnection;
    const audit = planToolAudit(plan, cat);
    expect(audit.writes).toEqual(["delete_document"]);
    expect(audit.unclassified).toEqual([]);
    const none = planToolAudit(plan, null);
    expect(none.writes).toEqual([]);
    expect([...none.unclassified].sort()).toEqual([...audit.used].sort());
  });

  test("a run is finished only on a terminal status; unknown and null keep polling", () => {
    for (const s of ["ok", "empty", "failed", "unverified", "incomplete"]) expect(isTerminalRunStatus(s)).toBe(true);
    for (const s of ["running", "queued", "starting", null, undefined]) expect(isTerminalRunStatus(s)).toBe(false);
  });

  test("a stage is complete only with done_at", () => {
    expect(stageCompleted({ done_at: "2026-10-01T00:00:00Z", seconds: 1 })).toBe(true);
    expect(stageCompleted({ breakdown: { failed: 3 } })).toBe(false);
    expect(stageCompleted(undefined)).toBe(false);
  });

  test("a truncated ledger never yields an exhaustive 'nothing excluded'", () => {
    expect(exclusionsVerdict({ exclusion_reasons: [], truncated: false })).toBe("none");
    expect(exclusionsVerdict({ exclusion_reasons: [], truncated: true })).toBe("none-in-examined");
    expect(exclusionsVerdict({ exclusion_reasons: [{ reason: "x", n: 1 }], truncated: true })).toBe("some");
  });

  test("the latest verification counts win over the report's snapshot", () => {
    const run = {
      report: { counts: { inventory: 5 } },
      verify: { counts: { inventory: 7 }, checked_at: "t" },
    } as unknown as RunDetail;
    expect(pickCounts(run)).toEqual({ counts: { inventory: 7 }, from: "verify" });
    expect(pickCounts({ ...run, verify: null } as RunDetail).from).toBe("report");
    expect(pickCounts({ ...run, verify: null, report: null } as RunDetail)).toEqual({ counts: null, from: null });
  });
});

// =============================================================================================
// @unit — Codex round 3 (R-019 "Round 3"), FX8b.
// =============================================================================================
import {
  mappingGate,
  planApprovalBlockers,
  redactCommand,
  registrationProblems,
  stageOrder,
} from "../lib/sources-api";

test.describe("@unit sources helpers (round 3)", () => {
  test("R3-S1 secrets are refused in every mode, in a command line, and redacted everywhere", () => {
    // import mode: the server-id list is free text too
    expect(registrationProblems({ mode: "import", only: "sk-1234567890abcdef" }).length).toBeGreaterThan(0);
    expect(registrationProblems({ mode: "import", only: "gdrive, bad id!" }).length).toBeGreaterThan(0);
    expect(registrationProblems({ mode: "import", only: "gdrive, crm_db" })).toEqual([]);
    // a credential flag embedded in the command field
    for (const command of ["runner --password=hunter2", "runner --password hunter2", "runner PASSWORD=hunter2"]) {
      expect(registrationProblems({ mode: "command", command }).length, command).toBeGreaterThan(0);
    }
    expect(registrationProblems({ mode: "command", command: "npx" })).toEqual([]);
    // every occurrence is redacted, not just the first
    const two = "sk-AAAAAAAAAAAAAAAAAAAA and sk-BBBBBBBBBBBBBBBBBBBB";
    expect(redactArgs([two]).join(" ")).not.toMatch(/AAAA|BBBB/);
    expect(redactUrl(`https://mcp.example.test/sk-AAAAAAAAAAAAAAAAAAAA/sk-BBBBBBBBBBBBBBBBBBBB`)).not.toMatch(/AAAA|BBBB/);
    expect(redactUrl("not a url sk-AAAAAAAAAAAAAAAAAAAA sk-BBBBBBBBBBBBBBBBBBBB")).not.toMatch(/AAAA|BBBB/);
    // the stored command is redacted before display
    const shown = redactCommand("runner --password=hunter2 --token abc123 ghp_0123456789abcdefghijABCDEFGHIJ012345");
    expect(shown).not.toMatch(/hunter2|abc123|ghp_0123/);
    expect(shown.startsWith("runner")).toBe(true);
  });

  test("R3-S2 a machine-approved table mapping gates the run until a person re-approves it", () => {
    const tables = { customers: { status: "approved", origin: "rules", node: null, edges: [], rationale: [] } };
    const auto = mappingGate({ source_id: "s", tables, updated_at: "", approved_by: "auto_approve (connection flag)" } as never);
    expect(auto.needsHuman).toBe(true);
    const human = mappingGate({ source_id: "s", tables, updated_at: "", approved_by: "PS-FD-014" } as never);
    expect(human.needsHuman).toBe(false);
    const unattributed = mappingGate({ source_id: "s", tables, updated_at: "", approved_by: null } as never);
    expect(unattributed.needsHuman).toBe(true);
    // nothing approved means nothing will be built from the mapping
    const proposedOnly = mappingGate({
      source_id: "s", tables: { t: { ...tables.customers, status: "proposed" } }, updated_at: "", approved_by: null,
    } as never);
    expect(proposedOnly.needsHuman).toBe(false);
    expect(mappingGate(null).needsHuman).toBe(false);
  });

  test("R3-S3 a plan with an unclassified tool, or with no catalogue, cannot be approved", () => {
    const plan = { problems: [], steps: [{ id: "s", strategy: "documents", list: { tool: "list_docs" }, read: { text_tool: "delete_records" } }] } as unknown as CrawlPlan;
    const cat = { tools: [{ name: "list_docs", write: false, basis: "name" }] } as CatalogueConnection;
    expect(planApprovalBlockers(plan, cat, "ingest").join(" ")).toContain("delete_records");
    expect(planApprovalBlockers(plan, null, "ingest").length).toBeGreaterThan(0);
    const readOnly = { tools: [...cat.tools, { name: "delete_records", write: false, basis: "name" }] } as CatalogueConnection;
    expect(planApprovalBlockers(plan, readOnly, "ingest")).toEqual([]);
    expect(planApprovalBlockers(plan, readOnly, "agent_write").length).toBeGreaterThan(0);
    const written = { tools: [...cat.tools, { name: "delete_records", write: true, basis: "name" }] } as CatalogueConnection;
    expect(planApprovalBlockers(plan, written, "ingest").join(" ")).toContain("writing");
  });

  test("R3-S5 embedded report verification counts win over report counts", () => {
    const run = {
      verify: null,
      report: { counts: { built: 3 }, verify: { counts: { built: 5 }, checked_at: "t" } },
    } as unknown as RunDetail;
    expect(pickCounts(run)).toEqual({ counts: { built: 5 }, from: "report-verify" });
  });

  test("R3-S7 a stage named only by an event or the live progress still appears", () => {
    const known = ["collect", "extract"];
    const ids = stageOrder(known, {
      stages: { collect: { done_at: "t" } },
      events: [{ stage: "hydrate" }],
      progress: { stage: "embed" },
    });
    expect(ids).toEqual(["collect", "extract", "hydrate", "embed"]);
    expect(stageOrder(known, { stages: null, events: [], progress: null })).toEqual(known);
  });
});
