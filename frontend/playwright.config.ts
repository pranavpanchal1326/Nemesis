import { defineConfig, devices } from "@playwright/test";

/**
 * The browser harness — §E24.
 *
 * Some of this project's gates cannot be asserted anywhere but in a real
 * engine: "text layers are byte-identical with the press on and off"
 * (ADR-0038), "the WebGL2 backend renders the same scene as WebGPU, verified
 * by golden image" (Phase 19), "every fallback tier is exercised in CI by
 * forcing its trigger" (Phase 20). A jsdom test cannot see a pixel.
 *
 * Determinism is the whole point, so: one worker, no retries locally, animations
 * disabled at screenshot time, and a fixed viewport and device scale factor.
 * A golden image that depends on how busy the machine was is not a gate.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: process.env["CI"] === undefined ? 0 : 1,
  reporter: process.env["CI"] === undefined ? "list" : [["list"], ["html", { open: "never" }]],

  use: {
    baseURL: "http://127.0.0.1:3210",
    trace: "retain-on-failure",
    deviceScaleFactor: 1,
    viewport: { width: 1280, height: 800 },
  },

  expect: {
    toHaveScreenshot: {
      // Zero tolerance. Every one of these assertions is about whether a
      // pipeline touched something it was told not to touch, and "nearly
      // identical" is not the claim §E6.2 makes.
      maxDiffPixels: 0,
      animations: "disabled",
    },
  },

  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],

  webServer: {
    command: "npm run dev -- --port 3210",
    url: "http://127.0.0.1:3210/developers",
    reuseExistingServer: process.env["CI"] === undefined,
    timeout: 180_000,
  },
});
