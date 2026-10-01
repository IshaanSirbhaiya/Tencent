"use client";
/**
 * `/frontdesk/[id]` — the summary card, and the confirmation gate.
 *
 * The page is ordered the way the counter works: verify identity, then read what was authorised,
 * then approve the billing. Identity is first because everything below it is downstream of a
 * decision nobody has made yet.
 *
 * Three rules this file exists to hold, all of which the first draft broke:
 *
 *   * **The SERVER decides whether the gate is open.** The browser renders `ready_to_commit`; it
 *     never computes it. A client-side rule can be wrong, stale, or edited in devtools.
 *   * **The confirm request must carry a real human act.** It sends the confirm checkbox's actual
 *     value. Hardcoding `attested_in_person: true` — which the first draft did — fabricates the
 *     exact attestation the ledger then records under someone's name.
 *   * **Never act on a card the server has since refused or failed to refresh.** If the refetch
 *     errors, the gate is treated as closed rather than falling back to the cached answer.
 */
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Lock, ShieldCheck } from "lucide-react";
import { intakeApi } from "@/lib/intake-api";
import { isRefusal, type SummaryCard } from "@/lib/intake-types";
import { IdentityPanel } from "@/components/frontdesk/identity-panel";
import {
  BillingSection,
  CoverageSection,
  HonestySection,
  QuestionnaireSection,
  ReadSourceBanner,
} from "@/components/frontdesk/card-sections";
import { cn } from "@/lib/utils";
import { useStaffSession } from "@/lib/staff-session";
import { useNouns } from "@/lib/pack-context";

