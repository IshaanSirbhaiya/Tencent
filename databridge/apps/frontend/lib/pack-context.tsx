"use client";
/**
 * The active pack, and the nouns that come with it.
 *
 * Every domain word on screen is read through `useNouns()`. A component that reaches for a literal
 * instead has hardcoded a vertical into the platform, which is the thing Phase 1 exists to undo.
 *
 * The choice is per-browser and deliberately NOT sent to the gateway yet: switching packs here
 * changes what the UI *says*, and the engine-side switch (which pack's rules and adapters answer)
 * lands with the adapter fleet in Phase 3. The control is honest about that — packs whose surfaces
 * are unbuilt render as unavailable rather than as a dead dropdown.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  FALLBACK_REGISTRY,
  fetchPackRegistry,
  resolvePack,
  type Pack,
  type PackNouns,
  type PackRegistry,
} from "./packs";

const STORAGE_KEY = "walnut.active.pack.v1";

interface PackContextValue {
  registry: PackRegistry;
  pack: Pack;
  setPack: (id: string) => void;
}

const PackContext = React.createContext<PackContextValue | null>(null);

export function PackProvider({ children }: { children: React.ReactNode }) {
  // `initialData` so the first paint has a full vocabulary. Without it every label on the shell
  // would flash empty for one frame on a cold load, which reads as a broken app rather than a
  // loading one.
  const { data: registry } = useQuery({
    queryKey: ["packs"],
    queryFn: fetchPackRegistry,
    initialData: FALLBACK_REGISTRY,
    // Stale from the first frame. `initialData` with a non-zero `staleTime` counts as FRESH, so
    // without this the query NEVER fetches on mount and the bundled fallback silently becomes the
    // only source of vocabulary — `GET /packs` is built and would never once be called.
    //
    // This surface is the worst place for that bug: every noun in the product comes from here, so
    // the whole app would look alive while being entirely offline, and the pack switcher would
    // appear to work while switching between two hardcoded fixtures.
    initialDataUpdatedAt: 0,
    staleTime: 5 * 60_000,
  });

  // Read AFTER mount: server and client must render the same first frame or React tears down the
  // whole tree on a hydration mismatch.
  const [activeId, setActiveId] = React.useState<string | null>(null);
  React.useEffect(() => {
    try {
      setActiveId(window.localStorage.getItem(STORAGE_KEY));
    } catch {
      /* storage disabled — the default pack is the answer. */
    }
  }, []);

  const setPack = React.useCallback((id: string) => {
    setActiveId(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* non-fatal: the switch still applies for this session. */
    }
  }, []);

  const pack = resolvePack(registry, activeId);
  const value = React.useMemo<PackContextValue>(
    () => ({ registry, pack, setPack }),
    [registry, pack, setPack],
  );

  return <PackContext.Provider value={value}>{children}</PackContext.Provider>;
}

export function usePack(): PackContextValue {
  const ctx = React.useContext(PackContext);
  if (!ctx) throw new Error("usePack must be used inside <PackProvider>");
  return ctx;
}

/** The active pack's vocabulary. The only sanctioned source of a domain noun in this app. */
export function useNouns(): PackNouns {
  return usePack().pack.nouns;
}
