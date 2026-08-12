"use client";
/**
 * The sign-in screen, and the signed-in chip in the sidebar.
 *
 * Read the header of `lib/staff-session.tsx` first — it explains the one line this must not cross.
 * The short version: signing in says *who* is at the terminal. It never says a patient's identity
 * was checked. That remains a per-patient tick, reset on every intake.
 *
 * This is deliberately NOT authentication. There is no password, no directory, no session token
 * the gateway validates. A POC that shipped a fake login and called it access control would be
 * worse than one with no login at all, so the screen says so out loud rather than implying a
 * security property it does not have.
 */
import * as React from "react";
import { LogOut, ShieldCheck, UserCircle2 } from "lucide-react";
import { isValidStaffId, useStaffSession } from "@/lib/staff-session";

/** Front-desk staff on the synthetic roster. Convenience for the demo, not a permission list. */
const ROSTER: { staffId: string; name: string; role: string }[] = [
  { staffId: "PS-FD-014", name: "Nurul Aisyah", role: "Front desk — Registration" },
  { staffId: "PS-FD-027", name: "Tan Jia Hui", role: "Front desk — Registration" },
  { staffId: "PS-RN-103", name: "Kavitha Raman", role: "Clinic nurse" },
];

export function StaffGate({ children }: { children: React.ReactNode }) {
  const { staff, ready, signIn } = useStaffSession();
  const [staffId, setStaffId] = React.useState("");
  const [name, setName] = React.useState("");

  // Render nothing for the one frame before `sessionStorage` has been read. Showing the sign-in
  // screen first would flash it at staff who are already signed in on every navigation.
  if (!ready) return null;
  if (staff) return <>{children}</>;

  // Same rule as the gateway's `STAFF_ID_PATTERN`. Rejecting here turns an opaque 422 on the
  // confirm button into an obvious "that is not a staff id" at the point of entry.
  const canSubmit = isValidStaffId(staffId);

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg p-6">
      <div className="w-full max-w-md">
        <div className="mb-5 text-center">
          <h1 className="text-lg font-semibold">Walnut — Parkway Shenton</h1>
          <p className="mt-1 text-xs text-subtle">
            Reads a patient&apos;s referring document before they reach the counter.
          </p>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit) signIn(staffId, name);
          }}
          className="rounded-lg border border-border bg-surface p-5"
        >
          <div className="mb-4 flex items-start gap-2">
            <UserCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
            <div>
              <h2 className="text-sm font-semibold">Who is at this terminal?</h2>
              <p className="mt-0.5 text-2xs text-subtle">
                Your staff id is written into the evidence ledger next to everything you confirm.
              </p>
            </div>
          </div>

          <label className="mb-1 block text-2xs font-medium uppercase tracking-wider text-subtle">
            Staff ID
          </label>
          <input
            type="text"
            value={staffId}
            onChange={(e) => setStaffId(e.target.value)}
            placeholder="PS-FD-014"
            data-testid="staff-id-input"
            autoFocus
            maxLength={64}
            className="mb-3 w-full rounded border border-border bg-bg px-3 py-2 text-xs"
          />
          {staffId.trim() && !canSubmit ? (
            <p className="mb-3 text-2xs text-band-review">
              2–64 characters: letters, digits, and <code>. _ - :</code>. This is written into the
              evidence ledger, so it has to be a real staff id.
            </p>
          ) : null}

          <label className="mb-1 block text-2xs font-medium uppercase tracking-wider text-subtle">
            Name <span className="font-normal normal-case tracking-normal text-subtle/70">(optional)</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Nurul Aisyah"
            data-testid="staff-name-input"
            className="mb-4 w-full rounded border border-border bg-bg px-3 py-2 text-xs"
          />

          <button
            type="submit"
            disabled={!canSubmit}
            data-testid="staff-sign-in"
            className="w-full rounded bg-accent px-3 py-2 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:bg-muted disabled:text-subtle"
          >
            Start shift
          </button>

          <div className="mt-4 border-t border-border pt-3">
            <p className="mb-1.5 text-2xs text-subtle">Or pick from the synthetic roster:</p>
            <div className="flex flex-col gap-1">
              {ROSTER.map((r) => (
                <button
                  key={r.staffId}
                  type="button"
                  data-testid={`roster-${r.staffId}`}
                  onClick={() => signIn(r.staffId, r.name)}
                  className="flex items-center justify-between rounded border border-border bg-bg px-2.5 py-1.5 text-left text-2xs transition-colors hover:bg-muted"
                >
                  <span className="text-fg">{r.name}</span>
                  <span className="tnum text-subtle">{r.staffId}</span>
                </button>
              ))}
            </div>
          </div>
        </form>

        {/* Say plainly what this is. An unauthenticated field that looks like a login is a
            governance claim we have not earned, and the submission writeup says the same. */}
        <p className="mt-4 flex items-start gap-1.5 rounded border border-band-review/40 bg-band-review/[0.05] px-3 py-2 text-2xs text-subtle">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-band-review" />
          <span>
            Prototype: this records who is operating the terminal, so the ledger can attribute each
            confirmation. It is not authentication — a production deployment signs in against the
            hospital directory (Entra ID). Signing in never attests to a patient&apos;s identity;
            that stays a per-patient check you make in person.
          </span>
        </p>
      </div>
    </div>
  );
}

function ShiftAge({ signedInAt }: { signedInAt: string }) {
  const started = Date.parse(signedInAt);
  if (!Number.isFinite(started)) return null;
  const hours = Math.floor((Date.now() - started) / 3_600_000);
  if (hours < 1) return null;
  return (
    <span
      className={hours >= 8 ? "shrink-0 tnum text-band-review" : "shrink-0 tnum text-subtle"}
      title={`Shift started ${new Date(started).toLocaleString("en-GB")}`}
    >
      {hours}h
    </span>
  );
}

/** The signed-in chip. Lives in the sidebar footer so the attributed name is always on screen. */
export function StaffChip() {
  const { staff, signOut } = useStaffSession();
  if (!staff) return null;
  return (
    <div className="flex items-center gap-1.5" data-testid="staff-chip">
      <UserCircle2 className="h-3 w-3 shrink-0 text-accent" />
      <span className="min-w-0 flex-1 truncate text-fg" title={`${staff.name} · ${staff.staffId}`}>
        {staff.name}
      </span>
      {/* The shift age, so a terminal left signed in overnight is visibly stale rather than
          silently attributing today's confirmations to yesterday's operator. */}
      <ShiftAge signedInAt={staff.signedInAt} />
      <button
        type="button"
        onClick={signOut}
        title="End shift"
        data-testid="staff-sign-out"
        className="shrink-0 rounded p-0.5 text-subtle transition-colors hover:bg-muted hover:text-fg"
      >
        <LogOut className="h-3 w-3" />
      </button>
    </div>
  );
}
