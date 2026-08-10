import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/sidebar";
import { QueryProvider } from "@/components/query-provider";
import { DemoOverlay } from "@/components/demo/demo-overlay";

export const metadata: Metadata = {
  title: "DataBridge",
  description: "Data understanding & conversion engine — provenance on every value.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>
        <QueryProvider>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
          </div>
          <DemoOverlay />
        </QueryProvider>
      </body>
    </html>
  );
}
