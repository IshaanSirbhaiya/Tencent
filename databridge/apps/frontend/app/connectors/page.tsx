"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Mail,
  MessageSquare,
  HardDrive,
  Calendar,
  SquareKanban,
  FileText,
  Cloud,
  Github,
  Box,
  Workflow,
  BookOpen,
  Video,
  Check,
  type LucideIcon,
} from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import { ConnectModal, type ConnectorInfo } from "@/components/connectors/connect-modal";

// Part 1 — /connectors. Shows investors DataBridge can connect to anything. The connector cards are
// VISUAL ONLY (demo mode badge); the connected cards' live stats are illustrative. The header's
// "entities discovered" counter is the REAL backend number (D-FE-2) — never fabricated.

interface Connector {
  name: string;
  icon: LucideIcon;
  extracts: string; // "What you'll get" — People, Decisions, Projects, Facts
  connected?: { stat: string; sync: string };
}

const CONNECTORS: Connector[] = [
  { name: "Gmail", icon: Mail, extracts: "People, Decisions, Projects, Facts", connected: { stat: "847 emails indexed", sync: "Last sync: 2 min ago" } },
  { name: "Slack", icon: MessageSquare, extracts: "People, Decisions, Projects", connected: { stat: "2,341 messages · 12 channels", sync: "Last sync: 5 min ago" } },
  { name: "Google Drive", icon: HardDrive, extracts: "Documents, Facts, Decisions", connected: { stat: "156 documents · 34 PDFs processed", sync: "Last sync: 12 min ago" } },
  { name: "Google Calendar", icon: Calendar, extracts: "Meetings → decisions + attendees" },
  { name: "Jira", icon: SquareKanban, extracts: "Tickets → projects + owners" },
  { name: "Notion", icon: FileText, extracts: "Docs → knowledge + decisions" },
  { name: "Salesforce", icon: Cloud, extracts: "CRM → customers + deals" },
  { name: "GitHub", icon: Github, extracts: "PRs → decisions + contributors" },
  { name: "Dropbox", icon: Box, extracts: "Files → documents + facts" },
  { name: "HubSpot", icon: Workflow, extracts: "Pipeline → contacts + opportunities" },
  { name: "Confluence", icon: BookOpen, extracts: "Wiki → policies + decisions" },
  { name: "Zoom", icon: Video, extracts: "Transcripts → meetings + decisions" },
];

function ConnectorCard({
  c,
  connected,
  onConnect,
}: {
  c: Connector;
  connected: boolean;
  onConnect: () => void;
}) {
  const Icon = c.icon;
  return (
    <div
      className={`group relative flex flex-col rounded-lg border bg-surface p-4 transition-colors ${
        connected ? "border-band-auto/40" : "border-border hover:border-accent/40"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-bg">
            <Icon className="h-4 w-4 text-fg" />
          </span>
          <div>
            <div className="text-sm font-semibold text-fg">{c.name}</div>
            <div className="text-2xs text-subtle">{c.extracts}</div>
          </div>
        </div>
        {connected ? (
          <span className="flex items-center gap-1 rounded-full border border-band-auto/40 bg-band-auto/10 px-2 py-0.5 text-2xs font-medium text-band-auto">
            <Check className="h-3 w-3" /> Connected
          </span>
        ) : null}
      </div>

      <div className="mt-3 flex items-end justify-between">
        {connected && c.connected ? (
          <div className="text-2xs text-subtle">
            <div className="tnum text-fg">{c.connected.stat}</div>
            <div>{c.connected.sync}</div>
          </div>
        ) : (
          <button
            onClick={onConnect}
            className="rounded bg-band-spot px-3 py-1.5 text-xs font-semibold text-[#1a1206] transition-opacity hover:opacity-90"
          >
            Connect
          </button>
        )}
      </div>

      {/* "What you'll get" tooltip on hover */}
      <div className="pointer-events-none absolute -top-2 left-3 z-10 w-max max-w-[220px] -translate-y-full rounded border border-border bg-elevated px-2 py-1 text-2xs text-fg opacity-0 shadow-md transition-opacity group-hover:opacity-100">
        {c.name} → {c.extracts}
      </div>
    </div>
  );
}

export default function ConnectorsPage() {
  const graphQ = useQuery({ queryKey: ["brain-graph"], queryFn: brainApi.graph });
  const entities = graphQ.data?.stats.nodes ?? 0;

  // demo-only connected state (starts with the 3 pre-connected; Connect adds locally)
  const [connected, setConnected] = React.useState<Set<string>>(
    () => new Set(CONNECTORS.filter((c) => c.connected).map((c) => c.name)),
  );
  const [modal, setModal] = React.useState<ConnectorInfo | null>(null);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-surface px-5">
        <h1 className="text-sm font-semibold tracking-tight">Connectors</h1>
        <span className="rounded border border-band-spot/40 bg-band-spot/10 px-2 py-0.5 text-2xs font-medium text-band-spot">
          Demo mode
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-5xl">
          <h2 className="text-xl font-semibold tracking-tight text-fg">Connect your data sources</h2>
          <p className="mt-1 max-w-2xl text-sm text-subtle">
            DataBridge learns from every source you connect. Each connection enriches the Company Brain.
          </p>
          <div className="mt-2 text-2xs text-subtle">
            {/* connectors enabled is a UI/demo count; entities is the REAL backend figure */}
            <span className="font-medium text-band-auto">
              <span className="tnum">{connected.size}</span> connectors active
            </span>
            <span className="mx-1.5">·</span>
            <span className="tnum text-fg">{entities}</span> entities discovered
          </div>

          <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {CONNECTORS.map((c) => (
              <ConnectorCard
                key={c.name}
                c={c}
                connected={connected.has(c.name)}
                onConnect={() => setModal({ name: c.name, extracts: c.extracts })}
              />
            ))}
          </div>
        </div>
      </div>

      <ConnectModal
        connector={modal}
        onClose={() => setModal(null)}
        onConnected={(name) => setConnected((s) => new Set(s).add(name))}
      />
    </div>
  );
}
