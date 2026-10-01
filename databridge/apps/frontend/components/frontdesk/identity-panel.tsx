"use client";
/**
 * Section 1 of the summary card: **Identity to verify in person.**
 *
 * This component is deliberately the least automated thing on the screen, and it is styled to look
 * that way. Everything else on the card is a machine reading a document; this is a person looking
 * at another person.
 *
 * Four things it must never do, each of which a "helpful" UI would do by default — and two of
 * which the first draft actually did:
 *
 *   1. Pre-select the top candidate. Even at score 1.0. Auto-selecting on high confidence is the
 *      machine deciding identity with extra steps, and it costs exactly one click to avoid.
 *   2. Hide the low-scoring candidates. A name-only match at 0.15 is shown WITH its score.
 *   3. **Carry an attestation to a person it was not made about.** Two ways this happened:
 *      across intakes (`attestedFor`/`picked` reset whenever the intake changes), and — the one
 *      review caught on the confirm button and which this panel had too — WITHIN one intake, by
 *      picking a different candidate after ticking the box. The intake id does not change when the
 *      selected candidate does, so the attestation is keyed to the PATIENT ID it was made for and
 *      `attested` is derived from `attestedFor === picked`, never stored.
 *   4. **Style a score as a verdict.** A high score means an identifier matched, not that the
 *      person is who they say. The pill is neutral-toned and labelled accordingly.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, ShieldAlert, UserCheck } from "lucide-react";
import type { IntakeCandidate, IntakeIdentity, ConfirmationView } from "@/lib/intake-types";
import { cn } from "@/lib/utils";
import { useNouns } from "@/lib/pack-context";

/** At or above this, at least one IDENTIFIER matched rather than only a name.
 *  Mirrors `resolve.HIGH_CONFIDENCE_SCORE`. Used only to LABEL the score, never to decide. */
const HIGH_CONFIDENCE = 0.6;

function ScorePill({ score }: { score: number }) {
  const weak = score < HIGH_CONFIDENCE;
  return (
    <span
      className={cn(
        "tnum rounded px-1.5 py-0.5 text-2xs font-medium",
        // Neither tone is "green/approved". A match score says how much of the document lined up
        // with a registry row — it is never a verdict on who the person is.
        weak ? "bg-band-review/15 text-band-review" : "bg-muted text-fg",
      )}
      title={
        weak
          ? "Name-only match — no identifier on the document corroborates it"
          : "An identifier on the document matched this registry row. Still not a verified identity."
      }
    >
      {score.toFixed(2)}
    </span>
  );
}

