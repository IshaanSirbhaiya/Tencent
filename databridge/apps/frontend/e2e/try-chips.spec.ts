import { test, expect, type Page, type Response } from "@playwright/test";

/**
 * The one-click trial chips on `/intelligence`, asserted through the real gateway.
 *
 * Two claims, and the second is the one that matters. A chip must run the request, not merely type
 * it — a chip that fills the box and stops is a hint dressed as a button, and a visitor who did not
 * know what to type will not know to press Go either. And the *Refuses* group must render its
 * refusal **as a refusal**: a refusal decided before the graph is read and before any model is
 * called is this product working, so it must not arrive wearing the styling of a gateway failure.
 * Those two look identical to a judge unless something pins them apart, which is what the
 * `role="status"` assertion below does.
 *
 * **The chips are the ACTIVE PACK's content** (`usePackTrials`, `trials.intelligence` in
 * `packs/<pack>/ui.yaml`, served by `GET /packs`). Which pack is active is a fact about the
 * deployment, so it is read from the `GET /packs` response the page itself received. A fresh
 * browser has no stored choice, so the active pack is the registry's `default_pack`. Nothing here
 * names a pack, a chip id or a prompt. E2E run 4 (Q-10) failed two tests that asserted
 * healthcare-pack chips under a deployment bound to the workspace pack. A pack that ships no
 * chips for a tier is a SKIP that names the pack; it is never a pass.
 *
 * Tiers are found by the group label the type documents (`try-chips.tsx`: "Ask", "Act",
 * "Refuses"). That is UI vocabulary, not a domain's. Within a tier the FIRST chip is used, so a
 * pack controls which chip is exercised by its order. (The healthcare pack lists `ask-issuers`
 * first on purpose: under a live model `ask-count` answered NO_GROUNDED_ANSWER about one run in
 * three, which is the product behaving correctly and a poor thing to assert on.)
 *
 * These run against whichever gateway is already up, including a live model — deliberately:
 * neither assertion depends on a model's words.
 */

// `/intelligence` is behind `StaffGate` like every other route — see the same block in
// `frontdesk.spec.ts` and the deliberate absence of it in `staff-gate.spec.ts`.
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

type Chip = { id: string; label: string; payload: string };
type Group = { label: string; note?: string; chips: Chip[] };
type Registry = { default_pack: string; packs: { id: string; trials?: { intelligence?: Group[] } }[] };

function frontendOrigin(): string {
  const base = test.info().project.use.baseURL;
  expect(base, "playwright config must set use.baseURL").toBeTruthy();
  return new URL(base!).origin;
}

const gatewayCall = (method: string, pathname: string, front: string) => (r: Response) => {
  const u = new URL(r.url());
  return u.origin !== front && r.request().method() === method && u.pathname === pathname;
};

/** Open `/intelligence` and return the active pack's id and its intelligence chip groups, as the
 *  page's own `GET /packs` delivered them. */
async function openIntelligence(page: Page): Promise<{ pack: string; groups: Group[] }> {
  const front = frontendOrigin();
  const packsP = page.waitForResponse(gatewayCall("GET", "/packs", front));
  await page.goto("/intelligence");
  const res = await packsP;
  expect(res.status(), "GET /packs, as the page sent it, must succeed (build token set?)").toBe(200);
  const registry = (await res.json()) as Registry;
  expect(registry.packs.length, "non-vacuous: the registry lists packs").toBeGreaterThan(0);
  // `resolvePack` with no stored choice: the default pack, else the first.
  const active = registry.packs.find((p) => p.id === registry.default_pack) ?? registry.packs[0];
  return { pack: active.id, groups: active.trials?.intelligence ?? [] };
}

const tier = (groups: Group[], re: RegExp) => groups.find((g) => re.test(g.label.trim()) && g.chips.length > 0);

