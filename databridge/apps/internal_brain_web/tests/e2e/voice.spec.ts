import { expect, test } from '@playwright/test';
import { expectAccessible, login, quietTourOffer, resetDemo } from './helpers';

// Voice dictation in the Ask composer (mock mode). A fake SpeechRecognition is injected; no real audio.
const FAKE = () => {
  const w = window as unknown as Record<string, unknown>;
  class FakeRecognition {
    lang = '';
    continuous = false;
    interimResults = false;
    onresult: ((e: unknown) => void) | null = null;
    onerror: ((e: unknown) => void) | null = null;
    onend: (() => void) | null = null;
    start() {
      w.__rec = this;
      w.__recLang = this.lang;
      // Keep the handler as it was at start, so a test can deliver a LATE result even after the hook has
      // detached its handlers (a real browser can still fire one after stop()).
      w.__late = this.onresult;
    }
    stop() {
      w.__stopped = true;
      this.onend?.();
    }
    abort() {
      this.stop();
    }
  }
  w.SpeechRecognition = FakeRecognition;
  w.__emit = (text: string) => {
    const r = w.__rec as FakeRecognition;
    r.onresult?.({ resultIndex: 0, results: { length: 1, 0: { length: 1, 0: { transcript: text } } } });
  };
};

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await resetDemo(page);
  await page.close();
});

test('dictation puts text in the box, never auto-asks, Esc stops, axe clean', async ({ page }) => {
  await quietTourOffer(page);
  await page.addInitScript(FAKE);
  let asked = 0;
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/ask|\/query/.test(r.url())) asked++;
  });
  await login(page, 'jdoe');
  const mic = page.getByTestId('dictate').first();
  await expect(mic).toBeVisible();
  await expect(mic).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByText('Dictation uses your browser’s speech service. Review the text before asking.').first()).toBeVisible();
  const box = await mic.boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(24);
  expect(box!.height).toBeGreaterThanOrEqual(24);

  await mic.click();
  await expect(mic).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('dictation-status').first()).toHaveText('Listening…');
  expect(await page.evaluate(() => (window as unknown as { __recLang: string }).__recLang)).toBe('en-SG');
  await page.evaluate(() => (window as unknown as { __emit: (t: string) => void }).__emit('what is the status of the migration'));
  await expect(page.locator('#question')).toHaveValue('what is the status of the migration');
  await expectAccessible(page, 'ask composer while dictating');

  // Esc stops, the text stays, and nothing was sent.
  await page.locator('#question').focus();
  await page.keyboard.press('Escape');
  await expect(mic).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('dictation-status').first()).toHaveText('Stopped');
  await expect(page.locator('#question')).toHaveValue('what is the status of the migration');
  await expect(page.getByTestId('answer')).toHaveCount(0);
  expect(asked).toBe(0);
});

test('sending ends dictation, and a late result never refills the cleared box', async ({ page }) => {
  await quietTourOffer(page);
  await page.addInitScript(FAKE);
  await login(page, 'jdoe');
  // First question typed, so the sticky follow-up composer (the one that stays mounted) is on screen.
  await page.locator('#question').first().fill('what is the status of the migration');
  await page.locator('#question').first().press('Enter');
  await expect(page.getByTestId('answer').first()).toBeVisible();

  const mic = page.getByTestId('dictate').last();
  const q = page.locator('#question').last();
  await mic.click();
  await expect(mic).toHaveAttribute('aria-pressed', 'true');
  await page.evaluate(() => (window as unknown as { __emit: (t: string) => void }).__emit('were there any blockers'));
  await expect(q).toHaveValue('were there any blockers');
  await q.focus();
  await page.keyboard.press('Enter');
  await expect(q).toHaveValue('');
  await expect(mic).toHaveAttribute('aria-pressed', 'false');
  expect(await page.evaluate(() => (window as unknown as { __stopped?: boolean }).__stopped)).toBe(true);

  // A late recognition event through the handler captured at start: dropped, the box stays empty.
  await page.evaluate(() => {
    const w = window as unknown as { __late?: (e: unknown) => void };
    w.__late?.({ resultIndex: 0, results: { length: 1, 0: { length: 1, 0: { transcript: 'were there any blockers and more' } } } });
  });
  await expect(q).toHaveValue('');
});

test('typing while dictating ends dictation, so the next result cannot overwrite the edit', async ({ page }) => {
  await quietTourOffer(page);
  await page.addInitScript(FAKE);
  await login(page, 'jdoe');
  const mic = page.getByTestId('dictate').first();
  const q = page.locator('#question').first();
  await mic.click();
  await page.evaluate(() => (window as unknown as { __emit: (t: string) => void }).__emit('status of'));
  await expect(q).toHaveValue('status of');
  await q.focus();
  await page.keyboard.type(' the migration');
  await expect(mic).toHaveAttribute('aria-pressed', 'false');
  await page.evaluate(() => {
    const w = window as unknown as { __late?: (e: unknown) => void };
    w.__late?.({ resultIndex: 0, results: { length: 1, 0: { length: 1, 0: { transcript: 'status of something else' } } } });
  });
  await expect(q).toHaveValue('status of the migration');
});

test('the dictation toggle has one fixed name, and Space toggles it exactly once', async ({ page }) => {
  await quietTourOffer(page);
  await page.addInitScript(FAKE);
  await login(page, 'jdoe');
  const mic = page.getByTestId('dictate').first();
  // A toggle button: one name, the state in aria-pressed (never "Stop dictating, pressed").
  await expect(mic).toHaveAttribute('aria-label', 'Dictate');
  await mic.focus();
  await page.keyboard.press(' ');
  await expect(mic).toHaveAttribute('aria-pressed', 'true');
  await expect(mic).toHaveAttribute('aria-label', 'Dictate');
  await page.keyboard.press(' ');
  await expect(mic).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('dictation-status').first()).toHaveText('Stopped');
});

test('the microphone button is hidden when the API is absent', async ({ page }) => {
  await quietTourOffer(page);
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    delete w.SpeechRecognition;
    delete w.webkitSpeechRecognition;
  });
  await login(page, 'jdoe');
  await expect(page.locator('#question')).toBeVisible();
  await expect(page.getByTestId('dictate')).toHaveCount(0);
  await expect(page.getByText('Dictation uses your browser')).toHaveCount(0);
  await expectAccessible(page, 'ask composer without dictation');
});
