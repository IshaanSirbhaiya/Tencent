import { test, expect } from "@playwright/test";

/**
 * R-027 ONE (Codex round 1, CRITICAL): the outbox client never sends an approver. Who approves,
 * rejects or executes is the principal the gateway authenticated the request as; a body naming
 * one is refused by the gateway (`APPROVER_NOT_AUTHENTICATED`). `@unit`: fetch is stubbed, no gateway.
 */
import { outboxApi } from "../lib/outbox-api";

const IDENTITY_FIELDS = ["approved_by", "rejected_by", "typed_by", "by", "staff", "approver"];

test.describe("@unit outbox client sends no identity", () => {
  test("approve, reject and execute bodies name nobody", async () => {
    const sent: { url: string; body: unknown }[] = [];
    const real = globalThis.fetch;
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      sent.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null });
      return new Response(JSON.stringify({}), { status: 200 });
    }) as typeof fetch;
    try {
      await outboxApi.approve("d1");
      await outboxApi.reject("d1", "not now");
      await outboxApi.execute("d1");
    } finally {
      globalThis.fetch = real;
    }
    expect(sent.map((s) => s.url.split("/outbox/")[1])).toEqual(["d1/approve", "d1/reject", "d1/execute"]);
    for (const { body } of sent) {
      const keys = body && typeof body === "object" ? Object.keys(body as object) : [];
      expect(keys.filter((k) => IDENTITY_FIELDS.includes(k))).toEqual([]);
    }
    expect(sent[1].body).toEqual({ reason: "not now" });
  });
});
