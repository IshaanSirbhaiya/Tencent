"use client";
/**
 * Connect a server — and choose, in the same breath, which process fronts it.
 *
 * The READ / ACT choice is the capability boundary, so it is the most prominent control on the
 * form and its consequence is printed beside it in the backend's own words (`kind_consequence`,
 * sent by `GET /mcp/servers`) rather than paraphrased here. A rule the UI restates in its own
 * language is a rule that drifts.
 *
 * There is deliberately no default selection. Registering something act-capable into the agent's
 * aggregator because a radio button happened to start on "read" is precisely the mistake this
 * screen exists to prevent (`AGENT_CAPABILITIES.md`, R-34) — so the submit button stays disabled
 * until a human has picked a side.
 */
import * as React from "react";
import { Bot, Loader2, ShieldCheck } from "lucide-react";
import type { ConnectRequest, ServerKind } from "@/lib/mcp-api";
import type { Refusal } from "@/lib/types";
import { RefusalCard } from "@/components/refusal-card";
import { cn } from "@/lib/utils";

const KIND_META: Record<
  ServerKind,
  { title: string; badge: string; icon: typeof Bot; tone: string }
> = {
  read: { title: "READ", badge: "agent-reachable", icon: Bot, tone: "band-auto" },
  act: { title: "ACT", badge: "approval required", icon: ShieldCheck, tone: "band-spot" },
};

function KindChoice({
  kind,
  selected,
  consequence,
  onSelect,
}: {
  kind: ServerKind;
  selected: boolean;
  consequence: string;
  onSelect: () => void;
}) {
  const meta = KIND_META[kind];
  const Icon = meta.icon;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "flex flex-1 flex-col rounded border p-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-accent",
        selected ? "border-accent bg-accent/5" : "border-border bg-bg hover:border-accent/40",
      )}
    >
      <span className="flex items-center gap-2">
        <Icon
          className={cn("h-4 w-4", kind === "read" ? "text-band-auto" : "text-band-spot")}
          aria-hidden
        />
        <span className="text-sm font-semibold text-fg">{meta.title}</span>
        <span
          className={cn(
            "rounded-full border px-2 py-0.5 text-2xs font-medium",
            kind === "read"
              ? "border-band-auto/40 bg-band-auto/10 text-band-auto"
              : "border-band-spot/40 bg-band-spot/10 text-band-spot",
          )}
        >
          {meta.badge}
        </span>
      </span>
      {/* verbatim from the gateway — the consequence is stated where the choice is made */}
      <span className="mt-2 text-2xs leading-relaxed text-subtle">{consequence}</span>
    </button>
  );
}

export function ConnectForm({
  consequence,
  onConnect,
  busy,
  refusal,
  disabled,
}: {
  consequence: Record<ServerKind, string>;
  onConnect: (body: ConnectRequest) => void;
  busy: boolean;
  refusal: Refusal | null;
  disabled: boolean;
}) {
  const [name, setName] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [kind, setKind] = React.useState<ServerKind | null>(null);

  const ready = name.trim() !== "" && url.trim() !== "" && kind !== null && !busy && !disabled;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || kind === null) return;
    onConnect({ name: name.trim(), url: url.trim(), description: description.trim(), kind });
  }

  const field =
    "w-full rounded border border-border bg-bg px-2.5 py-1.5 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent";

  return (
    <form onSubmit={submit} className="rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-subtle">
          Connect an MCP server
        </span>
      </div>
      <div className="space-y-3 px-4 py-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="text-2xs font-medium text-subtle">Name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="walnut_mail_act"
              className={cn(field, "mt-1 font-mono")}
            />
            <span className="mt-1 block text-2xs text-subtle">
              Letters, digits, <code>-</code> and single <code>_</code>. Tools are namespaced{" "}
              <code>name__tool</code>.
            </span>
          </label>
          <label className="block">
            <span className="text-2xs font-medium text-subtle">URL</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="http://host.docker.internal:8790/mcp"
              className={cn(field, "mt-1 font-mono")}
            />
            <span className="mt-1 block text-2xs text-subtle">
              The aggregator runs in a container, so a host process is{" "}
              <code>host.docker.internal</code> to it.
            </span>
          </label>
        </div>

        <label className="block">
          <span className="text-2xs font-medium text-subtle">Description</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What this server exposes, in one line — a model reads this."
            className={cn(field, "mt-1")}
          />
        </label>

        <div>
          <span className="text-2xs font-medium text-subtle">Capability</span>
          <div role="radiogroup" aria-label="Capability" className="mt-1 flex flex-col gap-2 sm:flex-row">
            <KindChoice
              kind="read"
              selected={kind === "read"}
              consequence={consequence.read}
              onSelect={() => setKind("read")}
            />
            <KindChoice
              kind="act"
              selected={kind === "act"}
              consequence={consequence.act}
              onSelect={() => setKind("act")}
            />
          </div>
          <p className="mt-2 text-2xs text-subtle">
            A server cannot be both, and the choice is not inferred from the URL or the tool names.
            It decides which process fronts the server, which is what makes the boundary real rather
            than a naming convention.
          </p>
        </div>

        {refusal ? <RefusalCard refusal={refusal} /> : null}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!ready}
            className="flex items-center gap-2 rounded bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            Connect
          </button>
          {kind === null ? (
            <span className="text-2xs text-subtle">Choose READ or ACT to continue.</span>
          ) : null}
          {disabled ? (
            <span className="text-2xs text-band-review">
              The aggregator is not reachable — nothing can be connected until it is back.
            </span>
          ) : null}
        </div>
      </div>
    </form>
  );
}
