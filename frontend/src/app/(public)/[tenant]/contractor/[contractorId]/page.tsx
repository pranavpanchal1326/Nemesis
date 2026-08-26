import type { Metadata } from "next";
import Link from "next/link";

import { ContractorLedger } from "@/components/ContractorLedger";
import { SuppressionNotice } from "@/components/SuppressionNotice";
import { formatDateOnly } from "@/lib/i18n/datetime";
import { notTranslatable, t } from "@/lib/i18n/strings";
import { asDisclaimer, ContractorFlags, responseHrefFor } from "@/public/ContractorFlags";
import { LabelledFigure } from "@/public/Figure";
import { PublicShell } from "@/public/PublicShell";
import { cityNameFallback, fetchContractor } from "@/server/public-data";
import { publicLocale } from "../../locale";

/**
 * A contractor's public record — §E18, §16.1, §16.4, §22.2.
 *
 * > **Contractor profiles are ledgers, never ratings** (§16.1): four
 * > independent metrics anyone can argue with … Flagged rows carry the
 * > fluorescent hatch, the disclaimer, **and the contractor's response and
 * > appeal status in the same frame**.
 *
 * **The ledger cannot be collapsed, and the mechanism is not this file.** The
 * API publishes no single score, so there is nothing here to collapse — that is
 * §16.1 enforced by the contract rather than by a component's restraint, and
 * `tests/contractor-ledger.test.ts` asserts it against the generated schema so
 * the day somebody adds a `rating` field the argument happens before it ships.
 *
 * **`rating_disclaimer` is above the figures, not below them.** §E18: *"first-class
 * UI, never as tooltips or footnotes."* A reader who scrolls past the numbers
 * and stops has read the numbers; a disclaimer that only exists below them is a
 * disclaimer for the readers who did not need it.
 *
 * **`robots: index` — and this is a deliberate call about a named commercial
 * entity.** §16.3 makes contractor records public; §22.2 makes them
 * disclaimer-bearing. Publishing and indexing are the same act once the page is
 * server-rendered on a public URL, and a page that is public but hidden from
 * search would be a transparency claim with a caveat nobody stated. What
 * protects the contractor is the disclaimer and the response path being on the
 * page, which is why both are required props rather than conventions.
 */
type Params = Promise<{ tenant: string; contractorId: string }>;
type Search = Promise<Record<string, string | string[] | undefined>>;

export async function generateMetadata({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}): Promise<Metadata> {
  const { tenant, contractorId } = await params;
  const { locale } = await publicLocale(tenant, await searchParams);
  const read = await fetchContractor(tenant, contractorId, locale);
  const city = read.ok ? read.value.cityName : cityNameFallback(tenant);

  if (!read.ok) return { title: city, robots: { index: false, follow: false } };

  return {
    title: `${read.value.contractorName} · ${city}`,
    // The description restates the disclaimer's substance, because a search
    // result is a place this record is read *without* the page around it.
    description: `Public work record for ${read.value.contractorName}. A track record, not a score.`,
    alternates: { canonical: `/${tenant}/contractor/${contractorId}` },
  };
}

