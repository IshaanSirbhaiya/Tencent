import { test, expect } from "@playwright/test";

// The 90-second investor demo flow (FRONTEND_PLAN_v2 §5), asserted end-to-end against the real
// gateway + frontend. Fully airgapped: no external origin is contacted (the gateway is loopback,
// the model surface is FakeBackend). Replay-first — no live model at any step (D-FE-4).

test("airgap: no external (non-loopback) request across ALL five panels", async ({ page }) => {
  // The zero-egress claim must hold for the WHOLE demo, not just the landing page (review L4):
  // visit every route AND trigger its primary action, asserting no non-loopback request fires.
  const external: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (!["127.0.0.1", "localhost"].includes(u.hostname)) external.push(r.url());
  });
  for (const route of ["/schema", "/convert", "/ask", "/evals", "/artifacts"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
  }
  // exercise the live actions that fetch (convert run, ask replay) — the riskiest egress points
  await page.goto("/convert");
  await page.getByRole("button", { name: /Run conversion/i }).click();
  await expect(page.getByText(/quarantine rate/i)).toBeVisible({ timeout: 15_000 });
  await page.goto("/ask");
  await page.getByRole("button", { name: /^Ask$/ }).click();
  await expect(page.getByText("generated SQL")).toBeVisible({ timeout: 15_000 });
  expect(external, `external requests: ${external.join(", ")}`).toHaveLength(0);
});

test("schema → confirm → finalize unlocks (P6 gate)", async ({ page }) => {
  await page.goto("/schema");
  // one mandatory review item blocks Finalize
  await expect(page.getByText(/item.* need review/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /Finalize schema/i })).toBeDisabled();
  // confirm the review item → queue clears → Finalize unlocks
  await page.getByRole("button", { name: /^Confirm$/ }).first().click();
  await expect(page.getByRole("button", { name: /Finalize schema/i })).toBeEnabled({ timeout: 10_000 });
  await page.getByRole("button", { name: /Finalize schema/i }).click();
  await expect(page.getByText(/confirmed by/i)).toBeVisible({ timeout: 10_000 });
});

test("convert runs and shows provenance + quarantine", async ({ page }) => {
  await page.goto("/convert");
  await page.getByRole("button", { name: /Run conversion/i }).click();
  await expect(page.getByText(/quarantine rate/i)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/fabrications/i)).toBeVisible();
  // converted cells carry the Grounded provenance affordance
  await expect(page.locator("[data-grounded]").first()).toBeVisible();
});

test("ask replays a known question with SQL-as-provenance", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("button", { name: /^Ask$/ }).click();
  await expect(page.getByText("generated SQL")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/SQL-as-provenance/i)).toBeVisible();
  await expect(page.getByText(/replayed/i)).toBeVisible();
});

test("ask injection attempt is refused (SQL_FORBIDDEN / multi-statement)", async ({ page }) => {
  await page.goto("/ask");
  await page.getByRole("button", { name: /Try injection attack/i }).click();
  await expect(page.getByText(/SQL_MULTI_STATEMENT|SQL_FORBIDDEN/)).toBeVisible({ timeout: 15_000 });
});

test("evals dashboard shows E1 accuracy + reliability", async ({ page }) => {
  await page.goto("/evals");
  await expect(page.getByText("E1 accuracy")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/reliability diagram/i)).toBeVisible();
});

test("brain: graph loads and shows entity nodes (T08)", async ({ page }) => {
  await page.goto("/brain");
  // the pre-seeded knowledge graph renders nodes
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Sarah Chen").first()).toBeVisible();
  // brain stats + provenance differentiator are present
  await expect(page.getByText("entities").first()).toBeVisible();
  await expect(page.getByText("provenance").first()).toBeVisible();
});

test("brain: ask returns a grounded answer with citations (T08)", async ({ page }) => {
  await page.goto("/brain");
  await page.getByRole("button", { name: "Who owns Project Atlas?" }).click();
  // the answer text (not just a node label) and the citation row appear
  await expect(page.getByText(/owned by Tom Walsh/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Cited:")).toBeVisible();
});

test("connectors: cards grid renders (12 connectors, demo mode)", async ({ page }) => {
  await page.goto("/connectors");
  await expect(page.getByRole("heading", { name: /Connect your data sources/i })).toBeVisible();
  await expect(page.getByText("Demo mode")).toBeVisible();
  // a connected card (unique live stat) + an available card's Connect button
  await expect(page.getByText("847 emails indexed")).toBeVisible();
  await expect(page.getByRole("button", { name: "Connect" }).first()).toBeVisible();
});

test("brain: drag-drop fixture ingests new entities (T08 Part 2)", async ({ page }) => {
  await page.goto("/brain");
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 15_000 });
  // quick-ingest the email fixture → toast reports the new entities (incl. Alice Wong).
  // exact name: React Flow renders edges as role=button, and email_*.eml edge labels contain "email".
  await page.getByRole("button", { name: "Email", exact: true }).click();
  await expect(page.getByText(/Found 2 new entities: Alice Wong/)).toBeVisible({ timeout: 15_000 });
});

test("brain: node click opens the Obsidian markdown note with clickable wikilinks (T08 Part 3)", async ({ page }) => {
  await page.goto("/brain");
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 15_000 });
  // click Sarah's node → her markdown note opens
  await page.locator('.react-flow__node[data-id="person:sarah@acmecorp.com"]').click();
  await expect(page.getByRole("dialog", { name: /Sarah Chen note/i })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  // a [[wikilink]] is rendered and clickable → navigates to that node's note
  // (accessible name is "Open note: <label>"; visible text is the [[label]] Obsidian style)
  const wiki = page.getByRole("button", { name: /^Open note:/ }).first();
  await expect(wiki).toBeVisible();
  await wiki.click();
  await expect(page.getByRole("dialog")).toBeVisible(); // still open, now showing the linked node
});

test("demo: /demo autoplays and drives the brain (T08 Part 4)", async ({ page }) => {
  await page.goto("/demo");
  // the overlay shows the step indicator and auto-navigates to /brain (graph renders)
  await expect(page.getByText(/Investor demo · step/i)).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 20_000 });
  // Skip ends the demo cleanly
  await page.getByRole("button", { name: /Skip/i }).click();
  await expect(page.getByText(/Investor demo · step/i)).toHaveCount(0);
});

test("artifacts replay proves determinism (EV-DET) + checksum", async ({ page }) => {
  await page.goto("/artifacts");
  // match the artifact-id heading EXACTLY — a loose substring also matches the "d3" hex in some
  // checksums (and convert runs in earlier tests register extra artifacts). D3 is the boot
  // artifact, inserted first, so its card's Replay is the first one.
  await expect(page.getByText("D3", { exact: true })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /^Replay$/ }).first().click();
  await expect(page.getByText(/EV-DET deterministic/i)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/checksum ok/i)).toBeVisible();
});
