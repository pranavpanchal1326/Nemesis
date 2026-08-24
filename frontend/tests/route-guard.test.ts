import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const APP = join(ROOT, "src", "app");

/**
 * §E24 — a screen that is not wired cannot reach a public URL.
 *
 * > Screens whose contract returns nulls today carry a permanent dev-only badge
 * > and **cannot be routed to a public URL** until the backing phase populates
 * > them. Track E races ahead of the backend without ever lying about it — §6
 * > Principle #8 **enforced by the build, not by discipline**.
 *
 * "Enforced by the build" is the operative phrase, and it is why this reads the
 * route sources rather than standing up a production server: the guard is a
 * property of the code. A test that only checked one deployment would pass on a
 * day somebody forgot it in a different one.
 */

function routes(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...routes(full));
    } else if (entry === "page.tsx" || entry === "route.ts") {
      found.push(full);
    }
  }
  return found;
}

const ALL = routes(APP);

describe("§E24 — proof and unwired screens are 404 in production", () => {
  const proofRoutes = ALL.filter(
    (path) => path.includes(`${join("proof")}\\`) || path.includes("/proof/"),
  );

  it("there are proof routes to check", () => {
    // A test that silently checks nothing is worse than no test: it reports
    // green for the absence of the thing it was written to protect.
    expect(proofRoutes.length).toBeGreaterThan(0);
  });

  it.each(proofRoutes.map((path) => [path.replace(APP, "app"), path] as const))(
    "%s calls devOnly()",
    (_label, path) => {
      const source = readFileSync(path, "utf8");
      expect(source).toContain("devOnly()");
    },
  );

  it("devOnly returns 404 rather than redirecting or rendering a notice", () => {
    // 404 and not 403: a distinguishable "exists but forbidden" tells an
    // unauthenticated caller that the screen is there, which is the same
    // disclosure `deps.py` refuses for tenant ids.
    const guard = readFileSync(join(ROOT, "src", "lib", "dev-only.ts"), "utf8");
    expect(guard).toContain("notFound()");
    expect(guard).toContain('process.env.NODE_ENV === "production"');
  });
});

describe("ADR-0040 — the browser never names its own tenant", () => {
  it("no route handler or page reads the tenant from the client", () => {
    /**
     * §E14.1: *"A browser client that names its own tenant would ship a trust
     * boundary that is not one."* `src/server/upstream.ts` is the only module
     * that touches the header, and `import "server-only"` makes pulling it into
     * a client bundle a build error — but a route handler could still read a
     * tenant off a query string and forward it, which would defeat the seam
     * without touching that module at all.
     */
    for (const path of ALL) {
      const source = readFileSync(path, "utf8");
      expect(source, `${path} sets the tenant header itself`).not.toMatch(/["']X-Tenant-ID["']/i);
    }
  });

  it("only the server module knows the upstream URL", () => {
    const holders = ALL.filter((path) => readFileSync(path, "utf8").includes("NEMESIS_API_URL"));
    expect(holders, "a route handler resolved the upstream itself").toEqual([]);
  });
});