export default async function Contractor({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}) {
  const { tenant, contractorId } = await params;
  const { locale, locales, strings } = await publicLocale(tenant, await searchParams);
  const read = await fetchContractor(tenant, contractorId, locale);
  const city = read.ok ? read.value.cityName : cityNameFallback(tenant);

  if (!read.ok) {
    return (
      <PublicShell
        city={city}
        citySlug={tenant}
        strings={strings}
        locale={locale}
        locales={locales}
      >
        <h1 className="type-display-1">{t(strings, "contractor.notFound")}</h1>
        <p className="type-caption">
          <Link href={`/${tenant}`}>{t(strings, "place.back")}</Link>
        </p>
      </PublicShell>
    );
  }

  const profile = read.value;

  return (
    <PublicShell
      city={city}
      citySlug={tenant}
      strings={strings}
      locale={locale}
      locales={locales}
      generatedAt={profile.generatedAt}
      notice={profile.notice}
    >
      <article className="contractor">
        <header className="contractor__identity">
          <h1 className="type-display-1">{notTranslatable(profile.contractorName)}</h1>
          <p className="type-mono-data">
            {t(strings, "contractor.registration", { id: profile.registrationId })}
          </p>
          <p className="type-caption">
            {profile.activeSince === null
              ? t(strings, "contractor.activeSinceUnknown")
              : t(strings, "contractor.activeSince", {
                  date: formatDateOnly(profile.activeSince, locale),
                })}
          </p>
        </header>

        {/*
         * §22.2, first-class and above the figures. Its own block rather than
         * `<FlaggedNotice>`: nothing here is flagged, and rendering the flag
         * treatment on an unflagged record would assert an anomaly that does
         * not exist — the same category of lie, in the other direction, that
         * ADR-0039 refuses when it bans red.
         */}
        <aside className="public__notice" data-notice="rating-disclaimer">
          <p className="type-caption">{notTranslatable(profile.ratingDisclaimer)}</p>
        </aside>

        {profile.suppressed ? (
          <p>
            <SuppressionNotice threshold={profile.suppressionThreshold} strings={strings} explain />
          </p>
        ) : null}

        <section aria-labelledby="contractor-workload">
          <h2 id="contractor-workload" className="type-heading">
            {t(strings, "contractor.workload")}
          </h2>
          <dl className="zone-panel__figures">
            <LabelledFigure
              label={t(strings, "contractor.completed")}
              figure={profile.completed}
              strings={strings}
            />
            <LabelledFigure
              label={t(strings, "contractor.open")}
              figure={profile.open}
              strings={strings}
            />
            <LabelledFigure
              label={t(strings, "contractor.disputed")}
              figure={profile.disputed}
              strings={strings}
            />
          </dl>
        </section>

        {/*
         * The four metrics. `<ContractorLedger>` renders cost variance and
         * repeat-defect rate as nulls behind their not-wired chips rather than
         * hiding the rows — "a ledger that quietly drops the metrics it does
         * not have yet is a ledger that flatters."
         */}
        <ContractorLedger
          strings={strings}
          suppressed={profile.suppressed}
          suppressionThreshold={profile.suppressionThreshold}
          metrics={{
            onTimeRate: profile.onTimeRate.kind === "known" ? profile.onTimeRate.value : null,
            costVariance: null,
            // **Not `profile.completed`.** An earlier pass wired the completed
            // count in here and it was wrong in the direction that matters:
            // `work_orders_completed` counts every closure including the ones
            // `auto_confirmed_resolutions` exists to hold apart, so publishing
            // it under "confirmed by reporters" would credit the contractor
            // with confirmations no reporter gave. §16.1's rule is that the
            // ledger must not flatter, so the row stays null behind its chip
            // until Phase 15 publishes a real confirmation count.
            confirmedCount: null,
            disputedCount: profile.disputed.kind === "known" ? profile.disputed.value : null,
            repeatDefectRate: null,
          }}
        />

        <section aria-labelledby="contractor-certified">
          <h2 id="contractor-certified" className="type-heading">
            {t(strings, "contractor.certified")}
          </h2>
          {profile.certifiedCategories.length === 0 ? (
            <p className="type-caption">{t(strings, "contractor.certifiedNone")}</p>
          ) : (
            <ul className="contractor__certified type-mono-data">
              {profile.certifiedCategories.map((key) => (
                <li key={key}>{notTranslatable(key)}</li>
              ))}
            </ul>
          )}
        </section>

        {/*
         * The response frame. Empty today and present anyway — §6 Principle #8
         * requires the appeal path to ship with the accountability feature, and
         * a section that appears only once there is something to appear about
         * is a section a contractor cannot find in advance.
         */}
        <ContractorFlags
          flags={[]}
          disclaimer={asDisclaimer(profile.ratingDisclaimer)}
          responseHref={responseHrefFor(tenant, contractorId)}
          strings={strings}
        />
      </article>
    </PublicShell>
  );
}
