import type { StorybookConfig } from "@storybook/nextjs-vite";

/**
 * §E24's design-ops half.
 *
 * > **Storybook** for every component across three densities × two themes × two
 * > scripts. … **Design QA ritual.** Every visual PR posts its Storybook diff
 * > and a five-second scene capture.
 *
 * The *verification* half of that clause lives in `tests/contracts.spec.ts`,
 * which drives the same twelve combinations through a real engine and runs
 * `axe` over each. This is the other half: a browsable catalogue for the people
 * doing design QA, and the artefact a visual PR diffs.
 *
 * Both render `<ContractMatrix>`, so the catalogue and the gate cannot show
 * different things and call it the same check.
 */
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  framework: { name: "@storybook/nextjs-vite", options: {} },
  addons: [],
  // §6 Principle #6 — zero-cost, self-hosted, offline-capable. A build step that
  // phones home is a build step that behaves differently on the air-gapped
  // laptop Phase 29 gates on, and it sends this project's shape to a third
  // party for no benefit to it.
  core: { disableTelemetry: true },
  staticDirs: ["../public"],
  typescript: { reactDocgen: "react-docgen-typescript" },
};

export default config;
