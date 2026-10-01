"use client";
/**
 * Sections 2–5 of the summary card: coverage, billing, questionnaire, and the honesty channels.
 *
 * Two rendering rules run through all of them:
 *
 *   * **Every value can be clicked through to its source.** `EvidenceLink` fetches the pointer and
 *     shows the exact characters the value was read from. That click is the product's whole claim,
 *     so it is a first-class control rather than a debug affordance.
 *   * **Absence and refusal are shown, not hidden.** "Not on document" and "we dropped this value
 *     because it wasn't on the page" are the two things a demo is most tempted to tidy away, and
 *     they are exactly what makes the rest of the card believable.
 */
import { useEffect, useState } from "react";
import { FileSearch, FlaskConical, Info, Network, XCircle } from "lucide-react";
import { intakeApi } from "@/lib/intake-api";
import { isRefusal, type BillingDraft, type SummaryCard } from "@/lib/intake-types";
import { cn } from "@/lib/utils";
import { useNouns } from "@/lib/pack-context";

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "determined"
      ? "bg-band-auto/15 text-band-auto"
      : status === "partial"
        ? "bg-band-spot/15 text-band-spot"
        : "bg-band-review/15 text-band-review";
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-2xs font-medium", tone)}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

/**
 * How this card's values were read — the first thing on the card (HC1/HC2, SWEEP #1).
 *
 * With no extraction model configured, the gateway serves a bundled sample extraction for a known
 * fixture. Those values were RECALLED, not read, and a card built from them is otherwise
 * indistinguishable from a live read. So an offline card says so in a banner that cannot be
 * missed, and a card whose read was not recorded says that too, rather than defaulting to "live".
 */
export function ReadSourceBanner({ card }: { card: Pick<SummaryCard, "offline_gold"> }) {
  if (card.offline_gold === false) {
    return (
      <p data-testid="read-source" data-read-source="live" className="text-2xs text-subtle">
        Read from this document by the live extraction model.
      </p>
    );
  }
  const offline = card.offline_gold === true;
  return (
    <div
      role="status"
      data-testid="read-source"
      data-read-source={offline ? "offline_gold" : "unrecorded"}
      className="flex items-start gap-2 rounded-lg border-2 border-band-spot/60 bg-band-spot/[0.07] px-3 py-2"
    >
      <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-band-spot" />
      <div className="text-xs">
        <p className="font-semibold text-band-spot">
          {offline ? "Offline sample, not a live read" : "How this card was read was not recorded"}
        </p>
        <p className="mt-0.5 text-2xs text-subtle">
          {offline
            ? "No extraction model is configured, so these values come from a bundled sample " +
              "extraction for this known document. They were not read from it just now. Every " +
              "value still carries its source pointer — check it before relying on it."
            : "The gateway did not say whether a live model or the offline sample produced " +
              "these values. Treat them as unverified until you have checked each source."}
        </p>
      </div>
    </div>
  );
}

/** A billing amount as the API declares it (SWEEP #15, HC3). The gateway sends currency-neutral
 *  amounts (`estimated_patient_payable`, `known_subtotal`) plus `currency`, which the pack's
 *  rules table declares. No currency is written into this file. A gateway older than HC3 sends
 *  only the DEPRECATED `<name>_<ccy>` keys; for that case alone the unit is read from the key's
 *  suffix. Remove that fallback when the deprecated keys are dropped. */
export function billingAmounts(b: BillingDraft): {
  currency: string | null;
  payable: string | null;
  subtotal: string | null;
} {
  const rec = b as unknown as Record<string, unknown>;
  const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
  const declared = str(rec.currency);
  if ("estimated_patient_payable" in rec || "known_subtotal" in rec) {
    return {
      currency: declared,
      payable: str(rec.estimated_patient_payable),
      subtotal: str(rec.known_subtotal),
    };
  }
  const pick = (prefix: string): [string | null, string | null] => {
    for (const [key, value] of Object.entries(rec)) {
      const m = key.match(new RegExp(`^${prefix}_([a-z]{3})$`));
      if (m) return [m[1].toUpperCase(), typeof value === "string" ? value : null];
    }
    return [null, null];
  };
  const [payCcy, payable] = pick("estimated_patient_payable");
  const [subCcy, subtotal] = pick("known_subtotal");
  return { currency: declared ?? payCcy ?? subCcy, payable, subtotal };
}

function money(currency: string | null, amount: string): string {
  // No currency declared → the number alone. Guessing one would be a fabricated unit.
  return currency ? `${currency} ${amount}` : amount;
}

