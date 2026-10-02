"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AuthChip } from "@/components/auth-chip";

// Top bar with the gateway connection status dot (green/red) — the demo's "is it live" signal.
export function Topbar({ title }: { title: string }) {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 5000,
  });
  const ok = !isError && data?.status === "ok";
  return (
    <header className="flex h-12 items-center justify-between border-b border-border bg-surface px-5">
      <h1 className="text-sm font-semibold tracking-tight">{title}</h1>
      <div className="flex items-center gap-3 text-2xs text-subtle">
        {ok && <AuthChip />}
        <span className="flex items-center gap-2">
          <span className={cn("h-2 w-2 rounded-full", ok ? "bg-band-auto" : "bg-band-review")} />
          gateway {ok ? "connected" : "offline"}
        </span>
      </div>
    </header>
  );
}