export default function FrontDeskCardPage() {
  const nouns = useNouns();
  const params = useParams<{ id: string }>();
  const intakeId = decodeURIComponent(params.id);
  const qc = useQueryClient();

  // Prefilled from the shift session: retyping a staff id on every card is the kind of friction
  // that gets worked around with a shared login. The ATTESTATION is deliberately not prefilled —
  // `IdentityPanel` resets its checkbox on every intake, and the gateway requires the boolean
  // explicitly (CLAUDE.md constraints 1 and 4).
  const { staff } = useStaffSession();
  const [staffId, setStaffId] = useState(staff?.staffId ?? "");
  // Separate error channels: a selection refusal rendered under "Confirm" tells staff the wrong
  // thing about which action failed.
  const [selectError, setSelectError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  // NOT a bare boolean: the patient id this confirmation was ticked FOR.
  //
  // As a bool this was a hard-constraint-1 violation, found in review. `confirmAttested` reset only
  // on `intakeId`, but selecting a different candidate does NOT change the intake id — so staff
  // could select candidate A, tick "I confirm I checked this patient's identity", then select
  // candidate B, and the button would commit B carrying an attestation nobody ever made for B.
  // Storing the id makes the claim unforgeable by state that outlived its subject: it is checked
  // against the CURRENT selected patient at the moment of confirming.
  const [confirmAttestedFor, setConfirmAttestedFor] = useState<string | null>(null);

  // Everything local resets when the intake changes — see IdentityPanel for why this matters.
  useEffect(() => {
    // Re-seed from the shift session rather than blanking. Setting "" here wiped the prefill on
    // every card (this effect also runs on mount), so the feature silently did nothing and staff
    // had to retype their id per patient — exactly the friction that gets solved with a shared
    // login.
    setStaffId(staff?.staffId ?? "");
    setSelectError(null);
    setConfirmError(null);
    setConfirmAttestedFor(null);
  }, [intakeId, staff?.staffId]);

  const cardQ = useQuery({
    queryKey: ["intake-card", intakeId],
    queryFn: () => intakeApi.card(intakeId),
    // Another workstation can change this intake's gate state. Re-read it rather than trusting a
    // card that has been sitting on screen.
    refetchInterval: 8000,
    refetchOnWindowFocus: true,
  });

  const select = useMutation({
    mutationFn: ({ patientId, attested }: { patientId: string; attested: boolean }) =>
      intakeApi.selectPatient(intakeId, patientId, staffId.trim(), attested),
    onSuccess: (res) => {
      setSelectError(isRefusal(res) ? res.refusal.message : null);
      qc.invalidateQueries({ queryKey: ["intake-card", intakeId] });
      qc.invalidateQueries({ queryKey: ["intake-queue"] });
    },
    onError: (e: Error) => setSelectError(`Could not reach the gateway: ${e.message}`),
  });

  const confirm = useMutation({
    mutationFn: (attested: boolean) => intakeApi.confirm(intakeId, staffId.trim(), attested),
    onSuccess: (res) => {
      setConfirmError(isRefusal(res) ? res.refusal.message : null);
      setConfirmAttestedFor(null);
      qc.invalidateQueries({ queryKey: ["intake-card", intakeId] });
      qc.invalidateQueries({ queryKey: ["intake-queue"] });
    },
    onError: (e: Error) => setConfirmError(`Could not reach the gateway: ${e.message}`),
  });

  if (cardQ.isLoading) return <p className="p-6 text-xs text-subtle">Loading…</p>;
  if (cardQ.isError) {
    return (
      <p className="p-6 text-xs text-band-review" role="alert">
        Could not load this intake: {(cardQ.error as Error).message}
      </p>
    );
  }
  if (!cardQ.data || isRefusal(cardQ.data)) {
    return (
      <p className="p-6 text-xs text-band-review" role="alert">
        {cardQ.data && isRefusal(cardQ.data) ? cardQ.data.refusal.message : "Intake not found."}
      </p>
    );
  }

  const card = cardQ.data as SummaryCard;
  const gate = card.confirmation;
  const busy = select.isPending || confirm.isPending;
  // Gated on: the server says ready, nothing else is in flight, the card is not stale after a
  // failed refetch, AND the confirm attestation is ticked now — not inherited from the selection.
  // The attestation must name the patient we are about to commit. A tick made for another
  // candidate is not consent for this one.
  const confirmAttested =
    confirmAttestedFor !== null && confirmAttestedFor === (gate?.selected_patient_id ?? null);
  const canConfirm =
    Boolean(gate?.ready_to_commit) &&
    !gate?.committed &&
    !busy &&
    !cardQ.isRefetchError &&
    confirmAttested &&
    Boolean(staffId.trim());

  return (
    <div className="flex flex-col gap-4 p-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold">{card.document.document_id}</h1>
          <p className="text-xs text-subtle">
            {card.document.document_type.replace(/_/g, " ")} · classified with{" "}
            {Math.round(card.document.confidence * 100)}% confidence · received via{" "}
            {card.document.channel}
          </p>
        </div>
        {gate?.committed && (
          <span className="inline-flex items-center gap-1.5 rounded bg-band-auto/15 px-2 py-1 text-2xs font-medium text-band-auto">
            <ShieldCheck className="h-3.5 w-3.5" /> committed to the ledger
          </span>
        )}
      </header>

      <ReadSourceBanner card={card} />

      {cardQ.isRefetchError && (
        <p
          role="alert"
          className="flex items-center gap-1.5 rounded border border-band-review/40 bg-band-review/[0.06] px-3 py-2 text-2xs text-band-review"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          This card could not be refreshed, so it may be out of date. Confirming is disabled until
          it reloads.
        </p>
      )}

      <IdentityPanel
        intakeId={intakeId}
        identity={card.identity}
        confirmation={gate}
        staffId={staffId}
        onStaffIdChange={setStaffId}
        onSelect={(patientId, attested) => select.mutate({ patientId, attested })}
        selecting={select.isPending}
        busy={busy}
        error={selectError}
      />

      <CoverageSection card={card} />
      <BillingSection card={card} />
      <QuestionnaireSection card={card} />
      <HonestySection card={card} />

      <section className="rounded-lg border border-border bg-surface p-4">
        <h2 className="mb-2 text-sm font-semibold">Confirm</h2>
        {card.identity.candidates.length === 0 ? (
          // The walk-in / no-match path. There is no registry row to select, so the gate can never
          // open here — say that, instead of rendering a permanently disabled button and a
          // "identity has not been verified" message the screen offers no way to act on.
          <p className="text-xs text-subtle" data-testid="no-candidate-path">
            No registry record matches this document, so there is nothing to confirm against.
            Register this {nouns.subject} at the counter; the intake stays here as a record of what the
            document said.
          </p>
        ) : gate?.committed ? (
          <p className="text-xs text-subtle">
            Committed by{" "}
            <span className="font-medium text-fg">{gate.identity_checked_in_person_by}</span>. The
            evidence ledger is append-only — a correction is a new entry, never an edit.
          </p>
        ) : (
          <>
            <p className="mb-2 flex items-start gap-1.5 text-xs text-subtle">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {gate?.ready_to_commit
                ? `A ${nouns.subject} has been selected against an in-person check. Confirming writes these values to the append-only evidence ledger.`
                : (gate?.blocked_because ?? "The gate is closed.")}
            </p>

            {gate?.ready_to_commit && (
              <label className="mb-2 flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  data-testid="confirm-attestation"
                  checked={confirmAttested}
                  disabled={busy}
                  onChange={(e) =>
                    setConfirmAttestedFor(e.target.checked ? gate?.selected_patient_id ?? null : null)
                  }
                  className="mt-0.5 h-3.5 w-3.5 accent-[var(--band-auto)]"
                />
                <span>
                  I confirm I checked this {nouns.subject}&apos;s identity in person and that these values
                  are correct. My staff ID will be recorded against every entry.
                </span>
              </label>
            )}

            <button
              type="button"
              data-testid="confirm-intake"
              disabled={!canConfirm}
              onClick={() => confirm.mutate(confirmAttested)}
              className={cn(
                "rounded px-3 py-1.5 text-xs font-medium transition-colors",
                canConfirm
                  ? "bg-band-auto text-accent-fg hover:opacity-90"
                  : "cursor-not-allowed bg-muted text-subtle",
              )}
            >
              {confirm.isPending ? "Committing…" : "Confirm and commit"}
            </button>
            {confirmError && (
              <p
                role="alert"
                className="mt-1.5 text-2xs font-medium text-band-review"
                data-testid="confirm-error"
              >
                {confirmError}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
