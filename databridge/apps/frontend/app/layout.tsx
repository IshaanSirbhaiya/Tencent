import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { QueryProvider } from "@/components/query-provider";
import { DemoOverlay } from "@/components/demo/demo-overlay";
import { StaffSessionProvider } from "@/lib/staff-session";
import { StaffGate } from "@/components/staff-gate";
import { PackProvider } from "@/lib/pack-context";

// Static, and deliberately domain-free: `metadata` is evaluated on the server, before any pack is
// resolved, so a vertical's noun here would be a vertical baked into the document head.
export const metadata: Metadata = {
  title: "Walnut — grounded data infrastructure",
  description:
    "Every emitted value carries the pointer it came from. Domain vocabulary is supplied by a "
    + "pack; the engine knows no domain.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Light by default. The dark palette is intact under `.dark` in `globals.css`, so a toggle is
    // a class swap here and nothing else.
    <html lang="en">
      <body>
        <QueryProvider>
          {/* The active pack supplies every domain noun below this point. It sits outside the
              session gate so the sign-in screen can name what it is a front door to. */}
          <PackProvider>
          {/* The gate answers "who is at this terminal", so the ledger can attribute each
              confirmation. It is NOT authentication, and it never attests to a subject's identity —
              see the header of `lib/staff-session.tsx` for the line it must not cross. */}
          <StaffSessionProvider>
            <StaffGate>
              <div className="flex h-screen overflow-hidden">
                <Sidebar />
                {/* `overflow-y-auto`, not `overflow-hidden`. Every page except /brain and
                    /connectors rendered straight into a clipped <main>, so anything below the fold
                    — the lower half of an operator card, the lower rows of /impact — was simply
                    unreachable. Pages that manage their own panes (/brain's graph canvas) set
                    `h-full overflow-hidden` on their own root and are unaffected. */}
                <main className="flex min-w-0 flex-1 flex-col overflow-y-auto">{children}</main>
              </div>
              <DemoOverlay />
            </StaffGate>
          </StaffSessionProvider>
          </PackProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
