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
  Check,
  Route,
  Shapes,
  PackageOpen,
  Wrench,
  type LucideIcon,
  Brain,
  Fingerprint,
  Plug,
} from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import { cn } from "@/lib/utils";
import { StaffChip } from "@/components/staff-gate";
import { PackSwitcher } from "@/components/pack-switcher";
import { usePack } from "@/lib/pack-context";

// Navigation, ordered as the SYSTEM's own flow: connect sources, let the agents build the graph
// under a human gate, then take data out in the shape you need.
//
//   Connectors -> Agents -> Knowledge Graph -> Data
//                              |
//                    Ontology / Trace / Evidence  (what it may say, who said it, why it is true)
//
// **Knowledge Graph is first and is the point.** Everything else in this product describes the
// graph; this is the graph. It led the list from the moment it existed, because a system whose
// central artefact is not on screen reads, to anyone looking at it, like a system that does not
// produce one.
//
// The engine knows no domain. A pack supplies vocabulary and synthetic estates; it is NOT a
// product mode, and nothing in this list names one. Pack surfaces render below, from pack
// metadata, so a second pack replaces them without touching this file.
//
// Deliberately absent, and each for a reason rather than an oversight:
//   `/schema`, `/evals` - DataBridge-era screens (`/convert` now lives inside Data). The work they did lives in the
//       adapter contract, the grounding wall and the eval suites; the screens described a product
//       this no longer is.
//   `/ask` - still answers stale DataBridge SQL questions. Restore when repointed.
const PLATFORM: { label: string; routes: { href: string; label: string; icon: LucideIcon }[] }[] = [
  {
    label: "Build",
    routes: [
      // Sources — connect an MCP server, approve its crawl plan, run it into the graph. Lives at
      // `/connectors` (the route every "connect a source" link already used); it replaced the
      // redirect to Builder's Adapter tab, which configures agent tools and never ingested.
      { href: "/connectors", label: "Sources", icon: Plug },
      // ONE Builder, two internal tabs: Agent Builder (automations) and Adapter Builder
      // (the connector flow - search an MCP, configure it, read/write, skills, extraction
      // context). Splitting these into two nav entries was wrong: they are two halves of one
      // job, and an operator building an automation needs the adapter beside it, not a
      // different page.
      { href: "/builder", label: "Builder", icon: Wrench },
      { href: "/ontology", label: "Ontology", icon: Shapes },
    ],
  },
  {
    label: "Graph",
    routes: [
      { href: "/graph", label: "Knowledge Graph", icon: Network },
      { href: "/trace", label: "Activity", icon: Route },
      // "Provenance", not "Evidence". The surface answers "where did this value come from, and is
      // it still true?" - which is what a reader is looking for when they go hunting, and they
      // were not finding it under the old name.
      { href: "/evidence", label: "Provenance", icon: Fingerprint },
    ],
  },
  {
    label: "Use",
    routes: [
      // Promoted out of the Pack section. `/intelligence` was declared only by
      // `packs/healthcare/ui.yaml`, so it appeared only while healthcare was the active pack and
      // vanished on a pack switch - along with four other surfaces. Nothing about asking the graph
      // a question is healthcare-specific; it operates on whatever graph is loaded. A platform
      // capability reachable only through one vertical's UI config is how a domain-free engine
      // ends up looking like that vertical's product.
      { href: "/intelligence", label: "Intelligence", icon: Brain },
      // One Data page (the data agent + the direct converter); `/materialise` and `/convert`
      // redirect to it.
      { href: "/data", label: "Data", icon: PackageOpen },
    ],
  },
];

/** Config carries an icon KEY, not a component. This is the whole mapping. */
const SURFACE_ICONS: Record<string, LucideIcon> = {
  queue: ClipboardList,
  upload: FileUp,
  graph: Network,
  brain: Brain,
  chart: BarChart3,
};

function NavLink({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2.5 rounded px-2.5 py-1.5 text-sm transition-colors",
        active ? "bg-muted font-medium text-fg" : "text-subtle hover:bg-muted hover:text-fg",
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
    </Link>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-subtle/70">
      {children}
    </div>
  );
}

export function Sidebar() {
  const path = usePathname();
  const { pack } = usePack();
  // shares the "brain-graph" cache with the pages → no extra round-trip when already loaded.
  const graphQ = useQuery({ queryKey: ["brain-graph"], queryFn: brainApi.graph });
  const stats = graphQ.data?.stats;
  const sources = graphQ.data ? Object.keys(graphQ.data.provenance_by_source).length : 0;

  const isActive = (href: string) => path === href || path.startsWith(`${href}/`);

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-12 items-center gap-2 border-b border-border px-4">
        <Boxes className="h-5 w-5 text-accent" />
        <span className="text-sm font-semibold tracking-tight">Walnut</span>
        <span className="rounded border border-border px-1 py-px text-[10px] uppercase tracking-wider text-subtle">
          platform
        </span>
      </div>

      <nav className="flex flex-1 flex-col gap-3 overflow-y-auto p-2">
        {PLATFORM.map((section) => (
          <div key={section.label}>
            <SectionLabel>{section.label}</SectionLabel>
            <div className="flex flex-col gap-0.5">
              {section.routes.map((r) => (
                <NavLink key={r.href} {...r} active={isActive(r.href)} />
              ))}
            </div>
          </div>
        ))}

        {/* The active pack's operator surfaces. Secondary, and entirely metadata-driven — this
            block contains no vertical's vocabulary, only whatever the pack declared. */}
        <div className="mt-1 border-t border-border pt-2">
          <SectionLabel>Pack</SectionLabel>
          <div className="px-1 pb-1.5">
            <PackSwitcher />
          </div>
          <div className="flex flex-col gap-0.5">
            {pack.surfaces.map((s) => (
              <NavLink
                key={s.href}
                href={s.href}
                label={s.label}
                icon={SURFACE_ICONS[s.icon] ?? ClipboardList}
                active={isActive(s.href)}
              />
            ))}
            {pack.surfaces.length === 0 && (
              <p
                data-testid="pack-no-surfaces"
                className="px-2.5 py-1 text-2xs leading-relaxed text-subtle"
              >
                {pack.unavailable_note ?? "This pack declares no operator surfaces yet."}
              </p>
            )}
          </div>
        </div>
      </nav>

      {/* Who is at this terminal. On screen at all times because their staff id is what the
          evidence ledger attributes every confirmation to. */}
      <div className="border-t border-border px-3 py-2 text-2xs">
        <StaffChip />
      </div>

      {/* live STATUS footer — real backend figures */}
      <div className="border-t border-border p-3">
        <SectionLabel>Status</SectionLabel>
        <div className="space-y-1 text-2xs">
          <div className="flex items-center gap-1.5 text-subtle">
            <span className={cn("h-2 w-2 rounded-full", graphQ.data ? "bg-band-auto" : "bg-band-review")} />
            <span className="tnum text-fg">{sources}</span> sources live
          </div>
          <div className="flex items-center gap-1.5 text-subtle">
            <Network className="h-3 w-3" />
            <span className="tnum text-fg">{stats ? stats.nodes : "—"}</span> graph nodes
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
