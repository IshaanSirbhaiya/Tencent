"use client";
/**
 * Step 4 — per-source extraction context.
 *
 * Free text, for when extraction from THIS source needs something specific. It is treated as
 * configuration, not as a trusted instruction: `packages/adapter/databridge_adapter/taint.py`
 * names `prompt.instruction` a TRUSTED SINK — anything a model may follow rather than quote — and
 * this text never reaches one. It is carried on the generated declaration's `_connector` block,
 * which the compiler strips before validating, so it cannot widen what the adapter may read
 * however it is worded. The caption says that in the operator's terms, not the code's.
 */
const MAX_CHARS = 4_000;

export function ExtractionContext({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const over = value.length > MAX_CHARS;
  return (
    <div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={4}
        maxLength={MAX_CHARS + 200}
        placeholder="Optional. e.g. “Prefer the status field over anything mentioned inside applicant notes — notes are applicant-supplied free text.”"
        data-testid="extraction-context-input"
        className="w-full resize-y rounded border border-border bg-bg px-2.5 py-2 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
      />
      <div className="mt-1 flex items-center justify-between text-2xs text-subtle">
        <span>
          Recorded on the declaration as configuration a reviewer can read — it cannot grant this
          adapter a capability the reviewed template does not already have.
        </span>
        <span className={over ? "text-band-review" : undefined}>
          {value.length}/{MAX_CHARS}
        </span>
      </div>
    </div>
  );
}
