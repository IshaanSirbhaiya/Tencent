// Showcase capture: the ORIGINAL Walnut screens running on Company A (the Internal Brain's world).
//
//   node tests/showcase/capture_company_a.cjs [outDir]
//
// Expects `bash scripts/run_walnut_original.sh` to be running (web :3300, gateway :8300). Headless
// Chromium at 1600x1000. Each screen is checked for Company A content before its screenshot is
// taken; any failed check exits non-zero. Read-only: it signs in with a roster persona (that only
// labels the terminal) and clicks, nothing is written to the graph.
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.env.WALNUT_ORIGINAL_WEB || "http://localhost:3300";
const OUT = path.resolve(
  process.argv[2] || path.join(__dirname, "..", "..", "..", "..", "..", "submission", "03_app_scenes", "walnut_original"),
);
const failures = [];

function check(name, ok, detail) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
  if (!ok) failures.push(name);
}

async function signIn(page) {
  await page.goto(`${BASE}/graph`);
  const roster = page.locator("[data-testid=roster-jdoe]");
  await roster.waitFor({ timeout: 30000 });
  check("sign-in offers only Jordan (the graph is his view)", (await page.locator("[data-testid^=roster-]").count()) === 1);
  await roster.click();
}

(async () => {
  require("fs").mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));

  await signIn(page);

  const showCanvas = async () => {
    await page.locator("[data-testid=graph-canvas]").evaluate((el) => el.scrollIntoView({ block: "start" }));
    await page.evaluate(() => window.scrollBy(0, -12));
    await page.waitForTimeout(600);
  };

  // 1. /graph — the knowledge graph opens on the best-connected nodes.
  await page.goto(`${BASE}/graph`);
  await page.waitForSelector("[data-testid^='rf__node-']", { timeout: 30000 });
  await page.waitForTimeout(2500);
  const nodes = await page.locator("[data-testid^='rf__node-']").count();
  const text = await page.locator("body").innerText();
  check("/graph draws Company A nodes", nodes > 20 && /Wiki page|Ticket/.test(text), `${nodes} nodes drawn`);
  await page.evaluate(() => window.scrollTo(0, 120));
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "01_graph.png") });

  // 2. /graph node panel — start from "DB Migration Plan" (seed search), click it for its panel.
  const search = page.locator("[data-testid=graph-seed-search] input");
  await search.fill("DB Migration Plan");
  await page.waitForTimeout(1500);
  await search.press("Enter");
  await page.waitForSelector("[data-testid='rf__node-knowledge_page:cf-1001']", { timeout: 20000 });
  await page.waitForTimeout(3000);
  await page.locator("[data-testid='rf__node-knowledge_page:cf-1001']").click();
  await page.waitForSelector("[data-testid=graph-attr-row]", { timeout: 15000 });
  await page.waitForTimeout(1500);
  const attrs = await page.locator("[data-testid=graph-attr-row]").count();
  const panel = await page.locator("body").innerText();
  check("/graph node panel shows cited attributes", attrs > 0 && /DB Migration Plan/.test(panel),
    `knowledge_page:cf-1001: ${attrs} attribute rows`);
  await showCanvas();
  await page.screenshot({ path: path.join(OUT, "02_graph_node_panel.png") });

  // 3. /brain — the company brain, with the time slider rewound.
  await page.goto(`${BASE}/brain`);
  await page.waitForFunction(() => {
    const el = document.querySelector("[data-testid=time-slider-count]");
    return el && /^[1-9]/.test(el.textContent || "");
  }, null, { timeout: 60000 });
  await page.waitForTimeout(4000);
  await page.locator("button[aria-label=Dismiss]").click().catch(() => {});
  const countNow = await page.locator("[data-testid=time-slider-count]").innerText();
  await page.locator("[data-testid=time-slider]").fill("80");
  await page.waitForTimeout(3500);
  const asOf = await page.locator("[data-testid=time-slider-asof]").innerText();
  const countThen = await page.locator("[data-testid=time-slider-count]").innerText();
  check("/brain time slider rewinds the graph", parseInt(countThen) < parseInt(countNow),
    `now: ${countNow} | ${asOf}: ${countThen}`);
  await page.screenshot({ path: path.join(OUT, "03_brain_time_slider.png") });

  // 4. /ontology — the enterprise pack's kinds.
  await page.goto(`${BASE}/ontology`);
  await page.waitForTimeout(5000);
  const onto = await page.locator("body").innerText();
  check("/ontology lists the enterprise kinds", /knowledge_page/.test(onto) && /reads_space/.test(onto));
  await page.screenshot({ path: path.join(OUT, "04_ontology.png") });

  // 5. /evidence — the provenance chain for one page.
  await page.goto(`${BASE}/evidence?entity=${encodeURIComponent("knowledge_page:cf-1001")}`);
  await page.waitForSelector("[data-testid=evidence-attribute]", { timeout: 30000 });
  await page.waitForTimeout(1500);
  const ev = await page.locator("body").innerText();
  check("/evidence traces DB Migration Plan to its row", /DB Migration Plan/.test(ev) && /pages\.json/.test(ev),
    `${await page.locator("[data-testid=evidence-attribute]").count()} attributes`);
  await page.screenshot({ path: path.join(OUT, "05_evidence_chain.png") });

  // 6. /demo — the original guided tour, from the pack's own demo_steps.
  await page.goto(`${BASE}/demo`);
  await page.waitForSelector("text=/Company A tour/", { timeout: 20000 });
  await page.waitForTimeout(4000);
  const bar = await page.locator("body").innerText();
  check("/demo starts the Company A tour", /Company A tour · step 1\/8/i.test(bar) && /knowledge graph/i.test(bar));
  await page.screenshot({ path: path.join(OUT, "06_demo_tour.png") });

  // 7. (extra) the tour's node beat on /brain: the page's evidence sheet opens beside the graph.
  await page.waitForFunction(() => /step 5\/8/i.test(document.body.innerText), null, { timeout: 90000 });
  await page.waitForTimeout(3500);
  const beat = await page.locator("body").innerText();
  check("/demo tour reaches the node beat on /brain", /DB Migration Plan/.test(beat) && /brain/.test(page.url()));
  await page.screenshot({ path: path.join(OUT, "07_demo_tour_node.png") });

  check("no uncaught page errors", errors.length === 0, errors.join(" | "));
  await browser.close();
  console.log(`screenshots -> ${OUT}`);
  process.exit(failures.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
