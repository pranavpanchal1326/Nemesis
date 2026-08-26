import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // The same `@/*` tsconfig path the application uses. A test that resolves
    // imports differently from the build is a test of something else.
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      // `server-only` throws on import unless it is resolved under React's
      // `react-server` condition, which Next applies to server modules and a
      // bare Node test runner does not. Pointing at the package's own
      // `empty.js` is exactly what that condition resolves to — not a stub of
      // ours, and not a weakening of M3's gate.
      //
      // **That gate is a *build* rule and is untouched here.** `server-only`
      // makes importing a server module from a client bundle a `next build`
      // failure; vitest builds no client bundle, so the marker has nothing to
      // assert in this process. Without the alias, a server module simply
      // cannot be unit-tested at all — which is why `src/server/strings.ts`
      // had no test until F18 went looking for one.
      "server-only": fileURLToPath(new URL("./node_modules/server-only/empty.js", import.meta.url)),
    },
  },
  test: {
    include: ["tests/**/*.test.ts", "src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
  },
});
