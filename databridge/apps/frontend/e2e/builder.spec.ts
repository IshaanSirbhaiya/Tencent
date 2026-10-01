import { test, expect, type APIRequestContext } from "@playwright/test";

/**
 * `/builder` — agents and adapter agents from plain English (Roadmap C2 + C3).
 *
 * Runs against the REAL gateway. Every expectation is DERIVED from the gateway's own response to
 * the same request the page makes — nothing here hard-codes what the compiler will say, how many
 * agents exist, or how many actions are pending. The specs are NON-MUTATING: they compile (drafts
 * are held in memory only) but never save, activate, run, approve or forward.
 *
 * Needs a build with NEXT_PUBLIC_WALNUT_TOKEN set and a gateway with auth enabled (the launcher does
 * both): every route here requires an authenticated principal.
 */

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8000";
const TOKEN = process.env.NEXT_PUBLIC_WALNUT_TOKEN ?? "dev-local-only";
const H = { Authorization: `Bearer ${TOKEN}` };

async function get<T>(request: APIRequestContext, path: string): Promise<T> {
  const res = await request.get(`${GATEWAY}${path}`, { headers: H });
  expect(res.ok(), `${path} → ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

async function compile(request: APIRequestContext, text: string) {
  const res = await request.post(`${GATEWAY}/agents/compile`, {
    headers: { ...H, "Content-Type": "application/json" },
    data: { text, answers: {} },
  });
  expect(res.ok()).toBeTruthy();
  return res.json();
}

const MODE_WORDS: Record<string, string> = {
  human_required: "asks you first",
  human_notified: "tells you after",
  autonomous: "runs on its own",
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.sessionStorage.setItem(
      "walnut.staff.session.v1",
      JSON.stringify({ staffId: "PS-FD-014", name: "Nurul Aisyah", signedInAt: new Date().toISOString() }),
    );
  });
});

test("/agents redirects to the Agents tab of the one Builder", async ({ page }) => {
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/builder\?tab=agents/);
  await expect(page.getByTestId("builder-tab-agents")).toHaveAttribute("aria-selected", "true");
});

test("an example chip fills the box and runs nothing; compiling shows exactly what the gateway understood", async ({
  page,
  request,
}) => {
  await page.goto("/builder");
  const box = page.getByTestId("agent-composer-box-text");
  await expect(box).toHaveValue("");

  // Each example is a placeholder the user picks — picking it compiles nothing.
  for (const i of [0, 1]) {
    await page.getByTestId(`agent-composer-box-example-${i}`).click();
    await expect(box).not.toHaveValue("");
    await expect(page.getByTestId("draft-card")).toHaveCount(0);
  }

  for (const i of [0, 1]) {
    await page.getByTestId(`agent-composer-box-example-${i}`).click();
    const text = await box.inputValue();
    const expected = await compile(request, text);

    await page.getByTestId("agent-composer-box-submit").click();
    const card = page.getByTestId("draft-card");
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-kind", expected.kind);
    await expect(page.getByTestId("draft-kind")).toHaveText(expected.kind === "adapter_agent" ? "adapter agent" : "agent");

    // One inline control per open question, and nothing more.
    const qs = expected.open_questions as { id: string }[];
    if (qs.length) {
      await expect(page.getByTestId("open-questions")).toBeVisible();
      for (const q of qs) await expect(page.getByTestId(`question-${q.id}`)).toBeVisible();
    } else {
      await expect(page.getByTestId("open-questions")).toHaveCount(0);
    }

    // Mode and each write action's approval rule, in words, from the spec the gateway built.
    if (expected.spec && expected.spec.spec === "workflow/v1") {
      await expect(page.getByTestId("understood-mode-value")).toHaveText(
        expected.spec.mode === "read_only" ? "read-only" : "read-write",
      );
      for (const a of expected.spec.actions as { name: string; automation_mode: string }[]) {
        await expect(page.getByTestId(`understood-action-${a.name}-mode`)).toContainText(MODE_WORDS[a.automation_mode]);
      }
      await expect(page.getByTestId("understood-trigger")).toContainText(
        expected.spec.trigger.type === "manual" ? "on demand" : "automation",
      );
    }

    // Activate is enabled exactly when the gateway says the draft is activatable; otherwise the
    // reasons are shown beside the disabled button.
    const activate = page.getByTestId("draft-activate");
    if (expected.activatable) {
      await expect(activate).toBeEnabled();
    } else {
      await expect(activate).toBeDisabled();
      await expect(page.getByTestId("draft-not-activatable")).toBeVisible();
    }
    // Save as draft needs a spec.
    if (expected.spec) await expect(page.getByTestId("draft-save")).toBeEnabled();
    else await expect(page.getByTestId("draft-save")).toBeDisabled();

    // The derivation list has one row per derivation the gateway recorded.
    await page.getByTestId("draft-derivations").getByRole("button").click();
    await expect(page.getByTestId("draft-derivations").locator("tbody tr")).toHaveCount(
      (expected.derivations as unknown[]).length,
    );
  }
});

test("the agents list and the approval inbox show what the gateway holds", async ({ page, request }) => {
  const agents = await get<{ agents: { agent: string; status: string }[] }>(request, "/agents");
  const approvals = await get<{ pending: { run_id: string; action_id: string }[] }>(request, "/agents/approvals");

  await page.goto("/builder");
  await expect(page.getByTestId("approvals-count")).toHaveText(String(approvals.pending.length));
  if (approvals.pending.length === 0) {
    await expect(page.getByTestId("approvals-empty")).toBeVisible();
  }
  for (const a of approvals.pending) {
    const card = page.getByTestId(`approval-${a.run_id}-${a.action_id}`);
    await expect(card).toBeVisible();
    await expect(page.getByTestId(`approval-${a.run_id}-${a.action_id}-args`)).toBeVisible();
  }

  if (agents.agents.length === 0) {
    await expect(page.getByTestId("agents-empty")).toBeVisible();
  } else {
    await expect(page.getByTestId("agents-table").locator("tbody tr")).toHaveCount(agents.agents.length);
    for (const a of agents.agents) {
      await expect(page.getByTestId(`agent-${a.agent}-status`)).toHaveText(a.status);
    }
    // Opening one shows its detail; run-now is offered only to an ACTIVE agent.
    const first = agents.agents[0];
    const res = await request.get(`${GATEWAY}/agents/${first.agent}`, { headers: H });
    const body = await res.json();
    await page.getByTestId(`agent-row-${first.agent}`).click();
    await expect(page.getByTestId("agent-detail")).toHaveAttribute("data-agent", first.agent);
    if (body.refusal) {
      // The gateway's refusal is shown verbatim, never replaced with a guess at the agent.
      await expect(page.getByTestId("agent-detail-error")).toContainText(body.refusal.code);
    } else if (first.status === "active") {
      await expect(page.getByTestId("run-now-button")).toBeVisible();
    } else {
      await expect(page.getByTestId("run-now-unavailable")).toBeVisible();
    }
  }
});

test("a recorded run renders its cited answer, flags and steps", async ({ page, request }) => {
  const agents = await get<{ agents: { agent: string }[] }>(request, "/agents");
  let target: { agent: string; run: string } | null = null;
  for (const a of agents.agents) {
    const runs = await get<{ runs: { run_id: string; status: string }[] }>(request, `/agents/${a.agent}/runs`);
    const readable = runs.runs.filter((r) => r.status !== "unreadable");
    if (readable.length) {
      target = { agent: a.agent, run: readable[readable.length - 1].run_id };
      break;
    }
  }
  test.skip(!target, "no agent has a recorded run on this gateway");
  const { record } = await get<{
    record: {
      steps: unknown[];
      coverage: { skipped: unknown[] };
      answer: { unsupported_claims: unknown[]; citations: unknown[] };
    };
  }>(request, `/agents/${target!.agent}/runs/${target!.run}`);

  await page.goto(`/builder?agent=${encodeURIComponent(target!.agent)}&run=${encodeURIComponent(target!.run)}`);
  await expect(page.getByTestId("run-view")).toHaveAttribute("data-run-id", target!.run);
  await expect(page.getByTestId("run-steps").locator("ol > li")).toHaveCount(record.steps.length);
  await expect(page.getByTestId("run-reduced-coverage")).toHaveCount(record.coverage.skipped.length ? 1 : 0);
  await expect(page.getByTestId("run-unsupported")).toHaveCount(record.answer.unsupported_claims.length ? 1 : 0);
  if (record.answer.citations.length) {
    const chip = page.locator("[data-testid^='citation-p']").first();
    await chip.click();
    await expect(page.locator("[data-testid^='citation-detail-']")).toBeVisible();
  }
});

test("adapter agents: list, health and proposals come from the gateway; drift approvals say they are not routed", async ({
  page,
  request,
}) => {
  const list = await get<{ adapter_agents: { spec: { id: string }; deleted: boolean; overall: string }[] }>(
    request,
    "/adapter-agents",
  );
  const proposals = await get<{ proposals: { proposal_id: string }[] }>(request, "/adapter-agents/proposals");
  const live = list.adapter_agents.filter((r) => !r.deleted);

  await page.goto("/builder?tab=adapter");
  await expect(page.getByTestId("builder-tab-adapter")).toHaveAttribute("aria-selected", "true");
  // The adapter example chip fills the box without compiling.
  await page.getByTestId("adapter-composer-box-example-0").click();
  await expect(page.getByTestId("adapter-composer-box-text")).not.toHaveValue("");
  await expect(page.getByTestId("draft-card")).toHaveCount(0);

  if (live.length === 0) {
    await expect(page.getByTestId("adapter-agents-empty")).toBeVisible();
    if (proposals.proposals.length === 0) await expect(page.getByTestId("adapter-proposals-empty")).toBeVisible();
    else
      for (const p of proposals.proposals) await expect(page.getByTestId(`proposal-${p.proposal_id}`)).toBeVisible();
    return;
  }
  await expect(page.getByTestId("adapter-agents-table").locator("tbody tr")).toHaveCount(live.length);
  for (const r of live) await expect(page.getByTestId(`adapter-${r.spec.id}-overall`)).toHaveText(r.overall);

  const first = live[0].spec.id;
  const health = await get<{ overall: string; connections: { connection: string }[] }>(
    request,
    `/adapter-agents/${first}/health`,
  );
  await page.getByTestId(`adapter-row-${first}`).click();
  await expect(page.getByTestId("adapter-detail")).toHaveAttribute("data-adapter", first);
  await expect(page.getByTestId("adapter-health-overall")).toHaveText(health.overall);
  for (const c of health.connections) await expect(page.getByTestId(`health-conn-${c.connection}`)).toBeVisible();
  // INT4: held changes are listed from the gateway's own answer to the same request the page makes.
  const held = await get<{ adaptations: { adaptation_id: string }[] }>(
    request,
    `/adapter-agents/${first}/adaptations?status=pending`,
  );
  if (held.adaptations.length === 0) await expect(page.getByTestId("held-changes-empty")).toBeVisible();
  else for (const a of held.adaptations) await expect(page.getByTestId(`held-${a.adaptation_id}`)).toBeVisible();
});

// =============================================================================================
// @unit — pure decision helpers (no gateway, no page). Codex batch 2, R-019 ui-builder-sources-graph.
// Run alone with a config that has no webServer and `grep: /@unit/`.
// =============================================================================================
import {
  ALWAYS_ASKED_DRIFT,
  adaptationDecisionBody,
  adaptationDecisionState,
  adaptProblems,
  adapterControls,
  approvalBlockers,
  draftActionState,
  jobGate,
  jobStatusWords,
  resolveCitations,
  verifiedFlagLines,
  type AdapterAgentSpec,
  type PendingAction,
  citationRefFromLink,
  freshnessOf,
  isTerminalJobStatus,
  missingAlwaysAsked,
  pairCitations,
  platformFlagCount,
  producerText,
  type ConnectionHealth,
  type PointerJson,
} from "../lib/agents-api";

const ptr = (id: string): PointerJson => ({ source_id: id, kind: "document" });
const gatedSpec = (over: Partial<AdapterAgentSpec> = {}) =>
  ({
    status: "active",
    connections: ["crm"],
    plans: { crm: "approved" },
    adapt: { policy: "ask_all", watch: { on_sync: true, on_list_changed: true }, always_ask: [...ALWAYS_ASKED_DRIFT] },
    ...over,
  }) as Pick<AdapterAgentSpec, "status" | "connections" | "plans" | "adapt">;

test.describe("@unit builder helpers", () => {
  test("a draft or paused adapter agent offers neither Sync now nor a path to active except Resume-from-paused", () => {
    for (const status of ["draft", "active", "paused"] as const) {
      for (const deleted of [false, true]) {
        const c = adapterControls(gatedSpec({ status }), deleted);
        // every gate passes here, so only the lifecycle decides: sync/pause for a live active agent,
        // resume for a paused (never a draft) one
        expect(c.canSync).toBe(!deleted && status === "active");
        expect(c.canPause).toBe(!deleted && status === "active");
        expect(c.canResume).toBe(!deleted && status === "paused");
      }
    }
  });

  test("the always-asked drift kinds are read from the policy, never assumed", () => {
    expect(ALWAYS_ASKED_DRIFT.length).toBeGreaterThan(0);
    expect(missingAlwaysAsked([...ALWAYS_ASKED_DRIFT])).toEqual([]);
    for (const k of ALWAYS_ASKED_DRIFT) {
      const without = ALWAYS_ASKED_DRIFT.filter((x) => x !== k);
      expect(missingAlwaysAsked(without)).toEqual([k]);
    }
  });

  test("citations pair only unique, ascending pN refs of the same count — never a length-only match", () => {
    const md = (refs: string[]) => `- a claim [p1]\n\n**Sources:**\n${refs.map((r) => `- [${r}] somewhere`).join("\n")}\n`;
    const good = pairCitations(md(["p1", "p3"]), [ptr("a"), ptr("b")]);
    expect(good.aligned).toBe(true);
    expect(good.byRef.p3.source_id).toBe("b");
    // same length, but a forged / out-of-order / duplicated ref list
    for (const refs of [["p1", "p1"], ["p3", "p1"], ["p999", "p1"]]) {
      const r = pairCitations(md(refs), [ptr("a"), ptr("b")]);
      expect(r.aligned, refs.join(",")).toBe(false);
      expect(Object.keys(r.byRef)).toEqual([]);
    }
    expect(pairCitations(md(["p1"]), [ptr("a"), ptr("b")]).aligned).toBe(false);
  });

  test("a citation link is trusted only when its text is exactly the ref it points at", () => {
    expect(citationRefFromLink("#cite-p2", "p2")).toBe("p2");
    expect(citationRefFromLink("#cite-p1", "supported")).toBeNull();
    expect(citationRefFromLink("#cite-p1", "p2")).toBeNull();
    expect(citationRefFromLink("https://x.test/#cite-p1", "p1")).toBeNull();
  });

  test("the platform's UNSUPPORTED markers are counted by line shape, so a mismatch with the record is visible", () => {
    const md = "- **UNSUPPORTED** (no_citation): x\n- fine claim with **UNSUPPORTED** bold mid-line [p1]\n";
    expect(platformFlagCount(md, "UNSUPPORTED")).toBe(1);
    expect(platformFlagCount("- plain [p1]", "UNSUPPORTED")).toBe(0);
  });

  test("missing producer metadata reads as not recorded, never as 'no model'", () => {
    expect(producerText(null)).toMatch(/not recorded/);
    expect(producerText(null)).not.toMatch(/no model/);
    expect(producerText({ agent: "a1", model: "m-x" })).toContain("m-x");
  });

  test("polling stops only on a terminal job status", () => {
    for (const s of ["completed", "failed", "refused", "stopped"]) expect(isTerminalJobStatus(s)).toBe(true);
    for (const s of ["running", "queued", "starting", "", null, undefined]) expect(isTerminalJobStatus(s)).toBe(false);
  });

  test("freshness is 'fresh' only for a valid successful timestamp inside the limit", () => {
    const base: ConnectionHealth = {
      connection: "c", runnable: true, skip_reason: null, plan: { spec: null, ingest: null }, last_run: null,
      last_success_at: "2026-10-01T00:00:00Z", age_hours: 1, stale: false, future_timestamp: false,
      consecutive_failures: 0, counts: {}, exclusions_by_rule: {}, incomplete_runs: [], issues: [],
    };
    expect(freshnessOf(base).label).toBe("fresh");
    expect(freshnessOf({ ...base, stale: true }).label).toBe("stale");
    expect(freshnessOf({ ...base, last_success_at: null, age_hours: null }).label).toBe("unknown");
    expect(freshnessOf({ ...base, future_timestamp: true }).label).toBe("invalid");
    const inc = freshnessOf({ ...base, incomplete_runs: ["r1"], skip_reason: "no plan" });
    expect(inc.reasons.join(" ")).toContain("r1");
    expect(inc.reasons.join(" ")).toContain("no plan");
  });
});

// =============================================================================================
// @unit — Codex round 3 (R-019 "Round 3"), FX8b.
// =============================================================================================
test.describe("@unit builder helpers (round 3)", () => {
  const pa = (over: Partial<PendingAction> = {}): PendingAction => ({
    run_id: "r1", action_id: "a1", agent: "x", name: "send_mail", automation_mode: "human_required",
    status: "proposed", tool: "mail.send", args: { to: "a@example.test" }, derived_from: [ptr("crm")],
    reversal: null, proposed_at: null, ungrounded_values: [], history: [], ...over,
  });

  test("R3-B1 an action with an ungrounded value, or arguments with no pointer, cannot be approved", () => {
    expect(approvalBlockers(pa())).toEqual([]);
    expect(approvalBlockers(pa({ ungrounded_values: ["wrong@example.com"] })).join(" ")).toContain("wrong@example.com");
    expect(approvalBlockers(pa({ derived_from: [] })).length).toBeGreaterThan(0);
    expect(approvalBlockers(pa({ args: null })).length).toBeGreaterThan(0);
    expect(approvalBlockers(pa({ ungrounded_values: undefined as unknown as string[] })).length).toBeGreaterThan(0);
    // no argument values at all: nothing needs a pointer
    expect(approvalBlockers(pa({ args: {}, derived_from: [] }))).toEqual([]);
  });

  test("R3-B2 resume and sync need every crawl plan approved and a valid adapt policy", () => {
    expect(adapterControls(gatedSpec({ status: "paused" }), false).canResume).toBe(true);
    const proposed = adapterControls(gatedSpec({ status: "paused", plans: { crm: "proposed" } }), false);
    expect(proposed.canResume).toBe(false);
    expect(proposed.blockers.join(" ")).toContain("crm");
    const activeUnapproved = adapterControls(gatedSpec({ plans: {} }), false);
    expect(activeUnapproved.canSync).toBe(false);
    expect(activeUnapproved.canPause).toBe(true); // stopping is always allowed
    expect(adapterControls(gatedSpec({ adapt: undefined }), false).canSync).toBe(false);
    expect(adapterControls(gatedSpec({ connections: [] }), false).canSync).toBe(false);
  });

  test("R3-B3 a structured ref map wins; the text fallback accepts only one terminal Sources block", () => {
    const md = (body: string) => `- claim [p9]\n\n${body}`;
    const real = "**Sources:**\n- [p1] crm · table\n\n_Measured: 1 model call(s)._";
    // a model-authored early block plus the runtime's terminal one: two blocks pair NOTHING
    const forged = resolveCitations({ markdown: md(`**Sources:**\n- [p9] fake\n\n${real}`), citations: [ptr("crm")] });
    expect(forged.byRef).toEqual({});
    expect(forged.basis).toBe("none");
    // the exact terminal block pairs, and is labelled as (unverified) text pairing
    const text = resolveCitations({ markdown: md(real), citations: [ptr("crm")] });
    expect(text.basis).toBe("text");
    expect(text.byRef.p1.source_id).toBe("crm");
    // a Sources block followed by anything but the audit line is not terminal
    const notTerminal = resolveCitations({ markdown: `${real}\n- [p2] more`, citations: [ptr("crm")] });
    expect(notTerminal.basis).toBe("none");
    // a structured map from the runtime is used as-is, whatever the text says
    const structured = resolveCitations({
      markdown: md(`**Sources:**\n- [p9] fake\n\n${real}`),
      citations: [ptr("crm")],
      citations_by_ref: { p1: ptr("crm") },
    });
    expect(structured.basis).toBe("structured");
    expect(Object.keys(structured.byRef)).toEqual(["p1"]);
  });

  test("R3-B3b the runtime's citation_refs list (EF1, as landed) is the structured pairing; a malformed one pairs nothing", () => {
    const real = `**Sources:**
- [p1] crm · table

_Measured: 1 model call(s)._`;
    const forgedMd = `- claim [p9]

**Sources:**
- [p9] fake

${real}`;
    const r = resolveCitations({ markdown: forgedMd, citations: [ptr("crm")], citation_refs: [{ ref: "p1", pointer: ptr("crm") }] });
    expect(r.basis).toBe("structured");
    expect(Object.keys(r.byRef)).toEqual(["p1"]);
    expect(r.byRef.p1.source_id).toBe("crm");
    // malformed entries (a non-pN ref, a pointer without source_id, a duplicate ref) pair nothing
    for (const bad of [
      [{ ref: "x1", pointer: ptr("crm") }],
      [{ ref: "p1", pointer: { kind: "document" } }],
      [{ ref: "p1", pointer: ptr("crm") }, { ref: "p1", pointer: ptr("erp") }],
    ]) {
      const m = resolveCitations({ markdown: `- claim [p1]

${real}`, citations: [ptr("crm")], citation_refs: bad });
      expect(m.basis).toBe("none");
      expect(m.byRef).toEqual({});
    }
    // an empty list (a record written before EF1 defaults to []) is "absent": text fallback, labelled
    const legacy = resolveCitations({ markdown: `- claim [p1]

${real}`, citations: [ptr("crm")], citation_refs: [] });
    expect(legacy.basis).toBe("text");
  });

  test("R3-B4 a spec with no adapt block is invalid for activation", () => {
    expect(adaptProblems(gatedSpec())).toEqual([]);
    expect(adaptProblems(gatedSpec({ adapt: undefined })).length).toBeGreaterThan(0);
    expect(
      adaptProblems(gatedSpec({ adapt: { policy: "off", watch: { on_sync: true, on_list_changed: false }, always_ask: [] } })).length,
    ).toBeGreaterThan(0);
  });

  test("R3-B5 a draft whose words changed, or that is recompiling, cannot be saved", () => {
    const base = { compiledText: "keep crm", text: "keep crm", compiling: false, saving: false, hasSpec: true, activatable: true };
    expect(draftActionState(base)).toMatchObject({ canSave: true, canActivate: true, stale: false });
    expect(draftActionState({ ...base, text: "keep crm hourly" })).toMatchObject({ canSave: false, canActivate: false, stale: true });
    expect(draftActionState({ ...base, compiling: true })).toMatchObject({ canSave: false, canActivate: false });
    expect(draftActionState({ ...base, saving: true })).toMatchObject({ canSave: false, canActivate: false });
    expect(draftActionState({ ...base, activatable: false })).toMatchObject({ canSave: true, canActivate: false });
  });

  test("R3-B6 a started job blocks another start until a terminal record is confirmed", () => {
    expect(jobGate(null, undefined, false)).toMatchObject({ outstanding: false, canStart: true });
    // started, first fetch not back yet
    expect(jobGate("j1", undefined, false)).toMatchObject({ outstanding: true, canStart: false, unverified: false });
    expect(jobGate("j1", { status: "running" }, false)).toMatchObject({ outstanding: true, canStart: false });
    // polling failed: still outstanding, and flagged so a person must choose to abandon it
    expect(jobGate("j1", undefined, true)).toMatchObject({ outstanding: true, canStart: false, unverified: true });
    expect(jobGate("j1", { status: "completed" }, false)).toMatchObject({ outstanding: false, canStart: true });
  });

  test("R3-B7 an UNSUPPORTED marker is verified per claim, never by count", () => {
    const a = { text: "revenue was 5", reason: "no_citation", citations: [] };
    const md = (line: string) => `**Q (verbatim):** q\n\n${line}\n- fine [p1]`;
    const ok = verifiedFlagLines(md("- **UNSUPPORTED** (no_citation): revenue was 5"), [a]);
    expect(ok.verified).toBe(true);
    expect(ok.lines).toEqual([3]);
    // same count, different claim: not verified, nothing styled
    const wrong = verifiedFlagLines(md("- **UNSUPPORTED** (no_citation): headcount was 9"), [a]);
    expect(wrong.verified).toBe(false);
    expect(wrong.lines).toEqual([]);
    // unclosed bold is not the platform's marker
    expect(platformFlagCount("- **UNSUPPORTED (no_citation): x", "UNSUPPORTED")).toBe(0);
    expect(verifiedFlagLines(md("- **UNSUPPORTED (no_citation): revenue was 5"), [a]).verified).toBe(false);
  });

  test("R3-B8 freshness is invalid for an unparseable timestamp or a negative / non-finite age", () => {
    const base: ConnectionHealth = {
      connection: "c", runnable: true, skip_reason: null, plan: { spec: null, ingest: null }, last_run: null,
      last_success_at: "2026-10-01T00:00:00Z", age_hours: 1, stale: false, future_timestamp: false,
      consecutive_failures: 0, counts: {}, exclusions_by_rule: {}, incomplete_runs: [], issues: [],
    };
    expect(freshnessOf(base).label).toBe("fresh");
    expect(freshnessOf({ ...base, last_success_at: "not-a-date" }).label).toBe("invalid");
    expect(freshnessOf({ ...base, age_hours: -1 }).label).toBe("invalid");
    expect(freshnessOf({ ...base, age_hours: Number.NaN }).label).toBe("invalid");
    const noAge = freshnessOf({ ...base, age_hours: undefined as unknown as null });
    expect(noAge.label).toBe("unknown");
    expect(noAge.ageText).not.toContain("undefined");
    expect(freshnessOf(base).ageText).toBe("1h old");
  });

  test("R3-B9 only a documented running state is described as work in progress", () => {
    expect(jobStatusWords("running")).toContain("working");
    for (const s of ["queued", "awaiting_approval", "something_new"]) {
      const w = jobStatusWords(s);
      expect(w).not.toContain("working");
      expect(w).toContain(s);
    }
    expect(jobStatusWords("completed")).toBe("");
  });
});

// =============================================================================================
// @unit — INT4: deciding a held drift adaptation from the builder.
// =============================================================================================
test.describe("@unit held adaptation decisions (INT4)", () => {
  const base = { status: "pending", mayDecide: true, owner: "analyst-7", reason: "", busy: false };

  test("INT4-1 the decision body never carries `by`, and a blank reason is omitted", () => {
    for (const r of ["", "   ", null, undefined, "column was dropped on purpose"]) {
      const body = adaptationDecisionBody(r) as Record<string, unknown>;
      expect("by" in body).toBe(false);
      expect(Object.keys(body).every((k) => k === "reason")).toBe(true);
    }
    expect(adaptationDecisionBody("  keep it  ")).toEqual({ reason: "keep it" });
    expect(adaptationDecisionBody("   ")).toEqual({});
  });

  test("INT4-2 the owner may approve without a reason; rejecting needs one", () => {
    const noReason = adaptationDecisionState(base);
    expect(noReason.canApprove).toBe(true);
    expect(noReason.canReject).toBe(false);
    expect(noReason.blockers.join(" ")).toContain("reason");
    const withReason = adaptationDecisionState({ ...base, reason: "revert the source" });
    expect(withReason).toEqual({ canApprove: true, canReject: true, blockers: [] });
  });

  test("INT4-3 a non-owner, a decided change, or a request in flight offers no decision", () => {
    const cases = [
      { ...base, reason: "x", mayDecide: false },
      { ...base, reason: "x", mayDecide: false, owner: null },
      { ...base, reason: "x", status: "approved" },
      { ...base, reason: "x", busy: true },
    ];
    for (const c of cases) {
      const st = adaptationDecisionState(c);
      expect(st.canApprove).toBe(false);
      expect(st.canReject).toBe(false);
    }
    // the refusal names who may decide, or says nobody is recorded — never silent
    expect(adaptationDecisionState(cases[0]).blockers.join(" ")).toContain("analyst-7");
    expect(adaptationDecisionState(cases[1]).blockers.join(" ")).toContain("No owner");
    expect(adaptationDecisionState(cases[2]).blockers.join(" ")).toContain("approved");
  });
});
