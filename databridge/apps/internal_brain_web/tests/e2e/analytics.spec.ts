import { type Page, expect, test } from '@playwright/test';
import { expectAccessible } from './helpers';

// Mission control, the request journey (both lenses) and end-to-end analytics, in mock mode.
// Sign-in goes through the demo-login API (the same signed cookie the persona picker sets), so these
// specs do not depend on the login screen's markup.

async function signIn(page: Page, persona: string) {
  const r = await page.request.post('/api/ib/auth/demo-login', { data: { persona } });
  expect(r.ok()).toBeTruthy();
  await page.addInitScript(() => {
    try {
      localStorage.setItem('ib-mode', 'dev');
      localStorage.setItem('ib-tour-done', '1');
    } catch {
      /* storage blocked */
    }
  });
}

const ALLOW = new Set(['verified', 'drift_material']);

/** As compliance: a released request of `sub` with at least one Gate-2 denial, and its journey. */
async function requestWithDenial(page: Page, sub: string) {
  const ev = (await (await page.request.get('/api/ib/audit/events?kind=request.accepted&limit=500')).json()).events as { req_id: string; sub: string }[];
  for (const e of ev.filter((x) => x.sub === sub)) {
    const j = await (await page.request.get(`/api/ib/audit/journey/${e.req_id}`)).json();
    if (j.status === 'released' && j.gate2.some((g: { verdict: string }) => !ALLOW.has(g.verdict))) return j as { req_id: string; gate2: { reason_code: string; verdict: string }[]; events: { seq: number }[] };
  }
  throw new Error(`no released request of ${sub} with a Gate-2 denial in the mock ledger`);
}

async function noHorizontalOverflow(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, 'horizontal overflow in px').toBeLessThanOrEqual(1);
}

test.describe('mission control', () => {
  test('compliance: KPIs, the ten-step pipeline with live p50, freshness and model status', async ({ page }) => {
    await signIn(page, 'maya.compliance');
    await page.goto('/dev');
    await expect(page.getByRole('heading', { level: 1, name: 'Mission control' })).toBeVisible();
    await expect(page.getByTestId('kpi-requests')).not.toContainText('—');
    await expect(page.getByTestId('kpi-ledger')).toContainText('seq');
    await expect(page.getByTestId('pipeline-diagram').getByRole('button')).toHaveCount(10);
    await expect(page.getByTestId('pipeline-node-gate2')).toContainText(/p50 [\d,]+ ms/);
    await page.getByTestId('pipeline-node-model').click();
    await expect(page.getByTestId('pipeline-node-model')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('pipeline-detail')).toContainText('Model chain');
    await expect(page.getByTestId('pipeline-detail')).toContainText('Guarantee');
    // keyboard: every step is a real button
    await page.getByTestId('pipeline-node-relevance').focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('pipeline-node-relevance')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('pipeline-detail')).toContainText('Relevance gate');
    await expect(page.getByTestId('freshness').locator('li')).toHaveCount(4);
    await expect(page.getByTestId('model-status')).toContainText('Extractive (no model)');
    await expect(page.getByTestId('org-numbers-lock')).toHaveCount(0);
  });

  test('employee: the pipeline and own requests, org numbers locked', async ({ page }) => {
    await signIn(page, 'jdoe');
    await page.goto('/dev');
    await expect(page.getByTestId('org-numbers-lock')).toContainText('each person sees what their role needs.');
    await expect(page.getByTestId('mission-kpis')).toHaveCount(0);
    await expect(page.getByTestId('pipeline-diagram').getByRole('button')).toHaveCount(10);
    // no live timings for a non-compliance viewer: the static description instead
    await expect(page.getByTestId('pipeline-node-gate2')).not.toContainText('p50');
    await expect(page.getByTestId('mission-recent')).toBeVisible();
  });
});

