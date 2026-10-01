import { test, expect, type Page, type Response } from "@playwright/test";

/**
 * `/ontology` is where the owner defines the entities and relations the system maps to.
 *
 * These run against the REAL gateway and its real pack files, so they are deliberately
 * NON-MUTATING: nothing here saves a kind. Mutating paths (define, rename, endpoints, audit) are
 * covered against a temporary pack tree in `apps/gateway/tests/test_ontology_owner_edits.py`.
 *
 * Nothing about the deployment is assumed (E2E run 4, Q-10).
 *   * The bound pack, its kinds and the pack list come from the responses THE PAGE received,
 *     captured with `waitForResponse`. The page sends the token the build inlined. The spec does
 *     not fetch with a token of its own, so a runner without `NEXT_PUBLIC_WALNUT_TOKEN` in its env
 *     cannot read a 401 that the page never saw.
 *   * `POST /ontology/kinds` is OWNER-only (`require_owner`, configured by `WALNUT_ONTOLOGY_OWNERS`;
 *     unset means only the dev principal). Whether the build's principal is an owner is read from
 *     the gateway's own answer to the page's request, never guessed:
 *       - not an owner: 403 `ONTOLOGY_OWNER_REQUIRED`, shown by name; no dialog, nothing saved;
 *       - an owner: `IDENTITY_CONFIRMATION_REQUIRED`, which the page turns into the explicit
 *         confirmation dialog.
 *     Each test asserts its own branch and skips, by name, when the deployment is in the other
 *     one. `E2E_ONTOLOGY_OWNER_TOKEN=<an owner's bearer token>` re-signs the page's ontology
 *     requests as that owner, so the owner branch can run against a build whose own principal
 *     is not an owner.
 */

// Every route is behind `StaffGate`; seed the shift session as `frontdesk.spec.ts` does. The gate
// itself is covered, unseeded, by `staff-gate.spec.ts`.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: new Date().toISOString() }),
    );
  });
});

const OWNER_TOKEN = process.env.E2E_ONTOLOGY_OWNER_TOKEN;
const OWNER_REQUIRED = "ONTOLOGY_OWNER_REQUIRED";
const IDENTITY_REQUIRED = "IDENTITY_CONFIRMATION_REQUIRED";

/** The frontend's own origin. Its `/ontology` page and RSC fetches must never be taken for the
 *  gateway's `/ontology` route, which has the same path. */
function frontendOrigin(): string {
  const base = test.info().project.use.baseURL;
  expect(base, "playwright config must set use.baseURL").toBeTruthy();
  return new URL(base!).origin;
}

/** A gateway response (any origin but the frontend's) for `method` + exact `pathname`. */
const gatewayCall = (method: string, pathname: string, front: string) => (r: Response) => {
  const u = new URL(r.url());
  return u.origin !== front && r.request().method() === method && u.pathname === pathname;
};

/** Re-sign the page's gateway `/ontology*` requests as an owner. Only used when the operator
 *  supplied an owner token. */
async function signOntologyAsOwner(page: Page, front: string, token: string) {
  await page.route(
    (url) => url.origin !== front && url.pathname.startsWith("/ontology"),
    (route) =>
      route.continue({ headers: { ...route.request().headers(), authorization: `Bearer ${token}` } }),
  );
}

/** Fill the "declare a subject kind" form and submit it; return the gateway's answer to the
 *  page's `POST /ontology/kinds` and the refusal code it carried (if any). */
async function declareSubject(page: Page, front: string) {
  await page.goto("/ontology");
  await page.getByTestId("open-add-node").click();
  await page.getByTestId("define-kind-name").fill(`e2e_subject_${Date.now()}`);
  await page.getByTestId("define-binds-to").selectOption("entity");
  await page.getByTestId("define-is-subject").check();
  await page.getByTestId("define-identifiers").fill("email");
  await page.getByTestId("define-grant-type").fill("E2eReadGrant");
  const postP = page.waitForResponse(gatewayCall("POST", "/ontology/kinds", front));
  await page.getByTestId("define-submit").click();
  const res = await postP;
  const body = (await res.json().catch(() => ({}))) as { refusal?: { code?: string; message?: string } };
  return { status: res.status(), code: body.refusal?.code ?? null, message: body.refusal?.message ?? "" };
}

