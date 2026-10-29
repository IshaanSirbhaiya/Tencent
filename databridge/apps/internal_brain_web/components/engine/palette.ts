// Colour and caption per node kind, resolved from PACK DATA (graph_vocabulary in the pack's ui.yaml,
// served by the gateway's GET /packs), never from a kind name written here: the engine knows no
// domain (test_no_domain_branching). A kind no pack declares gets a muted hue from a fixed ring, by
// its index in the sorted list of kinds the store holds (the original kind-colors.ts fallback).
import { humanizeKind, type PackRegistry } from '@/lib/engine-api';

const GOLDEN = 137.50776;

function hueRing(size: number): number[] {
  const out: number[] = [];
  let h = 8;
  let guard = 0;
  while (out.length < size && guard < 2000) {
    guard += 1;
    const hue = Math.round(h) % 360;
    // keep clear of the olive band, the accent (indigo) band and the warn (amber) band
    if (!(hue >= 55 && hue <= 72) && !(hue >= 225 && hue <= 255) && !(hue >= 18 && hue <= 50)) out.push(hue);
    h = (h + GOLDEN) % 360;
  }
  return out;
}
const RING = hueRing(24);

export interface KindStyle {
  color: string;
  label: string;
  fromPack: boolean;
}

export interface Palette {
  node: (kind: string) => KindStyle;
  edgeLabel: (kind: string) => string;
}

export function buildPalette(registry: PackRegistry | null, knownKinds: readonly string[]): Palette {
  const declared = new Map<string, { color: string; label: string }>();
  const edgeLabels = new Map<string, string>();
  if (registry) {
    const packs = [...registry.packs].sort((a, b) => Number(b.id === registry.default_pack) - Number(a.id === registry.default_pack));
    for (const p of packs) {
      for (const [kind, s] of Object.entries(p.graph_vocabulary?.node_kinds ?? {})) {
        if (!declared.has(kind) && s?.color && /^#[0-9a-f]{3,8}$/i.test(s.color)) declared.set(kind, { color: s.color, label: s.label || humanizeKind(kind) });
      }
      for (const [kind, label] of Object.entries(p.graph_vocabulary?.edge_labels ?? {})) {
        if (!edgeLabels.has(kind) && label) edgeLabels.set(kind, label);
      }
    }
  }
  const undeclared = Array.from(new Set(knownKinds)).filter((k) => !declared.has(k)).sort();
  const index = new Map(undeclared.map((k, i) => [k, i]));
  const cache = new Map<string, KindStyle>();
  return {
    node(kind) {
      const hit = cache.get(kind);
      if (hit) return hit;
      const d = declared.get(kind);
      let s: KindStyle;
      if (d) s = { ...d, fromPack: true };
      else {
        let i = index.get(kind);
        if (i === undefined) {
          let h = 5381;
          for (let c = 0; c < kind.length; c++) h = ((h << 5) + h + kind.charCodeAt(c)) | 0;
          i = Math.abs(h);
        }
        s = { color: `hsl(${RING[i % RING.length]} 42% 58%)`, label: humanizeKind(kind), fromPack: false };
      }
      cache.set(kind, s);
      return s;
    },
    edgeLabel(kind) {
      return edgeLabels.get(kind) ?? kind.replace(/_/g, ' ');
    },
  };
}
