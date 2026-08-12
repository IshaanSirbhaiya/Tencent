import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { QueryProvider } from "@/components/query-provider";
import { DemoOverlay } from "@/components/demo/demo-overlay";
import { StaffSessionProvider } from "@/lib/staff-session";
import { StaffGate } from "@/components/staff-gate";

export const metadata: Metadata = {
  title: "Walnut — Parkway Shenton",
  description:
    "Reads a patient's referring document before they reach the counter. Provenance on every "
    + "value; identity verified in person by staff.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <QueryProvider>
          {/* The gate answers "who is at this terminal", so the ledger can attribute each
              confirmation. It is NOT authentication, and it never attests to a patient's identity —
              see the header of `lib/staff-session.tsx` for the line it must not cross. */}
          <StaffSessionProvider>
            <StaffGate>
              <div className="flex h-screen overflow-hidden">
                <Sidebar />
                {/* `overflow-y-auto`, not `overflow-hidden`. Every page except /brain and
                    /connectors rendered straight into a clipped <main>, so anything below the fold
                    — the billing section of the front-desk card, the lower rows of /impact — was
                    simply unreachable. Pages that manage their own panes (/brain's graph canvas)
                    set `h-full overflow-hidden` on their own root and are unaffected. */}
                <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">{children}</main>
              </div>
              <DemoOverlay />
            </StaffGate>
          </StaffSessionProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
