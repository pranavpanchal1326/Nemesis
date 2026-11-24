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

  /**
   * Snapshot assertions run on Linux only — the rule `golden.spec.ts` and
   * `nem web-golden` both already state, now enforced instead of trusted.
   *
   * Both documents say baselines are captured on Linux, in the container image
   * CI runs, and *never* on the host, because "a gate that can only pass on one
   * machine is a gate somebody turns off". Nothing enforced it, so 53
   * `-chromium-win32` baselines were committed from host runs.
   *
   * Deleting them is not enough on its own. Playwright's default mode for an
   * absent baseline is `missing`, which **writes** the screenshot and fails that
   * test — so the next `npm run test:e2e` on a Windows machine would silently
   * recreate all 53, as valid PNGs that no integrity check would flag. The files
   * were a symptom; this flag is the cause.
   *
   * `ignoreSnapshots` skips only the snapshot expectations. Every other
   * assertion in those specs — axe sweeps, the press text-exemption, the
   * stamp-from-the-ledger checks — still runs on a developer's machine, which
   * is where their value is. The image comparison waits for Linux.
   */
  ignoreSnapshots: process.platform !== "linux",

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
