import { test, expect, type Page } from "@playwright/test";

/**
 * `/evidence` over the shared graph, asserted through the real gateway.
 *
 * The store behind the gateway may be empty (nothing ingested yet) or full; these specs do not
 * assume either. Every expectation is DERIVED from the JSON the page itself received — the
 * browse list from `/evidence/recent`, the register from `/evidence/sources` — so an empty store
 * must render its honest empty state, and a full one must render what the gateway sent.
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "e2e-evidence", name: "E2E", signedInAt: new Date().toISOString() }),
    );
  });
});

async function loadWith<T>(page: Page, path: string): Promise<T> {
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === path);
  await page.goto("/evidence");
  return (await (await response).json()) as T;
}

type Recent = { entities: { id: string; label: string }[]; total: number; withheld: number };

test("the browse list renders exactly what the gateway released, or says it is empty", async ({
  page,
}) => {
  const recent = await loadWith<Recent>(page, "/evidence/recent");
  if (recent.entities.length === 0) {
    await expect(page.getByTestId("evidence-recent-empty")).toBeVisible();
    return;
  }
  const list = page.getByTestId("evidence-recent");
  await expect(list.getByRole("button")).toHaveCount(recent.entities.length);

  // Tracing a listed entity walks the whole chain: it was listed because it is releasable.
  await list.getByRole("button").first().click();
  await expect(page.getByTestId("evidence-authorisation")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("evidence-authorisation")).toHaveAttribute(
    "data-basis",
    /pointer_provenance|committed_ledger_row/,
  );
  const sources = page.getByTestId("evidence-source");
  expect(await sources.count()).toBeGreaterThan(0);
  // Every pointer carries a re-verify result — verified, failed, or "nothing to re-read".
  await expect(sources.first().getByTestId("evidence-verify")).toBeVisible();
});

type Sources = { sources: { source: string }[]; note: string | null };

test("the source register lists every ingestion source, or says none has run", async ({
  page,
}) => {
  const register = await loadWith<Sources>(page, "/evidence/sources");
  if (register.sources.length === 0) {
    await expect(page.getByTestId("evidence-sources-empty")).toContainText(register.note ?? "");
    return;
  }
  const list = page.getByTestId("evidence-sources");
  for (const s of register.sources) {
    await expect(list).toContainText(s.source);
  }
});

test("an unknown id is a named refusal, not an error", async ({ page }) => {
  await page.goto("/evidence?entity=nothing%3Alike-this");
  const refusal = page.getByTestId("evidence-refusal");
  await expect(refusal).toBeVisible({ timeout: 20_000 });
  await expect(refusal).toHaveAttribute("data-refusal-code", "UNKNOWN_ENTITY");
});
