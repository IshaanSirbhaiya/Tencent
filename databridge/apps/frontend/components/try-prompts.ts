/**
 * Every one-click trial prompt in the product, in one file.
 *
 * **These are hardcoded, and that is a known compromise.** `/brain` reads its list from
 * `GET /brain/suggested`, which serves `config/assistant.yaml` — built that way on purpose so the
 * demo's prompts are config rather than strings compiled into this bundle. Nothing equivalent is
 * served for `/intelligence`, `/evidence`, `/intake` or `/connectors` yet, so the lists live here,
 * shaped as one exported constant per surface so that moving them behind an endpoint is a change
 * to four `useQuery` calls and a delete, not a hunt through four components.
 *
 * What the gateway would need to serve, to close that gap:
 *
 * ```
 * GET /intelligence/suggested  → { groups: [{ label, note, chips: [{ id, label, payload }] }] }
 * ```
 *
 * one endpoint per surface (or one endpoint keyed by surface), each reading a `suggested:` block
 * from the config that already governs that surface — `agent_playbooks.yaml` for the console,
 * `coverage_rules.yaml`/the graph for the evidence ids, the fixture allow-list `/intake/fixtures`
 * already exposes for the intake chips, and `AGENT_CAPABILITIES.md`'s example server for the
 * connector one. Two of those four are *already* server-side facts this file is duplicating: the
 * intake fixture names and the evidence entity ids both exist in the gateway, and a prompt list
 * that can drift from them is exactly the failure `/brain/suggested` was built to prevent.
 *
 * **Provenance of the prompts themselves.** Every one below was executed against the live gateway
 * (`openai/gpt-4o`) on 2026-08-13 and the observed outcome is recorded beside it. Most come
 * straight from `TRY.md` §B and §C; the exceptions are marked.
 */
import type { TryGroup } from "@/components/try-chips";

/* ------------------------------------------------------------------ /intelligence ------------ */

/**
 * The console's three tiers, which are three different code paths behind one box:
 *
 * * **Ask** — `intent: "answer"`. The agent reads through sealed tools and returns values with
 *   citations. `how many documents are here?` is in the list for the contrast: it is the only one
 *   that makes `analytics` appear, because figures are computed **on request** and are not the
 *   permanent content of the screen.
 * * **Act** — `intent: "propose"`. `databridge_agent.screen_question` calls the request
 *   send-shaped, so it goes to the propose path: a draft in the outbox that a named human approves,
 *   or a stated reason nothing was drafted. Never a send.
 * * **Refuses** — `IDENTITY_VERIFICATION_OUT_OF_SCOPE`, decided before the `as_of` parse, before
 *   the graph is touched and before a model is called (`model_calls: 0`, `TRY.md` §B5).
 *
 * Observed, one request each, no registration selected:
 * | prompt | outcome |
 * |---|---|
 * | which issuers sent documents? | 7 values, no figures (`TRY.md` B1) |
 * | what is blocked right now? | 2 values + figures (`TRY.md` B9) |
 * | which documents have no confirmed patient? | 2 values, document handles not filenames (B7) |
 * | how many documents are here? | 2 values + figures (B2) |
 * | email the patient about the test that is not covered | `intent: propose`, `needs_an_intake` (B8) |
 * | email the patient that their questionnaire is outstanding | `intent: propose`, `needs_an_intake` |
 * | verify the identity of this patient | refused, `model_calls: 0` (B5) |
 * | is candidate 2 the right person? | refused, `model_calls: 0` (B5) |
 *
 * The second Act prompt is **not** the phrasing originally drafted for it. *"let the patient know
 * their questionnaire is outstanding"* is not send-shaped to the screen — `let … know` is in none
 * of `_SEND_REQUESTS` — so it falls through to the read path and answers `NO_GROUNDED_ANSWER`,
 * which is the same phrase-based gap as `R-36` and would have put a chip labelled *Act* on a
 * refusal that has nothing to do with acting. The wording here routes; the gap is real and stays
 * recorded rather than papered over.
 */
export const INTELLIGENCE_TRIALS: readonly TryGroup<string>[] = [
  {
    label: "Ask",
    note: "Read straight from the clinic graph. Every value arrives with the pointer it came from — and only the last one asks for figures, which is why only it makes them appear.",
    chips: [
      { id: "ask-issuers", label: "which issuers sent documents?", payload: "which issuers sent documents?" },
      { id: "ask-blocked", label: "what is blocked right now?", payload: "what is blocked right now?" },
      {
        id: "ask-unconfirmed",
        label: "which documents have no confirmed patient?",
        payload: "which documents have no confirmed patient?",
      },
      { id: "ask-count", label: "how many documents are here?", payload: "how many documents are here?" },
    ],
  },
  {
    label: "Act",
    note: "A request that implies an act becomes a draft in the outbox, never a send. With no registration named, nothing is drafted at all — choosing which patient a message concerns is an identity determination, and this system does not make one.",
    chips: [
      {
        id: "act-uncovered-test",
        label: "email the patient about the test that is not covered",
        payload: "email the patient about the test that is not covered",
      },
      {
        id: "act-questionnaire",
        label: "email the patient that their questionnaire is outstanding",
        payload: "email the patient that their questionnaire is outstanding",
      },
    ],
  },
  {
    label: "Refuses",
    note: "These two are answered with a refusal, and the refusal is decided before the graph is read and before any model is called — model_calls: 0. Identity is checked in person, by a person, against the physical document. This is the product working, not a button that failed.",
    chips: [
      {
        id: "refuse-verify-identity",
        label: "verify the identity of this patient",
        payload: "verify the identity of this patient",
      },
      {
        id: "refuse-candidate-2",
        label: "is candidate 2 the right person?",
        payload: "is candidate 2 the right person?",
      },
    ],
  },
];

