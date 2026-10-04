import { test, expect } from "@playwright/test";
import {
  allDeferred,
  approvableTables,
  documentsDecisionLabel,
  documentsDecisionOffered,
  basisWords,
  canRediscover,
  citedEvidence,
  handoffOffered,
  mappingApprovalOffered,
  nextAutoStep,
  personWords,
  pointerText,
  type Evidence,
  type SetupStep,
  type StepId,
} from "../lib/adapter-setup-api";

/**
 * FEAT8 — set up a source through its adapter agent. `@unit`: the pure decision helpers the setup
 * panel uses (no gateway, no page). Run alone with a config that has no webServer and `grep: /@unit/`.
 */

const IDS: StepId[] = ["plan", "approve_plan", "discover", "propose_mapping", "explain", "approve_mapping", "handoff"];
function steps(done: StepId[], over: Partial<Record<StepId, SetupStep["status"]>> = {}): SetupStep[] {
  return IDS.map((id) => ({ id, label: id, detail: "", status: over[id] ?? (done.includes(id) ? "done" : "todo") }));
}

function ev(id: string, column: string, value: string | null = "v"): Evidence {
  return {
    id,
    fact: "f2",
    what: "primary_key",
    column,
    note: "",
    row_id: "1",
    value,
    masked: false,
    pointer: { source_id: "s", kind: "table_cell", table: "public.t", column, row_id: "1", capture_digest: "abcdef0123456789" },
  };
}

