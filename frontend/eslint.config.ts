import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import nextPlugin from "@next/eslint-plugin-next";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

/**
 * §E25 Phase 18 — "zero `any` in application source".
 *
 * The four *material* bans of §E24 and ADR-0037 — no colour literal, no GLSL,
 * no CDN asset, no hand-edited generated type — are not here. They live in
 * `scripts/check-guards.ts`, mirroring the backend's `scripts/check_*.py`
 * family, because a grep with an argued allowlist is easier to read, easier to
 * exempt deliberately, and easier to point at in a review than a custom ESLint
 * rule nobody maintains.
 *
 * ESLint's job in this repository is correctness and accessibility. The guards'
 * job is the design law.
 */
export default defineConfig(
  globalIgnores([
    ".next/**",
    "node_modules/**",
    "next-env.d.ts",
    "src/generated/**", // generated; the drift check owns it
    "tests/fixtures/lint/**", // deliberately-failing fixtures, linted in isolation
    "tests/fixtures/types/**", // deliberately-failing compiles, asserted by tests/types.test.ts
    "storybook-static/**", // a build output, like .next
    // Playwright's own output: the HTML report and, on a failure, a trace whose
    // `resources/` directory holds every script the page loaded — including
    // third-party ones. They are gitignored, but ESLint's flat config does not
    // read .gitignore, so `npm run lint` linted them and failed on files it had
    // no tsconfig for. Only visible when a run has left artifacts behind, which
    // is why it survived until F17.
    "test-results/**",
    "playwright-report/**",
  ]),

  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,

  // react-hooks ships its own flat config, which registers the plugin under the
  // name its rules expect. Spreading it is correct; re-registering the plugin
  // by hand is how you end up with two copies under two names.
  reactHooks.configs.flat["recommended-latest"],

  {
    languageOptions: {
      parserOptions: {
        projectService: {
          // postcss.config.mjs sits outside tsconfig's `include`; type-aware
          // linting still covers it, via the default project.
          allowDefaultProject: ["postcss.config.mjs"],
        },
        tsconfigRootDir: import.meta.dirname,
      },
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "@next/next": nextPlugin,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...jsxA11y.flatConfigs.strict.rules,

      // The Phase 18 gate, stated as an error rather than a convention.
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unsafe-assignment": "error",
      "@typescript-eslint/no-unsafe-member-access": "error",
      "@typescript-eslint/no-unsafe-call": "error",
      "@typescript-eslint/no-unsafe-return": "error",
      "@typescript-eslint/no-unsafe-argument": "error",

      // §E26 — a status chip that cannot render every member of the enum is a
      // defect. Exhaustive switches are how that becomes a compile error.
      "@typescript-eslint/switch-exhaustiveness-check": "error",

      "@typescript-eslint/consistent-type-imports": [
        "error",
        { prefer: "type-imports", fixStyle: "inline-type-imports" },
      ],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],

      // §E22 — WCAG 2.2 AA is a floor. A disabled a11y rule needs a written
      // reason at its call site, never a blanket-off here.
      "jsx-a11y/no-autofocus": "warn",
    },
  },

  {
    /**
     * The service worker (§E21, F17).
     *
     * Plain JavaScript in `public/`, so it is served verbatim rather than
     * bundled — a worker that went through the bundler would be fingerprinted,
     * and a fingerprinted worker cannot be registered at a stable path. It is
     * linted rather than ignored, because it is the one file in this repository
     * that can serve a stale application to a field team.
     *
     * Type-aware rules are off for it: it is outside `tsconfig`'s `include`
     * (correctly — it must not be compiled), and the service-worker globals are
     * not in the DOM lib.
     */
    files: ["public/sw.js"],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: {
      globals: { ...globals.serviceworker },
      parserOptions: { project: false, projectService: false },
    },
  },

  {
    // Node-side build scripts: no DOM, and they run under Node's native type
    // stripping, which is why `erasableSyntaxOnly` is on in tsconfig.
    files: ["scripts/**/*.ts", "*.config.ts", "*.config.mjs"],
    languageOptions: { globals: { ...globals.node } },
    rules: {
      "no-console": "off",
    },
  },
);
