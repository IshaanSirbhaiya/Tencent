"use client";
/**
 * Step 3 — the MCP config modal.
 *
 * The founder's own words: "a pop up happens like in ChatGPT or Claude where I can configure an
 * MCP." This is that dialog — a product modal over the builder, not a route change, opened the
 * moment an operator picks a server in step 2 and closed by Save, Escape, or a click outside it.
 *
 * It shows four things, and is honest about which are editable:
 *   - the server identity (name, vendor, transport, sandbox) — READ-ONLY, off the reviewed
 *     template `connector_routes.py::_template_info` reads at import time;
 *   - the launch source and its two pins (`digest`, `declaration_digest`) — READ-ONLY, because a
 *     server is vendored and pinned, never something this form re-launches from a different path;
 *   - which tools are exposed — READ-ONLY, the reviewed `capabilities.read` allowlist itself, so
 *     "configuring" an MCP here can never mean widening what it may invoke;
 *   - an env / credential profile — the ONE editable field, and only NAMES. `McpAdapterDeclaration
 *     .mcp.env` (`sources/mcp.py`, `McpServerRef.env: Mapping[str, str]`) is a real, validated
 *     field, so a name entered here lands on the actual declaration the loader compiles — but the
 *     VALUE is never asked for. It is filled in by the deployment's own secret store at connect
 *     time, which this route cannot reach and this form must not pretend to.
 */
import * as React from "react";
import { KeyRound, Plug, Server, Wrench, X } from "lucide-react";
import type { CatalogueEntry } from "@/lib/connector-api";
import { cn } from "@/lib/utils";

const ENV_NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-1.5">
      <dt className="shrink-0 text-2xs text-subtle">{label}</dt>
      <dd className="min-w-0 truncate text-right font-mono text-2xs text-fg">{value}</dd>
    </div>
  );
}

export function McpConfigModal({
  entry,
  initialEnvNames,
  onConfirm,
  onClose,
}: {
  entry: CatalogueEntry;
  initialEnvNames: string[];
  onConfirm: (envNames: string[]) => void;
  onClose: () => void;
}) {
  const [envNames, setEnvNames] = React.useState<string[]>(initialEnvNames);
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const dialogRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  function addEnvName() {
    const name = draft.trim().toUpperCase();
    if (!name) return;
    if (!ENV_NAME_RE.test(name)) {
      setError(`"${name}" must be UPPER_SNAKE_CASE — a name, never a secret value.`);
      return;
    }
    if (envNames.includes(name)) {
      setError(`${name} is already declared.`);
      return;
    }
    setEnvNames((prev) => [...prev, name]);
    setDraft("");
    setError(null);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-modal-title"
        data-testid="mcp-config-modal"
        className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl"
      >
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <Plug className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
            <h2 id="mcp-modal-title" className="truncate text-sm font-semibold text-fg">
              Configure {entry.name}
            </h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-subtle hover:bg-muted hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          <section className="mb-4">
            <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-subtle">
              <Server className="h-3 w-3" aria-hidden />
              Server — reviewed, read-only
            </p>
            <dl className="divide-y divide-border rounded border border-border">
              <Field label="vendor" value={entry.vendor} />
              <Field label="transport" value={entry.transport ?? "—"} />
              <Field label="sandbox" value={entry.sandbox ?? "—"} />
              <Field label="launch source" value={entry.vendored_path ?? "—"} />
              <Field label="artefact digest" value={entry.digest ?? "—"} />
              <Field label="declaration digest" value={entry.declaration_digest ?? "—"} />
            </dl>
            <p className="mt-1.5 text-2xs leading-relaxed text-subtle">
              Vendored and digest-pinned into this repository. A rug-pull that changes a tool
              description with the artefact byte-identical is caught by the second digest, not the
              first — that is why there are two.
            </p>
          </section>

          <section className="mb-4">
            <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-subtle">
              <Wrench className="h-3 w-3" aria-hidden />
              Tools exposed — the closed allowlist
            </p>
            <div className="flex flex-wrap gap-1.5 rounded border border-border bg-bg p-2.5">
              {entry.tools_exposed.length === 0 ? (
                <span className="text-2xs text-subtle">
                  No reviewed tool list yet — nothing can be invoked until one lands.
                </span>
              ) : (
                entry.tools_exposed.map((tool) => (
                  <span
                    key={tool}
                    className="rounded border border-border bg-surface px-1.5 py-0.5 font-mono text-2xs text-fg"
                  >
                    {tool}
                  </span>
                ))
              )}
            </div>
            <p className="mt-1.5 text-2xs leading-relaxed text-subtle">
              A tool the server advertises and this list omits can never be invoked. This form
              does not add or remove one — the next step only chooses whether these are asked for
              as read or write.
            </p>
          </section>

          <section>
            <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-subtle">
              <KeyRound className="h-3 w-3" aria-hidden />
              Env / credential profile
            </p>
            <div className="flex flex-wrap gap-1.5 rounded border border-border bg-bg p-2.5">
              {envNames.length === 0 ? (
                <span className="text-2xs text-subtle">No env vars declared.</span>
              ) : (
                envNames.map((name) => (
                  <span
                    key={name}
                    className="flex items-center gap-1 rounded border border-accent/40 bg-accent/5 px-1.5 py-0.5 font-mono text-2xs text-fg"
                  >
                    {name}
                    <button
                      onClick={() => setEnvNames((prev) => prev.filter((n) => n !== name))}
                      aria-label={`Remove ${name}`}
                      className="text-subtle hover:text-band-review"
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </span>
                ))
              )}
            </div>
            <div className="mt-2 flex gap-1.5">
              <input
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addEnvName();
                  }
                }}
                placeholder="e.g. AIRTABLE_API_KEY"
                data-testid="mcp-env-name-input"
                className="flex-1 rounded border border-border bg-surface px-2 py-1 font-mono text-2xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none"
              />
              <button
                onClick={addEnvName}
                type="button"
                data-testid="mcp-env-name-add"
                className="rounded border border-border px-2.5 py-1 text-2xs text-subtle hover:text-fg"
              >
                Add
              </button>
            </div>
            {error ? <p className="mt-1 text-2xs text-band-review">{error}</p> : null}
            <p className="mt-1.5 text-2xs leading-relaxed text-subtle">
              A NAME only — never type a secret here. It is recorded on the declaration&apos;s real
              <code className="mx-1 text-fg">mcp.env</code>
              field; the deployment&apos;s own secret store supplies the value at connect time, and
              this form has no path to see or store one.
            </p>
          </section>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button
            onClick={onClose}
            className="rounded border border-border px-3 py-1.5 text-2xs text-subtle hover:text-fg"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm(envNames)}
            data-testid="mcp-config-save"
            className={cn(
              "rounded bg-accent px-3.5 py-1.5 text-2xs font-semibold text-accent-fg hover:bg-accent/90",
            )}
          >
            Save configuration
          </button>
        </div>
      </div>
    </div>
  );
}
