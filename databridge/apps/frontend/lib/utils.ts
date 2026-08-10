import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** The 3-stop confidence scale → band class. Bands come from the BACKEND (never recomputed
 *  here); this is purely the band→colour mapping (D-FE-3, REVIEW_2 build-note 4). */
export function bandColor(band: string): string {
  return band === "auto" ? "text-band-auto" : band === "spot_check" ? "text-band-spot" : "text-band-review";
}

export function bandBg(band: string): string {
  return band === "auto"
    ? "bg-band-auto/10 text-band-auto border-band-auto/30"
    : band === "spot_check"
      ? "bg-band-spot/10 text-band-spot border-band-spot/30"
      : "bg-band-review/10 text-band-review border-band-review/30";
}

export function roleColor(role: string): string {
  return (
    {
      entity: "text-role-entity",
      event: "text-role-event",
      log: "text-role-log",
      relationship: "text-role-relationship",
      lookup: "text-role-lookup",
    }[role] ?? "text-subtle"
  );
}

export function roleBg(role: string): string {
  return (
    {
      entity: "bg-role-entity/10 text-role-entity border-role-entity/30",
      event: "bg-role-event/10 text-role-event border-role-event/30",
      log: "bg-role-log/10 text-role-log border-role-log/30",
      relationship: "bg-role-relationship/10 text-role-relationship border-role-relationship/30",
      lookup: "bg-role-lookup/10 text-role-lookup border-role-lookup/30",
    }[role] ?? "bg-muted text-subtle border-border"
  );
}
