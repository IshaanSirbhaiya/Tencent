"use client";
/**
 * What the server did to turn an uploaded FILE into text, shown before the card is opened.
 *
 * The failure mode this exists to prevent: an OCR'd scan producing a summary card that looks
 * exactly like one typed at the counter. Every value on that card came out of a recognition step
 * that can misread a digit, and four of them may have been withheld entirely because the ballot
 * glyphs could not be proved recovered. None of that is visible on the card itself, so it is shown
 * here, on the way in, and the "Open the card" button sits *below* it.
 *
 * Everything rendered here is server-supplied (`SummaryCard.extraction`, `unresolved`, `refused`).
 * Nothing is computed in the browser — the same rule the rest of `/intake` follows.
 */
import { ArrowRight, ScanLine, FileText, TriangleAlert } from "lucide-react";
import type { SummaryCard } from "@/lib/intake-types";

/** The four paths whose value describes what was SELECTED on a checkbox form.
 *  Mirrors `extract.CHECKBOX_SENSITIVE` — used only to LABEL what the server already decided,
 *  never to decide anything. */
const CHECKBOX_DERIVED = [
  "authorisation.package_code",
  "authorisation.package_name",
  "authorisation.requested_tests",
  "authorisation.add_ons",
];

const FIELD_LABELS: Record<string, string> = {
  "authorisation.package_code": "package code",
  "authorisation.package_name": "package name",
  "authorisation.requested_tests": "requested tests",
  "authorisation.add_ons": "add-ons",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-wide text-subtle">{label}</div>
      <div className="truncate font-mono text-xs text-fg">{value}</div>
    </div>
  );
}

export function ExtractionReport({
  card,
  onOpen,
}: {
  card: SummaryCard;
  onOpen: () => void;
}) {
  const x = card.extraction;
  const ocred = (x?.ocr_pages.length ?? 0) > 0;
  // Only meaningful for a recognised document. On text that was READ, an unresolved checkbox
  // field means the plain thing — the document does not say it — and the card already shows that.
  // Labelling it "not read from this document" here would dress an ordinary absence up as a
  // withheld value, which is the same overclaiming in the opposite direction.
  const withheld = ocred ? CHECKBOX_DERIVED.filter((p) => card.unresolved.includes(p)) : [];

  return (
    <div
      data-testid="extraction-report"
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-3"
    >
      <div className="flex items-center gap-2">
        {ocred ? (
          <ScanLine className="h-4 w-4 shrink-0 text-band-spot" />
        ) : (
          <FileText className="h-4 w-4 shrink-0 text-subtle" />
        )}
        <h2 className="text-xs font-semibold text-fg">
          {ocred ? "Read by OCR" : "Read as text"}
        </h2>
        <span className="font-mono text-2xs text-subtle">{card.document.document_id}</span>
      </div>

      {x && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="extraction-stats">
          <Stat label="engine" value={x.engine} />
          <Stat label="file type" value={x.media_type} />
          <Stat
            label="pages recognised"
            value={
              ocred
                ? `${x.ocr_pages.join(", ")} of ${x.page_count || x.ocr_pages.length}`
                : `none of ${x.page_count || 1}`
            }
          />
          <Stat label="page regions" value={String(x.regions)} />
        </div>
      )}

      {ocred && (
        <p className="text-2xs text-subtle" data-testid="ocr-provenance-note">
          Every value read from a recognised page carries the region of the scan it was recognised
          from, with the engine&rsquo;s own confidence. Click a field on the card to see it — a
          value with no region on it did not come from the scan.
        </p>
      )}

      {(x?.warnings.length ?? 0) > 0 && (
        <ul className="flex flex-col gap-1.5" data-testid="extraction-warnings">
          {x!.warnings.map((w) => (
            <li
              key={w}
              className="flex items-start gap-2 rounded border border-band-spot/40 bg-band-spot/[0.06] px-2 py-1.5"
            >
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-spot" />
              <span className="text-2xs text-fg">{w}</span>
            </li>
          ))}
        </ul>
      )}

      {withheld.length > 0 && (
        <div
          className="rounded border border-band-review/40 bg-band-review/[0.05] px-2 py-1.5"
          data-testid="withheld-fields"
        >
          <div className="text-2xs font-semibold text-band-review">
            Not read from this document — take these from the original
          </div>
          <div className="mt-0.5 text-2xs text-subtle">
            {withheld.map((p) => FIELD_LABELS[p] ?? p).join(", ")}. A tick box the engine failed
            to recognise looks exactly like one that was never printed, so a selection recovered
            from a scan cannot be proved complete. Rather than ground an option the form may have
            declined, these are left unresolved.
          </div>
        </div>
      )}

      {card.refused.length > 0 && (
        <details data-testid="refused-values">
          <summary className="cursor-pointer text-2xs text-subtle">
            {card.refused.length} value{card.refused.length === 1 ? "" : "s"} dropped by the
            grounding wall
          </summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {card.refused.map((r) => (
              <li key={r} className="font-mono text-2xs text-subtle">
                {r}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div>
        <button
          type="button"
          onClick={onOpen}
          data-testid="open-card"
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg transition-colors hover:opacity-90"
        >
          Open the front-desk card
          <ArrowRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
