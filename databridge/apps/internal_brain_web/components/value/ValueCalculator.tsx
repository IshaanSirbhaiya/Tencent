'use client';
import { ChevronDown, ExternalLink, RotateCcw } from 'lucide-react';
import { useEffect, useId, useMemo, useState } from 'react';
import { Badge, Button, Card } from '@/components/ui';
import { cn } from '@/lib/cn';
import { useMessages } from '@/lib/i18n';
import { analyticsMessages, type AnalyticsKey } from '@/lib/i18n/analytics';
import { BreakEvenChart } from './BreakEvenChart';
import { applyPreset, clamp, compute, DEFAULTS, INPUTS, oneDp, REFERENCE_LINES, usd, usd4dp, usdCompact, usdWhole, type InputId, type InputSpec, type PresetId, type ValueInputs } from './model';

const UNIT: Record<InputId, AnalyticsKey> = {
  staff: 'value.unit.people',
  minutes_saved_per_week: 'value.unit.minweek',
  hourly_cost_usd: 'value.unit.usdh',
  working_weeks: 'value.unit.weeks',
  annual_price_usd: 'value.unit.usdyear',
  answers_per_person_per_day: 'value.unit.perday',
  working_days_per_month: 'value.unit.days',
  tokens_in: 'value.unit.tokens',
  tokens_out: 'value.unit.tokens',
  price_in_per_mtok_usd: 'value.unit.usd',
  price_out_per_mtok_usd: 'value.unit.usd',
};

const NOTE: Partial<Record<InputId, AnalyticsKey>> = {
  staff: 'value.in.staff.note',
  minutes_saved_per_week: 'value.in.minutes_saved_per_week.note',
  hourly_cost_usd: 'value.in.hourly_cost_usd.note',
  annual_price_usd: 'value.in.annual_price_usd.note',
};

const PRESETS: PresetId[] = ['low', 'base', 'high', 'break_even_demo', 'list_price_only'];
const nf = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });

function InputRow({ spec, value, onChange, t }: { spec: InputSpec; value: number; onChange: (v: number) => void; t: (k: AnalyticsKey, v?: Record<string, string | number>) => string }) {
  const id = `v-${spec.id}`;
  const label = t(`value.in.${spec.id}` as AnalyticsKey);
  const note = NOTE[spec.id];
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = (raw: string) => {
    const n = Number(raw.replace(/,/g, ''));
    const v = clamp(spec, n);
    onChange(v);
    setText(String(v));
  };
  if (spec.readonly) {
    return (
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-1.5">
        <span className="text-body-sm text-fg">{label}</span>
        <span className="text-body-sm tabular-nums text-fg">
          US${nf.format(value)}{' '}
          <span className="text-caption text-subtle">
            · {t('value.readonly')} ·{' '}
            {spec.source ? (
              <a href={spec.source} target="_blank" rel="noreferrer noopener" className="text-accent underline-offset-2 hover:underline">
                {t('value.source')}
              </a>
            ) : null}
          </span>
        </span>
      </div>
    );
  }
  return (
    <div className="py-2">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id} className="text-body-sm font-medium text-fg">
          {label}
        </label>
        <span className="flex items-center gap-1.5">
          <input
            id={id}
            inputMode="decimal"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              const n = Number(e.target.value.replace(/,/g, ''));
              if (e.target.value.trim() !== '' && Number.isFinite(n) && (spec.min === undefined || n >= spec.min) && (spec.max === undefined || n <= spec.max)) onChange(n);
            }}
            onBlur={(e) => commit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit((e.target as HTMLInputElement).value);
            }}
            aria-describedby={note ? `${id}-note` : undefined}
            className="h-8 w-28 rounded-md border border-line-strong bg-bg px-2 text-right text-body-sm tabular-nums text-fg"
            data-testid={`input-${spec.id}`}
          />
          <span className="w-20 text-caption text-subtle">{t(UNIT[spec.id])}</span>
        </span>
      </div>
      <input
        type="range"
        min={spec.min}
        max={spec.max}
        step={spec.step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={t('value.slider', { label })}
        aria-valuetext={`${nf.format(value)} ${t(UNIT[spec.id])}`}
        className="mt-2 h-6 w-full cursor-pointer accent-[rgb(var(--accent-solid))]"
        data-testid={`slider-${spec.id}`}
      />
      {note ? (
        <p id={`${id}-note`} className="text-caption text-subtle">
          {t(note)}
        </p>
      ) : null}
    </div>
  );
}

