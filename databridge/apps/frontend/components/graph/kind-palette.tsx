"use client";
/**
 * One palette for the whole `/graph` surface, so the canvas, the legend, the filter bar and the
 * provenance panel can never disagree about what colour a kind is. Built in `graph-view.tsx` from
 * `GET /packs` + `GET /graph/facets` (see `kind-colors.ts`) and handed down by context.
 */
import * as React from "react";
import { buildKindPalette, type KindPalette } from "./kind-colors";

const KindPaletteContext = React.createContext<KindPalette>(buildKindPalette(null, []));

export function KindPaletteProvider({
  palette,
  children,
}: {
  palette: KindPalette;
  children: React.ReactNode;
}) {
  return <KindPaletteContext.Provider value={palette}>{children}</KindPaletteContext.Provider>;
}

export function useKindPalette(): KindPalette {
  return React.useContext(KindPaletteContext);
}
