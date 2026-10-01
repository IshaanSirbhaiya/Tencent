import { test, expect, type Page } from "@playwright/test";

/**
 * `/graph` — connected and traversable on WHATEVER graph the gateway's shared store holds.
 *
 * These specs never assume what is in the store (no kind, label or count is written down here):
 * an empty store must render the honest empty state that links to /connectors, and a populated
 * one must render a canvas the reader can expand and path-find across. Each test takes whichever
 * branch the live store puts it on and asserts that branch's contract.
 *
 * Session seeding: same `addInitScript` block as `frontdesk.spec.ts` — every route is behind
 * `StaffGate`, whose own coverage lives in `staff-gate.spec.ts`.
 */
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

/** Wait for /graph to settle on one of its two honest states. */
async function settle(page: Page): Promise<"canvas" | "empty"> {
  await page.goto("/graph");
  const canvas = page.getByTestId("graph-canvas");
  const empty = page.getByTestId("graph-empty-state");
  await expect(canvas.or(empty)).toBeVisible({ timeout: 30_000 });
  return (await empty.isVisible()) ? "empty" : "canvas";
}

test("graph renders a connected canvas, or says there is no data and links to connectors", async ({
  page,
}) => {
  const state = await settle(page);
  if (state === "empty") {
    await expect(
      page.getByText("No data yet — connect a source"),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /connectors/i }),
    ).toHaveAttribute("href", "/connectors");
    return;
  }
  const nodes = page.locator(".react-flow__node");
  await expect(nodes.first()).toBeVisible();
  // The default view draws no isolated node, so there is at least one edge whenever there are
  // two or more nodes on screen.
  const nodeCount = await nodes.count();
  if (nodeCount > 1) {
    await expect(page.locator(".react-flow__edge").first()).toBeAttached();
  }
  // Kind legend is drawn from the store's own kinds.
  await expect(page.getByTestId("graph-legend")).toBeVisible();
});

test("double-clicking a node merges its neighbourhood into the canvas", async ({
  page,
}) => {
  test.skip((await settle(page)) === "empty", "the shared store is empty");
  const nodes = page.locator(".react-flow__node");
  const before = await nodes.count();
  await nodes.first().dblclick();
  const notice = page.getByTestId("graph-notice");
  await expect(notice).toContainText(/Expanded/, { timeout: 20_000 });
  // A merge never removes what was already drawn.
  expect(await nodes.count()).toBeGreaterThanOrEqual(before);
  // The provenance panel opened on the expanded node, with its evidence link.
  await expect(page.getByTestId("graph-evidence-link")).toHaveAttribute(
    "href",
    /\/evidence\?entity=/,
  );
});

test("marking two nodes asks for the path between them and reports the answer", async ({
  page,
}) => {
  test.skip((await settle(page)) === "empty", "the shared store is empty");
  const nodes = page.locator(".react-flow__node");
  test.skip((await nodes.count()) < 2, "fewer than two nodes drawn");
  await nodes.nth(0).click();
  await page.getByTestId("graph-path-from").click();
  await nodes.nth(1).click();
  await page.getByTestId("graph-path-to").click();
  const result = page.getByTestId("graph-path-result");
  // Either a highlighted path or one of the server's named reasons — never silence.
  await expect(result).toHaveText(
    /hops? — highlighted|no path|budget|different connected/,
    {
      timeout: 20_000,
    },
  );
});

// ---- traversal trail --------------------------------------------------------------------------

