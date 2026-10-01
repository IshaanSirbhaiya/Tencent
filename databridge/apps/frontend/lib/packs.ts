/**
 * Packs — where every domain noun in this UI comes from.
 *
 * The engine knows no domain (PHASE_PLAN.md Phase 1). A *pack* supplies one vertical's vocabulary,
 * its operator surfaces and its demo trials; the components render whatever the active pack hands
 * them. No file under `app/`, `components/` or `lib/` may contain a domain literal — a grep gate
 * enforces that, and this module is the seam that makes it possible.
 *
 * One source: `GET /packs` on the gateway (`pack_routes.py`), which reads `packs/<pack>/ui.yaml`.
 *
 * HC1 (2026-10-01, SWEEP hardcode audit #6/#7). This used to fall back, silently, to the bundled
 * `config/packs.json` whenever the gateway failed — its vocabulary, trials, demo questions and
 * `available` flags (which marked finance and government unavailable whatever the deployment
 * said). The whole app then looked alive while running on a stale copy. Now an unreachable
 * registry is SHOWN: `UNAVAILABLE_REGISTRY` is a single placeholder pack that says so, with
 * neutral words and no surfaces. `config/packs.json` is kept on disk but no longer imported.
 */
import type { TryGroup } from "@/components/try-chips";
import { GATEWAY, authHeaders } from "./api";

/** What an `/intake` chip does. `fixture` loads a corpus document and reads it in the same click;
 *  `identity-file` posts a file NAMED like an identity document, to show the refusal. */
export type IntakeTrial =
  | { kind: "fixture"; name: string }
  | { kind: "identity-file"; filename: string };

/**
 * The pack's vocabulary. Every key is a word the UI is otherwise forbidden to hardcode.
 *
 * Values are lower-case where they appear mid-sentence and `*_title` where a heading needs them
 * capitalised, because a component that upper-cases a noun it did not choose gets it wrong in the
 * first language that does not capitalise that way.
 */
export interface PackNouns {
  /** The person or entity a determination is about. */
  subject: string;
  subject_plural: string;
  subject_title: string;
  subject_plural_title: string;
  /** The operating organisation the system is deployed inside. */
  org: string;
  org_plural: string;
  org_title: string;
  /** The system of record the subject is resolved against. */
  registry: string;
  /** What the organisation already holds, as opposed to what a document says. */
  record: string;
  /** The knowledge graph, in this pack's words. */
  graph: string;
  /** Display name for the graph workspace. */
  workspace: string;
  /** Whoever issued the incoming document. */
  counterparty: string;
  counterparty_plural: string;
  /** The incoming artefact the pipeline reads. */
  document: string;
  /** What the pipeline determines. */
  determination: string;
  /** The operator surface's name, e.g. the desk the work happens at. */
  operator_surface: string;
  operator_role: string;
  queue: string;
  /** The identity document class staff check in person — never automated. */
  identifier: string;
  /** The entitlement credential whose validity is likewise never checked by software. */
  credential: string;
  /** The document kinds this pack accepts, as a prose list. */
  document_examples: string;
  /** One line describing what the pack does, for the sign-in screen. */
  pitch: string;
}

export interface PackSurface {
  href: string;
  label: string;
  /** Key into the icon map in `components/sidebar.tsx`. Config cannot carry a React component. */
  icon: string;
}

export interface PackOperator {
  staffId: string;
  name: string;
  role: string;
}

export interface PackTrials {
  intelligence: readonly TryGroup<string>[];
  evidence: readonly TryGroup<string>[];
  intake: readonly TryGroup<IntakeTrial>[];
}

/** One beat of the scripted tour (`/demo`). Routes are the pack's own surfaces. */
export interface PackDemoStep {
  route?: string;
  caption: string;
  /** Emitted to whichever page is mounted: `load`, `ask`, `slider`, `node`, `finish`. */
  action?: string;
  ms: number;
}

/** Node/edge visual vocabulary, per pack. Keys mirror the graph kinds the gateway serialises. */
export interface PackGraphVocabulary {
  node_kinds: Record<string, { color: string; label: string; dim?: boolean }>;
  edge_labels: Record<string, string>;
}

export interface Pack {
  id: string;
  label: string;
  tagline: string;
  /** `false` while the pack's adapters and operator surfaces are unbuilt (Phase 3). */
  available: boolean;
  unavailable_note?: string;
  nouns: PackNouns;
  roster: readonly PackOperator[];
  surfaces: readonly PackSurface[];
  trials: PackTrials;
  demo_steps: readonly PackDemoStep[];
  /** The node the tour's `node` beat focuses. Empty string = the beat does nothing. */
  demo_focus_node: string;
  /** The instant the tour's `slider` beat rewinds to, ISO. Empty string = the beat does nothing. */
  demo_as_of: string;
  /** The question the tour's `ask` beat types. Empty string = the beat does nothing. */
  demo_question: string;
  graph_vocabulary: PackGraphVocabulary;
  /** Shown by the ask panel only when `GET /brain/suggested` cannot be reached. */
  brain_suggested_fallback: readonly string[];
}

export interface PackRegistry {
  default_pack: string;
  packs: readonly Pack[];
}

/** The id of the placeholder pack shown while the live registry cannot be reached. */
export const UNAVAILABLE_PACK_ID = "registry-unavailable";

