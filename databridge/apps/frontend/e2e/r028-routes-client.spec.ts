import { test, expect } from "@playwright/test";
import { failureFrom, plainMessage } from "../lib/sources-api";
import { refusalMessage } from "../lib/adapter-setup-api";
import { refusalText, type Refusal } from "../lib/data-api";

/**
 * R-028 (ROUTES track) — the clients of the Sources, source-batch, adapter-setup and materialise
 * routes read the common refusal envelope: the STANDARD code, the former specific `reason_code`,
 * and the `error_ref` a failure carries instead of its exception text. `@unit`: pure functions,
 * no gateway, no page.
 */

const STALE = {
  refusal: {
    code: "DECISION_STALE",
    reason_code: "PLAN_CHANGED",
    message: "the plan changed after it was reviewed; review it again",
    nearest_alternative: null,
  },
};
const FAILED = {
  refusal: {
    code: "SOURCES_UNREADABLE",
    message: "the registered sources could not be read (the server log has the details under the reference)",
    error_ref: "ERR-0123456789",
    nearest_alternative: null,
  },
};

test.describe("@unit R-028 refusal envelope", () => {
  test("the Sources client keeps the standard code, the reason code and the reference", () => {
    const stale = failureFrom(409, STALE, "/sources/s/plan/approve");
    expect(stale.code).toBe("DECISION_STALE");
    expect(stale.reason_code).toBe("PLAN_CHANGED");
    expect(stale.message).toBe(STALE.refusal.message);
    // a standard code has its own plain sentence (not the generic 409 one)
    expect(stale.plain).toBe(plainMessage(409, "DECISION_STALE", stale.message));
    expect(stale.plain).not.toBe(plainMessage(409, "SOMETHING_ELSE", stale.message));

    const failed = failureFrom(500, FAILED, "/sources");
    expect(failed.error_ref).toBe("ERR-0123456789");
    expect(failed.plain).toContain("ERR-0123456789");          // the operator can quote it
    expect(failureFrom(409, STALE, "/x").error_ref).toBeUndefined();
  });

  test("every standard code has a plain sentence of its own", () => {
    const generic = plainMessage(409, "NOT_A_CODE", "m");
    for (const code of ["APPROVER_NOT_AUTHENTICATED", "MACHINE_ACTOR", "DECISION_STALE", "DECISION_ALREADY_FINAL", "LEDGER_INTEGRITY_FAILED"]) {
      expect(plainMessage(409, code, "m"), code).not.toBe(generic);
    }
  });

  test("the setup client shows the reference with the gateway's message", () => {
    expect(refusalMessage(FAILED.refusal)).toBe(`${FAILED.refusal.message} (reference ERR-0123456789)`);
    expect(refusalMessage(STALE.refusal)).toBe(STALE.refusal.message);
  });

  test("the Data client's refusal text carries the code, the message and the reference", () => {
    const r: Refusal = { code: "DESIGN_FAILED", message: "the dataset design operation failed", error_ref: "ERR-aaaaaaaaaa" };
    expect(refusalText(r)).toBe("DESIGN_FAILED: the dataset design operation failed (reference ERR-aaaaaaaaaa)");
    const s: Refusal = { code: "DECISION_STALE", reason_code: "STALE_REVISION", message: "reload it" };
    expect(refusalText(s)).toBe("DECISION_STALE (STALE_REVISION): reload it");
  });
});

// R-028 TIDY #6: AgentsApiError carries the envelope's `reason_code` and `error_ref`, like the
// other clients, from every client that throws it (agents, one-time, adapter setup).
test.describe("@unit R-028 TIDY AgentsApiError", () => {
  const ENVELOPE = {
    refusal: {
      code: "DECISION_STALE",
      reason_code: "PLAN_CHANGED",
      message: "the plan changed after it was reviewed; review it again",
      error_ref: "ERR-abcdef0123",
    },
  };

  test("agentsApiErrorFrom keeps the code, the reason code and the reference", async () => {
    const { AgentsApiError, agentsApiErrorFrom } = await import("../lib/agents-api");
    const e = agentsApiErrorFrom(409, ENVELOPE.refusal);
    expect(e).toBeInstanceOf(AgentsApiError);
    expect([e.status, e.code, e.reason_code, e.error_ref]).toEqual([409, "DECISION_STALE", "PLAN_CHANGED", "ERR-abcdef0123"]);
    expect(e.message).toContain(ENVELOPE.refusal.message);
    expect(e.message).toContain("ERR-abcdef0123");
    const bare = agentsApiErrorFrom(403, { code: "MACHINE_ACTOR", message: "m" });
    expect([bare.reason_code, bare.error_ref, bare.message]).toEqual([undefined, undefined, "m"]);
  });

  test("every client that throws it fills both fields from the envelope", async () => {
    const { AgentsApiError } = await import("../lib/agents-api");
    const { agentsApi } = await import("../lib/agents-api");
    const { oneTimeApi } = await import("../lib/one-time-api");
    const { setupApi } = await import("../lib/adapter-setup-api");
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify(ENVELOPE), { status: 409, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    try {
      const calls: [string, () => Promise<unknown>][] = [
        ["agents", () => agentsApi.compile("x", {})],
        ["one-time", () => oneTimeApi.get("t")],
        ["setup", () => setupApi.get("s")],
      ];
      for (const [name, call] of calls) {
        const err = await call().then(
          () => null,
          (e: unknown) => e,
        );
        expect(err, name).toBeInstanceOf(AgentsApiError);
        const e = err as InstanceType<typeof AgentsApiError>;
        expect([e.code, e.reason_code, e.error_ref], name).toEqual(["DECISION_STALE", "PLAN_CHANGED", "ERR-abcdef0123"]);
      }
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
