"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { X } from "lucide-react";
import { brainApi } from "@/lib/brain-api";
import { NODE_STYLE } from "@/lib/brain-layout";
import type { NodeDetail, NodeKind } from "@/lib/brain-types";
import type { SourcePointer } from "@/lib/types";

// Part 3 — the Obsidian "note" panel (replaces the side drawer). A floating right panel rendering
// markdown generated from the node's graph data, with clickable [[wikilinks]] (the connections /
// decisions) that navigate the graph to that node and open ITS note — exactly like Obsidian.

const WIKI = "#wiki:";

// escape markdown-significant chars so a backend LABEL can't break out of link text / a heading and
// forge or destroy a [[wikilink]] (labels come from extracted documents — never trust their syntax).
const mdEsc = (s: string): string => String(s).replace(/([\\`*_{}[\]()#+\-.!|])/g, "\\$&");
const learned = (iso: string | null): string =>
  iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", year: "numeric" }) : "unknown";

function sourceLine(p: SourcePointer): string {
  const loc = p.char_start != null ? `chars ${p.char_start}–${p.char_end}` : p.part ? `part: ${p.part}` : p.kind;
  return `> ${mdEsc(p.source_id)} · ${loc}`;
}

function buildMarkdown(d: NodeDetail): string {
  const a = d.node.attrs as Record<string, unknown>;
  const meta: string[] = [];
  if (a.role) meta.push(`**Role:** ${mdEsc(String(a.role))}  `);
  if (a.status) meta.push(`**Status:** ${mdEsc(String(a.status))}  `);
  if (a.type) meta.push(`**Type:** ${mdEsc(String(a.type))}  `);
  if (a.email) meta.push(`**Email:** ${mdEsc(String(a.email))}  `);
  if (a.made_at) meta.push(`**Date:** ${mdEsc(String(a.made_at))}  `);
  if (a.rendered) meta.push(`**Value:** ${mdEsc(String(a.rendered))}  `);

  // wikilink: escaped label as link TEXT + encoded id as href → both label-injection and a forged
  // href are impossible (the renderer decodes the id and navigates only to that exact node).
  const link = (label: string, id: string) => `[${mdEsc(label)}](${WIKI}${encodeURIComponent(id)})`;

  const parts = [`## ${mdEsc(d.node.label)}`, meta.join("\n")];
  if (d.decisions.length) {
    parts.push(
      "## Decisions\n" +
        d.decisions.map((x) => `- ${link(x.label, x.id)}${x.made_at ? ` — ${mdEsc(x.made_at)}` : ""}`).join("\n"),
    );
  }
  if (d.connections.length) {
    parts.push(
      "## Connections\n" +
        d.connections
          .slice(0, 14)
          .map((c) => `- ${link(c.other_label, c.other_id)} — ${c.edge_kind.replace(/_/g, " ")}`)
          .join("\n"),
    );
  }
  parts.push("## Source\n" + d.sources.map(sourceLine).join("  \n"));
  parts.push(`---\n*system_time · learned ${learned(d.node.system_from)}*`);
  return parts.join("\n\n");
}

export function MarkdownPanel({
  nodeId,
  onClose,
  onNavigate,
}: {
  nodeId: string | null;
  onClose: () => void;
  onNavigate: (id: string) => void;
}) {
  const { data } = useQuery({
    queryKey: ["brain-node", nodeId],
    queryFn: () => brainApi.node(nodeId!),
    enabled: !!nodeId,
  });
  const open = !!nodeId;
  const panelRef = React.useRef<HTMLElement>(null);

  React.useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();                 // move focus into the note when it opens (a11y)
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, nodeId, onClose]);

  if (!open || !data || !data.node) return null;
  const kind = data.node.kind as NodeKind;
  const color = NODE_STYLE[kind].color;
  const md = buildMarkdown(data);

  const Wikilink = ({ href, children }: { href?: string; children?: React.ReactNode }) => {
    if (href?.startsWith(WIKI)) {
      const id = decodeURIComponent(href.slice(WIKI.length));
      const text = React.Children.toArray(children).join("");
      return (
        <button
          onClick={() => onNavigate(id)}
          aria-label={`Open note: ${text}`}
          className="font-mono text-[#7eb6ff] underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
        >
          [[{children}]]
        </button>
      );
    }
    return <span>{children}</span>;
  };

  return (
    <aside
      ref={panelRef}
      role="dialog"
      aria-label={`${data.node.label} note`}
      tabIndex={-1}
      className="fixed right-4 top-16 z-40 flex max-h-[78vh] w-[380px] flex-col overflow-hidden rounded-lg border shadow-2xl outline-none"
      style={{ background: "#1a1a1a", borderColor: "#2a2a2a", animation: "count-in 160ms ease-out" }}
    >
      <div className="flex items-center justify-between border-b px-4 py-2.5" style={{ borderColor: "#2a2a2a" }}>
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
          <span className="text-2xs uppercase tracking-wide text-white/50">{NODE_STYLE[kind].label}</span>
        </div>
        <button onClick={onClose} aria-label="Close" className="rounded text-white/50 hover:text-white focus:outline-none focus:ring-2 focus:ring-white/30">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="obsidian-md overflow-y-auto px-4 py-3 text-sm text-white/85">
        <ReactMarkdown
          components={{
            a: Wikilink as never,
            h2: ({ children }) => <h2 className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-white/45 first:mt-0">{children}</h2>,
            p: ({ children }) => <p className="mb-2 leading-relaxed">{children}</p>,
            ul: ({ children }) => <ul className="mb-2 space-y-1">{children}</ul>,
            li: ({ children }) => <li className="text-[13px] leading-snug">{children}</li>,
            strong: ({ children }) => <strong className="font-semibold text-white/95">{children}</strong>,
            em: ({ children }) => <em className="text-2xs not-italic text-white/40">{children}</em>,
            blockquote: ({ children }) => (
              <blockquote className="my-1 border-l-2 pl-2.5 font-mono text-2xs text-white/60" style={{ borderColor: color }}>
                {children}
              </blockquote>
            ),
            hr: () => <hr className="my-2 border-white/10" />,
          }}
        >
          {md}
        </ReactMarkdown>
      </div>
    </aside>
  );
}
