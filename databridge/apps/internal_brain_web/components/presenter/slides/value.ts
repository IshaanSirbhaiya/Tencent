// The value calculator's formulas, exactly as written in
// docs/finals/rubric/impact_feasibility/value_calculator.spec.json ("outputs"). Inputs come from
// public/finals/value_defaults.json (a copy of the spec's defaults). Full precision; round only for
// display. Pure functions, no I/O.

export interface ValueInputs {
  staff: number;
  minutes_saved_per_week: number;
  hourly_cost_usd: number;
  working_weeks: number;
  annual_price_usd: number;
  answers_per_person_per_day: number;
  working_days_per_month: number;
  tokens_in: number;
  tokens_out: number;
  price_in_per_mtok_usd: number;
  price_out_per_mtok_usd: number;
}

export interface ValueOutputs {
  annual_value_usd: number;
  model_cost_per_answer_usd: number;
  model_cost_per_year_usd: number;
  net_value_usd: number;
  break_even_minutes_per_week: number;
  payback_weeks: number | null;
  breaks_even: boolean;
}

const KEYS: (keyof ValueInputs)[] = [
  'staff',
  'minutes_saved_per_week',
  'hourly_cost_usd',
  'working_weeks',
  'annual_price_usd',
  'answers_per_person_per_day',
  'working_days_per_month',
  'tokens_in',
  'tokens_out',
  'price_in_per_mtok_usd',
  'price_out_per_mtok_usd',
];

/** The inputs object if every field is a finite number, else null (the slide then says so). */
export function readInputs(raw: unknown): ValueInputs | null {
  const o = (raw as { inputs?: Record<string, unknown> } | null)?.inputs;
  if (!o || typeof o !== 'object') return null;
  for (const k of KEYS) if (typeof o[k] !== 'number' || !Number.isFinite(o[k] as number)) return null;
  return o as unknown as ValueInputs;
}

export function computeValue(i: ValueInputs): ValueOutputs {
  const annual_value_usd = i.staff * (i.minutes_saved_per_week / 60) * i.working_weeks * i.hourly_cost_usd;
  const model_cost_per_answer_usd = (i.tokens_in * i.price_in_per_mtok_usd) / 1e6 + (i.tokens_out * i.price_out_per_mtok_usd) / 1e6;
  const model_cost_per_year_usd = i.staff * i.answers_per_person_per_day * i.working_days_per_month * 12 * model_cost_per_answer_usd;
  const net_value_usd = annual_value_usd - i.annual_price_usd - model_cost_per_year_usd;
  const break_even_minutes_per_week = (i.annual_price_usd / (i.staff * i.working_weeks * i.hourly_cost_usd)) * 60;
  const payback_weeks = i.minutes_saved_per_week > 0 ? i.annual_price_usd / (i.staff * (i.minutes_saved_per_week / 60) * i.hourly_cost_usd) : null;
  return {
    annual_value_usd,
    model_cost_per_answer_usd,
    model_cost_per_year_usd,
    net_value_usd,
    break_even_minutes_per_week,
    payback_weeks,
    breaks_even: i.minutes_saved_per_week >= break_even_minutes_per_week,
  };
}

/** "usd_compact" in the spec: US$1.28M, US$441.6K. */
export function usdCompact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `US$${(v / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`;
  if (a >= 1e3) return `US$${(v / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return `US$${Math.round(v)}`;
}

/** The spoken figure: one decimal in millions, whole thousands (US$1.3M, US$442K). */
export function usdRounded(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `US$${(v / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (a >= 1e3) return `US$${Math.round(v / 1e3)}K`;
  return `US$${Math.round(v)}`;
}

/** The exact figure in whole dollars (US$1,283,400). */
export function usdExact(v: number): string {
  return `US$${Math.round(v).toLocaleString('en-SG')}`;
}

/** Whole minutes when at least one ("7"), else one decimal ("0.4"). */
export function minutesRounded(v: number): string {
  return v >= 1 ? String(Math.round(v)) : v.toFixed(1);
}