test("each expand is recorded on the trail; back, forward, copy and clear work", async ({
  page,
}) => {
  test.skip((await settle(page)) === "empty", "the shared store is empty");
  const nodes = page.locator(".react-flow__node");
  test.skip((await nodes.count()) < 2, "fewer than two nodes drawn");
  const trail = page.getByTestId("graph-trail");
  await expect(trail).toHaveCount(0); // nothing traversed yet → no trail drawn

  await nodes.nth(0).dblclick();
  await expect(page.getByTestId("graph-notice")).toContainText(/Expanded/, {
    timeout: 20_000,
  });
  const crumbs = page.getByTestId("graph-trail-crumb");
  await expect(crumbs).toHaveCount(1);
  // The crumb names the node the notice says was expanded (the UI's own response, not a constant).
  const firstId = await crumbs.first().getAttribute("data-node-id");
  expect(firstId).toBeTruthy();
  await expect(page.getByTestId("graph-notice")).toContainText(
    firstId as string,
  );

  // A second, different node — the trail grows by one.
  const count = await nodes.count();
  let second = -1;
  for (let i = 0; i < count; i += 1) {
    const id = await nodes.nth(i).getAttribute("data-id");
    if (id && id !== firstId) {
      second = i;
      break;
    }
  }
  test.skip(second < 0, "no second node to expand");
  await nodes.nth(second).dblclick();
  await expect(crumbs).toHaveCount(2, { timeout: 20_000 });
  // Trail nodes are ringed and numbered on the canvas.
  await expect(page.getByTestId("graph-trail-badge").first()).toBeVisible();

  await expect(page.getByTestId("graph-trail-count")).toHaveText(/^2 of 2/);
  await page.getByTestId("graph-trail-back").click();
  await expect(page.getByTestId("graph-trail-count")).toHaveText(/^1 of 2/);
  await page.getByTestId("graph-trail-forward").click();
  await expect(page.getByTestId("graph-trail-count")).toHaveText(/^2 of 2/);

  // Clicking a crumb focuses it: the provenance panel opens on that node.
  await crumbs.first().click();
  await expect(page.getByTestId("graph-trail-count")).toHaveText(/^1 of 2/);
  await expect(page.getByTestId("graph-evidence-link")).toHaveAttribute(
    "href",
    `/evidence?entity=${encodeURIComponent(firstId as string)}`,
    { timeout: 20_000 },
  );

  await page.getByTestId("graph-trail-copy").click();
  await expect(page.getByTestId("graph-trail-copy")).toHaveText(
    /copied|clipboard unavailable/,
  );

  await page.getByTestId("graph-trail-clear").click();
  await expect(trail).toHaveCount(0);
});

// ---- agent traversal ----------------------------------------------------------------------------

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000";
const AUTH = process.env.NEXT_PUBLIC_WALNUT_TOKEN
  ? { Authorization: `Bearer ${process.env.NEXT_PUBLIC_WALNUT_TOKEN}` }
  : undefined;

test("an unknown agent run is reported honestly, never drawn", async ({
  page,
}) => {
  test.skip((await settle(page)) === "empty", "the shared store is empty");
  await page.goto("/graph?run=no-such-agent/no-such-run");
  await expect(page.getByTestId("graph-agent-traversal")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTestId("graph-agent-error")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("graph-agent-step")).toHaveCount(0);
});