test("the ontology page opens on the bound pack and offers relations only between defined kinds", async ({
  page,
}) => {
  const front = frontendOrigin();
  const packsP = page.waitForResponse(gatewayCall("GET", "/ontology/packs", front));
  const viewP = page.waitForResponse(gatewayCall("GET", "/ontology", front));
  await page.goto("/ontology");

  const packsRes = await packsP;
  expect(packsRes.status(), "GET /ontology/packs, as the page sent it, must succeed (build token set?)").toBe(200);
  const packs = (await packsRes.json()) as { packs: string[]; enabled: string[]; default: string | null };
  const viewRes = await viewP;
  expect(viewRes.status(), "GET /ontology, as the page sent it, must succeed").toBe(200);
  const ontology = (await viewRes.json()) as { node_kinds: { kind: string; deprecated: boolean }[] };

  // The default target is the deployment's first bound pack, as the gateway reports it.
  expect(packs.default, "non-vacuous: the deployment binds or offers a pack").toBeTruthy();
  if (packs.packs.length > 1) {
    await expect(page.getByTestId(`ontology-pack-${packs.default}`)).toBeVisible();
  } else {
    // The picker hides itself when there is nothing to pick between (`pack-picker.tsx`).
    await expect(page.locator('[data-testid^="ontology-pack-"]:not([data-testid="ontology-pack-unbound"])')).toHaveCount(0);
  }
  await expect(page.getByTestId("ontology-data-summary")).toBeVisible();

  // The relation form offers exactly the pack's live entity kinds as endpoints, never free text.
  await page.getByTestId("open-add-edge").click();
  const live = ontology.node_kinds.filter((k) => !k.deprecated);
  expect(live.length, "non-vacuous: the bound pack defines entity kinds").toBeGreaterThan(0);
  for (const k of live) {
    await expect(page.getByTestId(`define-src-${k.kind}`)).toBeVisible();
  }
  // Each meta-kind is explained in plain English.
  await expect(page.getByTestId("define-meta-explanation")).not.toHaveText("");
});

test("a principal that is not an ontology owner is refused by name: no confirmation opens and nothing is saved", async ({
  page,
}) => {
  const front = frontendOrigin();
  const answer = await declareSubject(page, front);
  test.skip(
    answer.code !== OWNER_REQUIRED,
    `the build's principal is an ontology owner on this gateway (it answered ${answer.status} ${answer.code}); ` +
      "the owner branch is the next test",
  );
  expect(answer.status).toBe(403);
  // The page shows the gateway's own refusal, verbatim, and treats it as a refusal.
  await expect(page.getByTestId("define-refusal")).toBeVisible();
  await expect(page.getByTestId("define-refusal")).toContainText(answer.message.slice(0, 60));
  await expect(page.getByTestId("identity-confirm-dialog")).toHaveCount(0);
  await expect(page.getByTestId("define-result")).toHaveCount(0);
});

test("an owner declaring a subject kind is stopped for an explicit identity confirmation", async ({ page }) => {
  const front = frontendOrigin();
  if (OWNER_TOKEN) await signOntologyAsOwner(page, front, OWNER_TOKEN);
  const answer = await declareSubject(page, front);
  test.skip(
    answer.code === OWNER_REQUIRED,
    "the principal this run signs as is not an ontology owner (WALNUT_ONTOLOGY_OWNERS on the gateway). " +
      "Set E2E_ONTOLOGY_OWNER_TOKEN to an owner's bearer token to exercise this branch",
  );
  // The first attempt is refused for the identity confirmation before anything is written.
  expect(answer.code, `expected ${IDENTITY_REQUIRED}, got ${answer.status} ${answer.code}`).toBe(IDENTITY_REQUIRED);

  const dialog = page.getByTestId("identity-confirm-dialog");
  await expect(dialog).toBeVisible();
  // The confirm button stays disabled until the human ticks the explicit acknowledgement.
  await expect(page.getByTestId("identity-confirm-submit")).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("define-result")).toHaveCount(0);
});
