import { devOnly } from "@/lib/dev-only";
import { SEEDED_LOCALES } from "@/server/strings";
import { PublicProof } from "@/public/PublicProof";

/**
 * §E18's states, rendered for the M6 gate.
 *
 * `tests/public.spec.ts` drives this route and asserts the rule the milestone
 * turns on: **a suppressed figure never renders as a zero**. The cases live in
 * `<PublicProof>` rather than here so the accessibility sweep, the gate and any
 * later golden image cannot drift into proving different things while claiming
 * the same check — the same split `<ContractMatrix>` uses.
 *
 * Dev-only, per §E24: a proof surface is not a public URL, and `devOnly()`
 * makes that a 404 in a production build rather than a convention.
 */
export default async function PublicProofPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  devOnly();
  const params = await searchParams;
  const raw = params["locale"];
  const requested = Array.isArray(raw) ? raw[0] : raw;
  const locale = SEEDED_LOCALES.includes(requested ?? "en") ? (requested ?? "en") : "en";

  return <PublicProof locale={locale} />;
}
