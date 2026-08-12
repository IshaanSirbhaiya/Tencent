import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// The front-desk walkthrough, asserted end-to-end against the real gateway + frontend.
//
// The test PLAN.md §5 Phase 4 asks for, verbatim: upload chit 2 → the summary card renders →
// confirm is disabled until the attestation box is ticked → after confirm, the ledger view shows
// the committed fields. Plus the two negative cases that matter more than the happy path: an
// ambiguous document must not let you skip the choice, and the assistant must refuse an identity
// question.

const FIXTURES = join(
  __dirname, "..", "..", "..", "fixtures", "hackathon", "chits",
);

function chit(name: string): string {
  return readFileSync(join(FIXTURES, name), "utf-8");
}

// `intake_id` is deterministic in `document_id + sha256(text)`, the gateway holds intake state in
// memory, and `playwright.config.ts` sets `reuseExistingServer`. A static document id therefore
// lands on an ALREADY-COMMITTED intake on the second run — the attestation controls are gone and
// the happy-path test times out. One id per run keeps the suite re-runnable.
const RUN = `${Date.now()}`;

// Every route in this app is behind `StaffGate`, so a bare `page.goto()` lands on the shift
// sign-in screen and every assertion below times out. Seed the session BEFORE page scripts run,
// on every navigation — `addInitScript` rather than an `evaluate` after `goto`, because the gate
// reads `sessionStorage` during the first render.
//
// Bypassing a gate in every test silently deletes all coverage OF that gate, so the gate itself is
// tested in `e2e/staff-gate.spec.ts`, which deliberately does not seed anything. If you add a
// third spec file, it needs this block or that one's assumption.
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

/** Upload a document through the real UI and land on its card. */
async function upload(page: import("@playwright/test").Page, file: string, suffix: string) {
  await page.goto("/intake");
  await page.getByTestId("document-id").fill(`${suffix}-${RUN}__${file}`);
  await page.getByTestId("document-text").fill(chit(file));
  await page.getByTestId("upload-document").click();
  await page.waitForURL(/\/frontdesk\/.+/);
}

test("chit 2: card renders, confirm is gated on the attestation, ledger shows the commit", async ({
  page,
}) => {
  await upload(page, "chit_02_everwell_voucher.txt", "e2e-happy");

  // 1. The card renders, and identity leads it.
  await expect(page.getByTestId("identity-panel")).toBeVisible();
  await expect(page.getByText(/verify in person/i)).toBeVisible();
  await expect(page.getByTestId("candidate-list").locator("li")).toHaveCount(1);

  // 2. Confirm is DISABLED before anything is attested — the gate, from the outside.
  await expect(page.getByTestId("confirm-intake")).toBeDisabled();

  // 3. Selecting is disabled until the attestation box is ticked, even with a candidate clicked.
  await page.getByTestId("candidate-list").locator("button").first().click();
  await page.getByTestId("staff-id").fill("staff:e2e");
  await expect(page.getByTestId("select-patient")).toBeDisabled();

  // 4. Tick the attestation → selection becomes possible.
  await page.getByTestId("attestation-checkbox").check();
  await expect(page.getByTestId("select-patient")).toBeEnabled();
  await page.getByTestId("select-patient").click();

  // 5. The gate opens, but confirm still needs its OWN attestation — the request must carry a
  //    real human act, not a hardcoded `true`.
  await expect(page.getByTestId("confirm-attestation")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("confirm-intake")).toBeDisabled();
  await page.getByTestId("confirm-attestation").check();
  await expect(page.getByTestId("confirm-intake")).toBeEnabled();
  await page.getByTestId("confirm-intake").click();

  // 6. Committed, and the ledger holds the fields.
  await expect(page.getByText(/committed to the ledger/i)).toBeVisible({ timeout: 10_000 });
  const intakeId = page.url().split("/frontdesk/")[1];
  const ledger = await page.request.get(
    `http://127.0.0.1:8000/intake/${intakeId}/ledger`,
  );
  const body = await ledger.json();
  expect(body.rows.length).toBeGreaterThan(0);
  for (const row of body.rows) {
    expect(row.confirmed_by).toBe("staff:e2e");
    expect(row.pointer.source_id).toBeTruthy();
    expect(row.selected_patient_id).toBeTruthy();
  }
});

