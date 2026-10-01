// Gateway client for /intake/*. Fetch and render only — every decision is server-side.
//
// Note the shape of `selectPatient` and `confirm`: both REQUIRE `attested_in_person`. It is not
// defaulted here, and it is not inferred from a staff id being present, because the gateway
// refuses without it. A client that forgets it gets a refusal rather than a silent success —
// which is the behaviour we want the UI to be built against.
import { GATEWAY, authHeaders } from "./api";
import type {
  EvidenceView,
  LedgerRow,
  QueueRow,
  Refusal,
  SummaryCard,
} from "./intake-types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${GATEWAY}${path}`, {
    ...init,
    // INT1: every /intake route requires a principal — send the credential
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({}));
  // A refusal is a normal, expected response here — `IDENTITY_NOT_VERIFIED` is the gate doing its
  // job, not an error. Pass it through so the UI can render the reason instead of a stack trace.
  if (!res.ok && !("refusal" in body)) {
    throw new Error(`${path} → ${res.status}`);
  }
  return body as T;
}

export const intakeApi = {
  queue: () => call<{ intakes: QueueRow[] }>("/intake"),

  /** The demo corpus, so the fixture buttons are a real one-click path rather than a label. */
  fixtures: () => call<{ fixtures: string[] }>("/intake/fixtures"),
  fixtureText: (name: string) =>
    call<{ document_id: string; text: string } | Refusal>(
      `/intake/fixtures/${encodeURIComponent(name)}`,
    ),

  card: (intakeId: string) =>
    call<SummaryCard | Refusal>(`/intake/${encodeURIComponent(intakeId)}`),

  upload: (documentId: string, text: string, channel = "upload") =>
    call<SummaryCard | Refusal>("/intake/upload", {
      method: "POST",
      body: JSON.stringify({ document_id: documentId, text, channel }),
    }),

  /** Upload a referring document as a FILE — `.txt`/`.md`/`.eml`, a PDF, or a scan.
   *
   *  Deliberately NOT routed through `call`: that helper pins `Content-Type: application/json`,
   *  and a multipart body needs the boundary the browser generates. Setting the header by hand
   *  here would produce a body FastAPI cannot parse, which surfaces as a 422 that reads like a
   *  refusal — so the header is omitted on purpose, not by oversight.
   *
   *  This is the SAME endpoint and the same pipeline the paste box uses one step later: the file
   *  is extracted server-side into one text, and that text is what the record is built from. There
   *  is no client-side parsing and no separate demo path.
   *
   *  A refusal is an expected outcome here (`IDENTITY_DOCUMENT_REFUSED`, `UNSUPPORTED_DOCUMENT`,
   *  `EXTRACTION_UNAVAILABLE`), so it is returned rather than thrown, exactly as elsewhere. */
  uploadFile: async (
    file: File,
    documentId?: string,
    channel = "upload",
  ): Promise<SummaryCard | Refusal> => {
    const form = new FormData();
    form.append("file", file);
    if (documentId?.trim()) form.append("document_id", documentId.trim());
    form.append("channel", channel);
    const res = await fetch(`${GATEWAY}/intake/upload-file`, {
      method: "POST",
      body: form,
      headers: authHeaders(),         // no Content-Type: the browser sets the multipart boundary
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok && !("refusal" in body)) {
      throw new Error(`/intake/upload-file → ${res.status}`);
    }
    return body as SummaryCard | Refusal;
  },

  evidence: (intakeId: string, fieldPath: string) =>
    call<EvidenceView | Refusal>(
      // The route is `{field_path:path}`, so the dots must survive — encode per SEGMENT rather
      // than whole, or a future path containing `#`/`?` would silently truncate into a wrong-field
      // lookup.
      `/intake/${encodeURIComponent(intakeId)}/evidence/${fieldPath
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`,
    ),

  selectPatient: (
    intakeId: string,
    patientId: string,
    staffId: string,
    attestedInPerson: boolean,
  ) =>
    call<Refusal | { selected_patient_id: string; ready_to_commit: boolean; questionnaire_prefilled: number }>(
      `/intake/${encodeURIComponent(intakeId)}/select-patient`,
      {
        method: "POST",
        body: JSON.stringify({
          patient_id: patientId,
          staff_id: staffId,
          attested_in_person: attestedInPerson,
        }),
      },
    ),

  confirm: (intakeId: string, staffId: string, attestedInPerson: boolean) =>
    call<Refusal | { committed: boolean; entries_written: number }>(
      `/intake/${encodeURIComponent(intakeId)}/confirm`,
      {
        method: "POST",
        body: JSON.stringify({
          staff_id: staffId,
          attested_in_person: attestedInPerson,
        }),
      },
    ),

  ledger: (intakeId: string) =>
    call<{ rows: LedgerRow[] } | Refusal>(
      `/intake/${encodeURIComponent(intakeId)}/ledger`,
    ),

  claimDraft: (intakeId: string) =>
    call<Record<string, unknown> | Refusal>(
      `/intake/${encodeURIComponent(intakeId)}/claim-draft`,
    ),

  /** Screen a question BEFORE sending it, so the ask box can grey out rather than refuse after. */
  screen: (question: string) =>
    call<{ refused: boolean; code: string; answer: string }>("/brain/screen", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

  suggested: () =>
    call<{ questions: string[]; scope_note: string }>("/brain/suggested"),
};
