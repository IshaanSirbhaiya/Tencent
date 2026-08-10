import { defineConfig, devices } from "@playwright/test";

// Smoke-tests the 90-second demo flow against BOTH real servers (gateway + frontend) — fully
// airgapped (loopback only). Mirrors the §10 self-eval gate: scripted flow < 2 min, zero errors.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "C:/Python313/python.exe serve.py",
      cwd: "../gateway",
      url: "http://127.0.0.1:8000/health",
      timeout: 120_000,
      reuseExistingServer: true,
    },
    {
      command: "npm run start",
      url: "http://127.0.0.1:3000",
      timeout: 120_000,
      reuseExistingServer: true,
    },
  ],
});