test("chit 3: an ambiguous document shows both candidates and pre-selects neither", async ({
  page,
}) => {
  await upload(page, "chit_03_everwell_group_invitation.txt", "e2e-ambiguous");

  const candidates = page.getByTestId("candidate-list").locator("li");
  await expect(candidates).toHaveCount(2);
  // Neither is chosen for the user — the machine does not pick between two people.
  await expect(page.getByTestId("select-patient")).toBeDisabled();
  await expect(page.getByTestId("confirm-intake")).toBeDisabled();
  await expect(page.getByText(/staff must select/i)).toBeVisible();
});

test("an attestation does not survive a change of candidate", async ({ page }) => {
  // CLAUDE.md §Hard constraints 1, and the one thing the source-scanning guard tests could not
  // see. Review found this shape on the confirm button: `attested` was a bare boolean reset only
  // when the INTAKE changed, and picking a different candidate does not change the intake id — so
  // staff could attest for candidate A, click candidate B, and submit B carrying an attestation
  // nobody ever made about B.
  //
  // Chit 3 carries no NRIC and matches two registry rows, which is exactly the situation where a
  // hurried operator clicks the other name.
  await upload(page, "chit_03_everwell_group_invitation.txt", "e2e-attest-swap");

  const candidates = page.getByTestId("candidate-list").locator("button");
  await expect(candidates).toHaveCount(2);
  await page.getByTestId("staff-id").fill("staff:e2e");

  // Attest for the first candidate.
  await candidates.nth(0).click();
  await page.getByTestId("attestation-checkbox").check();
  await expect(page.getByTestId("attestation-checkbox")).toBeChecked();
  await expect(page.getByTestId("select-patient")).toBeEnabled();

  // Now pick the OTHER person. The attestation was about someone else.
  await candidates.nth(1).click();
  await expect(page.getByTestId("attestation-checkbox")).not.toBeChecked();
  await expect(page.getByTestId("select-patient")).toBeDisabled();
  // And say why, rather than clearing it silently — an operator who thinks the click failed will
  // simply re-tick by reflex, which is how this stayed invisible.
  await expect(page.getByText(/attestation was cleared/i)).toBeVisible();

  // Re-attesting for the person actually selected is what re-enables it.
  await page.getByTestId("attestation-checkbox").check();
  await expect(page.getByTestId("select-patient")).toBeEnabled();
});

test("the attestation cannot be ticked before a candidate is chosen", async ({ page }) => {
  // "I checked this patient's ID" is a claim about a specific registry record. With no record
  // selected there is nothing for it to be about, so the box is not tickable.
  await upload(page, "chit_03_everwell_group_invitation.txt", "e2e-attest-order");

  await expect(page.getByTestId("attestation-checkbox")).toBeDisabled();
  await expect(page.getByTestId("attestation-hint")).toContainText(/choose the registry record/i);
});

test("chit 8: an undetermined package is stated as such, never guessed", async ({ page }) => {
  await upload(page, "chit_08_mol_authorisation_vital.txt", "e2e-undetermined");

  const stated = page.getByTestId("coverage-undetermined");
  await expect(stated).toBeVisible({ timeout: 10_000 });
  await expect(stated).toContainText(/not guessed/i);
});

test("chit 5: an age-band conflict is flagged before the patient is even identified", async ({
  page,
}) => {
  await upload(page, "chit_05_bluepeak_wellness_voucher.txt", "e2e-conflict");

  await expect(page.getByTestId("eligibility-flags")).toContainText(/age outside package band/i);
  await expect(page.getByTestId("candidate-list").locator("li")).toHaveCount(2);
});

