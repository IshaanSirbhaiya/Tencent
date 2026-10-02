"use client";
/**
 * `/builder` — describe agents and adapter agents in plain English, and run what you built.
 *
 * Replaces the old declaration-compiler page (Roadmap Phase C2 + C3). Two tabs, both starting from
 * an English box whose words go to `POST /agents/compile`:
 *
 *   Agents          — agents that DO work, on demand or as automations. "Here's what I understood"
 *                     (mode, trigger, sources, tools, each write action's approval rule) → save /
 *                     activate; run now; runs with cited answers and every step's policy decision
 *                     and producer; the approval inbox for actions that ask first.
 *   Adapter agents  — agents that MANAGE SOURCES: connections, sync trigger, screening, freshness,
 *                     and how they adapt to source-format changes. Health per connection, sync now
 *                     with live job status, ontology proposals to forward.
 *
 * Both lists render the gateway's own records; nothing here computes a status or a count. The tab
 * and the selection live in the URL (`?tab=`, `?agent=`, `?run=`, `?adapter=`) so any of them is
 * linkable. Connecting a NEW source is not done here: that is Sources (`/connectors`), linked from the
 * Adapter agents tab. Every source added there is listed in the Adapter agents tab, ready to set up
 * through its adapter agent (`?setup=<source>`, FEAT8).
 */
import * as React from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Bot, Plug } from "lucide-react";
import { Topbar } from "@/components/topbar";
import { cn } from "@/lib/utils";
import { AgentsPanel } from "@/components/agents/agents-panel";
import { AdapterAgentsPanel } from "@/components/agents/adapter-agents-panel";

type Tab = "agents" | "adapter";

const TABS: { id: Tab; label: string; icon: typeof Bot; blurb: string }[] = [
  {
    id: "agents",
    label: "Agents",
    icon: Bot,
    blurb:
      "Agents automate work across your sources. Say what it should do, on demand or on a schedule or event; it is read-only unless you give it write actions, and for each one you decide whether it asks you first, tells you after, or runs on its own with a declared undo.",
  },
  {
    id: "adapter",
    label: "Adapter agents",
    icon: Plug,
    blurb:
      "Adapter agents manage your sources: which connections are kept in the graph, how often they sync, what is screened out, how fresh they must be, and how they adapt when a source changes shape. New write tools and people-related changes are always put to a person.",
  },
];

function BuilderInner() {
  const router = useRouter();
  const params = useSearchParams();
  const tab: Tab = params.get("tab") === "adapter" ? "adapter" : "agents";
  const agent = params.get("agent");
  const run = params.get("run");
  const adapter = params.get("adapter");
  const setup = params.get("setup");

  function go(next: Record<string, string | null>) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(next)) if (v) q.set(k, v);
    router.replace(`/builder${q.toString() ? `?${q}` : ""}`, { scroll: false });
  }

  const current = TABS.find((t) => t.id === tab)!;
  return (
    <>
      <Topbar title="Builder" />
      <div className="flex flex-col gap-4 p-6">
        <header className="max-w-3xl">
          <h1 className="text-lg font-semibold">Builder</h1>
          <p data-testid="builder-blurb" className="text-xs text-subtle">{current.blurb}</p>
        </header>
        <div role="tablist" aria-label="Builder" className="flex gap-1 border-b border-border">
          {TABS.map((t) => {
            const Icon = t.icon;
            const selected = t.id === tab;
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={selected}
                data-testid={`builder-tab-${t.id}`}
                onClick={() => go({ tab: t.id === "agents" ? null : "adapter" })}
                className={cn(
                  "flex items-center gap-1.5 border-b-2 px-3.5 py-2 text-xs font-medium transition-colors",
                  selected ? "border-accent text-fg" : "border-transparent text-subtle hover:text-fg",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {t.label}
              </button>
            );
          })}
        </div>
        {tab === "agents" ? (
          <AgentsPanel agentId={agent} runId={run} select={(a, r) => go({ tab: null, agent: a, run: r })} />
        ) : (
          <AdapterAgentsPanel
            adapterId={adapter}
            select={(id) => go({ tab: "adapter", adapter: id })}
            setupId={setup}
            selectSetup={(sid) => go({ tab: "adapter", setup: sid })}
          />
        )}
      </div>
    </>
  );
}

export default function BuilderPage() {
  return (
    <Suspense fallback={<p className="p-6 text-xs text-subtle">Loading…</p>}>
      <BuilderInner />
    </Suspense>
  );
}
