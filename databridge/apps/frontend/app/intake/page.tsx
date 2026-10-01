"use client";
/**
 * `/intake` — the intake side of the active pack: drop an incoming document in and read it.
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
 * **What is NOT accepted has not changed.** Identity documents and entitlement credentials are
 * refused, and the banner says exactly how — including the part that is uncomfortable: a
 * photographed identity card cannot be recognised from its filename, so it is read in order to be
 * screened, and then nothing from it survives the call. R-12 exists because that sentence once
 * claimed more than the code did.
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
import { TryChips } from "@/components/try-chips";
import { usePackTrials, type IntakeTrial } from "@/components/try-prompts";
import { useNouns } from "@/lib/pack-context";

/** The body of the file the "Refuses" chip uploads. Synthetic, one line, and never read: the guard
 *  fires on the NAME before a byte is parsed, so this text exists only so the upload is a real file
 *  rather than an empty one. */
const IDENTITY_TRIAL_FILE_TEXT =
  "Synthetic placeholder. Nothing real, and nothing here is read — this file is refused on its " +
  "name before extraction runs.\n";

export default function IntakeUploadPage() {
  const router = useRouter();
  const nouns = useNouns();
  const trials = usePackTrials("intake");
  const [documentId, setDocumentId] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Kept apart from `error`, which renders next to the Read button at the foot of the page: a
  // refusal a chip at the top of the page caused has to appear where the click happened, or it
  // reads as a chip that did nothing.
  const [trialRefusal, setTrialRefusal] = useState<string | null>(null);
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

  // The chips' version of the fixture buttons: the same two gateway calls, without the click in
  // between. The buttons above fill the box and stop, which is one click short of an outcome and
  // leaves a visitor who does not know this page staring at 6 KB of letterhead.
  const trialRead = useMutation({
    mutationFn: async (name: string) => {
      const loaded = await intakeApi.fixtureText(name);
      if (isRefusal(loaded)) return loaded;
      // Show what is about to be read before reading it, so the pipeline's input is on screen.
      setDocumentId(name);
      setText(loaded.text);
      return intakeApi.upload(name, loaded.text);
    },
    onSuccess: (res) => {
      if (isRefusal(res)) {
        setTrialRefusal(res.refusal.message);
        return;
      }
      setTrialRefusal(null);
      router.push(`/frontdesk/${encodeURIComponent(res.intake_id)}`);
    },
    onError: (e: Error) => setTrialRefusal(`Could not read that document: ${e.message}`),
  });

  // `TRY.md` §E6, without a file picker. The name is the whole payload — `screen_upload` refuses on
  // it before extraction, so nothing is stored whichever way this lands.
  const trialIdentityFile = useMutation({
    mutationFn: (filename: string) =>
      intakeApi.uploadFile(
        new File([IDENTITY_TRIAL_FILE_TEXT], filename, { type: "text/plain" }),
        filename,
      ),
    onSuccess: (res) => {
      if (isRefusal(res)) {
        setExtracted(null);
        setTrialRefusal(res.refusal.message);
        return;
      }
      // Not expected, and not swallowed: if the guard ever stops firing, the card it built is
      // shown rather than a chip that quietly looks like it worked.
      setTrialRefusal(null);
      setExtracted(res);
    },
    onError: (e: Error) => setTrialRefusal(`Could not reach the gateway: ${e.message}`),
  });

  function runTrial(trial: IntakeTrial) {
    setError(null);
    setTrialRefusal(null);
    setExtracted(null);
    if (trial.kind === "fixture") trialRead.mutate(trial.name);
    else trialIdentityFile.mutate(trial.filename);
  }

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
        <h1 className="text-lg font-semibold">Read a {nouns.document}</h1>
        <p className="text-xs text-subtle">
          {nouns.document_examples}. Upload the file, or paste its text. The system reads it before
          the {nouns.subject} reaches the {nouns.operator_role}.
        </p>
      </header>

      <div className="flex items-start gap-2 rounded-lg border border-band-review/40 bg-band-review/[0.05] px-3 py-2">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
        <div className="text-2xs text-subtle" data-testid="identity-refusal-notice">
          <p>
            <span className="font-semibold text-band-review">
              Do not upload {nouns.identifier} or {nouns.credential} documents.
            </span>{" "}
            Identity and credential validity are checked in person, by a person, against the
            physical document. This system never does that and has no code path that could.
          </p>
          <p className="mt-1">
            <span className="font-medium text-fg">What is accepted:</span> the {nouns.document} —
            {" "}{nouns.document_examples} — as a PDF, a photograph or scan, or as text.
          </p>
          <p className="mt-1">
            <span className="font-medium text-fg">What happens to a refused file:</span> if it is
            named like an identity document it is refused before it is opened. Otherwise it is{" "}
            <span className="font-medium text-fg">read by OCR first</span> — a photographed
            identity card cannot be recognised any other way — and refused on the extracted text, before that
            text is stored, before any model sees it, and before a record exists. Nothing from it
            is kept: no document, no queue entry, no ledger row, and the refusal never quotes what
            it read. (A file over 1 MB briefly touches a temporary file that the server then
            deletes.)
          </p>
        </div>
      </div>

      <TryChips
        groups={trials}
        onPick={(chip) => runTrial(chip.payload)}
        disabled={trialRead.isPending || trialIdentityFile.isPending}
        testId="intake-try-chips"
        className="max-w-3xl"
      />

      {trialRefusal && (
        // Rendered where the click was, in the gateway's own words. `IDENTITY_DOCUMENT_REFUSED` is
        // the mechanism behind the banner above working, so it is shown as a result, not an error.
        <p
          className="max-w-3xl whitespace-pre-line rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-2xs text-fg"
          data-testid="intake-trial-refusal"
          role="status"
        >
          {trialRefusal}
        </p>
      )}

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
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:bg-muted disabled:text-subtle"
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