test("the chips on /intelligence are exactly the active pack's", async ({ page }) => {
  const { groups } = await openIntelligence(page);
  const row = page.getByTestId("console-try-chips");
  const expected = groups.flatMap((g) => g.chips);
  // Every chip the pack declares is on the page with its label...
  for (const c of expected) {
    await expect(row.getByTestId(`try-chip-${c.id}`)).toHaveText(c.label);
  }
  // ...and nothing else is: no chip from another pack and none from a bundled copy.
  await expect(row.locator('[data-testid^="try-chip-"]')).toHaveCount(expected.length);
});

test("an Ask chip fills the request box and submits it", async ({ page }) => {
  const { pack, groups } = await openIntelligence(page);
  const ask = tier(groups, /^ask$/i);
  test.skip(!ask, `the active pack '${pack}' ships no Ask chips for /intelligence`);
  const spec = ask!.chips[0];

  const chip = page.getByTestId(`try-chip-${spec.id}`);
  await expect(chip).toBeVisible();
  await expect(chip).toHaveText(spec.label);

  await chip.click();

  // Filled — the box shows what was asked, so the next edit starts from it.
  await expect(page.getByTestId("console-input")).toHaveValue(spec.payload);

  // And submitted. A grounded answer with its citations came back; nothing needed a second click.
  // Generous, because a real model answers in 3–30s and this suite must not depend on which.
  await expect(page.getByTestId("console-answer")).toBeVisible({ timeout: 50_000 });
  await expect(page.getByTestId("console-values")).not.toBeEmpty();
});

test("a Refuses chip is answered with a refusal, rendered as one and not as an error", async ({
  page,
}) => {
  const { pack, groups } = await openIntelligence(page);
  const refuses = tier(groups, /^refuse/i);
  test.skip(!refuses, `the active pack '${pack}' ships no Refuses chips for /intelligence`);
  const spec = refuses!.chips[0];

  const front = frontendOrigin();
  const answerP = page.waitForResponse(gatewayCall("POST", "/intelligence/console", front), { timeout: 30_000 });
  await page.getByTestId(`try-chip-${spec.id}`).click();
  await expect(page.getByTestId("console-input")).toHaveValue(spec.payload);

  // What the gateway decided, read from its answer to the page's own request.
  const res = await answerP;
  expect(res.ok(), `POST /intelligence/console answered ${res.status()}`).toBe(true);
  const body = (await res.json()) as {
    refusal?: { code: string };
    answer?: { refused?: boolean; refusal_code?: string } | null;
    proposed?: unknown[];
  };
  const code = body.refusal?.code ?? (body.answer?.refused ? body.answer.refusal_code : undefined);
  expect(code, "a Refuses chip must be REFUSED by the gateway, with a named code").toBeTruthy();

  const refusal = page.getByTestId("console-refusal");
  await expect(refusal).toBeVisible({ timeout: 30_000 });
  // Named, because a refusal nobody can name is a refusal nobody can check.
  await expect(refusal).toContainText(code!);
  // A refusal is an ANSWER: it is a status, not an alert, and the page's only `role="alert"` is
  // the "could not reach the gateway" line. If this ever fails, the product's strongest moment is
  // being shown to a judge as a bug.
  await expect(refusal).toHaveAttribute("role", "status");
  // The page's only error line, asserted absent by its text rather than by `getByRole("alert")`:
  // Playwright pierces shadow DOM, and Next's dev overlay carries an empty `role="alert"` of its
  // own, so a role count here passes under `npm run start` and fails under a dev server for a
  // reason that has nothing to do with this product.
  await expect(page.getByText("Could not reach the gateway")).toHaveCount(0);

  // Nothing was answered and nothing was drafted — the request was declined, not half-served.
  expect(body.proposed ?? []).toHaveLength(0);
  await expect(page.getByTestId("console-answer")).toHaveCount(0);
  await expect(page.getByTestId("proposed-actions")).toHaveCount(0);

  // And the group still says, in the pack's own words, that this is the product working.
  if (refuses!.note) await expect(page.getByTestId("console-try-chips")).toContainText(refuses!.note);
});
