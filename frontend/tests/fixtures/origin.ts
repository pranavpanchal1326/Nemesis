import { test as base, expect, type Page, type Request } from "@playwright/test";

/**
 * The off-origin assertion, generalised — A13, §6 Principle #6, §E24.
 *
 * §6 Principle #6 is *zero-cost, self-hosted, offline-capable*, and Phase 29
 * gates on a clean checkout booting air-gapped. Until F1 that claim was checked
 * two ways, and neither was the claim:
 *
 * * `tests/type.spec.ts` fails when a **font** is fetched from a third party —
 *   one resource type, on one route.
 * * `scripts/check-guards.ts` greps `src/` for CDN hosts — which sees a URL
 *   written in a source file and cannot see one assembled at runtime, injected
 *   by a dependency, or requested by a stylesheet the grep never opened.
 *
 * A13's complaint is the gap between them: *"a runtime fetch the grep cannot
 * see would pass."* This closes it by asserting on what the **engine actually
 * requested** — every resource type, on every route — which is the only place
 * the question can be answered honestly.
 *
 * Import `test` from here instead of from `@playwright/test` and the assertion
 * is automatic: any test that loads a page in a spec using this fixture fails if
 * that page reached off the origin. A spec that deliberately seeds a violation
 * uses `collectOffOrigin` against the plain `test` instead, so that the seeded
 * failure is the thing being asserted rather than the thing failing the run.
 */

/** The dev server `playwright.config.ts` starts. */
export const ORIGIN = "http://127.0.0.1:3210";

/**
 * Schemes that never leave the machine.
 *
 * `data:` and `blob:` are the press's own output — the paper texture and the
 * captured photograph — and `about:` is the blank page a context starts on.
 * None of them is a network request, which is what this fixture is about.
 */
const LOCAL_SCHEMES = new Set(["data:", "blob:", "about:", "file:"]);

/** Hostnames that are this machine under a different name. */
const LOCAL_HOSTS = new Set(["127.0.0.1:3210", "localhost:3210"]);

export function isOffOrigin(rawUrl: string): boolean {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    // An unparseable URL is not a request that left the origin, and throwing
    // here would turn a malformed href into a failure of a different gate.
    return false;
  }
  if (LOCAL_SCHEMES.has(url.protocol)) return false;
  return !LOCAL_HOSTS.has(url.host);
}

function describe(request: Request): string {
  return `${request.resourceType()} ${request.url()}`;
}

/**
 * Attach a collector to a page and return the live list it fills.
 *
 * Exported for the seeded-violation test, which has to observe the collector
 * catching something rather than have the run fail because it did.
 */
export function collectOffOrigin(page: Page): string[] {
  const collected: string[] = [];
  page.on("request", (request) => {
    if (isOffOrigin(request.url())) collected.push(describe(request));
  });
  return collected;
}

/**
 * `test` with the assertion attached to every case in the file.
 *
 * Auto-scoped: a test does not have to name the fixture to be covered by it,
 * which is the point — an assertion you have to remember to add is an assertion
 * that is missing from the route added next month.
 */
export const test = base.extend<{ offOrigin: string[] }>({
  offOrigin: [
    async ({ page }, use, testInfo) => {
      const collected = collectOffOrigin(page);
      await use(collected);

      // A test that has already failed has its own reason; adding a second one
      // buries it. Only assert on a case that otherwise passed.
      if (testInfo.status === testInfo.expectedStatus) {
        expect(
          collected,
          "a request left the origin — §6 Principle #6, and the reason Phase 29's " +
            "air-gapped bootstrap would fail on the demo laptop:\n" +
            collected.join("\n"),
        ).toEqual([]);
      }
    },
    { auto: true },
  ],
});

export { expect } from "@playwright/test";
