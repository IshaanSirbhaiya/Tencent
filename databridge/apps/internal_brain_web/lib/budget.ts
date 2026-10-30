// The "Model budget" line from /ib/status's model_budget block (admin and compliance only; D-42).
import type { BudgetKey } from './i18n/budget';
import type { ModelBudget } from './types';

type T = (key: BudgetKey, vars?: Record<string, string | number>) => string;

const usd = (v: number | null | undefined) => (typeof v === 'number' ? `US$${v.toFixed(2)}` : '?');

/** `{ label, status, detail }`: pass while calls are allowed, warn at a cap or a bad ledger. */
export function budgetLine(b: ModelBudget, t: T): { label: string; status: 'pass' | 'warn'; detail: string } {
  const label = t('budget.label');
  if (!b.ok) return { label, status: 'warn', detail: t('budget.closed') };
  const parts = [
    t('budget.spent', { spent: usd(b.spent_usd), cap: usd(b.total_cap_usd) }),
    t('budget.today', { today: usd(b.today_usd), daily: usd(b.daily_cap_usd) }),
    t('budget.calls', { n: b.calls ?? '?' }),
  ];
  if (!b.available) parts.push(t('budget.capReached'));
  return { label, status: b.available ? 'pass' : 'warn', detail: parts.join(' · ') };
}