test.describe("@unit adapter setup helpers", () => {
  test("the page advances only through steps that are not decisions", () => {
    // nothing proposed yet: the person presses Set up
    expect(nextAutoStep({ steps: steps([]), running: null })).toBeNull();
    // a plan waits for a person: never approved by the page
    expect(nextAutoStep({ steps: steps(["plan"], { approve_plan: "waiting_for_person" }), running: null })).toBeNull();
    // approved by a person, nothing run: discover
    expect(nextAutoStep({ steps: steps(["plan", "approve_plan"]), running: null })).toBe("discover");
    // a run in flight: wait
    expect(nextAutoStep({ steps: steps(["plan", "approve_plan"]), running: { job_id: "j" } })).toBeNull();
    // a mapping proposed, not explained: explain
    expect(nextAutoStep({ steps: steps(["plan", "approve_plan", "discover", "propose_mapping"]), running: null })).toBe("explain");
    // explained: the mapping waits for a person; the page does not approve it
    const waiting = steps(["plan", "approve_plan", "discover", "propose_mapping", "explain"], { approve_mapping: "waiting_for_person" });
    expect(nextAutoStep({ steps: waiting, running: null })).toBeNull();
  });

  test("a claim shows only the evidence the explanation carries", () => {
    const evidence = [ev("e1", "id"), ev("e2", "name")];
    expect(citedEvidence({ text: "x", evidence: ["e2", "e9", "e1"] }, evidence).map((e) => e.id)).toEqual(["e2", "e1"]);
    expect(citedEvidence({ text: "x", evidence: ["e9"] }, evidence)).toEqual([]);
  });

  test("a pointer reads as where the value sits", () => {
    const p = ev("e1", "customer_id").pointer;
    const text = pointerText(p);
    for (const part of [p.table!, p.column!, `row ${p.row_id}`, p.capture_digest!.slice(0, 12)]) expect(text).toContain(part);
  });

  test("the hand-off is offered only when the gateway's gate is empty and the agent is not the keeper yet", () => {
    expect(handoffOffered(null)).toBe(false);
    expect(handoffOffered({ handoff_problems: ["no table approved"], keeper: { id: "k", status: "draft", title: "" } })).toBe(false);
    expect(handoffOffered({ handoff_problems: [], keeper: { id: "k", status: "draft", title: "" } })).toBe(true);
    expect(handoffOffered({ handoff_problems: [], keeper: { id: "k", status: "active", title: "" } })).toBe(false);
  });

  test("people are named as never merged; only proposed tables are approvable", () => {
    expect(personWords({ kind: "person", is_person: true, is_subject: true })).toContain("never merged");
    expect(personWords({ kind: "party", is_person: false, is_subject: true })).toContain("never merged");
    expect(personWords({ kind: "account", is_person: false, is_subject: false })).toBe("not people");
    const tables = [
      { table: "a", status: "proposed" },
      { table: "b", status: "approved" },
      { table: "c", status: "unmapped" },
    ] as Parameters<typeof approvableTables>[0] extends infer X ? (X extends { tables: infer T } ? T : never) : never;
    expect(approvableTables({ tables })).toEqual(["a"]);
    // approved after the explanation was written: the live mapping wins
    expect(approvableTables({ tables }, { a: { status: "approved" } })).toEqual([]);
    expect(approvableTables({ tables }, { c: { status: "proposed" } })).toEqual(["a", "c"]);
    expect(approvableTables(null)).toEqual([]);
  });

  test("a failed discovery is offered again by a person, never looped by the page", () => {
    const blocked = steps(["plan", "approve_plan"], { discover: "blocked" });
    expect(nextAutoStep({ steps: blocked, running: null })).toBeNull();
    expect(canRediscover({ steps: blocked, running: null })).toBe(true);
    expect(canRediscover({ steps: blocked, running: { job_id: "j" } })).toBe(false);
    expect(canRediscover({ steps: steps(["plan", "approve_plan", "discover"]), running: null })).toBe(false);
    expect(canRediscover(null)).toBe(false);
    // the explanation waits for a successful discovery
    const notDiscovered = steps(["plan", "approve_plan", "propose_mapping"], { discover: "blocked", explain: "blocked" });
    expect(nextAutoStep({ steps: notDiscovered, running: null })).toBeNull();
  });

  test("a proposal or a vocabulary rule is labelled, never shown as evidenced", () => {
    expect(basisWords({ basis: "capture", evidence: ["e1"] })).toBe("");
    expect(basisWords({ basis: "mapper", evidence: [] })).toContain("proposed");
    expect(basisWords({ basis: "vocabulary", evidence: [] })).toContain("vocabulary");
    expect(basisWords({ basis: "capture", evidence: [] })).toContain("proposed");
  });

  test("the hand-off needs every pending decision deferred by name", () => {
    const pending = [{ key: "hold:a" }, { key: "table:t" }];
    expect(allDeferred(pending, ["table:t", "hold:a"])).toBe(true);
    expect(allDeferred(pending, ["table:t"])).toBe(false);
    expect(allDeferred(pending, ["table:t", "hold:a", "link:x"])).toBe(false);
    expect(allDeferred([], [])).toBe(true);
    const s = { handoff_problems: [], keeper: { id: "k", status: "draft", title: "" }, pending_decisions: pending as never };
    expect(handoffOffered(s, [])).toBe(false);
    expect(handoffOffered(s, ["hold:a", "table:t"])).toBe(true);
  });

  test("the documents decision is offered once, for a discovery with documents", () => {
    const d = { captured: 3, allowed: 2, held: 1, digest: "sha256:x", decided: false, discovery_run_id: "r" };
    expect(documentsDecisionOffered(d)).toBe(true);
    expect(documentsDecisionOffered({ ...d, decided: true })).toBe(false);
    expect(documentsDecisionOffered({ ...d, digest: null })).toBe(false);
    expect(documentsDecisionOffered(null)).toBe(false);
  });

  test("W-05: an already-built document source is offered to be KEPT, never silently adopted", () => {
    const kept = { captured: 15, allowed: 0, kept: 13, held: 1, digest: "sha256:k", decided: false, discovery_run_id: "r" };
    expect(documentsDecisionOffered(kept)).toBe(true);
    expect(documentsDecisionLabel(kept)).toContain(`keep these ${kept.kept}`.replace("keep", "Keep"));
    expect(documentsDecisionLabel({ allowed: 2, kept: 3 })).toContain("Build 2 and keep 3");
    expect(documentsDecisionLabel({ allowed: 2 })).toBe("Build these 2 document(s)");
  });

  test("the mapping approval is offered only against the current explanation", () => {
    expect(mappingApprovalOffered(null)).toBe(false);
    expect(mappingApprovalOffered({ explanation_fresh: false, explanation_digest: null })).toBe(false);
    expect(mappingApprovalOffered({ explanation_fresh: true, explanation_digest: null })).toBe(false);
    expect(mappingApprovalOffered({ explanation_fresh: true, explanation_digest: "sha256:x" })).toBe(true);
  });
});