test("an agent run's graph steps are listed in order with who produced them", async ({
  page,
  request,
}) => {
  const agents = await request.get(`${GATEWAY}/agents`, { headers: AUTH });
  test.skip(!agents.ok(), "the gateway's /agents route is not reachable");
  let found: { agent: string; runId: string } | null = null;
  for (const a of ((await agents.json()).agents ?? []) as { agent: string }[]) {
    const runs = await request.get(
      `${GATEWAY}/agents/${encodeURIComponent(a.agent)}/runs`,
      {
        headers: AUTH,
      },
    );
    if (!runs.ok()) continue;
    const list = ((await runs.json()).runs ?? []) as {
      run_id: string;
      status: string;
    }[];
    const run = list.find((r) => r.status !== "unreadable");
    if (run) {
      found = { agent: a.agent, runId: run.run_id };
      break;
    }
  }
  test.skip(!found, "no agent has a recorded run");
  const { agent, runId } = found as { agent: string; runId: string };
  const detail = await (
    await request.get(
      `${GATEWAY}/agents/${encodeURIComponent(agent)}/runs/${encodeURIComponent(runId)}`,
      { headers: AUTH },
    )
  ).json();
  const graphSteps = (
    (detail.record?.steps ?? []) as { tool: string }[]
  ).filter((s) => s.tool.startsWith("graph."));

  await page.goto(
    `/graph?run=${encodeURIComponent(agent)}/${encodeURIComponent(runId)}`,
  );
  await expect(page.getByTestId("graph-agent-summary")).toBeVisible({
    timeout: 30_000,
  });
  if (graphSteps.length === 0) {
    await expect(page.getByTestId("graph-agent-empty")).toBeVisible();
    return;
  }
  const steps = page.getByTestId("graph-agent-step");
  await expect(steps).toHaveCount(graphSteps.length);
  // Order and tool names follow the run record exactly.
  for (let i = 0; i < graphSteps.length; i += 1) {
    await expect(steps.nth(i)).toContainText(graphSteps[i].tool);
  }
  await expect(page.getByTestId("graph-agent-produced-by")).toHaveCount(
    graphSteps.length,
  );
});

test("?source=<connection id> opens the canvas on what that source grounds", async ({
  page,
  request,
}) => {
  const res = await request.get(`${GATEWAY}/graph/facets`, { headers: AUTH });
  test.skip(!res.ok(), "the gateway's /graph/facets route is not reachable");
  const facets = await res.json();
  const first = (facets.sources?.items ?? [])[0] as
    { source_id: string } | undefined;
  test.skip(!first, "the store has no sources");
  const id = (first as { source_id: string }).source_id;
  await page.goto(`/graph?source=${encodeURIComponent(id)}`);
  const banner = page.getByTestId("graph-source-filter");
  await expect(banner).toBeVisible({ timeout: 30_000 });
  await expect(banner).toContainText(id);
  // The filter bar carries the same source, so clearing it is one click away.
  await expect(
    page
      .locator("select")
      .filter({ has: page.locator(`option[value="${id}"]`) })
      .first(),
  ).toHaveValue(id);
});

// =============================================================================================
// @unit — pure decision helpers (no gateway, no page). Codex batch 2, R-019 ui-builder-sources-graph.
// =============================================================================================
import {
  GraphApiError,
  describeGraphError,
  graphApi,
  readAgentSteps,
  resolveTrailLinks,
  splitAttributes,
  stepMeasures,
  type GraphEdgeSummary,
} from "../lib/graph-api";

