import { defineConfig, devices } from '@playwright/test';

// Runs ONLY tests/e2e/deliverables*.spec.ts against a dev server you started yourself (mock mode):
//   NEXT_PUBLIC_IB_MOCK=1 NEXT_DIST_DIR=.next-deliv npx next dev -H 127.0.0.1 -p 3128
//   npx playwright test -c tests/e2e/deliverables.config.ts
// No webServer block: it never builds or starts the app. Headless only.
const PORT = Number(process.env.DELIV_PORT ?? 3128);

export default defineConfig({
  testDir: '.',
  testMatch: /deliverables.*\.spec\.ts$/,
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
    acceptDownloads: true,
  },
});
