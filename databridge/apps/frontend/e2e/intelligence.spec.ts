import { test, expect } from "@playwright/test";

/**
 * `/intelligence` figures over the shared, generic graph, asserted through the real gateway.
 *
 * Nothing about the graph is assumed: it may be empty, hold one pack's data or several. Every
 * expectation is derived from the `/intelligence` JSON the page itself received, so the spec
 * checks that the page renders the gateway's figures — and its honest empty states — and never a
 * number of its own.
 */

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "e2e-intel", name: "E2E", signedInAt: new Date().toISOString() }),
    );
  });
});

type Figures = {
  graph: null | {
    nodes: number;
    kinds: { kind: string; n: number; subject: boolean }[];
    most_connected: { id: string }[];
    subjects: unknown | null;
    entities: Record<string, { kind: string }>;
  };
  graph_unavailable: string | null;
  ingestion?: { sources: { source: string }[]; note: string | null } | null;
};

test("the figures render the gateway's own counts, or say why there are none", async ({
  page,
}) => {
  await page.goto("/intelligence");
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === "/intelligence");
  await page.getByTestId("open-figures").click();
  const body = (await (await response).json()) as Figures;
  await expect(page.getByTestId("figures")).toBeVisible();

  if (!body.graph) {
    await expect(page.getByTestId("figures")).toContainText(body.graph_unavailable ?? "");
    return;
  }
  if (body.graph.nodes === 0) {
    await expect(page.getByTestId("figures-empty")).toBeVisible();
    return;
  }

  // One chip per kind the gateway counted, each carrying its count.
  const chips = page.getByTestId("kind-counts").locator("li");
  await expect(chips).toHaveCount(body.graph.kinds.length);
  for (const [i, k] of body.graph.kinds.entries()) {
    await expect(chips.nth(i)).toContainText(String(k.n));
  }

  // Rankings never contain a subject, and the list shows exactly what was ranked.
  const subjectKinds = new Set(body.graph.kinds.filter((k) => k.subject).map((k) => k.kind));
  for (const r of body.graph.most_connected) {
    expect(subjectKinds.has(body.graph.entities[r.id]?.kind ?? "")).toBe(false);
  }
  if (body.graph.most_connected.length > 0) {
    await expect(page.getByTestId("ranked-most-connected").locator("li")).toHaveCount(
      body.graph.most_connected.length,
    );
  }

  // The identity-work figures appear exactly when the vocabulary supports them.
  if (body.graph.subjects === null) {
    await expect(page.getByTestId("identity-figures-absent")).toBeVisible();
  }

  // The ingestion register: the sources the ledgers name, or the empty-register sentence.
  if (body.ingestion && body.ingestion.sources.length === 0) {
    await expect(page.getByTestId("ingestion-empty")).toContainText(body.ingestion.note ?? "");
  } else if (body.ingestion) {
    for (const s of body.ingestion.sources) {
      await expect(page.getByTestId("ingestion-sources")).toContainText(s.source);
    }
  }
});
