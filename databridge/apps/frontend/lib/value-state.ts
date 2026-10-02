// The standard withheld/absent envelope (R-028 #12).
//
// Where the gateway will not show a value it sends `{status, reason, pointers?}` in the value's
// place: never a bare `null` with a flag beside it, a dropped field, or a "redacted" string. So a
// page can tell "withheld, and why" from "absent" from a value, the same way on every surface.

export interface ValueState {
  status: "withheld" | "absent";
  /** A closed, machine-readable reason (e.g. `no_valid_pointer`, `instruction_shaped`). */
  reason: string;
  /** Where the value would have come from, when that can be said. */
  pointers?: unknown[];
}

const KEYS = new Set(["status", "reason", "pointers"]);

/** Strict: an object with exactly the envelope's keys, a known status and a string reason. A
 *  value that merely has a `status` key (a write's own args, say) is not an envelope. */
export function isValueState(value: unknown): value is ValueState {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  if (v.status !== "withheld" && v.status !== "absent") return false;
  if (typeof v.reason !== "string" || !v.reason) return false;
  if (v.pointers !== undefined && !Array.isArray(v.pointers)) return false;
  return Object.keys(v).every((k) => KEYS.has(k));
}

/** Plain words for a reason the gateway can give. Unknown reasons are shown as their code. */
export function describeValueState(state: ValueState): string {
  const why: Record<string, string> = {
    no_valid_pointer:
      "no source is recorded for these values, and a value is never shown without its source",
    instruction_shaped:
      "this text is addressed to an automated reader (an instruction inside a document), so it is not reproduced",
  };
  const verb = state.status === "absent" ? "Absent" : "Withheld";
  return `${verb}: ${why[state.reason] ?? state.reason}.`;
}
