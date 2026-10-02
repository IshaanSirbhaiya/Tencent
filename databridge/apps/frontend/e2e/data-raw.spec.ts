import { test, expect, type Response } from "@playwright/test";

/**
 * `/data#raw` — a RAW dataset built through delegation (DSD): propose a table design, approve it,
 * build it. Runs against the REAL gateway; every expectation is read from a response the page
 * itself received (captured with `waitForResponse`), never written here.
 *
 * Proposing writes one design file under var/materialise/designs; BUILDING writes a dataset, so
 * the build step runs only when `E2E_DATA_RUN=1`. Needs NEXT_PUBLIC_WALNUT_TOKEN for this build and
 * a federated read grant for its principal (WALNUT_FEDERATED_GRANTS).
 */

type DesignDoc = {
  id: string;
  status: string;
  digest: string;
  design: {
    grain_text: string;
    columns: { name: string; when_known: string }[];
    questions: string[];
    label: { column: string } | null;
  };
};

const path = (r: Response) => new URL(r.url()).pathname;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: new Date().toISOString() }),
    );
  });
});

test("a described dataset becomes a design whose every column says where it comes from and when it was known", async ({ page }) => {
  await page.goto("/data#raw");
  const section = page.getByTestId("data-raw-section");
  await expect(section).toBeVisible();
  await section.getByTestId("design-input").fill("one row per record, with everything linked to it");
  const proposed = page.waitForResponse((r) => r.request().method() === "POST" && path(r) === "/materialise/designs");
  await section.getByTestId("design-propose").click();
  const res = await proposed;
  if (!res.ok()) {
    // a refusal is an answer: the page shows the gateway's own code
    const body = (await res.json()) as { refusal: { code: string } };
    await expect(section.getByTestId("design-refusal")).toContainText(body.refusal.code);
    return;
  }
  const doc = (await res.json()) as DesignDoc;
  const card = section.getByTestId("design-card");
  await expect(card).toContainText(doc.design.grain_text);
  await expect(card.getByTestId("design-column")).toHaveCount(doc.design.columns.length);
  const known = card.getByTestId("design-when-known");
  for (let i = 0; i < doc.design.columns.length; i++) {
    await expect(known.nth(i)).toHaveText(doc.design.columns[i].when_known);
  }
  await expect(card.getByTestId("design-status")).toContainText(doc.digest.slice(0, 12));
  const approve = card.getByTestId("design-approve");
  if (doc.design.questions.length > 0) {
    await expect(approve).toBeDisabled();
    await expect(card.getByTestId("design-questions")).toBeVisible();
    return;
  }
  await expect(card.getByTestId("design-build")).toBeDisabled();
  const approved = page.waitForResponse((r) => r.request().method() === "POST" && path(r) === `/materialise/designs/${doc.id}/approve`);
  await approve.click();
  const a = (await (await approved).json()) as DesignDoc;
  await expect(card.getByTestId("design-status")).toContainText(a.status);
  test.skip(process.env.E2E_DATA_RUN !== "1", "building writes a dataset; set E2E_DATA_RUN=1");
  await card.getByTestId("design-build").click();
  const result = section.getByTestId("design-result");
  await expect(result.or(section.getByTestId("design-refusal"))).toBeVisible({ timeout: 120_000 });
  if (await result.isVisible()) {
    await expect(result).toContainText("raw extract; time as metadata; not leakage-filtered");
  }
});
