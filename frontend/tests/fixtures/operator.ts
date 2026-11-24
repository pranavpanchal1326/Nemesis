import type { Page } from "@playwright/test";

/**
 * Sign a browser context in as an operator — ADR-0062.
 *
 * Every console spec needs this now, because `(console)/layout.tsx` redirects
 * an unauthenticated request to `/sign-in` before any screen renders.
 *
 * **Through the real endpoint, not by minting a cookie.** A helper that signed
 * its own token with the HMAC key would pass while `/api/session` was broken,
 * and the thing these specs exist to prove is that an officer can get in. This
 * posts the passphrase exactly as the form does, so the sign-in path is
 * exercised once per spec file as a side effect of the suite running at all.
 *
 * `page.request` shares the context's cookie jar, so the `Set-Cookie` the
 * handler returns is the cookie the subsequent `page.goto` sends. No
 * `storageState` file, nothing to regenerate when the signing key rotates.
 */

/**
 * The local development passphrase from `frontend/.env.example`.
 *
 * A literal in a test file, deliberately. The dev server reads `.env.local`;
 * this Playwright process does not, and threading one variable through
 * `playwright.config.ts` into every spec would be more machinery than the fact
 * deserves. It is published in `.env.example`, it is marked
 * `change-me`, and `NEMESIS_CONSOLE_PASSPHRASE` overrides it when a developer
 * has set their own — which is what CI will do if it ever needs to.
 *
 * If this value stops matching `.env.example`, every console spec fails at
 * sign-in with a clear 401 rather than mysteriously later.
 */
const LOCAL_DEV_PASSPHRASE = "dev-only-insecure-console-passphrase-change-me";

export async function signInAsOperator(page: Page): Promise<void> {
  const passphrase = process.env["NEMESIS_CONSOLE_PASSPHRASE"] ?? LOCAL_DEV_PASSPHRASE;

  const response = await page.request.post("/api/session", { data: { passphrase } });

  if (!response.ok()) {
    // Named rather than left as a bare assertion failure in the first console
    // test that happens to run: a 503 here means the deployment under test has
    // no passphrase configured, which is a different problem from a wrong one.
    throw new Error(
      `sign-in failed with ${String(response.status())}. ` +
        "A 401 means the passphrase does not match this deployment's " +
        "NEMESIS_CONSOLE_PASSPHRASE; a 503 means it has none set. See ADR-0062.",
    );
  }
}
