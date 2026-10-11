import { test, expect, type APIRequestContext } from "@playwright/test";
import {
  ACTIVITY_KINDS,
  DECISION_KINDS,
  INTEGRITY_TEXT,
  KIND_LABEL,
  RUNLESS_KINDS,
  NOT_RECORDED,
  decisionRefusalText,
  envelopeText,
  isEnvelope,
  isPerson,
  actorType,
  ACTOR_TYPE_NOT_RECORDED,
  valueText,
  type Actor,
  DEV_TOKEN_NOTE,
  actorText,
  deciderText,
  coverageWarning,
  decisionText,
  formatDuration,
  formatTokens,
  itemTone,
  mergePages,
  producerText,
  refusalText,
  runIdOf,
  subtaskTone,
  viewFromParam,
  ACTIVITY_VIEWS,
  type ActivityItem,
  type DelegationTree,
  type SubtaskNode,
} from "../lib/activity-api";
import { adaptGatewayTrace, type GatewayTrace, type GatewayTraceStep } from "../lib/trace-adapt";

/**
 * `/trace` — Activity & trace. Live specs run against the REAL gateway and derive every expectation
 * from the gateway's own `/activity` answer; they are skipped when no gateway answers. They are
 * NON-MUTATING: nothing is run, approved or fired.
 *
 * The `@unit` block tests the pure helpers with no gateway and no page. Run it alone with a config
 * that has no webServer and `grep: /@unit/`.
 */
const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000";
const TOKEN = process.env.NEXT_PUBLIC_WALNUT_TOKEN ?? "dev-local-only";
const H = { Authorization: `Bearer ${TOKEN}` };

async function gatewayUp(request: APIRequestContext): Promise<boolean> {
  try {
    return (await request.get(`${GATEWAY}/health`, { timeout: 5_000 })).ok();
  } catch {
    return false;
  }
}

test.describe("activity (live)", () => {
  test.beforeEach(async ({ page, request }) => {
    test.skip(!(await gatewayUp(request)), "no gateway answering");
    await page.addInitScript(() => {
      window.sessionStorage.setItem(
        "walnut.staff.session.v1",
        JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: new Date().toISOString() }),
      );
    });
  });

  test("the sidebar entry is Activity, and a bare /trace still opens the per-request view", async ({ page }) => {
    await page.goto("/trace");
    await expect(page.getByRole("link", { name: "Activity" })).toBeVisible();
    await expect(page.getByTestId("activity-tab-request")).toHaveAttribute("aria-selected", "true");
  });

  test("the timeline shows what GET /activity returns, or its error — never example rows", async ({ page, request }) => {
    const res = await request.get(`${GATEWAY}/activity?limit=50`, { headers: H });
    await page.goto("/trace?view=timeline");
    if (!res.ok()) {
      await expect(page.getByTestId("activity-error")).toBeVisible({ timeout: 30_000 });
      return;
    }
    const body = (await res.json()) as { items: ActivityItem[] };
    if (body.items.length === 0) {
      await expect(page.getByTestId("activity-empty")).toBeVisible({ timeout: 30_000 });
      return;
    }
    const rows = page.getByTestId("activity-row");
    await expect(rows).toHaveCount(body.items.length, { timeout: 30_000 });
    await expect(page.locator('[data-testid="activity-row"][data-loud="true"]')).toHaveCount(
      body.items.filter((i) => i.loud).length,
    );
    await rows.first().click();
    await expect(page.getByTestId("activity-inspector")).toBeVisible();
  });

  test("a delegated question opens its delegation tree with one node per recorded sub-task", async ({ page, request }) => {
    const res = await request.get(`${GATEWAY}/activity?kinds=federated_run&limit=1`, { headers: H });
    test.skip(!res.ok(), "the gateway refused /activity");
    const [fed] = ((await res.json()) as { items: ActivityItem[] }).items;
    test.skip(!fed, "no delegated question visible on this gateway");
    const run = await (await request.get(`${GATEWAY}/activity/runs/${fed.run_id}`, { headers: H })).json();
    await page.goto("/trace?view=timeline");
    await page.getByTestId("activity-kind-federated_run").click();
    await page.getByTestId("activity-row").first().click();
    await expect(page.getByTestId("delegation-tree")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("delegation-subtask")).toHaveCount(run.delegation_tree.subtasks.length);
    await expect(page.getByTestId("delegation-reduced")).toHaveCount(coverageWarning(run.delegation_tree) ? 1 : 0);
  });

  test("automations show each recorded firing", async ({ page, request }) => {
    const res = await request.get(`${GATEWAY}/activity/automations`, { headers: H });
    await page.goto("/trace?view=automations");
    if (!res.ok()) {
      await expect(page.getByTestId("automations-error")).toBeVisible({ timeout: 30_000 });
      return;
    }
    const body = (await res.json()) as { automations: { firings: unknown[] }[] };
    if (!body.automations.length) {
      await expect(page.getByTestId("automations-empty")).toBeVisible({ timeout: 30_000 });
      return;
    }
    await expect(page.getByTestId("automation-firing")).toHaveCount(
      body.automations.reduce((n, s) => n + s.firings.length, 0),
      { timeout: 30_000 },
    );
  });
});

