// The outbox client — proposed actions, and the two human decisions that move them.
//
// Everything here is a wire shape the gateway already sanitised. Note what these types do NOT
// carry, and that the absence is structural rather than a habit of this file:
//
//   * no recipient address. `RecipientView.masked` is a masked mailbox and `handle` is an opaque,
//     stable id for the same person. A draft is the one object in this system that carries a
//     patient's mailbox, which is a stronger identifier than a name, and `/intelligence`'s whole
//     contract is that it never names one;
//   * no derivation strings. `Citation` is a projection onto a closed shape, so a field added to
//     `SourcePointer` later cannot travel through it;
//   * no send. There is no method below that causes a message to leave: `approve` records a named
//     human's decision, and `execute` starts a separate, model-free process that reads the outbox.
//
// Nothing in this file computes. Every string it renders was produced server-side.
import { GATEWAY } from "./api";
import type { Refusal } from "./types";

/** One pointer behind a draft field, in the closed shape the gateway publishes.
 *
 *  `repo_config` is the important flag: true means `source` is a file in this repository that a
 *  human can open (`clinic_monitors.yaml`, `coverage_rules.yaml`), so its identifiers print. False
 *  means the source is a data store, and its row is a handle. */
export interface Citation {
  kind: string;
  source: string;
  repo_config: boolean;
  table: string | null;
  column: string | null;
  row: string | null;
  document: string | null;
  char_start: number | null;
  char_end: number | null;
  derived_from: Citation[];
}

/** Who a draft is addressed to, as much as this surface may say — and how that was decided.
 *
 *  `established` is a code-authored sentence from a closed table keyed on `origin`. It is the part
 *  an approver actually needs: not "who is this mailbox" but "the address the clinic already held
 *  for the patient a named human selected in person". */
export interface RecipientView {
  origin: "committed_ledger" | "typed_by_human" | string;
  /** The member of STAFF whose act put this address here. Never a patient. */
  chosen_by: string;
  masked: string;
  handle: string;
  established: string;
  pointer: Citation;
}

/** One proposed action: what it would do, to whom, why, and where it stands. */
export interface ActionCard {
  draft_id: string;
  status: "drafted" | "approved" | "rejected" | "sent" | string;
  channel: string;
  playbook_id: string;
  /** The clinic rule's own prose from `clinic_monitors.yaml` — config, so it prints. */
  title: string | null;
  when: string | null;
  escalate_to: string | null;
  intake_id: string | null;
  recipient: RecipientView;
  subject: string;
  body: string;
  /** The draft's own text was instruction-shaped and has been replaced. Still approvable — that
   *  is the reviewer's call — but it must render as withheld, not as text somebody read. */
  redacted: boolean;
  because: Citation[];
  /** Always true. There is no value of this field that means "send without asking". */
  requires_human_approval: true;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  reason: string | null;
  sent_at: string | null;
  message_id: string | null;
}

/** A clinic rule that applies but produced no draft, and the code-authored reason.
 *
 *  Rendered, never swallowed. "The system decided not to tell you why it did nothing" is the
 *  failure mode a review queue dies of, and each of these reasons is something staff can act on. */
export interface BlockedProposal {
  playbook_id: string;
  title: string | null;
  code: string;
  message: string;
}

/** What actually happened. A demo whose send is invisible proves nothing. */
export interface ExecutionResult {
  draft_id: string;
  status: string;
  message_id: string | null;
  sent_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  recipient_masked: string;
  /** WHICH PROCESS did it — named in the payload, not only in a docstring. */
  executed_by: string;
  note: string;
}

export interface OutboxListing {
  drafts: ActionCard[];
  counts: Record<string, number>;
  /** The store file. "The dispatcher cannot see my approval" is answerable by looking at this. */
  database: string;
  note: string;
  refusal?: Refusal;
}

export interface ExecuteResponse {
  draft?: ActionCard;
  result?: ExecutionResult;
  refusal?: Refusal;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  // Refusals ride in the body with a 4xx (house convention) — surface them, do not throw. A
  // refusal is the answer here: "this has not been approved" is the system working.
  if (!res.ok && !("refusal" in body)) throw new Error(`${path} → ${res.status}`);
  return body as T;
}

export const outboxApi = {
  list: () => call<OutboxListing>("/outbox"),
  draft: (draftId: string) =>
    call<ActionCard & { refusal?: Refusal }>(`/outbox/${encodeURIComponent(draftId)}`),
  /** Records a named human's agreement. There is no authentication behind the name — the page
   *  says so, because pretending otherwise would be the one dishonest thing on this screen. */
  approve: (draftId: string, approvedBy: string) =>
    call<ActionCard & { refusal?: Refusal }>(`/outbox/${encodeURIComponent(draftId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved_by: approvedBy }),
    }),
  reject: (draftId: string, rejectedBy: string, reason: string) =>
    call<ActionCard & { refusal?: Refusal }>(`/outbox/${encodeURIComponent(draftId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ rejected_by: rejectedBy, reason }),
    }),
  /** Triggers the dispatcher — a separate process — and reports the row afterwards. */
  execute: (draftId: string) =>
    call<ExecuteResponse>(`/outbox/${encodeURIComponent(draftId)}/execute`, { method: "POST" }),
};

/** A citation rendered for a reader, in one line. Never a guess: an identifier prints only when
 *  the backend said it came from a file in this repo. */
export function citationText(citation: Citation): string {
  if (citation.kind === "doc_span") {
    const span =
      citation.char_start === null || citation.char_end === null
        ? ""
        : ` chars ${citation.char_start}–${citation.char_end}`;
    return `${citation.document ?? citation.source}${span}`;
  }
  if (citation.kind === "table_cell") {
    const cell = [citation.table, citation.column].filter(Boolean).join(".");
    return `${citation.source} · ${cell}${citation.row ? ` row ${citation.row}` : ""}`;
  }
  if (citation.kind === "derived") {
    return `${citation.source} · derived from ${citation.derived_from.length} citation${
      citation.derived_from.length === 1 ? "" : "s"
    }`;
  }
  return `${citation.source} (${citation.kind})`;
}

/** Every citation in a tree, flattened for display, parents before children. */
export function flattenCitations(citations: Citation[]): Citation[] {
  const out: Citation[] = [];
  for (const citation of citations) {
    out.push(citation);
    out.push(...flattenCitations(citation.derived_from));
  }
  return out;
}
