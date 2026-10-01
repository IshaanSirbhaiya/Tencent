import { test, expect } from "@playwright/test";

/**
 * The shift sign-in gate — tested WITHOUT the session seeding that `frontdesk.spec.ts` applies.
 *
 * That seeding exists because every route is behind `StaffGate` and a bare `page.goto()` otherwise
 * lands on the sign-in screen. But a bypass applied in every test deletes all coverage of the
 * thing being bypassed: after it was added, nothing in the suite would have failed if `StaffGate`
 * had stopped gating anything at all. This file is the other half of that trade.
 *
 * It also pins the two claims the gate makes on its own face, both of which are governance
 * statements rather than styling:
 *
 *   * it is **not authentication** — there is no directory, no password, and no token the gateway
 *     validates, and `STATUS.md` carries that as an accepted limitation *on condition that we say
 *     it out loud*;
 *   * signing in says WHO is at the terminal and never that a patient's identity was checked.
 */

const GATED_ROUTES = ["/", "/frontdesk", "/intake", "/brain", "/impact", "/evals"];

// One test, one `test.step` per route, rather than a loop that DECLARES six tests. The submission
// writeup quotes the suite size and `test_submission_figures.py` pins that quote to the spec files
// by counting `test(` declarations — a loop makes declarations and runs disagree, and a figure
// that drifts from what it claims to count is the exact failure that test exists to prevent.
test("every route is unreachable without a shift sign-in", async ({ page }) => {
  for (const route of GATED_ROUTES) {
    await test.step(route, async () => {
      await page.goto(route);
      // The sign-in form is what renders instead of the app.
      await expect(page.getByTestId("staff-sign-in")).toBeVisible();
      // And nothing behind the gate leaked through.
      await expect(page.getByTestId("identity-panel")).toHaveCount(0);
      await expect(page.getByTestId("candidate-list")).toHaveCount(0);
    });
  }
});

test("the gate states on its face that it is not authentication", async ({ page }) => {
  await page.goto("/frontdesk");
  await expect(page.getByText(/not authentication/i)).toBeVisible();
});

test("signing in reaches the app and attests to nothing", async ({ page }) => {
  await page.goto("/frontdesk");
  await page.getByTestId("roster-PS-FD-014").click();

  // Through the gate. The chip shows the display NAME; the staff id the ledger keys on is in its
  // title, so both are asserted — a chip that showed the wrong person is how a confirmation gets
  // attributed to someone who was not there.
  await expect(page.getByTestId("staff-sign-in")).toHaveCount(0);
  const chip = page.getByTestId("staff-chip");
  await expect(chip).toContainText("Nurul Aisyah");
  await expect(chip.locator("[title]").first()).toHaveAttribute("title", /PS-FD-014/);

  // The session is who is at the terminal. It must not carry an attestation into storage, because
  // `sessionStorage` survives navigation and would outlive the patient it referred to.
  const stored = await page.evaluate(() =>
    window.sessionStorage.getItem("walnut.staff.session.v1"),
  );
  expect(stored).toBeTruthy();
  const session = JSON.parse(stored as string);
  expect(Object.keys(session).sort()).toEqual(["name", "signedInAt", "staffId"]);
  expect(JSON.stringify(session).toLowerCase()).not.toContain("attest");
  expect(JSON.stringify(session).toLowerCase()).not.toContain("verified");
});

test("a malformed session is treated as signed out, not as a session with an empty id", async ({
  page,
}) => {
  // The ledger's `identity_checked_in_person_by` column exists to name an accountable person. A
  // session that restores with a blank or junk staff id would fill it with nothing.
  await page.addInitScript(() => {
    window.sessionStorage.setItem("walnut.staff.session.v1", JSON.stringify({ staffId: "   " }));
  });
  await page.goto("/frontdesk");
  await expect(page.getByTestId("staff-sign-in")).toBeVisible();
});

test("a session older than the shift does not carry into the next one", async ({ page }) => {
  // A terminal left open overnight must not attribute tomorrow's confirmations to yesterday's
  // staff. The shift window is 12h (`lib/staff-session.tsx`).
  await page.addInitScript(() => {
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: yesterday }),
    );
  });
  await page.goto("/frontdesk");
  await expect(page.getByTestId("staff-sign-in")).toBeVisible();
});
