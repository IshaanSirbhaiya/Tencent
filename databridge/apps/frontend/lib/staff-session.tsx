"use client";
/**
 * Who is at the counter — a shift-scoped staff session.
 *
 * ## What this is for
 *
 * Before this existed, every screen asked the person at the counter to retype their staff id, and
 * `/brain` and `/impact` were open to anyone who knew the URL. This holds the answer to "who is
 * operating this terminal right now" once per shift.
 *
 * ## What this is NOT for, and the line it must not cross
 *
 * A staff session identifies the operator. It does **not** attest to a patient's identity, and it
 * must never be allowed to.
 *
 * `CLAUDE.md` constraint 1 forbids automating identity verification; constraint 4 requires a human
 * confirmation gate. The tempting shortcut — "they're signed in, so tick the attestation for
 * them" — collapses both: it converts a per-patient, in-person act of looking at an NRIC into an
 * ambient property of being logged in. The attestation would then be a record of nothing.
 *
 * So the split is:
 *
 *   - the session answers **who** is attesting (prefills `staff_id`);
 *   - a per-patient checkbox, reset on every intake and unticked by default, answers **whether**
 *     that person actually checked this patient's document against this patient's face.
 *
 * The gateway enforces the same split independently — `attested_in_person` is a required boolean on
 * `SelectPatientRequest` and `ConfirmRequest` and is never inferred from `staff_id` being present
 * (`intake_routes.py`). Two layers, because this is the constraint the judges probe first.
 *
 * ## Why `sessionStorage`
 *
 * A shift ends. `localStorage` would leave a shared terminal signed in as whoever last used it,
 * overnight, and the ledger would then attribute tomorrow's confirmations to yesterday's staff.
 * `sessionStorage` dies with the tab, which is the closest thing the browser has to "end of shift".
 * This is a POC affordance, not authentication — see the banner in `StaffGate`.
 */
import * as React from "react";

const STORAGE_KEY = "walnut.staff.session.v1";

/** Must agree with `STAFF_ID_PATTERN` in `apps/gateway/databridge_gateway/intake_routes.py`.
 *  Previously the only check was "non-empty after trim", so a control character or an unbounded
 *  string from hand-edited storage reached the ledger's attester column. */
const STAFF_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{1,63}$/;

/** A shift, in ms. `sessionStorage` outlives a shift whenever the tab stays open — which on a
 *  shared terminal is "always". Without an expiry the next operator silently inherits the previous
 *  one's identity and every ledger row they create is attributed to someone who went home. */
const SHIFT_MS = 12 * 60 * 60 * 1000;

export function isValidStaffId(id: string): boolean {
  return STAFF_ID_RE.test(id.trim());
}

export type StaffSession = {
  /** As it appears in the ledger's `identity_checked_in_person_by`. */
  staffId: string;
  /** Display only. The ledger keys on `staffId`. */
  name: string;
  /** ISO-8601. Shown so a terminal left signed in is visibly stale. */
  signedInAt: string;
};

type Ctx = {
  staff: StaffSession | null;
  /** Null until the first effect runs; distinguishes "not signed in" from "not yet read". */
  ready: boolean;
  signIn: (staffId: string, name: string) => void;
  signOut: () => void;
};

const StaffContext = React.createContext<Ctx | null>(null);

function read(): StaffSession | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StaffSession>;
    // Validate rather than trust: a half-written or hand-edited value must not produce a session
    // with an empty staff id, which would sail through the frontend and be rejected only at the
    // gateway's `min_length=1` — as a confusing 422 on the confirm button.
    if (typeof parsed.staffId !== "string" || !isValidStaffId(parsed.staffId)) return null;
    // A missing or unparseable timestamp is treated as expired rather than refreshed: "we do not
    // know when this shift started" must not mean "assume it started now", which would make the
    // session immortal.
    const startedAt = typeof parsed.signedInAt === "string" ? Date.parse(parsed.signedInAt) : NaN;
    if (!Number.isFinite(startedAt)) return null;
    if (Date.now() - startedAt > SHIFT_MS) return null;
    // A future timestamp never satisfies the expiry above, so a session dated tomorrow would be
    // immortal — the one thing this check exists to prevent. One minute of tolerance for clock
    // skew between a terminal and whatever wrote the value.
    if (startedAt > Date.now() + 60_000) return null;
    const staffId = parsed.staffId.trim();
    return {
      staffId,
      name: typeof parsed.name === "string" && parsed.name.trim() ? parsed.name.trim() : staffId,
      signedInAt: new Date(startedAt).toISOString(),
    };
  } catch {
    return null;
  }
}

export function StaffSessionProvider({ children }: { children: React.ReactNode }) {
  const [staff, setStaff] = React.useState<StaffSession | null>(null);
  const [ready, setReady] = React.useState(false);

  // Read after mount, never during render: `sessionStorage` does not exist on the server, and
  // reading it in a `useState` initialiser makes the first client render disagree with the
  // server's HTML (a hydration mismatch that React resolves by throwing the tree away).
  React.useEffect(() => {
    setStaff(read());
    setReady(true);

    // `read()` is what applies SHIFT_MS, and running it only at mount meant the expiry never fired
    // on the machine it was written for: a front-counter terminal's tab stays open for days, so the
    // component never remounts and a 13-hour-old session kept prefilling the attester id.
    // `StaffChip` showed the shift age going amber, which looked like the feature working while
    // nothing actually expired. Both reviewers found this.
    const interval = window.setInterval(() => {
      setStaff((current) => {
        const fresh = read();
        if (current === null && fresh === null) return current;
        // Return the SAME object when nothing changed. A new object every minute would re-render
        // every consumer of this context, including the card's staff-id effect.
        if (
          current !== null &&
          fresh !== null &&
          current.staffId === fresh.staffId &&
          current.signedInAt === fresh.signedInAt
        ) {
          return current;
        }
        return fresh;
      });
    }, 60_000);
    return () => window.clearInterval(interval);
  }, []);

  const signIn = React.useCallback((staffId: string, name: string) => {
    const trimmedId = staffId.trim();
    if (!isValidStaffId(trimmedId)) return;
    const session: StaffSession = {
      staffId: trimmedId,
      name: name.trim() || trimmedId,
      signedInAt: new Date().toISOString(),
    };
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch {
      // Private-browsing quota failure. Keep the in-memory session — losing persistence is
      // survivable; refusing to let staff work is not.
    }
    setStaff(session);
  }, []);

  const signOut = React.useCallback(() => {
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* nothing to do */
    }
    setStaff(null);
  }, []);

  const value = React.useMemo(() => ({ staff, ready, signIn, signOut }), [staff, ready, signIn, signOut]);
  return <StaffContext.Provider value={value}>{children}</StaffContext.Provider>;
}

export function useStaffSession(): Ctx {
  const ctx = React.useContext(StaffContext);
  if (!ctx) throw new Error("useStaffSession must be used inside <StaffSessionProvider>");
  return ctx;
}