test.describe('request journey', () => {
  test('compliance: waterfall, verdicts with reason codes and the sealed chain', async ({ page }) => {
    await signIn(page, 'maya.compliance');
    const j = await requestWithDenial(page, 'jdoe');
    await page.goto(`/dev/journey/${j.req_id}`);
    await expect(page.getByTestId('journey-compliance')).toBeVisible({ timeout: 2_000 + 8_000 });
    const wf = page.getByTestId('waterfall');
    await expect(wf.locator('li[data-stage]')).toHaveCount(6);
    await expect(page.getByTestId('ledger-chain').locator('li')).toHaveCount(j.events.length);
    await expect(page.getByTestId('ledger-chain')).toContainText(`#${j.events[0].seq}`);
    const denied = j.gate2.find((g) => !ALLOW.has(g.verdict))!;
    await expect(page.getByTestId('journey-verdicts')).toContainText(denied.reason_code);
    // the table twin of the waterfall
    await page.getByTestId('journey-waterfall').getByTestId('view-as-table').click();
    await expect(page.getByTestId('journey-waterfall').getByRole('table')).toContainText('retrieval.decided');
  });

  test('employee: own request shows the receipt only: no reason codes, no seq, no denials', async ({ page, browser }) => {
    // find one of jdoe's requests that HAD a Gate-2 denial, using a compliance session
    const maya = await browser.newPage();
    await signIn(maya, 'maya.compliance');
    const j = await requestWithDenial(maya, 'jdoe');
    await maya.close();

    await signIn(page, 'jdoe');
    await page.goto(`/dev/journey/${j.req_id}`);
    const receipt = page.getByTestId('journey-receipt');
    await expect(receipt).toBeVisible();
    await expect(page.getByTestId('receipt-stepper').locator('li')).toHaveCount(6);
    await expect(page.getByTestId('receipt-chain')).toContainText('Record 1');
    await expect(page.getByTestId('journey-compliance')).toHaveCount(0);
    await expect(page.getByTestId('waterfall')).toHaveCount(0);
    const text = await page.locator('main').innerText();
    for (const g of j.gate2.filter((x) => !ALLOW.has(x.verdict))) expect(text).not.toContain(g.reason_code);
    for (const word of ['forbidden', 'Denied', 'denied', 'Reason code', 'Gate-2 verdict', 'considered', 'hidden']) expect(text).not.toContain(word);
    for (const e of j.events) expect(text).not.toContain(`#${e.seq}`);
    expect(text).not.toMatch(/#\d{2,}/);
  });

  test('employee: someone else’s request and an unknown id look the same', async ({ page, browser }) => {
    const maya = await browser.newPage();
    await signIn(maya, 'maya.compliance');
    const other = await requestWithDenial(maya, 'ken.ext').catch(async () => requestWithDenial(maya, 'priya.sre'));
    await maya.close();
    await signIn(page, 'jdoe');
    await page.goto(`/dev/journey/${other.req_id}`);
    await expect(page.getByText('Request not found')).toBeVisible();
    const a = await page.locator('main').innerText();
    await page.goto('/dev/journey/req_doesnotexist000');
    await expect(page.getByText('Request not found')).toBeVisible();
    const b = await page.locator('main').innerText();
    expect(a).toContain('Request not found');
    expect(a).toBe(b);
  });

  test('journey list: compliance sees everyone, an employee sees their own', async ({ page }) => {
    await signIn(page, 'jdoe');
    await page.goto('/dev/journey');
    await expect(page.getByRole('heading', { name: 'Your recent requests' })).toBeVisible();
    await expect(page.getByTestId('recent-requests')).toBeVisible();
    await expect(page.getByTestId('recent-requests')).not.toContainText('Asked by');
    await page.getByTestId('recent-requests').locator('tr[data-row]').first().click();
    await expect(page.getByTestId('journey-receipt')).toBeVisible();
  });
});

test.describe('analytics', () => {
  test('non-compliance gets the lock card', async ({ page }) => {
    await signIn(page, 'jdoe');
    await page.goto('/dev/analytics');
    await expect(page.getByTestId('role-lock')).toContainText('compliance role only');
    await expect(page.getByTestId('analytics-body')).toHaveCount(0);
  });

  test('compliance: flows in separate units, every chart has a table, window switch, replay label', async ({ page }) => {
    await signIn(page, 'maya.compliance');
    await page.goto('/dev/analytics');
    await expect(page.getByTestId('analytics-body')).toBeVisible();
    await expect(page.getByTestId('replay-notice')).toContainText('seeded demo history');
    await expect(page.getByTestId('request-sankey')).toContainText('Answered');
    await expect(page.getByTestId('doc-funnel')).toContainText('Cited in an answer');
    await expect(page.getByTestId('eval-panel')).toContainText('/ 7,374');
    for (const id of ['chart-daily', 'chart-sankey', 'chart-doc-funnel', 'chart-reasons', 'chart-latency', 'chart-models', 'chart-channels']) {
      const card = page.getByTestId(id);
      await card.getByTestId('view-as-table').click();
      await expect(card.getByRole('table')).toBeVisible();
    }
    await page.getByTestId('window-control').getByRole('radio', { name: '7 days' }).click();
    await expect(page.getByTestId('analytics-kpis')).toContainText('7 d');
    await page.getByTestId('replay-control').getByRole('radio', { name: 'Live only' }).click();
    await expect(page.getByTestId('replay-notice')).toContainText('excluded');
  });
});

for (const theme of ['dark', 'light'] as const) {
  for (const vp of [
    { name: '1280', width: 1280, height: 900 },
    { name: '390', width: 390, height: 844 },
  ]) {
    test(`axe and reflow @ ${vp.name} ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.addInitScript((t) => {
        try {
          localStorage.setItem('ib-theme', t);
        } catch {
          /* storage blocked */
        }
      }, theme);
      await signIn(page, 'maya.compliance');
      const j = await requestWithDenial(page, 'jdoe');
      for (const [url, ready] of [
        ['/dev', 'mission-pipeline'],
        ['/dev/journey', 'recent-requests'],
        [`/dev/journey/${j.req_id}`, 'journey-compliance'],
        ['/dev/analytics', 'analytics-body'],
        ['/value', 'value-calculator'],
      ] as const) {
        await page.goto(url);
        await expect(page.getByTestId(ready)).toBeVisible();
        await expectAccessible(page, `${url} ${vp.name} ${theme}`);
        if (vp.width === 390) await noHorizontalOverflow(page);
      }
    });
  }
}

test('axe: employee receipt and analytics lock', async ({ page, browser }) => {
  const maya = await browser.newPage();
  await signIn(maya, 'maya.compliance');
  const j = await requestWithDenial(maya, 'jdoe');
  await maya.close();
  await signIn(page, 'jdoe');
  await page.goto(`/dev/journey/${j.req_id}`);
  await expect(page.getByTestId('journey-receipt')).toBeVisible();
  await expectAccessible(page, 'receipt');
  await page.goto('/dev/analytics');
  await expect(page.getByTestId('role-lock')).toBeVisible();
  await expectAccessible(page, 'analytics lock');
  await page.goto('/dev');
  await expect(page.getByTestId('org-numbers-lock')).toBeVisible();
  await expectAccessible(page, 'mission employee');
});
