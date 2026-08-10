"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Boxes,
  Network,
  Link2,
  Database,
  MessageSquare,
  FileText,
  BarChart3,
  Package,
  Check,
  type LucideIcon,
} from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import { cn } from "@/lib/utils";

// Grouped navigation with PURPOSE LABELS (Part 3) — the section headers tell an investor what each
// area does without reading anything. Live status footer reads the REAL backend numbers (D-FE-2).
const SECTIONS: { label: string; routes: { href: string; label: string; icon: LucideIcon }[] }[] = [
  {
    label: "The Brain",
    routes: [
      { href: "/brain", label: "Company Brain", icon: Network },
      { href: "/connectors", label: "Connectors", icon: Link2 },
    ],
  },
  {
    label: "Understand your data",
    routes: [
      { href: "/schema", label: "Schema Explorer", icon: Database },
      { href: "/ask", label: "Question Answerer", icon: MessageSquare },
    ],
  },
  {
    label: "Transform your data",
    routes: [{ href: "/convert", label: "Conversion Studio", icon: FileText }],
  },
  {
    label: "Measure quality",
    routes: [
      { href: "/evals", label: "Eval Dashboard", icon: BarChart3 },
      { href: "/artifacts", label: "Artifact Browser", icon: Package },
    ],
  },
];

export function Sidebar() {
  const path = usePathname();
  // shares the "brain-graph" cache with the pages → no extra round-trip when already loaded.
  const graphQ = useQuery({ queryKey: ["brain-graph"], queryFn: brainApi.graph });
  const stats = graphQ.data?.stats;
  const sources = graphQ.data ? Object.keys(graphQ.data.provenance_by_source).length : 0;

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-12 items-center gap-2 border-b border-border px-4">
        <Boxes className="h-5 w-5 text-accent" />
        <span className="text-sm font-semibold tracking-tight">DataBridge</span>
      </div>

      <nav className="flex flex-1 flex-col gap-3 overflow-y-auto p-2">
        {SECTIONS.map((section) => (
          <div key={section.label}>
            <div className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-subtle/70">
              {section.label}
            </div>
            <div className="flex flex-col gap-0.5">
              {section.routes.map((r) => {
                const active = path === r.href || (path === "/" && r.href === "/brain");
                const Icon = r.icon;
                return (
                  <Link
                    key={r.href}
                    href={r.href}
                    className={cn(
                      "flex items-center gap-2.5 rounded px-2.5 py-1.5 text-sm transition-colors",
                      active ? "bg-muted font-medium text-fg" : "text-subtle hover:bg-muted hover:text-fg",
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {r.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* live STATUS footer — real backend figures */}
      <div className="border-t border-border p-3">
        <div className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-subtle/70">
          Status
        </div>
        <div className="space-y-1 text-2xs">
          <div className="flex items-center gap-1.5 text-subtle">
            <span className={cn("h-2 w-2 rounded-full", graphQ.data ? "bg-band-auto" : "bg-band-review")} />
            <span className="tnum text-fg">{sources}</span> sources live
          </div>
          <div className="flex items-center gap-1.5 text-subtle">
            <Network className="h-3 w-3" />
            <span className="tnum text-fg">{stats?.nodes ?? 0}</span> entities
          </div>
          <div className="flex items-center gap-1.5 text-subtle">
            <Check className="h-3 w-3 text-band-auto" />
            <span className="tnum text-fg">{stats?.provenance_pct ?? 100}%</span> provenance
          </div>
        </div>
      </div>
    </aside>
  );
}
