// Colour and caption per KIND for `/graph` — resolved from PACK DATA, never from a kind name
// written in this file (`test_no_domain_branching` forbids the latter, and a kind list compiled
// into the frontend is wrong the first time a pack adds one).
//
// Resolution order, per kind:
//   1. a pack's own `graph_vocabulary.node_kinds[kind]` (colour + caption), read from the LIVE
//      `GET /packs` registry — the same `ui.yaml` the gateway serves, so a pack that recolours a
//      kind is reflected on the next page load without a frontend build;
//   2. otherwise a deterministic fallback: the kind's INDEX in the sorted list of kinds the store
//      actually holds (`GET /graph/facets`), mapped onto a fixed hue ring. Index, not hash, so the
//      first `RING_SIZE` undeclared kinds never collide with each other.
//
// Muted, not neon — the founder rejected full-saturation tailwind-500s on `/brain` ("the glows
// bleed into each other until you cannot see the edges", `lib/brain-layout.ts`), so the fallback
// sits in the same 45-55% saturation / 55-65% lightness band as the hand-tuned pack hexes.
import type { PackRegistry } from "@/lib/packs";

const GOLDEN_ANGLE = 137.50776;
const RING_SIZE = 24;

function buildHueRing(): number[] {
  const ring: number[] = [];
  let h = 8; // start just off pure red
  let guard = 0;
  while (ring.length < RING_SIZE && guard < 2000) {
    guard += 1;
    const hue = Math.round(h) % 360;
    const inOliveBand = hue >= 55 && hue <= 72; // dull olive — the muddy zone
    const inAccentBand = hue >= 205 && hue <= 236; // reserved for --accent / selection
    // reserved for the amber path/attention ring — a kind landing here would be
    // indistinguishable from that ring on a node carrying both
    const inAttentionBand = hue >= 18 && hue <= 50;
    if (!inOliveBand && !inAccentBand && !inAttentionBand) ring.push(hue);
    h = (h + GOLDEN_ANGLE) % 360;
  }
  return ring;
}

/** Generated once at module load — a constant ring, not a per-call computation. */
const HUE_RING = buildHueRing();

/** djb2-style hash — only for a kind that is in neither the pack nor the known-kinds list. */
function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

const SAT = 48;
const LIGHT = 62;

export function humanizeKind(kind: string): string {
  return kind
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export interface KindColor {
  /** Fill/rim colour — the pack's hex when it declares one, else a generated one. */
  color: string;
  /** Human caption — the pack's own when it declares one, else the humanised kind. */
  label: string;
  /** True when this came from pack data, not the generated fallback. */
  fromPack: boolean;
}

export interface KindPalette {
  node: (kind: string) => KindColor;
  edgeLabel: (kind: string) => string;
}

/**
 * Build the palette from a pack registry (or `null` while it loads) and the kinds the store
 * holds. Pure: same inputs, same colours. Packs are read default-first, then in registry order,
 * and the FIRST pack to declare a kind wins — a later pack cannot silently recolour it.
 */
export function buildKindPalette(
  registry: PackRegistry | null,
  knownKinds: readonly string[],
): KindPalette {
  const declared = new Map<string, { color: string; label: string }>();
  const edgeLabels = new Map<string, string>();
  if (registry) {
    const packs = [...registry.packs].sort(
      (a, b) =>
        Number(b.id === registry.default_pack) - Number(a.id === registry.default_pack),
    );
    for (const pack of packs) {
      const vocab = pack.graph_vocabulary;
      for (const [kind, style] of Object.entries(vocab?.node_kinds ?? {})) {
        if (!declared.has(kind) && style?.color) {
          declared.set(kind, { color: style.color, label: style.label || humanizeKind(kind) });
        }
      }
      for (const [kind, label] of Object.entries(vocab?.edge_labels ?? {})) {
        if (!edgeLabels.has(kind) && label) edgeLabels.set(kind, label);
      }
    }
  }
  const undeclared = Array.from(new Set(knownKinds))
    .filter((k) => !declared.has(k))
    .sort();
  const index = new Map(undeclared.map((k, i) => [k, i]));
  const cache = new Map<string, KindColor>();

  return {
    node(kind: string): KindColor {
      const hit = cache.get(kind);
      if (hit) return hit;
      const d = declared.get(kind);
      let out: KindColor;
      if (d) {
        out = { color: d.color, label: d.label, fromPack: true };
      } else {
        const i = index.get(kind) ?? hashString(kind);
        const hue = HUE_RING[i % HUE_RING.length];
        out = { color: `hsl(${hue} ${SAT}% ${LIGHT}%)`, label: humanizeKind(kind), fromPack: false };
      }
      cache.set(kind, out);
      return out;
    },
    edgeLabel(kind: string): string {
      return edgeLabels.get(kind) ?? kind.replace(/_/g, " ");
    },
  };
}
