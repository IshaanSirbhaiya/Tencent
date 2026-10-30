import { defineConfig, devices } from '@playwright/test';

// Runs ONLY tests/e2e/grow*.spec.ts against a dev server you started yourself (mock mode):
//   NEXT_PUBLIC_IB_MOCK=1 NEXT_DIST_DIR=.next-grow npx next dev -H 127.0.0.1 -p 3127
//   npx playwright test -c tests/e2e/grow.config.ts
// No webServer block: it never builds or starts the app (the main config uses 3101).
const PORT = Number(process.env.GROW_PORT ?? 3127);

export default defineConfig({
  testDir: '.',
  testMatch: /grow.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 150_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices['Desktop Chrome'],
    headless: true,
    viewport: { width: 1280, height: 860 },
  },
});
