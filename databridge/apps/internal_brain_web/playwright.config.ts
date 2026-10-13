import { defineConfig, devices } from '@playwright/test';

// E2E runs against MOCK mode (fixtures served by the app itself; no IB backend needed).
// The mock keeps demo state in one server process, so tests run serially.
const PORT = 3101;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 860 },
  },
  webServer: {
    command: 'npm run build:mock && npm run start:mock',
    url: `http://127.0.0.1:${PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 600_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