// =============================================================================================
// @unit — pure helpers (no gateway, no page)
// =============================================================================================
const absent = (reason = "not_recorded_by_producer") => ({ status: "absent" as const, reason });
/** The common actor object (R-028 #7) exactly as the gateway serves it. */
const machine = (id: string, type: string): Actor => ({
  principal_id: id, principal_type: type,
  authenticated_by: absent("machine_actor"), dev_only: absent("machine_actor"),
});
// the served shape (E2E run 5 W-09): the gateway serves every person as `person`, under the
// canonical mechanism name
const person = (id: string, devOnly: boolean | null, auth: string | null = "gateway"): Actor => ({
  principal_id: id, principal_type: "person",
  authenticated_by: auth ?? absent(), dev_only: devOnly === null ? absent() : devOnly,
});

const item = (over: Partial<ActivityItem>): ActivityItem =>
  ({
    id: "agent_run:r", kind: "agent_run", at: null, sort_key: "", cursor: "", title: "t", status: null,
    actor: machine("a", "agent"), produced_by: null, models_used: null, tokens: null,
    duration_ms: null, loud: false, ...over,
  }) as ActivityItem;

const sub = (status: SubtaskNode["status"], agent = "crm_agent"): SubtaskNode => ({
  id: agent, adapter_agent: agent, connection: null, connection_basis: "not recorded", request: {},
  decision: null, method: null, pushed_down: null, query: null, delegate_note: null,
  rows_returned: null, rows_basis: "", pointers_recorded: 0, skipped: [], status, ms: null,
  produced_by: null,
});

