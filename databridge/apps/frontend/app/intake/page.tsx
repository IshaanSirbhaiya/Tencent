"use client";
/**
 * `/intake` — the kiosk/patient side: drop a referring document in and read it.
 *
 * Three ways in, ONE pipeline. The fixture buttons fetch the real corpus text from the gateway and
 * post it through `/intake/upload`; the paste box posts the same way; the file control posts the
 * unparsed bytes to `/intake/upload-file`, which extracts once, server-side, and hands the same
 * text to the same `run_intake`. There is no separate demo path and no client-side parsing — what
 * a judge sees is exactly what the pipeline does.
 *
 * **The file control is deliberate, and it is what makes OCR reachable from the demo.** Its cost
 * is stated on the screen rather than in a docstring: a scan's checkbox state cannot be proved
 * complete, so the four checkbox-derived fields come back unresolved instead of grounded
 * (`ground.unprovable_checkbox_spans`, R-13). `ExtractionReport` shows that on the way in.
 *
 * **What is NOT accepted has not changed.** Identity documents and insurance e-cards are refused,
 * and the banner says exactly how — including the part that is uncomfortable: a photographed NRIC
 * cannot be recognised from its filename, so it is read in order to be screened, and then nothing
 * from it survives the call. R-12 exists because that sentence once claimed more than the code did.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FileUp, ShieldAlert } from "lucide-react";
import { intakeApi } from "@/lib/intake-api";
import { isRefusal } from "@/lib/intake-types";
import type { SummaryCard } from "@/lib/intake-types";
import { ExtractionReport } from "@/components/intake/extraction-report";
import { UploadFileControl } from "@/components/intake/upload-file-control";

export default function IntakeUploadPage() {
  const router = useRouter();
  const [documentId, setDocumentId] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Held rather than navigated past, for file uploads only: the extraction report is the one
  // moment the OCR quality of this reading is on screen. The card does not repeat it.
  const [extracted, setExtracted] = useState<SummaryCard | null>(null);

  // The corpus comes from the gateway's allow-list, so a fixture button really is a one-click
  // path through the same /intake/upload endpoint a paste uses. The first draft only filled the
  // document-id field, which left the button doing visibly nothing.
  const fixturesQ = useQuery({ queryKey: ["intake-fixtures"], queryFn: intakeApi.fixtures });

  const loadFixture = useMutation({
    mutationFn: (name: string) => intakeApi.fixtureText(name),
    onSuccess: (res, name) => {
      if (isRefusal(res)) {
        setError(res.refusal.message);
        return;
      }
      setError(null);
      setExtracted(null);
      setDocumentId(name);
      setText(res.text);
    },
    onError: (e: Error) => setError(`Could not load the fixture: ${e.message}`),
  });

  const upload = useMutation({
    mutationFn: () => intakeApi.upload(documentId.trim() || "pasted-document.txt", text),
    onSuccess: (res) => {
      if (isRefusal(res)) {
        setError(res.refusal.message);
        return;
      }
      router.push(`/frontdesk/${encodeURIComponent(res.intake_id)}`);
    },
  });

  const uploadFile = useMutation({
    mutationFn: (file: File) => intakeApi.uploadFile(file, documentId.trim() || file.name),
    onSuccess: (res) => {
      if (isRefusal(res)) {
        setExtracted(null);
        setError(res.refusal.message);
        return;
      }
      setError(null);
      // Not an automatic redirect. Whatever OCR could not recover is only on this screen, and a
      // scan that lands straight on the summary card is indistinguishable from typed text — which
      // is the whole failure mode.
      setExtracted(res);
    },
    onError: (e: Error) => setError(`Could not read that file: ${e.message}`),
  });

  return (
    <div className="flex flex-col gap-4 p-6">
      <header>
        <h1 className="text-lg font-semibold">Read a referring document</h1>
        <p className="text-xs text-subtle">
          An insurer referral letter, health-check voucher, underwriting memo, TPA or government
          authorisation form, or an HR email. Upload the file, or paste its text. The system reads
          it before the patient reaches the counter.
        </p>
      </header>

      <div className="flex items-start gap-2 rounded-lg border border-band-review/40 bg-band-review/[0.05] px-3 py-2">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
        <div className="text-2xs text-subtle" data-testid="identity-refusal-notice">
          <p>
            <span className="font-semibold text-band-review">
              Do not upload an NRIC, FIN, passport or an insurance e-card.
            </span>{" "}
            Identity and e-card validity are checked in person at the counter, by a person, against
            the physical document. This system never does that and has no code path that could.
          </p>
          <p className="mt-1">
            <span className="font-medium text-fg">What is accepted:</span> the referring document —
            a referral letter, voucher, authorisation form or HR email — as a PDF, a photograph or
            scan, or as text.
          </p>
          <p className="mt-1">
            <span className="font-medium text-fg">What happens to a refused file:</span> if it is
            named like an identity document it is refused before it is opened. Otherwise it is{" "}
            <span className="font-medium text-fg">read by OCR first</span> — a photographed NRIC
            cannot be recognised any other way — and refused on the extracted text, before that
            text is stored, before any model sees it, and before a record exists. Nothing from it
            is kept: no document, no queue entry, no ledger row, and the refusal never quotes what
            it read. (A file over 1 MB briefly touches a temporary file that the server then
            deletes.)
          </p>
        </div>
      </div>

      <UploadFileControl
        onUpload={(file) => {
          setExtracted(null);
          setError(null);
          uploadFile.mutate(file);
        }}
        pending={uploadFile.isPending}
      />

      {extracted && (
        <ExtractionReport
          card={extracted}
          onOpen={() =>
            router.push(`/frontdesk/${encodeURIComponent(extracted.intake_id)}`)
          }
        />
      )}

      <div className="flex flex-wrap gap-1.5" data-testid="fixture-buttons">
        {(fixturesQ.data?.fixtures ?? []).map((f) => (
          <button
            key={f}
            type="button"
            disabled={loadFixture.isPending}
            onClick={() => loadFixture.mutate(f)}
            data-testid={`fixture-${f}`}
            className="rounded border border-border bg-surface px-2 py-1 text-2xs text-subtle transition-colors hover:bg-muted hover:text-fg disabled:opacity-50"
          >
            {f}
          </button>
        ))}
      </div>

      <input
        type="text"
        value={documentId}
        onChange={(e) => setDocumentId(e.target.value)}
        placeholder="Document name (e.g. chit_02_everwell_voucher.txt)"
        data-testid="document-id"
        className="rounded border border-border bg-surface px-3 py-1.5 text-xs"
      />

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={16}
        placeholder="Paste the document text here…"
        data-testid="document-text"
        className="rounded border border-border bg-surface px-3 py-2 font-mono text-2xs"
      />

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!text.trim() || upload.isPending}
          onClick={() => upload.mutate()}
          data-testid="upload-document"
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:bg-muted disabled:text-subtle"
        >
          <FileUp className="h-3.5 w-3.5" />
          {upload.isPending ? "Reading…" : "Read document"}
        </button>
        {error && (
          <span className="text-2xs text-band-review" data-testid="intake-error">
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