/** Click-through to the source span. The evidence path is the product; make it visible. */
function EvidenceLink({ intakeId, fieldPath }: { intakeId: string; fieldPath: string }) {
  const [shown, setShown] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Evidence must never outlive the value it belongs to. Without this, navigating to another
  // intake left the previous document's source text sitting next to a new field, which reads as
  // provenance for something it never supported.
  useEffect(() => setShown(null), [intakeId, fieldPath]);

  async function look() {
    setBusy(true);
    setShown(null);                       // clear BEFORE the fetch, not only on success
    try {
      const res = await intakeApi.evidence(intakeId, fieldPath);
      setShown(
        isRefusal(res)
          ? res.refusal.message
          : (res.source_text ?? `${res.pointer.kind} · ${res.pointer.source_id}`),
      );
    } catch (e) {
      setShown(`could not load evidence: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        onClick={look}
        title={`Show the source text behind ${fieldPath}`}
        className="text-subtle transition-colors hover:text-accent"
        data-testid={`evidence-${fieldPath}`}
      >
        <FileSearch className="h-3 w-3" />
      </button>
      {busy && <span className="text-2xs text-subtle">…</span>}
      {shown && (
        <code
          data-testid={`evidence-text-${fieldPath}`}
          className="rounded bg-muted px-1 py-0.5 text-2xs text-fg"
        >
          {shown}
        </code>
      )}
    </span>
  );
}

export function CoverageSection({ card }: { card: SummaryCard }) {
  const c = card.coverage;
  const nouns = useNouns();
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold">Package &amp; authorised tests</h2>
        <StatusPill status={c.status} />
      </header>

      {c.status === "undetermined" && (
        <p
          data-testid="coverage-undetermined"
          className="mb-3 rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-xs"
        >
          The package could not be determined from this document. It is not guessed — confirm with
          the issuer before the visit.
        </p>
      )}

      {/* Shown even when the package is undetermined: the ISSUER usually IS resolved (chit 8
          knows it is MOL0199VME), and "we could not determine the package" is a different answer
          from "we know nothing about this document". */}
      <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-subtle">Issuer</dt>
        <dd className="flex items-center gap-1.5">
          {c.issuer_code ?? <span className="text-subtle">not on document</span>}
          {c.issuer_code && (
            <EvidenceLink intakeId={card.intake_id} fieldPath="issuer.issuer_code" />
          )}
        </dd>
        <dt className="text-subtle">Package</dt>
        <dd className="flex items-center gap-1.5">
          {c.package_code ?? "not stated on document"}
          {c.package_code && (
            <EvidenceLink intakeId={card.intake_id} fieldPath="authorisation.package_code" />
          )}
        </dd>
        <dt className="text-subtle">Questionnaire</dt>
        <dd>{c.questionnaire}</dd>
      </dl>

      {/* Graph-derived findings, called out separately and labelled as coming from the
          record rather than from this document. The distinction matters: staff reading "another
          document disagrees" need to know it is not something the letter in their hand says. */}
      {c.history_findings?.length > 0 && (
        <div
          data-testid="history-findings"
          className="mb-3 rounded border border-band-spot/40 bg-band-spot/[0.06] px-3 py-2"
        >
          <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-band-spot">
            <Network className="h-3 w-3" /> from the {nouns.record}, not this document
          </p>
          <ul className="flex flex-col gap-1">
            {c.history_findings.map((f, i) => (
              <li key={`${f.document_id}-${i}`} className="text-xs">
                {f.message}
                <span className="ml-1.5 text-2xs text-subtle">
                  ({f.sources.length} source{f.sources.length === 1 ? "" : "s"} cited)
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {c.eligibility_flags.length > 0 && (
        <ul className="mb-3 flex flex-wrap gap-1" data-testid="eligibility-flags">
          {c.eligibility_flags.map((f) => (
            <li
              key={f}
              className="rounded bg-band-review/15 px-1.5 py-0.5 text-2xs font-medium text-band-review"
            >
              {f.replace(/_/g, " ")}
            </li>
          ))}
        </ul>
      )}

      {c.authorised_tests.length === 0 && c.status !== "undetermined" && (
        <p className="mb-3 text-xs text-subtle">
          This document names no tests. The authorised scope must be confirmed with the issuer.
        </p>
      )}

      {c.authorised_tests.length > 0 && (
        <ul className="mb-3 flex flex-col gap-1">
          {c.authorised_tests.map((t, i) => (
            <li key={`${t.as_printed}-${i}`} className="flex items-center gap-2 text-xs">
              <span className="flex-1">{t.as_printed}</span>
              {t.canonical === null && (
                <span className="rounded bg-band-review/15 px-1.5 py-0.5 text-2xs text-band-review">
                  not in catalogue
                </span>
              )}
              {!t.in_package && t.canonical !== null && (
                <span className="rounded bg-band-spot/15 px-1.5 py-0.5 text-2xs text-band-spot">
                  beyond package
                </span>
              )}
              {t.source_field && (
                <EvidenceLink intakeId={card.intake_id} fieldPath={t.source_field} />
              )}
            </li>
          ))}
        </ul>
      )}

      {/* The rule-firing trace, verbatim. It is what makes the determination arguable rather
          than oracular — staff can disagree with a reason they can read. */}
      <details className="text-2xs text-subtle">
        <summary className="cursor-pointer select-none hover:text-fg">
          Why this determination ({c.reasoning.length} steps)
        </summary>
        <ol className="mt-1 list-decimal space-y-0.5 pl-4">
          {c.reasoning.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ol>
      </details>
    </section>
  );
}

export function BillingSection({ card }: { card: SummaryCard }) {
  const nouns = useNouns();
  const b = card.billing;
  const amounts = billingAmounts(b);
  const lowerBound = b.caveats.includes("TOTAL_IS_LOWER_BOUND");
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold">Billing</h2>
        <span className="rounded bg-muted px-1.5 py-0.5 text-2xs font-medium text-subtle">
          draft — staff approve
        </span>
      </header>

      <dl className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <dt className="text-subtle">Billed to</dt>
        <dd className="font-medium">{b.billed_to}</dd>
        <dt className="text-subtle">{nouns.subject_title} payable</dt>
        <dd className="tnum font-medium">
          {amounts.payable === null ? "unknown" : money(amounts.currency, amounts.payable)}
          {lowerBound && <span className="ml-1 text-band-review">(lower bound)</span>}
          {/* The gateway sends this ONLY when the total is unknown. Showing it as "so far"
              rather than as a total is the whole point: staff need the figure they can already
              quote, and must not read it as the amount owed. */}
          {amounts.payable === null && amounts.subtotal !== null && (
            <div className="text-2xs text-subtle">
              {money(amounts.currency, amounts.subtotal)} priced so far — not a total
            </div>
          )}
        </dd>
        <dt className="text-subtle">TPA claim</dt>
        <dd>{b.tpa_claim_required ? "required after visit" : "not required"}</dd>
      </dl>

      <ul className="flex flex-col gap-0.5 text-2xs text-subtle">
        {b.caveats
          .filter((c) => c !== "TOTAL_IS_LOWER_BOUND")
          .map((c, i) => (
            <li key={i} className="flex items-start gap-1">
              <Info className="mt-0.5 h-3 w-3 shrink-0" />
              {c}
            </li>
          ))}
      </ul>
    </section>
  );
}

export function QuestionnaireSection({ card }: { card: SummaryCard }) {
  const nouns = useNouns();
  const q = card.questionnaire;
  const prefilled = q.fields.filter((f) => f.status === "prefilled");
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold">Questionnaire pre-fill</h2>
        <span className="text-2xs text-subtle">
          {q.prefilled} pre-filled · {q.patient_must_answer} for the {nouns.subject}
        </span>
      </header>

      {q.prefilled === 0 ? (
        <p className="text-xs text-subtle">
          Nothing is pre-filled until staff select a {nouns.subject}. That is the gate, not a gap.
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-0.5 text-xs">
            {prefilled.map((f) => (
              <li key={f.field_id} className="flex items-center gap-2">
                <span className="w-40 shrink-0 text-subtle">{f.label}</span>
                <span className="flex-1 truncate">{String(f.value)}</span>
                {f.requires_confirmation && (
                  <span className="rounded bg-band-spot/15 px-1.5 py-0.5 text-2xs text-band-spot">
                    {nouns.subject} confirms
                  </span>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-2xs text-subtle">
            Medical, family and lifestyle history, and the declaration, are never pre-filled.
          </p>
        </>
      )}
    </section>
  );
}

/** `unresolved` and `refused` — the two channels a demo is most tempted to hide. */
export function HonestySection({ card }: { card: SummaryCard }) {
  if (card.unresolved.length === 0 && card.refused.length === 0) return null;
  return (
    <section className="rounded-lg border border-border bg-surface p-4" data-testid="honesty">
      <h2 className="mb-2 text-sm font-semibold">What this document does not say</h2>

      {card.unresolved.length > 0 && (
        <div className="mb-2">
          <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-subtle">
            Not on document ({card.unresolved.length})
          </p>
          <ul className="flex flex-wrap gap-1">
            {card.unresolved.map((u) => (
              <li key={u} className="rounded bg-muted px-1.5 py-0.5 text-2xs text-subtle">
                {u}
              </li>
            ))}
          </ul>
        </div>
      )}

      {card.refused.length > 0 && (
        <div>
          <p className="mb-1 text-2xs font-medium uppercase tracking-wide text-subtle">
            Values dropped by the grounding wall ({card.refused.length})
          </p>
          <ul className="flex flex-col gap-0.5">
            {card.refused.map((r, i) => (
              <li key={i} className="flex items-start gap-1 text-2xs text-band-review">
                <XCircle className="mt-0.5 h-3 w-3 shrink-0" />
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
