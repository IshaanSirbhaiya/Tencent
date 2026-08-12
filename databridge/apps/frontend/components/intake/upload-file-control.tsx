"use client";
/**
 * The file drop for a referring document — the control that makes the OCR path reachable.
 *
 * It posts the file, unparsed, to `POST /intake/upload-file`. Nothing is read in the browser:
 * extraction happens once, server-side, and the text it produces is what is stored, what the
 * pipeline reads, and what every `doc_span` pointer resolves against. Doing any of that here
 * would give the record two possible texts and silently move every offset under the pointers.
 *
 * The accepted list below is a HINT to the file picker, not a control. The controls are on the
 * server: an identity-document screen on the filename before a byte is parsed, a second screen on
 * the extracted text before anything is stored, and a media sniff that refuses a file whose bytes
 * are not what it claims. A browser `accept` attribute is trivially bypassed and is not relied on.
 */
import { useRef, useState } from "react";
import { Loader2, Upload } from "lucide-react";

const ACCEPT = ".txt,.md,.eml,.json,.pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp";
const MAX_MB = 20;

export function UploadFileControl({
  onUpload,
  pending,
  disabled,
}: {
  onUpload: (file: File) => void;
  pending: boolean;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [name, setName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  function take(file: File | undefined) {
    if (!file) return;
    setName(file.name);
    onUpload(file);
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (!disabled && !pending) take(e.dataTransfer.files?.[0]);
      }}
      data-testid="upload-file-zone"
      className={`flex flex-col items-start gap-1.5 rounded-lg border border-dashed px-3 py-3 transition-colors ${
        dragging ? "border-accent bg-accent/[0.06]" : "border-border bg-surface"
      }`}
    >
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        data-testid="upload-file-input"
        className="hidden"
        onChange={(e) => {
          take(e.target.files?.[0]);
          // Reset so choosing the SAME file twice fires `change` again — otherwise a re-upload
          // after a refusal looks like a dead button.
          e.target.value = "";
        }}
      />
      <button
        type="button"
        disabled={pending || disabled}
        onClick={() => input.current?.click()}
        data-testid="upload-file-button"
        className="inline-flex items-center gap-1.5 rounded border border-border bg-elevated px-2.5 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
      >
        {pending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Upload className="h-3.5 w-3.5" />
        )}
        {pending ? "Reading the file…" : "Upload a document or scan"}
      </button>
      <p className="text-2xs text-subtle">
        Or drop it here. PDFs, photographs and scans (PNG, JPEG, TIFF) are read by OCR on the
        server; <span className="font-mono">.txt</span>, <span className="font-mono">.md</span> and{" "}
        <span className="font-mono">.eml</span> are taken verbatim. Up to {MAX_MB} MB.
        {name && (
          <>
            {" "}
            Last file: <span className="font-mono text-fg">{name}</span>.
          </>
        )}
      </p>
    </div>
  );
}
