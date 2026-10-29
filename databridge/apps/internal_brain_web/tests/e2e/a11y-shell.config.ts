import { defineConfig, devices } from '@playwright/test';

// Runs ONLY tests/e2e/a11y-shell*.spec.ts against a dev server you started yourself (mock mode):
//   NEXT_PUBLIC_IB_MOCK=1 NEXT_DIST_DIR=.next-a11y npx next dev -H 127.0.0.1 -p 3123
//   npx playwright test -c tests/e2e/a11y-shell.config.ts
// No webServer block: it never builds or starts the app (the main config uses 3101).
const PORT = Number(process.env.A11Y_PORT ?? 3123);

export default defineConfig({
  testDir: '.',
  testMatch: /a11y-shell.*\.spec\.ts$/,
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
