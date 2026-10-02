import { test, expect } from "@playwright/test";
import { answerValueText, type AnswerValue } from "../lib/intelligence-api";
import { describeValueState } from "../lib/value-state";

/**
 * R-028 TIDY #7 — a console answer value the gateway would not print arrives as the standard
 * withheld envelope `{status, reason, pointers}` in `text`'s place (never a "redacted" string with a
 * flag beside it), and the page says why in the shared words of `lib/value-state.ts`. `@unit`.
 */
const base = {
  key: "k",
  tool: "node_detail",
  row: 0,
  field: "label",
  scope: "",
  citation: "doc-1 0-5",
  source: { source_id: "doc-1", kind: "doc_span" },
} as unknown as Omit<AnswerValue, "text">;

test.describe("@unit R-028 TIDY console values", () => {
  test("a withheld value is shown in the shared words, a plain value verbatim", () => {
    const state = { status: "withheld" as const, reason: "instruction_shaped", pointers: [base.source] };
    const held = answerValueText({ ...base, text: state } as AnswerValue);
    expect(held).toEqual({ text: describeValueState(state), withheld: true });
    const plain = answerValueText({ ...base, text: "Larkspur Analytics Ltd" } as AnswerValue);
    expect(plain).toEqual({ text: "Larkspur Analytics Ltd", withheld: false });
  });

  test("an object that is not the strict envelope is never printed as a value", () => {
    const odd = answerValueText({ ...base, text: { status: "redacted", reason: "x" } } as unknown as AnswerValue);
    expect(odd.withheld).toBe(true);
    expect(odd.text).not.toContain("redacted");
  });
});
