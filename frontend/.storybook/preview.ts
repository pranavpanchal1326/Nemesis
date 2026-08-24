import type { Preview } from "@storybook/nextjs-vite";

import "../src/app/globals.css";

/**
 * The matrix, as Storybook globals — §E24.
 *
 * Three densities × two grounds × two scripts, switchable from the toolbar, so
 * a reviewer can walk the same twelve combinations `tests/contracts.spec.ts`
 * asserts. `lang` is set on the decorator's element rather than on the
 * documentElement because the per-script type scale keys on `:lang()`, which is
 * the same attribute a screen reader uses to pick a voice (§E10.1).
 */
const preview: Preview = {
  globalTypes: {
    density: {
      description: "§E19 — three density modes, persisted per user",
      defaultValue: "compact",
      toolbar: {
        title: "Density",
        items: ["comfortable", "compact", "dense"],
      },
    },
    ground: {
      description: "§E9.3 — paper, or the light table",
      defaultValue: "paper",
      toolbar: { title: "Ground", items: ["paper", "light-table"] },
    },
    locale: {
      description: "§E10.1 — Devanagari is a design partner, not a fallback",
      defaultValue: "en",
      toolbar: { title: "Script", items: ["en", "mr"] },
    },
  },
  parameters: {
    layout: "fullscreen",
    controls: { expanded: true },
  },
};

export default preview;