test.describe("@unit graph helpers", () => {
  test("an attribute with no per-attribute pointer is withheld, never shown as a value", () => {
    const r = splitAttributes(
      { salary: 100000, name: "A", blank: null },
      { name: { source_id: "s", kind: "document" } as never },
    );
    expect(r.grounded.map((g) => g.field)).toEqual(["name"]);
    expect(r.withheld).toEqual(["salary"]);
    expect(r.empty).toEqual(["blank"]);
    // nothing about the withheld value survives the split
    expect(JSON.stringify(r)).not.toContain("100000");
  });

  test("a trail link is an edge only when a response named it; canvas adjacency is never a walk", () => {
    const e = (id: string, a: string, b: string): GraphEdgeSummary =>
      ({ id, kind: "k", src_id: a, dst_id: b, weight: 1, valid_from: null, valid_to: null, system_from: null, system_to: null, is_open: true });
    const edges = new Map([["e1", e("e1", "A", "B")], ["e2", e("e2", "B", "A")]]);
    const steps = [{ nodeId: "A", action: "expand" }, { nodeId: "B", action: "expand" }, { nodeId: "A", action: "path", edgeId: "e2" }];
    const links = resolveTrailLinks(steps, edges);
    expect(links[0].edgeId).toBeNull();
    expect(links[0].edge).toBeNull();
    expect(links[0].adjacent).toBe(2);
    expect(links[1].edgeId).toBe("e2");
  });

  test("missing step measurements stay unknown; a record without a steps array is malformed, not 'no calls'", () => {
    expect(stepMeasures({})).toEqual({ ms: null, pointers: null });
    expect(stepMeasures({ ms: 0, result_pointers: [] })).toEqual({ ms: 0, pointers: 0 });
    expect(readAgentSteps({}).ok).toBe(false);
    expect(readAgentSteps(null).ok).toBe(false);
    const ok = readAgentSteps({ steps: [] });
    expect(ok.ok).toBe(true);
  });

  test("an HTTP failure from a reachable gateway is not reported as unreachable", async () => {
    const real = globalThis.fetch;
    try {
      globalThis.fetch = (async () => new Response(JSON.stringify({ detail: "nope" }), { status: 401 })) as typeof fetch;
      const e401 = await graphApi.facets().then(() => null, (e) => e);
      expect(e401).toBeInstanceOf(GraphApiError);
      expect((e401 as GraphApiError).status).toBe(401);
      expect(describeGraphError(e401)).not.toMatch(/could not reach/i);

      globalThis.fetch = (async () => new Response("<html>bad gateway</html>", { status: 502 })) as typeof fetch;
      const e502 = await graphApi.facets().then(() => null, (e) => e);
      expect((e502 as GraphApiError).status).toBe(502);
      expect(describeGraphError(e502)).not.toMatch(/could not reach/i);

      globalThis.fetch = (async () => {
        throw new TypeError("fetch failed");
      }) as typeof fetch;
      const e0 = await graphApi.facets().then(() => null, (e) => e);
      expect((e0 as GraphApiError).status).toBe(0);
      expect(describeGraphError(e0)).toMatch(/could not reach/i);

      // a refusal body is still an ordinary response, passed through
      globalThis.fetch = (async () =>
        new Response(JSON.stringify({ refusal: { code: "X", message: "m" } }), { status: 404 })) as typeof fetch;
      const r = await graphApi.facets();
      expect("refusal" in (r as object)).toBe(true);
    } finally {
      globalThis.fetch = real;
    }
  });
});

// =============================================================================================
// @unit — Codex round 3 (R-019 "Round 3"), FX8b.
// =============================================================================================
import { currentTrail, structuralGrounding } from "../lib/graph-api";

test.describe("@unit graph helpers (round 3)", () => {
  test("R3-G4 a structural value is cited, entity-grounded, or withheld — never shown bare", () => {
    const p = { source_id: "crm", kind: "table" } as never;
    // no entity source at all: the node's own values cannot be shown as facts
    expect(structuralGrounding({ sources: [] }, "label").state).toBe("withheld");
    // entity-level grounding only: shown, labelled as not individually cited
    expect(structuralGrounding({ sources: [p] }, "label").state).toBe("entity");
    // a per-field pointer (the contract the gateway is adding) is a citation
    const cited = structuralGrounding({ sources: [p], field_sources: { label: p } }, "label");
    expect(cited.state).toBe("cited");
    expect(cited.pointer).toBe(p);
    expect(structuralGrounding({ sources: undefined as never }, "kind").state).toBe("withheld");
  });

  test("R3-G6 copying the trail copies the current trail, not forward history", () => {
    const steps = [1, 2, 3, 4, 5].map((i) => ({ nodeId: `n${i}` }));
    const links = [1, 2, 3, 4].map((i) => ({ edge: null, edgeId: `e${i}`, adjacent: 1 }));
    const cur = currentTrail(steps, links, 2);
    expect(cur.steps.map((s) => s.nodeId)).toEqual(["n1", "n2", "n3"]);
    expect(cur.links.map((l) => l.edgeId)).toEqual(["e1", "e2"]);
    expect(currentTrail(steps, links, -1).steps).toEqual([]);
  });
});
