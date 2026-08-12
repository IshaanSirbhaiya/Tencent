"use client";
/**
 * `/intake` — the kiosk/patient side: drop a referring document in and read it.
 *
 * The fixture buttons exist because a live demo cannot depend on someone having a chit letter to
 * hand. Each one fetches the real corpus text from the gateway and then posts it through the same
 * `/intake/upload` endpoint a paste uses — there is no separate demo path, so what a judge sees is
 * exactly what the pipeline does.
 *
 * There is deliberately no e-card upload. Insurance e-cards are verified in person at the counter;
 * we never OCR, parse or validate one (PLAN.md §6.3).
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FileUp, ShieldAlert } from "lucide-react";
import { intakeApi } from "@/lib/intake-api";
import { isRefusal } from "@/lib/intake-types";

export default function IntakeUploadPage() {
  const router = useRouter();
  const [documentId, setDocumentId] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="flex flex-col gap-4 p-6">
      <header>
        <h1 className="text-lg font-semibold">Read a referring document</h1>
        <p className="text-xs text-subtle">
          Paste an insurer referral letter, voucher, underwriting memo, TPA authorisation form or HR
          email. The system reads it before the patient reaches the counter.
        </p>
      </header>

      <div className="flex items-start gap-2 rounded-lg border border-band-review/40 bg-band-review/[0.05] px-3 py-2">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-band-review" />
        <p className="text-2xs text-subtle">
          Do not upload insurance e-cards or photographs of identity documents. Identity and
          e-card validity are checked in person at the counter. This screen accepts no images at
          all, and any identity document submitted to the API is refused and nothing from it is
          stored.
        </p>
      </div>

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
        {error && <span className="text-2xs text-band-review">{error}</span>}
      </div>
    </div>
  );
}
