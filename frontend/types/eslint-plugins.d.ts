/**
 * Ambient types for the two ESLint plugins that ship no declarations.
 *
 * This is not a hand-written contract in the §E24 sense — it describes an npm
 * package's shape, not a backend response. Without it the flat config cannot be
 * type-checked, and an untyped config is where `any` re-enters a codebase whose
 * Phase 18 gate is "zero `any` in application source".
 */

declare module "@next/eslint-plugin-next" {
  import type { ESLint, Linter } from "eslint";
  const plugin: ESLint.Plugin & {
    readonly configs: Record<"recommended" | "core-web-vitals", { rules: Linter.RulesRecord }>;
  };
  export default plugin;
}

declare module "eslint-plugin-jsx-a11y" {
  import type { ESLint, Linter } from "eslint";
  const plugin: ESLint.Plugin & {
    readonly flatConfigs: Record<"recommended" | "strict", { rules: Linter.RulesRecord }>;
  };
  export default plugin;
}