test.describe("@unit activity helpers", () => {
  test("FIN-16 a decision made with the dev token is marked, a real one is not", () => {
    expect(deciderText("analyst-7", true)).toContain(DEV_TOKEN_NOTE);
    expect(deciderText("analyst-7", false)).toBe("analyst-7");
    expect(deciderText(null, true)).toBe(NOT_RECORDED);
    expect(actorText(person("analyst-7", true))).toContain(DEV_TOKEN_NOTE);
    expect(actorText(person("analyst-7", false))).toBe("person analyst-7");
  });

  test("R-028 #7 the actor is read from the structured object, never from a decorated id", () => {
    // a sign-in the producer did not record is said as such — never shown as a real sign-in
    const unrecorded = actorText(person("analyst-7", null, null));
    expect(unrecorded).toContain("analyst-7");
    expect(unrecorded).toContain("not recorded");
    expect(unrecorded).not.toContain(DEV_TOKEN_NOTE);
    // machines are named as machines; authentication does not apply to them
    expect(actorText(machine("keeper-crm", "adapter_agent"))).toBe("adapter agent keeper-crm");
    expect(actorText(machine("schedule", "automation"))).toBe("automation (schedule)");
    expect(isPerson(machine("x", "agent"))).toBe(false);
    expect(isPerson(person("x", false))).toBe(true);
    // a delegated decision says so
    expect(actorText({ ...person("lead-2", false), delegation: { agent_id: "keeper" } })).toContain("delegation");
    // an id the producer did not record is "not recorded", never an empty name
    expect(actorText({ ...person("x", false), principal_id: absent() })).toContain(NOT_RECORDED);
  });

  test("R-028 #12 a withheld or absent value is the envelope, said in words", () => {
    const w = { status: "withheld" as const, reason: "free_text" };
    expect(isEnvelope(w)).toBe(true);
    expect(isEnvelope({ status: "approved" })).toBe(false);
    expect(isEnvelope(null)).toBe(false);
    expect(envelopeText(w)).toMatch(/^withheld/);
    expect(envelopeText(absent())).toBe(NOT_RECORDED);
    expect(valueText(w)).toBe(envelopeText(w));
    expect(valueText(null)).toBe(NOT_RECORDED);
    expect(valueText("open")).toBe("open");
    // an unknown reason code is still said, never dropped
    expect(envelopeText({ status: "withheld", reason: "some_new_code" })).toContain("some_new_code");
    // the check is the shared strict one: an object that merely has a status is not an envelope
    expect(isEnvelope({ status: "withheld", reason: "x", extra: 1 })).toBe(false);
  });

  test("R-028 #8 every kind has its own label; decision rows say what was verified", () => {
    const labels = ACTIVITY_KINDS.map((k) => KIND_LABEL[k]);
    expect(labels.every(Boolean)).toBe(true);
    expect(new Set(labels).size).toBe(ACTIVITY_KINDS.length);
    expect(KIND_LABEL.adapter_setup).not.toBe(KIND_LABEL.adapter_sync);
    expect(KIND_LABEL.one_time_task).not.toBe(KIND_LABEL.agent_run);
    for (const k of DECISION_KINDS) expect(ACTIVITY_KINDS).toContain(k);
    expect(INTEGRITY_TEXT.failed).toContain("does not verify");
    expect(decisionRefusalText({ code: "LEDGER_INTEGRITY_FAILED", message: "m" })).toBe("LEDGER_INTEGRITY_FAILED: m");
  });

  test("a missing measurement is 'not recorded', never zero", () => {
    expect(formatDuration(null)).toBe(NOT_RECORDED);
    expect(formatDuration(undefined)).toBe(NOT_RECORDED);
    expect(formatTokens(null)).toBe(NOT_RECORDED);
    expect(formatDuration(0)).toBe("0 ms");                      // a MEASURED zero is still shown
    expect(formatTokens(0)).toContain("0");
    expect(producerText(null)).toBe(NOT_RECORDED);
    expect(actorText(null)).toBe(NOT_RECORDED);
    expect(decisionText(undefined)).toBe(NOT_RECORDED);
  });

  test("the producer names the model it recorded, and never invents one for a rules step", () => {
    expect(producerText({ agent: "followup", model: "m/x" })).toBe("followup · m/x");
    expect(producerText({ by: "rules" })).toBe("rules (no model)");
    expect(producerText({ agent: "planner" })).toContain(NOT_RECORDED);
    expect(producerText({ agent: "planner" })).not.toContain("rules");
  });

  test("a failure is bad whatever the loud flag says; a loud row is at least a warning", () => {
    for (const s of ["failed", "refused", "execution_failed", "dead_lettered"]) {
      expect(itemTone(item({ status: s, loud: false }))).toBe("bad");
    }
    expect(itemTone(item({ status: "completed", loud: true }))).toBe("warn");
    expect(itemTone(item({ status: "completed", loud: false }))).toBe("good");
    expect(itemTone(item({ status: null, loud: false }))).toBe("neutral");
  });

  test("a delegation tree warns loudly for every sub-task that did not answer, and only then", () => {
    const clean = { subtasks: [sub("answered"), sub("answered", "docs")], combine: null,
      coverage: { searched: [], skipped: [], reasons: [], reduced: false } } as Pick<DelegationTree, "subtasks" | "coverage" | "combine">;
    expect(coverageWarning(clean)).toBeNull();
    const bad = { ...clean, subtasks: [sub("answered"), sub("refused", "docs")] };
    const w = coverageWarning(bad)!;
    expect(w).toContain("1 of 2");
    expect(w).toContain("docs refused");
    const reducedOnly = { ...clean, coverage: { ...clean.coverage, reduced: true } };
    expect(coverageWarning(reducedOnly)).not.toBeNull();
    for (const s of ["answered", "reduced", "refused", "failed"] as const) {
      expect(subtaskTone(s)).toBe(s === "answered" ? "good" : s === "reduced" ? "warn" : "bad");
    }
  });

  test("merging pages keeps newest first and never repeats a row", () => {
    const a = item({ id: "x:1", sort_key: "2026-01-03" });
    const b = item({ id: "x:2", sort_key: "2026-01-02" });
    const c = item({ id: "x:3", sort_key: "2026-01-01" });
    const merged = mergePages([a, b], [b, c]);
    expect(merged.map((i) => i.id)).toEqual(["x:1", "x:2", "x:3"]);
  });

  test("an ontology proposal or a decision opens no run; every other kind opens the run it names", () => {
    for (const k of ACTIVITY_KINDS) {
      const got = runIdOf(item({ kind: k, run_id: "r1" }));
      expect(got).toBe(RUNLESS_KINDS.includes(k) ? null : "r1");
    }
    expect(RUNLESS_KINDS).toContain("plan_approval");
    expect(RUNLESS_KINDS).not.toContain("one_time_task");      // a one-time task's run opens
  });

  test("an unknown or missing view falls back to the per-request view", () => {
    for (const v of ACTIVITY_VIEWS) expect(viewFromParam(v)).toBe(v);
    expect(viewFromParam(null)).toBe("request");
    expect(viewFromParam("nope")).toBe("request");
  });

  test("a refusal names its code; a 401 says the credential was refused", () => {
    expect(refusalText(404, { refusal: { code: "UNKNOWN_RUN", message: "m" } })).toBe("UNKNOWN_RUN: m");
    expect(refusalText(401, {})).toContain("credential");
    expect(refusalText(500, null)).toBe("the gateway answered 500");
  });
});

