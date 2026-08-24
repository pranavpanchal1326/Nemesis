import { ContractMatrix } from "@/components/ContractMatrix";
import type { DensityMode } from "@/design/generated/tokens";
import { devOnly } from "@/lib/dev-only";
import { loadStrings, SEEDED_LOCALES } from "@/server/strings";

/**
 * The §E26 contracts, across the matrix §E24 asks for.
 *
 * > Storybook for every component across **three densities × two themes × two
 * > scripts**.
 *
 * `tests/contracts.spec.ts` drives all twelve combinations and runs `axe` over
 * each. The matrix content lives in `<ContractMatrix>` so this route, the
 * accessibility sweep and the golden images cannot drift into rendering
 * different things and calling it the same check.
 *
 * Dev-only, per §E24 — a proof surface is not a public URL.
 */
export default async function ContractsProof({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  devOnly();
  const params = await searchParams;
  const one = (key: string): string | undefined => {
    const value = params[key];
    return Array.isArray(value) ? value[0] : value;
  };

  const density = (one("density") ?? "compact") as DensityMode;
  const ground = one("ground") === "light-table" ? "light-table" : "paper";
  const locale = SEEDED_LOCALES.includes(one("locale") ?? "en") ? (one("locale") ?? "en") : "en";
  const strings = await loadStrings("common", locale);

  return (
    // `lang` drives the per-script type scale *and* the screen reader's voice —
    // §E10.1's rule is keyed on `:lang()`, so this attribute is load-bearing
    // rather than metadata.
    <div
      lang={locale}
      data-density={density}
      data-ground={ground}
      data-proof="contracts"
      style={
        ground === "light-table"
          ? { background: "var(--color-mitti-950)", color: "var(--color-bone-200)" }
          : { background: "var(--color-paper-50)", color: "var(--color-riso-black)" }
      }
    >
      <ContractMatrix strings={strings} />
    </div>
  );
}
