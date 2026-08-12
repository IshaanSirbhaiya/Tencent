"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Boxes,
  Network,
  BarChart3,
  ClipboardList,
  FileUp,
  ShieldCheck,
  Check,
  Plug,
  Waypoints,
  RefreshCw,
  Archive,
  type LucideIcon,
  Brain,
  Fingerprint,
} from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import { cn } from "@/lib/utils";
import { StaffChip } from "@/components/staff-gate";

// Navigation, ordered the way the clinic works: the counter first, then the brain behind it, then
// the evidence that the whole thing is honest.
//
// The DataBridge engine pages were dropped from the nav in Phase 4b (PLAN.md §6.7) without being
// deleted — the packages behind them are still load-bearing (`packages/convert`, the grounding
// wall, the evidence ledger), only the screens went dark. PHASE_7 §1.6/T-35 restores what still
// earns its place:
//   - `/connectors` — the Gmail/Slack/Drive surface remembered as "the MCP tab". Phase 8 wires real
//     MCP servers behind these same cards, so the honest demo-mode badge on that page matters now,
//     not just later.
//   - `/schema`, `/convert`, `/artifacts` — the confirmation-gate, format-conversion and
//     replay-determinism story (EV-DET) is exactly the Governance & Safety material `/evals` also
//     serves, and neither page carries any Acme-era content (checked: no hardcoded issuer/company
//     references survive in either).
// `/ask` is deliberately left OUT. It still serves `databridge_evals.e_sql.PAIRS` — stale DataBridge
// SQL questions with no healthcare content (TASKS.md T-21) — and unlike the three pages above it
// would read as a wrong product to a judge who clicks it, not just an old one. Restore it once T-21
// repoints it at intake data; until then a nav entry here would be worse than the gap it fills.
const SECTIONS: { label: string; routes: { href: string; label: string; icon: LucideIcon }[] }[] = [
  {
    label: "Front desk",
    routes: [
      { href: "/frontdesk", label: "Queue", icon: ClipboardList },
      { href: "/intake", label: "Read a document", icon: FileUp },
    ],
  },
  {
    label: "Clinic brain",
    routes: [{ href: "/brain", label: "Knowledge graph", icon: Network }],
  },
  {
    label: "Connections",
    routes: [{ href: "/connectors", label: "Connectors", icon: Plug }],
  },
  {
    label: "Data engine",
    routes: [
      { href: "/schema", label: "Schema mapping", icon: Waypoints },
      { href: "/convert", label: "Convert", icon: RefreshCw },
    ],
  },
  {
    label: "Evidence",
    routes: [
      { href: "/intelligence", label: "Intelligence", icon: Brain },
      { href: "/evidence", label: "Provenance", icon: Fingerprint },
      { href: "/impact", label: "Impact", icon: BarChart3 },
      { href: "/evals", label: "Eval dashboard", icon: ShieldCheck },
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
        <span className="text-sm font-semibold tracking-tight">Walnut</span>
      </div>

      <nav className="flex flex-1 flex-col gap-3 overflow-y-auto p-2">
        {SECTIONS.map((section) => (
          <div key={section.label}>
            <div className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-subtle/70">
              {section.label}
            </div>
            <div className="flex flex-col gap-0.5">
              {section.routes.map((r) => {
                const active =
                  path === r.href ||
                  path.startsWith(`${r.href}/`) ||
                  (path === "/" && r.href === "/frontdesk");
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

      {/* Who is at this terminal. On screen at all times because their staff id is what the
          evidence ledger attributes every confirmation to. */}
      <div className="border-t border-border px-3 py-2 text-2xs">
        <StaffChip />
      </div>

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
            <span className="tnum text-fg">{stats?.nodes ?? 0}</span> graph nodes
          </div>
          <div className="flex items-center gap-1.5 text-subtle">
            <Check className="h-3 w-3 text-band-auto" />
            <span className="tnum text-fg">
              {/* No optimistic default: with the backend down this used to read "0 nodes,
                  100% provenance", which is the worst possible lie on a governance panel. */}
              {stats ? `${stats.provenance_pct}%` : "—"}
            </span>{" "}
            provenance
          </div>
        </div>
      </div>
    </aside>
  );
}
