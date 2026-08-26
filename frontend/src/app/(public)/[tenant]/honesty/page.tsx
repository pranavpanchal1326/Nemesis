import type { Metadata } from "next";
import Link from "next/link";

import { notTranslatable, t } from "@/lib/i18n/strings";
import { HonestyTable } from "@/public/HonestyTable";
import { PublicShell } from "@/public/PublicShell";
import { cityNameFallback } from "@/server/public-data";
import { publicLocale } from "../locale";

/**
 * §44 and §E28 on a public URL — §E16.2, §E18.
 *
 * > Act 9 renders §44 on the marketing surface. **Every competitor
 * > overclaims**; §6 Principle #8 says this is a competitive advantage rather
 * > than a limitation, and Act 9 is where that belief is actually tested in
 * > public.
 *
 * This is that test, and it is on the transparency surface rather than the
 * marketing one on purpose. A limitations table on a landing page is marketing
 * copy about candour; the same table one click from a ward's figures is a thing
 * a journalist checks the figures against.
 *
 * **It takes no tenant data and makes no upstream call.** The rows describe the
 * platform, not the city, so the page renders identically for every tenant and
 * needs nothing from the API — which also means it is the one page here that
 * cannot be wrong because a backend was down.
 */
type Params = Promise<{ tenant: string }>;
type Search = Promise<Record<string, string | string[] | undefined>>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { tenant } = await params;
  return {
    title: "What is real, what is not",
    description:
      "Every product claim in this system, with its status label, published rather than kept internally.",
    alternates: { canonical: `/${tenant}/honesty` },
  };
}

export default async function Honesty({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}) {
  const { tenant } = await params;
  const { locale, locales, strings } = await publicLocale(tenant, await searchParams);

  return (
    <PublicShell
      city={cityNameFallback(tenant)}
      citySlug={tenant}
      strings={strings}
      locale={locale}
      locales={locales}
    >
      <h1 className="type-display-1">{t(strings, "honesty.title")}</h1>
      <HonestyTable strings={strings} />
      <p className="type-caption">
        {/*
         * §E18 asks for this table "as data, not as prose", and the page is
         * prose-shaped however carefully it is built. The JSON is the same
         * generated rows, so a researcher diffs releases rather than reading
         * two HTML pages side by side.
         *
         * A plain `<a>` and not `<Link>`: the target is a JSON document, not a
         * route in this application, and asking the client router to prefetch
         * it would be asking it to prefetch a download.
         */}
        <a href={`/${tenant}/honesty.json`} download={`nemesis-honesty-${tenant}.json`}>
          {t(strings, "honesty.download")}
        </a>
        {" · "}
        <Link href={`/${tenant}`}>{notTranslatable(cityNameFallback(tenant))}</Link>
      </p>
    </PublicShell>
  );
}
