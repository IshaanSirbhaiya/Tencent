import { defineConfig, devices } from '@playwright/test';

// Runs ONLY tests/e2e/views.spec.ts against a dev server you started yourself (mock mode):
//   NEXT_PUBLIC_IB_MOCK=1 npx next dev -H 127.0.0.1 -p 3100      (or: npm run dev:mock)
//   npx playwright test -c tests/e2e/views.config.ts
// No webServer block: it never builds or starts the app. Headless only.
const PORT = Number(process.env.VIEWS_PORT ?? 3100);

export default defineConfig({
  testDir: '.',
  testMatch: /views.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices['Desktop Chrome'],
    headless: true,
    viewport: { width: 1280, height: 860 },
  },
});