// Codex f1 #3 / #4 — the per-request projection never fills a gap with 0, "absent" or "covered".
const step = (seq: number, kind: string, detail: Record<string, unknown>): GatewayTraceStep => ({
  seq, at: "", kind, summary: kind, detail,
});
const gwTrace = (steps: GatewayTraceStep[], answer: Record<string, unknown> = {}): GatewayTrace => ({
  request_id: "req-000001-abcd1234", surface: "s", agent: "a", version: "v1", started_at: "",
  ended_at: null, outcome: "answered", principal: null, steps: steps.length, grounded: 0,
  withheld: 0, absent: 0, escalations: 0,
  integrity: { complete: true, defect_count: 0, defect_codes: [] },
  step_list: steps, defect_list: [],
  view: { request: {}, branches: [], results: [], answer },
});

test.describe("@unit per-request trace projection", () => {
  test("pointer, verdict and cell messages carry no invented rows / cells / capture counts", () => {
    const t = adaptGatewayTrace(gwTrace([
      step(1, "capability", { source_id: "src", capability: "read" }),
      step(2, "pointer", { origin: "live_source", source_id: "src" }),
      step(3, "verdict", { verdict: "verified", source_id: "src" }),
      step(4, "cell", { state: "grounded", source_id: "src", field_path: "a.b" }),
    ]), "e");
    const kinds = new Set(["pointer", "verify", "grounded"]);
    const checked = (t.edges ?? []).filter((e) => [...kinds].some((k) => e.label.includes(k)));
    expect(checked.length).toBe(kinds.size);                     // non-vacuous
    for (const e of checked) expect(e.detail.pulled).toBeNull();
  });

  test("citations the recorder did not report are derived from released steps, else unknown — never 0", () => {
    const released = [step(1, "released", { field_path: "f", source_id: "s", ledger_covered: false, covered_by: null })];
    expect(adaptGatewayTrace(gwTrace(released, { citations: 7 }), "e").answer.citations).toBe(7);
    expect(adaptGatewayTrace(gwTrace(released), "e").answer.citations).toBe(released.length);
    const noSteps = { ...gwTrace([]), step_list: undefined } as unknown as GatewayTrace;
    expect(Number.isNaN(adaptGatewayTrace(noSteps, "e").answer.citations)).toBe(true);
  });

  test("a cell with no recorded state is not drawn as absent", () => {
    const t = adaptGatewayTrace(gwTrace([
      step(1, "cell", { state: "grounded", field_path: "a" }),
      step(2, "cell", { state: null, field_path: "b" }),
    ]), "e");
    expect((t.cells ?? []).map((c) => c.path)).toEqual(["a"]);
    expect(t.cells_note).toContain("1 cell recorded with no valid state");
  });

  test("a release is certified only when the recorder names the covering ledger entry", () => {
    const edgeFor = (detail: Record<string, unknown>) =>
      (adaptGatewayTrace(gwTrace([step(1, "released", { field_path: "f", source_id: "s", ...detail })]), "e").edges ?? [])
        .find((e) => e.kind === "answer");
    for (const d of [{}, { ledger_covered: true }, { ledger_covered: false, covered_by: null }]) {
      expect(edgeFor(d)?.detail.decided?.reason_code).toBe("LEDGER_APPEND_NOT_RECORDED");
    }
    const ok = edgeFor({ ledger_covered: true, covered_by: "e-1" });
    expect(ok?.detail.decided).toBeNull();
    expect(ok?.detail.notes.join(" ")).toContain("e-1");
  });
});

