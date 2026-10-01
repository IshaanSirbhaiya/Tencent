import { test, expect } from "@playwright/test";

/**
 * Honesty of `/trace` and `/ontology` (ROADMAP §3 items 18–19). `/materialise` moved to `/data`:
 * see the note at the end of this file.
 *
 * None of these pages may draw invented data. Each test derives what the page SHOULD show from the
 * gateway's own response (or forces the gateway away with `page.route`) and asserts the page shows
 * that — never the old `config/platform-preview.json` shapes.
 */
const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000";
const AUTH = process.env.NEXT_PUBLIC_WALNUT_TOKEN
  ? { Authorization: `Bearer ${process.env.NEXT_PUBLIC_WALNUT_TOKEN}` }
  : undefined;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({
        staffId: "PS-FD-014",
        name: "Nurul Aisyah",
        signedInAt: new Date().toISOString(),
      }),
    );
  });
});

test("/trace shows the recorder's answer: a live trace, or 'no requests recorded yet'", async ({
  page,
  request,
}) => {
  const res = await request.get(`${GATEWAY}/trace?limit=25`, { headers: AUTH });
  await page.goto("/trace");
  if (!res.ok()) {
    await expect(page.getByTestId("trace-error")).toBeVisible({ timeout: 30_000 });
    return;
  }
  const traces = ((await res.json()).traces ?? []) as unknown[];
  if (traces.length === 0) {
    await expect(page.getByTestId("trace-empty")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("No requests recorded yet")).toBeVisible();
  } else {
    await expect(page.getByTestId("live-notice")).toBeVisible({ timeout: 30_000 });
  }
  // The preview notice (which names the preview file) never appears.
  await expect(page.getByText("platform-preview.json")).toHaveCount(0);
});

test("/trace with the gateway unreachable shows an error, not an example trace", async ({
  page,
}) => {
  await page.route(`${GATEWAY}/trace**`, (route) => route.abort());
  await page.goto("/trace");
  await expect(page.getByTestId("trace-error")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("live-notice")).toHaveCount(0);
  await expect(page.getByText("Communication graph")).toHaveCount(0);
});

test("/ontology with the gateway unreachable shows an error and no kinds", async ({ page }) => {
  await page.route(`${GATEWAY}/ontology**`, (route) => route.abort());
  await page.goto("/ontology");
  await expect(page.getByTestId("ontology-error")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("ontology-data-summary")).toHaveCount(0);
});

// `/materialise` is no longer a page of its own. It redirects to `/data#agent`, and the sidebar
// has one Data entry and no `/materialise` link. The two tests that used to sit here asserted the
// opposite: a "not built" page and a `/materialise` nav link. They contradicted `data.spec.ts` and
// failed live in E2E run 4 (Q-10). The honesty claims they carried now live, against the page
// that exists, in `data.spec.ts`:
//   * the redirect, and no `/materialise` nav link;
//   * the shape picker lists exactly what `GET /materialise/plans` lists, and an unattached
//     materialiser shows `materialiser-missing` with send disabled. No fabricated shapes.
//   * an unbuilt shape is refused, and the page shows the gateway's own refusal code.
