"use client";
/**
 * One-click trial prompts.
 *
 * **The domain ones no longer live here.** `/intelligence`, `/evidence` and `/intake` are the
 * *active pack's* operator surfaces, so their chips are the active pack's content: they come from
 * `trials` in `config/packs.json` (and, once workstream 1C serves it, from `GET /packs`) via
 * `usePackTrials`. Switching packs therefore switches the prompts, which is the property that makes
 * the pack switcher mean something rather than merely relabel headings.
 *
 * What remains here is the one list that is **not** a vertical's: the connector chip, which fills a
 * form pointing at a reference MCP server and has no domain content at all.
 *
 * Provenance of the pack prompts: every one was executed against the live gateway
 * (`openai/gpt-4o`) on 2026-08-13 and the observed outcome is recorded in `TRY.md` §B and §C. The
 * three tiers on `/intelligence` are three different code paths behind one box — **Ask**
 * (`intent: "answer"`), **Act** (`intent: "propose"`, a draft in the outbox a named human
 * approves, never a send), and **Refuses** (`IDENTITY_VERIFICATION_OUT_OF_SCOPE`, decided before
 * the graph is touched and before a model is called, `model_calls: 0`). `/evidence` has two tiers
 * for the same reason: static pack configuration traces immediately, while anything that came out
 * of a document answers `403 AWAITING_HUMAN_CONFIRMATION` — the gate holding, not a lookup failure.
 */
import type { TryGroup } from "@/components/try-chips";
import { usePack } from "@/lib/pack-context";
import type { IntakeTrial, PackTrials } from "@/lib/packs";

export type { IntakeTrial } from "@/lib/packs";

/**
 * The active pack's chips for one surface.
 *
 * Overloaded rather than generic because each surface's payload type differs and a caller should
 * not have to name it: `/intake` gets `IntakeTrial`, the two question boxes get `string`.
 */
export function usePackTrials(surface: "intake"): readonly TryGroup<IntakeTrial>[];
export function usePackTrials(surface: "intelligence" | "evidence"): readonly TryGroup<string>[];
export function usePackTrials(surface: keyof PackTrials) {
  const { pack } = usePack();
  return pack.trials[surface] ?? [];
}

/* ------------------------------------------------------------------ /connectors -------------- */

export type ConnectorTrial = { name: string; url: string; description: string };

/**
 * The one chip on this page **fills the form and stops**, which is the opposite of what every other
 * chip does, and it is deliberate: the READ/ACT choice has no default precisely so that nothing
 * act-capable is registered into the agent's aggregator because a control happened to start
 * somewhere (`connect-form.tsx`, R-34). A chip that submitted would be choosing a capability
 * boundary on a visitor's behalf — the exact mistake this screen exists to prevent. So it types the
 * awkward part (`host.docker.internal`, the name rules) and leaves the decision.
 *
 * The server is `TRY.md` §D7–D8: `supergateway` bridging the stdio memory server onto
 * `:8790/mcp`. §D9 is why it is worth trying — it exposes `create_entities` and `delete_entities`,
 * so connecting it as READ shows mutating tools listed as agent-reachable, which is the argument
 * for ACT made by the page rather than in a doc.
 */
export const CONNECTOR_TRIALS: readonly TryGroup<ConnectorTrial>[] = [
  {
    label: "Fill",
    note: "Fills the fields and stops. READ or ACT stays unchosen — that boundary decides which process fronts the server, and nothing here will pick it for you.",
    chips: [
      {
        id: "connector-memory",
        label: "the memory server on :8790",
        title: "npx -y supergateway --stdio \"npx -y @modelcontextprotocol/server-memory\" --outputTransport streamableHttp --port 8790 --streamableHttpPath /mcp",
        payload: {
          name: "thirdparty_memory",
          url: "http://host.docker.internal:8790/mcp",
          description: "Reference MCP memory server, bridged from stdio by supergateway.",
        },
      },
    ],
  },
];