test.describe("@unit activity nulls (Codex f1 #3)", () => {
  test("coverage the record did not carry is neither 'reduced' nor 'not reduced'", () => {
    const unrecorded = { subtasks: [sub("answered")], combine: null,
      coverage: { searched: null, skipped: null, reasons: null, reduced: null } } as Pick<DelegationTree, "subtasks" | "coverage" | "combine">;
    expect(coverageWarning(unrecorded)).toBeNull();
    expect(actorText(null)).toBe(NOT_RECORDED);                 // a starter nobody recorded
  });
});

test.describe("@unit E2E run 5 F-12 decision families", () => {
  test("the page's kind vocabulary is the gateway's, hold lifts and identity rules included", async () => {
    // derived from the gateway's own source, not written down twice
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const py = readFileSync(
      join(__dirname, "..", "..", "gateway", "databridge_gateway", "activity_routes.py"),
      "utf-8",
    );
    const tuple = (name: string) => {
      const m = py.match(new RegExp(`^${name} = \(([^)]*)\)`, "m"));
      expect(m, name).toBeTruthy();
      return Array.from((m as RegExpMatchArray)[1].matchAll(/"([a-z_]+)"/g), (x) => x[1]);
    };
    const gatewayDecisions = tuple("DECISION_KINDS");
    expect(gatewayDecisions.length).toBeGreaterThan(0);                      // non-vacuous
    expect([...DECISION_KINDS]).toEqual(gatewayDecisions);
    expect(gatewayDecisions).toEqual(expect.arrayContaining(["screen_override", "identity_rule_decision"]));
    for (const k of gatewayDecisions) {
      expect(KIND_LABEL[k as keyof typeof KIND_LABEL]).toBeTruthy();
      expect(RUNLESS_KINDS).toContain(k);                                    // a decision opens no run
    }
    // every integrity value the decision families serve is worded, and the override log's
    // (sealed + chained, not anchored) is never worded as anchored
    const integrity = Array.from(
      readFileSync(
        join(__dirname, "..", "..", "gateway", "databridge_gateway", "activity_decisions.py"),
        "utf-8",
      ).matchAll(/^[A-Z_]+ = "([a-z_]+)"\s+#/gm),
      (x) => x[1],
    );
    expect(integrity).toContain("sealed_and_chained");
    for (const v of integrity) expect(INTEGRITY_TEXT[v], v).toBeTruthy();
    expect(INTEGRITY_TEXT.sealed_and_chained).not.toContain("anchored —");
  });
});

test.describe("@unit R-029 Codex f1 #3 an unrecorded actor type is not a person", () => {
  test("absent, withheld or unknown types are null and said as not recorded", () => {
    const base = { principal_id: "x", authenticated_by: absent(), dev_only: absent() };
    for (const t of [absent(), { status: "withheld", reason: "not_in_allow_list" }, "Approver", ""]) {
      const a = { ...base, principal_type: t } as Actor;
      expect(actorType(a)).toBeNull();
      expect(isPerson(a)).toBe(false);
      expect(actorText(a)).toContain(ACTOR_TYPE_NOT_RECORDED);
      expect(actorText(a)).not.toMatch(/^person /);
    }
    expect(actorType(person("p", false))).toBe("person");                // non-vacuous
    expect(actorType(machine("m", "agent"))).toBe("agent");
  });
});
