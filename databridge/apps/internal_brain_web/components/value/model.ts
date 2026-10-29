// The value calculator's arithmetic, from docs/finals/rubric/impact_feasibility/value_calculator.spec.json
// (spec_version 1.0). Pure functions, no imports: the unit test runs them in Node against the spec's
// own test vectors, and checks that the defaults and ranges below still match the spec file.
// Everything here is a PROJECTION; hours saved are measured in the pilot (ROI_MODEL.md).

export type InputId =
  | 'staff'
  | 'minutes_saved_per_week'
  | 'hourly_cost_usd'
  | 'working_weeks'
  | 'annual_price_usd'
  | 'answers_per_person_per_day'
  | 'working_days_per_month'
  | 'tokens_in'
  | 'tokens_out'
  | 'price_in_per_mtok_usd'
  | 'price_out_per_mtok_usd';

export type ValueInputs = Record<InputId, number>;

export interface InputSpec {
  id: InputId;
  default: number;
  min?: number;
  max?: number;
  step?: number;
  advanced?: boolean;
  readonly?: boolean;
  source?: string;
}

export const INPUTS: InputSpec[] = [
  { id: 'staff', default: 600, min: 50, max: 10000, step: 50 },
  { id: 'minutes_saved_per_week', default: 90, min: 0, max: 480, step: 1 },
  { id: 'hourly_cost_usd', default: 31, min: 10, max: 150, step: 1 },
  { id: 'working_weeks', default: 46, min: 40, max: 52, step: 1 },
  { id: 'annual_price_usd', default: 100000, min: 0, max: 1000000, step: 1000 },
  { id: 'answers_per_person_per_day', default: 10, min: 0, max: 100, step: 1 },
  { id: 'working_days_per_month', default: 21, min: 15, max: 23, step: 1, advanced: true },
  { id: 'tokens_in', default: 4000, min: 500, max: 50000, step: 100, advanced: true },
  { id: 'tokens_out', default: 300, min: 50, max: 4000, step: 10, advanced: true },
  { id: 'price_in_per_mtok_usd', default: 0.132, advanced: true, readonly: true, source: 'https://www.tencentcloud.com/document/product/1300/78937' },
  { id: 'price_out_per_mtok_usd', default: 0.528, advanced: true, readonly: true, source: 'https://www.tencentcloud.com/document/product/1300/78937' },
];

export const DEFAULTS: ValueInputs = Object.fromEntries(INPUTS.map((i) => [i.id, i.default])) as ValueInputs;

export type PresetId = 'low' | 'base' | 'high' | 'break_even_demo' | 'list_price_only';

/** Presets change only the inputs they name (spec "presets"). */
export function applyPreset(id: PresetId, cur: ValueInputs): ValueInputs {
  switch (id) {
    case 'low':
      return { ...cur, minutes_saved_per_week: 30, hourly_cost_usd: 32 };
    case 'base':
      return { ...cur, minutes_saved_per_week: 90, hourly_cost_usd: 31 };
    case 'high':
      return { ...cur, minutes_saved_per_week: 240, hourly_cost_usd: 37 };
    case 'break_even_demo':
      return { ...cur, minutes_saved_per_week: 7 };
    case 'list_price_only':
      return { ...cur, annual_price_usd: cur.staff * 10 * 12 };
  }
}

export interface ValueOutputs {
  annual_value_usd: number;
  model_cost_per_answer_usd: number;
  model_cost_per_year_usd: number;
  net_value_usd: number;
  break_even_minutes_per_week: number;
  /** null = never (no time saved). */
  payback_weeks: number | null;
  breaks_even: boolean;
}

/** The spec's output formulas, in full precision (round only for display). */
export function compute(i: ValueInputs): ValueOutputs {
  const annual_value_usd = i.staff * (i.minutes_saved_per_week / 60) * i.working_weeks * i.hourly_cost_usd;
  const model_cost_per_answer_usd = (i.tokens_in * i.price_in_per_mtok_usd) / 1e6 + (i.tokens_out * i.price_out_per_mtok_usd) / 1e6;
  const model_cost_per_year_usd = i.staff * i.answers_per_person_per_day * i.working_days_per_month * 12 * model_cost_per_answer_usd;
  const net_value_usd = annual_value_usd - i.annual_price_usd - model_cost_per_year_usd;
  const denom = i.staff * i.working_weeks * i.hourly_cost_usd;
  const break_even_minutes_per_week = denom > 0 ? (i.annual_price_usd / denom) * 60 : Infinity;
  const payback_weeks = i.minutes_saved_per_week > 0 ? i.annual_price_usd / (i.staff * (i.minutes_saved_per_week / 60) * i.hourly_cost_usd) : null;
  const breaks_even = i.minutes_saved_per_week >= break_even_minutes_per_week;
  return { annual_value_usd, model_cost_per_answer_usd, model_cost_per_year_usd, net_value_usd, break_even_minutes_per_week, payback_weeks, breaks_even };
}

/** Clamp a typed value into the input's range (number fields accept anything while typing). */
export function clamp(spec: InputSpec, v: number): number {
  if (!Number.isFinite(v)) return spec.default;
  let x = v;
  if (typeof spec.min === 'number') x = Math.max(spec.min, x);
  if (typeof spec.max === 'number') x = Math.min(spec.max, x);
  return x;
}

/* ------------------------------------------------------------------ display formats (spec "format") */

const usd0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function usdCompact(v: number): string {
  const sign = v < 0 ? '−' : '';
  const a = Math.abs(v);
  if (a >= 1e6) return `${sign}US$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${sign}US$${(a / 1e3).toFixed(1)}k`;
  return `${sign}US$${usd0.format(a)}`;
}

export function usd(v: number): string {
  const sign = v < 0 ? '−' : '';
  return `${sign}US$${usd2.format(Math.abs(v))}`;
}

export function usdWhole(v: number): string {
  const sign = v < 0 ? '−' : '';
  return `${sign}US$${usd0.format(Math.abs(v))}`;
}

export function usd4dp(v: number): string {
  return `US$${v.toFixed(4)}`;
}

export function oneDp(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return '—';
  return v.toFixed(1);
}

/** The spec's reference lines (minutes per person per week), each with its source. */
export const REFERENCE_LINES = [
  { id: 'apqc', minutes: 168, source: 'https://www.businesswire.com/news/home/20211109005449/en', tag: 'secondary' },
  { id: 'atlassian', minutes: 600, source: 'https://www.atlassian.com/blog/state-of-teams-2025', tag: 'primary; 40-hour week is our conversion' },
  { id: 'brief', minutes: 720, source: 'docs/tencent/SUBMISSION.md section 2', tag: 'challenge brief' },
] as const;
