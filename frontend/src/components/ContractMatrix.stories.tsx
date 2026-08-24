import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { shippedStrings } from "@/lib/i18n/bundles";
import { ContractMatrix } from "./ContractMatrix";

/**
 * The §E26 contracts, browsable — §E24's design-ops half.
 *
 * The toolbar switches the same three axes `tests/contracts.spec.ts` asserts:
 * three densities × two grounds × two scripts. Both render `<ContractMatrix>`,
 * so the catalogue a reviewer looks at and the gate CI runs cannot show
 * different things.
 */
const meta = {
  title: "Contracts/The §E26 matrix",
  component: ContractMatrix,
  parameters: { layout: "fullscreen" },
  render: (_args, context) => {
    const density = String(context.globals["density"] ?? "compact");
    const ground = String(context.globals["ground"] ?? "paper");
    const locale = String(context.globals["locale"] ?? "en");

    return (
      <div
        lang={locale}
        data-density={density}
        data-ground={ground}
        style={
          ground === "light-table"
            ? { background: "var(--color-mitti-950)", color: "var(--color-bone-200)" }
            : { background: "var(--color-paper-50)", color: "var(--color-riso-black)" }
        }
      >
        <ContractMatrix strings={shippedStrings("common", locale)} />
      </div>
    );
  },
} satisfies Meta<typeof ContractMatrix>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Every component, every enum member, in whichever combination the toolbar
 *  is set to. */
export const Everything: Story = { args: { strings: shippedStrings("common", "en") } };