function Result({
  label,
  value,
  unit,
  formula,
  note,
  hero,
  testId,
  estimate,
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  formula: string;
  note?: string;
  hero?: boolean;
  testId: string;
  estimate: string;
}) {
  return (
    <div className={cn('flex min-w-0 flex-col rounded-lg border border-line bg-surface px-4 py-3', hero && 'border-accent/50 bg-accent/[0.05]')} data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-caption font-medium text-muted">{label}</p>
        <Badge tone="neutral">{estimate}</Badge>
      </div>
      <p className={cn('mt-1 tabular-nums text-fg', hero ? 'text-[2.5rem] font-semibold leading-tight tracking-tight' : 'text-kpi')} data-value>
        {value}
        {unit ? <span className="ml-1.5 text-body-sm font-normal text-muted">{unit}</span> : null}
      </p>
      <p className="mt-2 border-t border-line pt-2 font-mono text-micro leading-4 text-subtle">= {formula}</p>
      {note ? <p className="mt-1 text-micro text-subtle">{note}</p> : null}
    </div>
  );
}

/** /value: the ROI calculator from value_calculator.spec.json. Arithmetic only, in the browser. */
export function ValueCalculator() {
  const t = useMessages(analyticsMessages);
  const [inputs, setInputs] = useState<ValueInputs>(DEFAULTS);
  const [preset, setPreset] = useState<PresetId | null>('base');
  const [advanced, setAdvanced] = useState(false);
  const out = useMemo(() => compute(inputs), [inputs]);
  const advId = useId();

  // announce once per change, debounced 300 ms (spec ui_rules)
  const [live, setLive] = useState('');
  useEffect(() => {
    const h = setTimeout(
      () => setLive(t('value.live', { net: usdCompact(out.net_value_usd), verdict: t(out.breaks_even ? 'value.live.yes' : 'value.live.no') })),
      300,
    );
    return () => clearTimeout(h);
  }, [out, t]);

  const set = (id: InputId) => (v: number) => {
    setPreset(null);
    setInputs((cur) => ({ ...cur, [id]: v }));
  };

  const main = INPUTS.filter((i) => !i.advanced);
  const adv = INPUTS.filter((i) => i.advanced);
  const be = out.break_even_minutes_per_week;
  const valuePerMinute = (inputs.staff * inputs.working_weeks * inputs.hourly_cost_usd) / 60;

  return (
    <div className="space-y-4" data-testid="value-calculator">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('value.presets')}>
        <span className="mr-1 text-caption font-medium text-muted">{t('value.presets')}</span>
        {PRESETS.map((p) => (
          <Button
            key={p}
            size="sm"
            variant={preset === p ? 'primary' : 'secondary'}
            aria-pressed={preset === p}
            onClick={() => {
              setInputs((cur) => applyPreset(p, cur));
              setPreset(p);
            }}
            data-testid={`preset-${p}`}
          >
            {t(`value.preset.${p}` as AnalyticsKey)}
          </Button>
        ))}
        <Button
          size="sm"
          variant="ghost"
          icon={RotateCcw}
          onClick={() => {
            setInputs(DEFAULTS);
            setPreset('base');
          }}
        >
          {t('value.reset')}
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Card className="min-w-0 self-start" aria-labelledby="inputs-h">
          <div className="border-b border-line px-4 py-3">
            <h2 id="inputs-h" className="text-heading-sm text-fg">
              {t('value.inputs')}
            </h2>
          </div>
          <div className="divide-y divide-line px-4">
            {main.map((s) => (
              <InputRow key={s.id} spec={s} value={inputs[s.id]} onChange={set(s.id)} t={t} />
            ))}
          </div>
          <div className="border-t border-line">
            <button
              type="button"
              aria-expanded={advanced}
              aria-controls={advId}
              onClick={() => setAdvanced((v) => !v)}
              className="flex w-full items-center justify-between px-4 py-3 text-left text-body-sm font-medium text-fg hover:bg-raised/50"
            >
              {t('value.advanced')}
              <ChevronDown aria-hidden className={cn('h-4 w-4 text-muted transition-transform duration-fast', advanced && 'rotate-180')} />
            </button>
            <div id={advId} hidden={!advanced} className="divide-y divide-line px-4 pb-3">
              <p className="pb-2 text-caption text-subtle">{t('value.advanced.hint')}</p>
              {adv.map((s) => (
                <InputRow key={s.id} spec={s} value={inputs[s.id]} onChange={set(s.id)} t={t} />
              ))}
            </div>
          </div>
        </Card>

        <section className="min-w-0 space-y-4" aria-labelledby="results-h">
          <h2 id="results-h" className="sr-only">
            {t('value.results')}
          </h2>
          <p className="sr-only" aria-live="polite" data-testid="value-live">
            {live}
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Result
              hero
              testId="out-net"
              label={t('value.out.net_value_usd')}
              value={usdCompact(out.net_value_usd)}
              formula={t('value.f.net_value_usd')}
              estimate={t('value.estimate')}
            />
            <Result
              hero
              testId="out-annual"
              label={t('value.out.annual_value_usd')}
              value={usdCompact(out.annual_value_usd)}
              formula={t('value.f.annual_value_usd')}
              estimate={t('value.estimate')}
            />
            <Result
              testId="out-break-even"
              label={t('value.out.break_even_minutes_per_week')}
              value={oneDp(be)}
              unit={t('value.out.break_even.unit')}
              formula={t('value.f.break_even')}
              note={t('value.f.break_even.note')}
              estimate={t('value.estimate')}
            />
            <Result
              testId="out-payback"
              label={t('value.out.payback_weeks')}
              value={out.payback_weeks === null ? t('value.out.payback.never') : oneDp(out.payback_weeks)}
              unit={out.payback_weeks === null ? undefined : t('value.out.payback.unit')}
              formula={t('value.f.payback')}
              estimate={t('value.estimate')}
            />
            <Result
              testId="out-model-year"
              label={t('value.out.model_cost_per_year_usd')}
              value={usd(out.model_cost_per_year_usd)}
              formula={t('value.f.model_year')}
              estimate={t('value.estimate')}
            />
            <Result
              testId="out-model-answer"
              label={t('value.out.model_cost_per_answer_usd')}
              value={usd4dp(out.model_cost_per_answer_usd)}
              formula={t('value.f.model_answer')}
              estimate={t('value.estimate')}
            />
          </div>

          <Card className="px-4 py-3" aria-labelledby="be-h">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 id="be-h" className="text-heading-sm text-fg">
                  {t('value.chart.title')}
                </h3>
                <p className="mt-0.5 text-caption text-muted">{t('value.chart.desc')}</p>
              </div>
              <Badge tone={out.breaks_even ? 'ok' : 'warn'} data-testid="out-breaks-even">
                {t('value.out.breaks_even')} {t(out.breaks_even ? 'value.yes' : 'value.no')}
              </Badge>
            </div>
            <div className="mt-3">
              <BreakEvenChart
                valuePerMinute={valuePerMinute}
                cost={inputs.annual_price_usd}
                minutes={inputs.minutes_saved_per_week}
                breakEven={be}
                labels={{
                  value: t('value.chart.value'),
                  cost: t('value.chart.cost'),
                  you: `${t('value.chart.you')} · ${nf.format(inputs.minutes_saved_per_week)} min`,
                  x: t('value.chart.x'),
                  breakEven: `${oneDp(be)} min`,
                }}
              />
              <p className="mt-2 text-caption text-muted">
                {t('value.chart.alt', {
                  be: oneDp(be),
                  mins: nf.format(inputs.minutes_saved_per_week),
                  value: usdWhole(out.annual_value_usd),
                  cost: usdWhole(inputs.annual_price_usd),
                })}
              </p>
            </div>
          </Card>

          <Card className="px-4 py-3" aria-labelledby="refs-h">
            <h3 id="refs-h" className="text-heading-sm text-fg">
              {t('value.refs.title')}
            </h3>
            <ul className="mt-2 divide-y divide-line">
              {REFERENCE_LINES.map((r) => (
                <li key={r.id} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-2">
                  <span className="min-w-0 text-body-sm text-fg">
                    {r.source.startsWith('http') ? (
                      <a href={r.source} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 underline-offset-2 hover:underline">
                        {t(`value.ref.${r.id}` as AnalyticsKey)}
                        <ExternalLink aria-hidden className="h-3 w-3 text-subtle" />
                      </a>
                    ) : (
                      <span>
                        {t(`value.ref.${r.id}` as AnalyticsKey)} <span className="font-mono text-micro text-subtle">{r.source}</span>
                      </span>
                    )}
                    <span className="ml-2 text-micro text-subtle">{r.tag}</span>
                  </span>
                  <span className="text-caption tabular-nums text-muted">
                    {t('value.refs.min', { n: r.minutes })} ·{' '}
                    {t('value.refs.share', { pct: Number.isFinite(be) ? `${((be / r.minutes) * 100).toFixed(1)}%` : '—' })}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      </div>

      <footer className="rounded-md border border-line bg-surface px-4 py-3 text-caption text-muted" data-testid="value-footer">
        <p>{t('value.footer')}</p>
        <p className="mt-1 text-subtle">{t('value.footer.private')}</p>
      </footer>
    </div>
  );
}
