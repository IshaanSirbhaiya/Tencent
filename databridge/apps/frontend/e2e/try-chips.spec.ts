import { test, expect } from "@playwright/test";

/**
 * The one-click trial chips on `/intelligence`, asserted through the real gateway.
 *
 * Two claims, and the second is the one that matters. A chip must run the request, not merely type
 * it — a chip that fills the box and stops is a hint dressed as a button, and a visitor who did not
 * know what to type will not know to press Go either. And the *Refuses* group must render its
 * refusal **as a refusal**: `IDENTITY_VERIFICATION_OUT_OF_SCOPE` is this product working, decided
 * before the graph is read and before any model is called, so it must not arrive wearing the
 * styling of a gateway failure. Those two look identical to a judge unless something pins them
 * apart, which is what the `role="alert"` assertion below does.
 *
 * These run against whichever gateway is already up (`reuseExistingServer`), including a live
 * model — deliberately: neither assertion depends on a model's words. The first asserts that a
 * grounded answer rendered at all; the second asserts a refusal the deterministic screen produces
 * with `model_calls: 0`, which is the same under every backend.
 */

// `/intelligence` is behind `StaffGate` like every other route — see the same block in
// `frontdesk.spec.ts` and the deliberate absence of it in `staff-gate.spec.ts`.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "parkway.staff.session.v1",
      JSON.stringify({
        staffId: "PS-FD-014",
        name: "Nurul Aisyah",
        signedInAt: new Date().toISOString(),
      }),
    );
  });
});

// Deliberately `ask-issuers` and not `ask-count`. Both are real, but under a live model
// (`openai/gpt-4o`) `how many documents are here?` answered `NO_GROUNDED_ANSWER` on roughly one
// run in three while the issuer question answered 7 values on every one — the planner sometimes
// declines to ground the count rather than guess it. That is the product behaving correctly and a
// terrible thing to assert on; the chip stays on the page because its figures still appear either
// way, and the test picks the stable one.
test("an Ask chip fills the request box and submits it", async ({ page }) => {
  await page.goto("/intelligence");

  const chip = page.getByTestId("try-chip-ask-issuers");
  await expect(chip).toBeVisible();
  const prompt = (await chip.textContent())?.trim() ?? "";
  expect(prompt).toBe("which issuers sent documents?");

  await chip.click();

  // Filled — the box shows what was asked, so the next edit starts from it.
  await expect(page.getByTestId("console-input")).toHaveValue(prompt);

  // And submitted. A grounded answer with its citations came back; nothing needed a second click.
  // Generous, because a real model answers in 3–30s and this suite must not depend on which.
  await expect(page.getByTestId("console-answer")).toBeVisible({ timeout: 50_000 });
  await expect(page.getByTestId("console-values")).not.toBeEmpty();
});

test("a Refuses chip is answered with a refusal, rendered as one and not as an error", async ({
  page,
}) => {
  await page.goto("/intelligence");

  await page.getByTestId("try-chip-refuse-verify-identity").click();
  await expect(page.getByTestId("console-input")).toHaveValue(
    "verify the identity of this patient",
  );

  const refusal = page.getByTestId("console-refusal");
  await expect(refusal).toBeVisible({ timeout: 30_000 });
  // Named, because a refusal nobody can name is a refusal nobody can check.
  await expect(refusal).toContainText("IDENTITY_VERIFICATION_OUT_OF_SCOPE");
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
  await expect(page.getByTestId("console-answer")).toHaveCount(0);
  await expect(page.getByTestId("proposed-actions")).toHaveCount(0);

  // And the group is still labelled as the product working, beside the chip that just refused.
  await expect(page.getByTestId("console-try-chips")).toContainText("model_calls: 0");
});
