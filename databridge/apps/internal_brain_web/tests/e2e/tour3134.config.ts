import { defineConfig, devices } from '@playwright/test';

// TEMPORARY (deleted after the run): tour.spec.ts against a mock server on 3134 (.next-p3134).
export default defineConfig({
  testDir: '.',
  testMatch: process.env.T3134 ? new RegExp(process.env.T3134) : /tour\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  outputDir: '../../test-results-3134',
  use: {
    baseURL: 'http://127.0.0.1:3134',
    ...devices['Desktop Chrome'],
    viewport: { width: 1280, height: 860 },
  },
});