export function IdentityPanel({
  intakeId,
  identity,
  confirmation,
  staffId,
  onStaffIdChange,
  onSelect,
  selecting,
  busy,
  error,
}: {
  intakeId: string;
  identity: IntakeIdentity;
  confirmation?: ConfirmationView;
  staffId: string;
  onStaffIdChange: (v: string) => void;
  onSelect: (patientId: string, attested: boolean) => void;
  selecting: boolean;
  /** True while ANY mutation on this intake is in flight — selection is locked during a confirm. */
  busy: boolean;
  error?: string | null;
}) {
  // NOT a bare boolean: the patient id the attestation was ticked FOR.
  //
  // As a bool this had the same shape as the hard-constraint-1 bug review found on the confirm
  // button. `attested` reset only when `intakeId` changed — but picking a different candidate does
  // NOT change the intake id, so staff could tick "I checked this patient's ID against the person
  // in front of me" for candidate A, click a different candidate B, and submit B carrying an
  // attestation nobody ever made for B. An attestation is about ONE person; storing whose it is
  // makes it unforgeable by state that outlived its subject.
  const [attestedFor, setAttestedFor] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const nouns = useNouns();

  // Reset every local decision when the intake changes. An attestation is a record of something a
  // person did for a SPECIFIC patient; carrying it to the next screen would be a forged one.
  useEffect(() => {
    setAttestedFor(null);
    setPicked(null);
  }, [intakeId]);

  const alreadySelected = confirmation?.selected_patient_id ?? null;
  const locked = Boolean(confirmation?.committed);
  const candidateIds = new Set(identity.candidates.map((c) => c.patient_id));
  // Belt and braces with the reset above: the pick must be a candidate OF THIS intake.
  const pickIsValid = picked !== null && candidateIds.has(picked);
  // Derived, never stored: the box is ticked only while the pick still matches who it was ticked
  // for. Changing candidate therefore unticks it on screen — the state cannot silently disagree
  // with what the operator is looking at.
  const attested = picked !== null && attestedFor === picked;
  const canSelect = attested && pickIsValid && !selecting && !busy && !locked;

  return (
    <section
      data-testid="identity-panel"
      className="rounded-lg border-2 border-band-review/50 bg-band-review/[0.04] p-4"
    >
      <header className="mb-3 flex items-start gap-2">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-band-review" />
        <div>
          <h2 className="text-sm font-semibold">Identity — verify in person</h2>
          <p className="mt-0.5 text-2xs text-subtle">
            The system does not verify identity and never will. Check the {nouns.subject}&apos;s{" "}
            {nouns.identifier} against the person in front of you, then select their registry record.
          </p>
        </div>
      </header>

      {identity.notes.map((note) => (
        <p
          key={note}
          className="mb-2 flex items-start gap-1.5 rounded bg-muted/60 px-2 py-1.5 text-2xs text-subtle"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-band-review" />
          {note}
        </p>
      ))}

      {identity.candidates.length === 0 ? (
        <p className="rounded border border-border bg-surface px-3 py-4 text-xs text-subtle">
          No registry record matches this document
          {identity.status === "insufficient_identifiers"
            ? ` — it carries no name or identifier to search by. Register this ${nouns.subject} at the counter.`
            : `. Register this ${nouns.subject} at the counter.`}
        </p>
      ) : (
        <ul
          className="flex flex-col gap-1.5"
          data-testid="candidate-list"
          role="radiogroup"
          aria-label="Registry candidates — select one after checking identity in person"
        >
          {identity.candidates.map((c: IntakeCandidate) => {
            const isPicked = picked === c.patient_id;
            const wasSelected = alreadySelected === c.patient_id;
            return (
              <li key={c.patient_id}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={isPicked || wasSelected}
                  disabled={locked || busy}
                  data-testid={`candidate-${c.patient_id}`}
                  onClick={() => setPicked(isPicked ? null : c.patient_id)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded border px-3 py-2 text-left transition-colors",
                    wasSelected
                      ? "border-accent bg-accent/10"
                      : isPicked
                        ? "border-accent bg-accent/10"
                        : "border-border bg-surface hover:bg-muted",
                    (locked || busy) && "cursor-not-allowed opacity-70",
                  )}
                >
                  <span className="flex-1">
                    <span className="block text-sm font-medium">{c.name}</span>
                    <span className="tnum block text-2xs text-subtle">
                      {c.id_number} · born {c.date_of_birth}
                    </span>
                    <span className="block text-2xs text-subtle">from the {nouns.registry}</span>
                  </span>
                  <span className="flex flex-col items-end gap-1">
                    <ScorePill score={c.score} />
                    <span className="text-2xs text-subtle">
                      matched on {c.matched_on.join(", ") || "nothing"}
                    </span>
                    {c.conflicts.length > 0 && (
                      <span className="text-2xs font-medium text-band-review">
                        {c.conflicts.join(", ")}
                      </span>
                    )}
                  </span>
                  {wasSelected && <UserCheck className="h-4 w-4 shrink-0 text-accent" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {identity.candidates.length > 0 && !locked && (
        <div className="mt-3 border-t border-border pt-3">
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              data-testid="attestation-checkbox"
              checked={attested}
              // Unattestable until a candidate is chosen: "I checked this patient's ID" is a claim
              // about a specific registry record, so there has to be one to make it about.
              disabled={busy || picked === null}
              onChange={(e) => setAttestedFor(e.target.checked && picked ? picked : null)}
              className="mt-0.5 h-3.5 w-3.5 accent-[var(--band-review)] disabled:opacity-50"
            />
            <span className={cn(picked === null && "text-subtle")}>
              I have checked this {nouns.subject}&apos;s {nouns.identifier}{" "}
              <strong>against the person in front of me</strong>.
            </span>
          </label>

          <div className="mt-2 flex items-center gap-2">
            <label htmlFor="staff-id" className="text-2xs text-subtle">
              Your staff ID
            </label>
            <input
              id="staff-id"
              type="text"
              value={staffId}
              disabled={busy}
              onChange={(e) => onStaffIdChange(e.target.value)}
              data-testid="staff-id"
              className="w-40 rounded border border-border bg-surface px-2 py-1 text-xs"
            />
            <button
              type="button"
              data-testid="select-patient"
              disabled={!canSelect || !staffId.trim()}
              onClick={() => picked && onSelect(picked, attested)}
              className={cn(
                "rounded px-3 py-1.5 text-xs font-medium transition-colors",
                canSelect && staffId.trim()
                  ? "bg-accent text-accent-fg hover:opacity-90"
                  : "cursor-not-allowed bg-muted text-subtle",
              )}
            >
              {selecting ? "Recording…" : `Select this ${nouns.subject}`}
            </button>
          </div>

          {!attested && (
            <p className="mt-1.5 text-2xs text-subtle" data-testid="attestation-hint">
              {picked === null
                ? "Choose the registry record for the person in front of you, then attest."
                : "Tick the attestation to enable selection."}
            </p>
          )}
          {attestedFor !== null && picked !== null && attestedFor !== picked && (
            // Shown from the moment a different candidate is picked until the operator attests
            // again. An explicit statement rather than a silent untick: staff should see WHY the
            // box cleared, or they will assume the click failed and re-tick by reflex — which is
            // the behaviour that made the original bug harmless-looking.
            <p className="mt-1.5 text-2xs text-band-review">
              You changed the selected record, so the attestation was cleared. Check this
              {nouns.subject}&apos;s ID against the person in front of you again.
            </p>
          )}
          {error && (
            <p
              role="alert"
              className="mt-1.5 text-2xs font-medium text-band-review"
              data-testid="identity-error"
            >
              {error}
            </p>
          )}
        </div>
      )}

      {confirmation?.identity_checked_in_person_by && (
        <p className="mt-3 border-t border-border pt-2 text-2xs text-subtle">
          {/* "attested by", never "verified". The record is that a person SAID they checked. */}
          Attested by{" "}
          <span className="font-medium text-fg">{confirmation.identity_checked_in_person_by}</span>
          {confirmation.identity_checked_at && (
            <> at {new Date(confirmation.identity_checked_at).toLocaleTimeString()}</>
          )}
          . Only that staff member can confirm this intake.
        </p>
      )}
    </section>
  );
}
