import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { DEFAULTS, INPUTS, compute, type InputId, type ValueOutputs } from '../../components/value/model';
import { expectAccessible } from './helpers';

// The /value calculator against docs/finals/rubric/impact_feasibility/value_calculator.spec.json:
// the spec's six test vectors (pure arithmetic, no browser), the defaults and ranges, and the page.

const SPEC_PATH = resolve(__dirname, '../../../../../docs/finals/rubric/impact_feasibility/value_calculator.spec.json');
const spec = JSON.parse(readFileSync(SPEC_PATH, 'utf8')) as {
  inputs: { id: InputId; default: number; min?: number; max?: number; step?: number }[];
  test_vectors: { name: string; inputs: Partial<Record<InputId, number>>; expect: Partial<Record<keyof ValueOutputs, number | boolean>> }[];
};

/** Spec "rounding": tolerance 0.5% or 0.01 absolute. */
const close = (got: number, want: number) => Math.abs(got - want) <= Math.max(0.01, Math.abs(want) * 0.005);

test.describe('value model (spec test vectors)', () => {
  test('defaults and ranges match the spec', () => {
    for (const s of spec.inputs) {
      const mine = INPUTS.find((i) => i.id === s.id);
      expect(mine, s.id).toBeTruthy();
      expect(mine!.default, `${s.id} default`).toBe(s.default);
      if (s.min !== undefined) expect(mine!.min, `${s.id} min`).toBe(s.min);
      if (s.max !== undefined) expect(mine!.max, `${s.id} max`).toBe(s.max);
    }
  });

  test('there are six vectors', () => {
    expect(spec.test_vectors).toHaveLength(6);
  });

  for (const v of spec.test_vectors) {
    test(`vector: ${v.name}`, () => {
      const out = compute({ ...DEFAULTS, ...v.inputs });
      for (const [k, want] of Object.entries(v.expect) as [keyof ValueOutputs, number | boolean][]) {
        const got = out[k];
        if (typeof want === 'boolean') expect(got, `${v.name}.${k}`).toBe(want);
        else expect(close(got as number, want), `${v.name}.${k}: got ${got}, want ${want}`).toBe(true);
      }
    });
  }

  test('payback is null with no time saved', () => {
    expect(compute({ ...DEFAULTS, minutes_saved_per_week: 0 }).payback_weeks).toBeNull();
  });
});

test.describe('/value page', () => {
  test.beforeEach(async ({ page }) => {
    const r = await page.request.post('/api/ib/auth/demo-login', { data: { persona: 'jdoe' } });
    expect(r.ok()).toBeTruthy();
  });

  test('presets, the 7-minute edge and keyboard input', async ({ page }) => {
    await page.goto('/value');
    await expect(page.getByTestId('out-annual')).toContainText('US$1.28M');
    await expect(page.getByTestId('out-break-even')).toContainText('7.0');
    await page.getByTestId('preset-break_even_demo').click();
    await expect(page.getByTestId('out-breaks-even')).toContainText('No');
    await expect(page.getByTestId('out-annual')).toContainText('US$99.8k');
    const field = page.getByTestId('input-minutes_saved_per_week');
    await field.fill('8');
    await field.press('Enter');
    await expect(page.getByTestId('out-breaks-even')).toContainText('Yes');
    await expect(page.getByTestId('out-annual')).toContainText('US$114.1k');
    // the slider is keyboard operable
    await page.getByTestId('slider-minutes_saved_per_week').focus();
    await page.keyboard.press('ArrowRight');
    await expect(field).toHaveValue('9');
    await page.getByTestId('preset-high').click();
    await expect(page.getByTestId('out-annual')).toContainText('US$4.08M');
    await page.getByTestId('preset-list_price_only').click();
    await expect(page.getByTestId('input-annual_price_usd')).toHaveValue('72000');
    await expect(page.getByTestId('value-footer')).toContainText('Projection, not a measurement.');
    await expectAccessible(page, 'value');
  });
});
