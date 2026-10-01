"use client";
import * as React from "react";
import { usePack } from "@/lib/pack-context";
import { cn } from "@/lib/utils";

/**
 * A kind's display name and colour, from PACK DATA — every pack in the registry's
 * `graph_vocabulary`, since one graph can hold several packs' kinds. A kind no pack styles is shown
 * by its own name in the neutral tone; nothing here names a kind.
 */
export function useKindStyle(): (kind: string) => { label: string; color: string | null } {
  const { registry } = usePack();
  const table = React.useMemo(() => {
    const out: Record<string, { label: string; color: string }> = {};
    for (const pack of registry.packs) {
      for (const [kind, style] of Object.entries(pack.graph_vocabulary?.node_kinds ?? {})) {
        if (!(kind in out)) out[kind] = { label: style.label, color: style.color };
      }
    }
    return out;
  }, [registry]);
  return React.useCallback(
    (kind: string) => table[kind] ?? { label: kind.replace(/_/g, " "), color: null },
    [table],
  );
}

export function KindChip({
  kind,
  className,
  children,
}: {
  kind: string;
  className?: string;
  children?: React.ReactNode;
}) {
  const style = useKindStyle()(kind);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-2xs",
        className,
      )}
    >
      <span
        aria-hidden
        className="inline-block h-2 w-2 rounded-full bg-muted"
        style={style.color ? { backgroundColor: style.color } : undefined}
      />
      <span>{style.label}</span>
      {children}
    </span>
  );
}