test("the assistant refuses identity questions and answers clinic ones", async ({ page }) => {
  await page.goto("/frontdesk");

  const refused = await page.request.post("http://127.0.0.1:8000/brain/query", {
    data: { question: "Is this the right person?" },
  });
  expect(refused.status()).toBe(422);
  expect((await refused.json()).refusal.code).toBe("IDENTITY_VERIFICATION_OUT_OF_SCOPE");

  const answered = await page.request.post("http://127.0.0.1:8000/brain/query", {
    data: { question: "What does the WELL2 package include?" },
  });
  expect(answered.status()).toBe(200);
  expect((await answered.json()).answer).toContain("WELL2");
});

test("a fixture button loads the real corpus text in one click", async ({ page }) => {
  await page.goto("/intake");
  await page.getByTestId("fixture-chit_01_meridian_referral.txt").click();
  // The button must actually fill the textarea — the first draft only set the document id, so it
  // appeared to do nothing and left "Read document" disabled.
  await expect(page.getByTestId("document-text")).toContainText("Meridian Life Assurance", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("upload-document")).toBeEnabled();
});

test("a walk-in with no registry match explains itself instead of showing a dead gate", async ({
  page,
}) => {
  await page.goto("/intake");
  await page.getByTestId("document-id").fill(`walkin-${RUN}.txt`);
  await page.getByTestId("document-text").fill("Walk-in patient, self-pay, no referring document.");
  await page.getByTestId("upload-document").click();
  await page.waitForURL(/\/frontdesk\/.+/);
  await expect(page.getByTestId("no-candidate-path")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("confirm-intake")).toHaveCount(0);
});

test("a graph-derived conflict is shown, and labelled as not coming from this document", async ({
  page,
}) => {
  // Phase 6's success criterion, end to end. Chit 1 says nothing about Northstar — only the clinic
  // graph knows the same patient also has a Northstar referral on file. The flag must reach the
  // browser AND be attributed to the clinic record rather than to the letter in the reader's hand.
  await upload(page, "chit_01_meridian_referral.txt", "e2e-conflict-graph");

  const findings = page.getByTestId("history-findings");
  await expect(findings).toBeVisible({ timeout: 10_000 });
  await expect(findings).toContainText(/from the clinic record, not this document/i);
  await expect(findings).toContainText("chit_10_northstar_second_referral.txt");
  await expect(findings).toContainText(/source(s)? cited/);
  await expect(page.getByTestId("eligibility-flags")).toContainText(/conflicting document on file/i);
});

test("the evidence click-through shows the exact source text", async ({ page }) => {
  await upload(page, "chit_01_meridian_referral.txt", "e2e-evidence");

  await page.getByTestId("evidence-issuer.issuer_code").click();
  // The rendered value is the exact source slice the pointer names — assert on the element, not on
  // quote characters the component happens to wrap it in.
  await expect(page.getByTestId("evidence-text-issuer.issuer_code")).toHaveText("MRDEB", {
    timeout: 10_000,
  });
});

test("/impact renders numbers computed from real intakes, not literals", async ({ page }) => {
  // Read a document first, then assert the page reflects IT — a dashboard whose headline is
  // hardcoded looks identical in a screenshot, which is why this asserts movement rather than
  // presence.
  await upload(page, "chit_02_everwell_voucher.txt", "e2e-impact");
  await page.goto("/impact");

  const identityRow = page.getByTestId("impact-step-2");
  await expect(identityRow).toContainText(/never automated/i);
  await expect(identityRow).toContainText("0.0 min");

  // A step that DID save shows a non-zero figure, so the zero above is a decision and not a
  // page-wide failure to compute anything.
  await expect(page.getByTestId("impact-step-4")).not.toContainText("0.0 min");
});

test("airgap: the front-desk flow contacts no external origin", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (!["127.0.0.1", "localhost"].includes(u.hostname)) external.push(r.url());
  });
  for (const route of ["/frontdesk", "/intake", "/brain", "/impact", "/evals"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
  }
  expect(external).toEqual([]);
});
