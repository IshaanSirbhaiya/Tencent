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
