import type { Metadata } from "next";
import Link from "next/link";

import { notTranslatable, t } from "@/lib/i18n/strings";
import { formatAmount } from "@/public/figures";
import { Figure } from "@/public/Figure";
import { PublicShell } from "@/public/PublicShell";
import { cityName, fetchBudget } from "@/server/public-data";
import { publicLocale } from "../../locale";

/**
 * §17.6's ward budget — §E18, §16.2.
 *
 * **Nothing on this page is suppressed, and the reason is worth stating on the
 * page rather than only in the code.** `public/aggregates.py`: *"A budget
 * allocation is a published public-finance figure about a municipality, not an
 * observation about any citizen … withholding a line because only one scheme
 * funded a ward would hide precisely the thing an RTI applicant is looking
 * for."* So the k-anonymity floor that governs every other figure on this
 * surface deliberately does not reach here, and the footnote says so — because
 * a reader who has learned that figures get withheld will otherwise assume
 * these were too.
 *
 * **The amounts are strings and stay strings.** They are `NUMERIC` upstream for
 * §17.2's rate-card arithmetic, and this page is the one place a reader holds
 * the figure against a printed municipal document. `Number()` is never called
 * on them, here or in `readBudget` — a sub-rupee float ghost in a citation is a
 * defect with no upper bound on how embarrassing it gets.
 *
 * **The financial year is in the URL.** It is a required query parameter
 * upstream and there is no way to default it without inventing a tenant's
 * calendar, so it is a search param with a stated default rather than a guess
 * the page makes silently.
 */
type Params = Promise<{ tenant: string; zoneCode: string }>;
type Search = Promise<Record<string, string | string[] | undefined>>;

/**
 * The Indian financial year runs April to March, which is what the tenants this
 * product is built for use. Stated as a derivation rather than a constant so a
 * demo in December does not ask for a year that has not started.
 */
function currentFiscalYear(now: Date = new Date()): string {
  const start = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
  return `${String(start)}-${String((start + 1) % 100).padStart(2, "0")}`;
}

function requestedYear(search: Record<string, string | string[] | undefined>): string {
  const raw = search["year"];
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value === undefined || value === "" ? currentFiscalYear() : value;
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { tenant, zoneCode } = await params;
  const city = cityName(tenant);
  return {
    title: `${zoneCode} · ${city}`,
    description: `Budget allocation and spend recorded against ${zoneCode} by ${city}.`,
    alternates: { canonical: `/${tenant}/budget/${zoneCode}` },
  };
}

export default async function Budget({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}) {
  const { tenant, zoneCode } = await params;
  const search = await searchParams;
  const { locale, strings } = await publicLocale(search);
  const city = cityName(tenant);
  const year = requestedYear(search);
  const read = await fetchBudget(tenant, zoneCode, year);

  if (!read.ok) {
    return (
      <PublicShell city={city} citySlug={tenant} strings={strings} locale={locale}>
        <h1 className="type-display-1">{t(strings, "place.notFound")}</h1>
        <p className="type-caption">
          <Link href={`/${tenant}`}>{t(strings, "place.back")}</Link>
        </p>
      </PublicShell>
    );
  }

  const budget = read.value;
  const money = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: budget.currency,
    // The upstream sends two decimal places and means them. Rendering fewer
    // would round a published figure on the page a citation is made from.
    minimumFractionDigits: 2,
  });

  return (
    <PublicShell
      city={city}
      citySlug={tenant}
      strings={strings}
      locale={locale}
      generatedAt={budget.generatedAt}
      notice={budget.notice}
    >
      <h1 className="type-display-1">{t(strings, "budget.title", { place: zoneCode })}</h1>
      <p className="type-mono-data">{t(strings, "budget.year", { year: budget.fiscalYear })}</p>

      {budget.allocations.length === 0 ? (
        <>
          <p className="type-body">{t(strings, "budget.empty", { year: budget.fiscalYear })}</p>
          <p className="budget__note type-caption">{t(strings, "budget.emptyWhy")}</p>
        </>
      ) : (
        <div className="zone-panel__scroll">
          <table className="budget__lines">
            <caption className="type-caption">
              {t(strings, "budget.year", { year: budget.fiscalYear })}
            </caption>
            <thead>
              <tr>
                <th scope="col">{t(strings, "budget.source")}</th>
                <th scope="col" className="budget__amount">
                  {t(strings, "budget.allocated")}
                </th>
                <th scope="col" className="budget__amount">
                  {t(strings, "budget.spent")}
                </th>
                <th scope="col" className="budget__amount">
                  {t(strings, "budget.utilisation")}
                </th>
              </tr>
            </thead>
            <tbody>
              {budget.allocations.map((line) => (
                <tr key={line.fundingSource}>
                  <th scope="row" className="type-caption">
                    {notTranslatable(line.fundingSource)}
                  </th>
                  {/*
                   * `Intl.NumberFormat.format` accepts a string and formats it
                   * without going through a double — which is exactly why the
                   * amount is passed through as the string the API sent rather
                   * than parsed on the way in.
                   */}
                  <td className="budget__amount type-mono-data">
                    {notTranslatable(formatAmount(line.allocated, money))}
                  </td>
                  <td className="budget__amount type-mono-data">
                    {notTranslatable(formatAmount(line.spent, money))}
                  </td>
                  <td className="budget__amount">
                    <Figure figure={line.utilisation} strings={strings} format="percent" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="budget__note type-caption">{t(strings, "budget.notSuppressed")}</p>

      <nav className="type-caption">
        <Link href={`/${tenant}/ward/${zoneCode}`}>{t(strings, "place.summary", { place: zoneCode })}</Link>
        {" · "}
        <Link href={`/${tenant}`}>{t(strings, "place.back")}</Link>
      </nav>
    </PublicShell>
  );
}