const NEUTRAL_NOUNS: PackNouns = {
  subject: "subject",
  subject_plural: "subjects",
  subject_title: "Subject",
  subject_plural_title: "Subjects",
  org: "organisation",
  org_plural: "organisations",
  org_title: "Organisation",
  registry: "registry",
  record: "record",
  graph: "knowledge graph",
  workspace: "Workspace",
  counterparty: "counterparty",
  counterparty_plural: "counterparties",
  document: "document",
  determination: "determination",
  operator_surface: "Workspace",
  operator_role: "operator",
  queue: "Queue",
  identifier: "identity document",
  credential: "credential",
  document_examples: "a document",
  pitch: "The pack registry could not be reached.",
};

/**
 * What the app shows when `GET /packs` cannot be reached: ONE placeholder pack that says so. Not a
 * vertical, not a copy of one — no surfaces, no trials, no graph vocabulary, `available: false`.
 * Every noun is a plain word, so nothing on screen pretends to be a deployment's vocabulary.
 */
export const UNAVAILABLE_REGISTRY: PackRegistry = {
  default_pack: UNAVAILABLE_PACK_ID,
  packs: [
    {
      id: UNAVAILABLE_PACK_ID,
      label: "Registry unavailable",
      tagline: "The gateway's pack registry (GET /packs) could not be reached.",
      available: false,
      unavailable_note:
        "The pack registry could not be reached, so this screen has no vocabulary, surfaces or " +
        "trials to show. Check that the gateway is running and that you are signed in.",
      nouns: NEUTRAL_NOUNS,
      roster: [],
      surfaces: [],
      trials: { intelligence: [], evidence: [], intake: [] },
      demo_steps: [],
      demo_focus_node: "",
      demo_as_of: "",
      demo_question: "",
      graph_vocabulary: { node_kinds: {}, edge_labels: {} },
      brain_suggested_fallback: [],
    },
  ],
};

/**
 * @deprecated The bundled registry is no longer a fallback. Kept as an alias of the honest
 * placeholder so existing `initialData: FALLBACK_REGISTRY` call sites render "registry unavailable"
 * until the live registry arrives, instead of a stale bundled vertical.
 */
export const FALLBACK_REGISTRY = UNAVAILABLE_REGISTRY;

export function isRegistryUnavailable(registry: PackRegistry): boolean {
  return registry.packs.length === 1 && registry.packs[0].id === UNAVAILABLE_PACK_ID;
}

/** Raised when `GET /packs` fails or returns something that is not a registry. */
export class RegistryUnavailableError extends Error {
  constructor(reason: string) {
    super(`pack registry unavailable: ${reason}`);
    this.name = "RegistryUnavailableError";
  }
}

// --- the last registry the gateway actually served ------------------------------------------------
// Modules that need the graph vocabulary outside React (`brain-layout.ts`) subscribe here rather
// than reading a bundled copy. Listeners get the LIVE registry or nothing.
let liveRegistry: PackRegistry | null = null;
const listeners = new Set<(registry: PackRegistry) => void>();

/** The last registry `GET /packs` returned, or `null` if it has never been reached. */
export function currentLiveRegistry(): PackRegistry | null {
  return liveRegistry;
}

/** Call `listener` with the live registry now (if loaded) and whenever a new one arrives. */
export function onLiveRegistry(listener: (registry: PackRegistry) => void): () => void {
  listeners.add(listener);
  if (liveRegistry) listener(liveRegistry);
  return () => {
    listeners.delete(listener);
  };
}

function publish(registry: PackRegistry): void {
  liveRegistry = registry;
  listeners.forEach((listener) => listener(registry));
}

/** Cheap shape check — a malformed `/packs` payload is reported as unavailable, never guessed at. */
function isRegistry(value: unknown): value is PackRegistry {
  if (!value || typeof value !== "object") return false;
  const r = value as Partial<PackRegistry>;
  return (
    typeof r.default_pack === "string" &&
    Array.isArray(r.packs) &&
    r.packs.length > 0 &&
    r.packs.every((p) => p && typeof p.id === "string" && !!p.nouns)
  );
}

/**
 * Ask the gateway. On any failure, THROW `RegistryUnavailableError` — never substitute a copy.
 *
 * React Query then keeps whatever it already had: the live registry from an earlier success, or
 * `UNAVAILABLE_REGISTRY` (the honest placeholder) on a cold load. Either way nothing on screen is
 * a bundled vertical presented as this deployment's.
 */
export async function fetchPackRegistry(): Promise<PackRegistry> {
  let res: Response;
  try {
    res = await fetch(`${GATEWAY}/packs`, { cache: "no-store", headers: authHeaders() });
  } catch (err) {
    throw new RegistryUnavailableError(err instanceof Error ? err.message : "network error");
  }
  if (!res.ok) throw new RegistryUnavailableError(`GET /packs returned ${res.status}`);
  const body: unknown = await res.json().catch(() => null);
  if (!isRegistry(body)) throw new RegistryUnavailableError("GET /packs returned no registry");
  publish(body);
  return body;
}

export function resolvePack(registry: PackRegistry, id: string | null): Pack {
  return (
    registry.packs.find((p) => p.id === id) ??
    registry.packs.find((p) => p.id === registry.default_pack) ??
    registry.packs[0]
  );
}
