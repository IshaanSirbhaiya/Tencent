import { defineConfig, devices } from '@playwright/test';

// Runs ONLY tests/e2e/act*.spec.ts against a dev server you started yourself (mock mode):
//   NEXT_PUBLIC_IB_MOCK=1 NEXT_DIST_DIR=.next-act npx next dev -H 127.0.0.1 -p 3124
//   npx playwright test -c tests/e2e/act.config.ts
// No webServer block: it never builds or starts the app (the main config uses 3101).
const PORT = Number(process.env.ACT_PORT ?? 3124);

export default defineConfig({
  testDir: '.',
  testMatch: /act.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 860 },
  },
});
