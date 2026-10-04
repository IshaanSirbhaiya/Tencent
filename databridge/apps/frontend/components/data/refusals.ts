/**
 * Plain words for the refusal codes the materialiser and its compiler answer with.
 *
 * Keyed by the code the gateway sends (the error class name from
 * `packages/materialise/databridge_materialise/errors.py`, or a `RunRefused` code from
 * `platform.py`, or a route-level refusal from `materialise_routes.py`). This is a glossary of the
 * RULES, not of any answer: the compiler's own message is always shown beneath it, verbatim, and a
 * code that is not listed here is shown with that message alone.
 */
export interface Explained {
  headline: string;
  remedy?: string;
}

const GLOSSARY: Record<string, Explained> = {
  FeatureAxisRefused: {
    headline:
      "A feature would be read on the wrong time axis, so a value recorded after the decision time could leak into the row.",
    remedy:
      "Features must be cut on the time the platform learned them. If the source is a facts table, it records no valid time — add a dated source (ingest one whose rows carry dates).",
  },
  NoSystemTimeAxis: {
    headline: "A feature comes from a source that records no system time, so it cannot be cut at the decision time.",
    remedy: "Add a dated source, or ask for the dataset without that feature.",
  },
  LabelAxisRefused: {
    headline: "The label would be read on the wrong time axis. An outcome has to be read on when it actually happened (valid time).",
  },
  MaturityLagMissing: {
    headline:
      "The label has no maturity lag — how long after the decision an outcome counts as final. Without one, recent rows would be silently labelled negative.",
    remedy: 'Say how long to wait, e.g. "label = overdue after 30 days".',
  },
  PolicyNotInHash: { headline: "The policy is not inside the plan's hash, so two different disclosures could share one identity." },
  PointerRequirementDisabled: { headline: "The plan turned off the rule that every cell carries a pointer to its source. That rule cannot be turned off." },
  AbstentionNotFeasible: { headline: "The plan asks the model to abstain at a rate nobody has tested is achievable." },
  ImputationRefused: { headline: "The plan asked to fill in missing values. The platform does not invent values; a missing cell stays missing, with its reason." },
  DeterminismIncomplete: { headline: "The plan does not pin enough (seed, order, executor) to be re-run and produce the same bytes." },
  AirGapUnpinned: { headline: "The plan claims an air-gapped build over sources this build cannot replay air-gapped." },
  ShapeNotImplemented: {
    headline: "That output shape is not built yet. The builder refuses by name rather than produce something it cannot account for.",
    remedy: "Pick a built shape in the picker above.",
  },
  TransformedDisclosureUnrepresentable: {
    headline: "A masked or transformed value would reach the dataset, and the receipt has no honest way to count a partial disclosure yet.",
  },
  MissingnessLeaks: {
    headline: "Which cells were withheld differs by label class — the pattern of missing values would itself leak the label.",
  },
  UngroundedCell: { headline: "A cell reached the dataset without a pointer to its source, so the build was stopped." },
  ChunkResliceFailed: { headline: "A chunk could not be re-sliced from its source document to the same text, so the corpus was not issued." },
  AgentShapedPlanViolated: { headline: "The agent's shaping broke a rule the compiler enforces, so the dataset was refused rather than shipped with a warning." },
  EMPTY_CATALOGUE: {
    headline: "The brain holds nothing the data agent can read yet — no graph kinds with rows, no facts tables, no passage stores.",
    remedy: "Connect and ingest a source on the Sources page first.",
  },
  PLAN_NOT_AUTHORED: { headline: "The planner could not produce a usable plan from this request." },
  PLAN_INVALID: { headline: "The submitted plan is not a valid Materialisation Plan." },
  PRINCIPAL_MISMATCH: { headline: "A plan runs as the principal it names, and this one names somebody else." },
  BAD_REQUEST: { headline: "The gateway could not accept this request as sent — describe the dataset you need in words (or check the answers given)." },
  PlanNeedsInput: {
    headline: "The planner needs more from you before it can write a plan that compiles. Nothing was read.",
    remedy: "Answer its question below — your answer is sent with the same request.",
  },
  // E2E5 W-11: the standard codes the planner answers with now (the class name is `reason_code`)
  PLAN_NEEDS_INPUT: {
    headline: "The planner needs more from you before it can write a plan that compiles. Nothing was read.",
    remedy:
      "Answer its question below — your answer is sent with the same request. For a plain table with no label, use Raw dataset by delegation below.",
  },
  SHAPE_NOT_IMPLEMENTED: {
    headline: "That output shape is not built yet. The builder refuses by name rather than produce something it cannot account for.",
    remedy: "Pick a built shape — or, for a plain table with no label, use Raw dataset by delegation below.",
  },
  BASE_TOO_LARGE: {
    headline: "The table's selection has more rows than this deployment builds in one raw table. The source counted them; nothing was read.",
    remedy: "Add a filter to the design (a time filter keeps only those rows), or ask an operator to raise the bound.",
  },
  ALREADY_RUNNING: { headline: "This exact request is already running. Wait for it to finish." },
  MATERIALISER_NOT_IMPLEMENTED: { headline: "No materialiser is attached to this gateway, so nothing can be built." },
  NOT_AUTHENTICATED: { headline: "The gateway did not accept this page's credential." },
  GATEWAY_UNREACHABLE: { headline: "The gateway did not answer." },
  JOB_NOT_FOUND: { headline: "The gateway no longer knows this job (it may have restarted)." },
};

export function explain(code: string): Explained | null {
  return GLOSSARY[code] ?? null;
}
