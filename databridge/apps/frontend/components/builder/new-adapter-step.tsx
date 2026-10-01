"use client";
/**
 * Step 1 — "New adapter — name it, and see the adapters that already exist."
 *
 * The list is the SAME `/builder/adapter/declarations` the Agent Builder's sibling pane opens
 * from — shipped, reviewed adapter declarations discovered under `packs/` and `fixtures/packs/`
 * — so "adapters that already exist" means the same thing on both tabs of Builder.
 */
import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import { connectorApi } from "@/lib/connector-api";

export function NewAdapterStep({
  name,
  onNameChange,
}: {
  name: string;
  onNameChange: (v: string) => void;
}) {
  const existing = useQuery({
    queryKey: ["existing-adapters"],
    queryFn: () => connectorApi.existingAdapters(),
  });
  const declarations = existing.data?.ok ? existing.data.data.declarations : [];

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label htmlFor="adapter-name" className="mb-1 block text-2xs font-medium text-fg">
          Adapter name
        </label>
        <input
          id="adapter-name"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="Defaults to the selected server's catalogue id"
          data-testid="adapter-name-input"
          className="w-full rounded border border-border bg-bg px-2.5 py-1.5 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
      </div>

      <div>
        <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-subtle">
          <Boxes className="h-3 w-3" aria-hidden />
          Adapters already in this deployment
        </p>
        {existing.isLoading ? (
          <p className="rounded border border-border bg-surface px-3 py-4 text-center text-2xs text-subtle">
            Loading…
          </p>
        ) : declarations.length === 0 ? (
          <p className="rounded border border-border bg-surface px-3 py-4 text-center text-2xs text-subtle">
            None shipped with this deployment yet.
          </p>
        ) : (
          <ul className="flex flex-col gap-1 rounded border border-border">
            {declarations.map((d) => (
              <li
                key={d.id}
                className="flex items-baseline justify-between gap-3 border-b border-border px-3 py-1.5 last:border-b-0"
              >
                <code className="min-w-0 truncate text-2xs text-fg">{d.id}</code>
                <span className="shrink-0 text-2xs text-subtle">{d.pack || "—"}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