/* ------------------------------------------------------------------ /evidence ---------------- */

/**
 * Two tiers, because `/evidence` has two honest outcomes and they must not look alike.
 *
 * Static clinic configuration traces immediately — it is not about a patient and no confirmation
 * is owed. Anything that came out of a document is served **only** once a named member of staff has
 * checked identity in person and confirmed, so it answers `403 AWAITING_HUMAN_CONFIRMATION`. That
 * is the gate, not a lookup failure, and the page already says so in its own words.
 *
 * Observed (`TRY.md` §A10 is exactly the first and third of these):
 * `test:full_blood_count` 200 · `package:blphs-well2` 200 · `coverage_rule:rule-bluepeak-well2` 200
 * · `org:meridian-life-assurance` 200 · `document:chit_03_everwell_group_invitation.txt` 403 ·
 * `patient:pat:4e487c99807b` 403.
 */
export const EVIDENCE_TRIALS: readonly TryGroup<string>[] = [
  {
    label: "Traces",
    note: "Clinic configuration — packages, tests, coverage rules, issuers. No patient is involved, so there is nothing to confirm and the chain is readable now.",
    chips: [
      { id: "ev-test", label: "test:full_blood_count", payload: "test:full_blood_count" },
      { id: "ev-package", label: "package:blphs-well2", payload: "package:blphs-well2" },
      {
        id: "ev-rule",
        label: "coverage_rule:rule-bluepeak-well2",
        payload: "coverage_rule:rule-bluepeak-well2",
      },
      { id: "ev-org", label: "org:meridian-life-assurance", payload: "org:meridian-life-assurance" },
    ],
  },
  {
    label: "Gated",
    note: "These exist. Their evidence is withheld until a named member of staff has checked identity in person and confirmed — AWAITING_HUMAN_CONFIRMATION is the gate holding, and it is deliberately worded so it cannot be mistaken for “no such entity”.",
    chips: [
      {
        id: "ev-document",
        label: "document:chit_03_everwell_group_invitation.txt",
        payload: "document:chit_03_everwell_group_invitation.txt",
      },
      {
        id: "ev-patient",
        label: "patient:pat:4e487c99807b",
        payload: "patient:pat:4e487c99807b",
      },
    ],
  },
];

/* ------------------------------------------------------------------ /intake ------------------ */

/** What an `/intake` chip does. `fixture` loads the corpus text and reads it in the same click;
 *  `identity-file` posts a file NAMED like an identity document, to show the refusal. */
export type IntakeTrial =
  | { kind: "fixture"; name: string }
  | { kind: "identity-file"; filename: string };

/**
 * The fixture buttons already on this page fill the box and stop there — one click short of an
 * outcome. These go the whole way: fetch the corpus text through `/intake/fixtures/{name}`, post it
 * through the same `/intake/upload` a paste uses, and land on the card.
 *
 * The refusal chip is `TRY.md` §E6 without a file picker: it posts a `.txt` whose **name** is
 * identity-shaped to `/intake/upload-file`, where `screen_upload` refuses it on the name alone,
 * before a byte is parsed (`422 IDENTITY_DOCUMENT_REFUSED`, verified). Its content is a single
 * synthetic placeholder line, and nothing about it is stored either way — which is the property
 * being demonstrated. The text path (`/intake/upload`) is deliberately not screened, so this chip
 * has to take the file route to reach the guard at all.
 */
export const INTAKE_TRIALS: readonly TryGroup<IntakeTrial>[] = [
  {
    label: "Read",
    note: "One click: fetch the document, run the whole pipeline, land on its summary card. The same endpoint a paste uses — there is no separate demo path.",
    chips: [
      {
        id: "intake-meridian",
        label: "insurer referral letter",
        title: "chit_01_meridian_referral.txt",
        payload: { kind: "fixture", name: "chit_01_meridian_referral.txt" },
      },
      {
        id: "intake-mol",
        label: "government authorisation form",
        title: "chit_07_mol_authorisation_pee226.txt",
        payload: { kind: "fixture", name: "chit_07_mol_authorisation_pee226.txt" },
      },
      {
        id: "intake-hr-email",
        label: "HR email",
        title: "chit_09_coralharbour_hr_email.eml",
        payload: { kind: "fixture", name: "chit_09_coralharbour_hr_email.eml" },
      },
    ],
  },
  {
    label: "Refuses",
    note: "Uploads a file named like an NRIC scan. It is refused on the name, before a byte of it is parsed — the banner above is a mechanism, not a promise.",
    chips: [
      {
        id: "intake-identity-refusal",
        label: "upload something named like an NRIC",
        title: "posts nric_front.txt — refused by screen_upload before extraction",
        payload: { kind: "identity-file", filename: "nric_front.txt" },
      },
    ],
  },
];

/* ------------------------------------------------------------------ /connectors -------------- */

export type ConnectorTrial = { name: string; url: string; description: string };

/**
 * The one chip on this page **fills the form and stops**, which is the opposite of what every other
 * chip in this file does, and it is deliberate: the READ/ACT choice has no default precisely so
 * that nothing act-capable is registered into the agent's aggregator because a control happened to
 * start somewhere (`connect-form.tsx`, R-34). A chip that submitted would be choosing a capability
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
