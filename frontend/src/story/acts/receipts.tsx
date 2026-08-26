import Link from "next/link";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { PortalPlate } from "@/portal/PortalPlate";
import { HONESTY_COUNTS, HONESTY_STATUSES } from "@/public/generated/honesty";

import type { StoryZone } from "./close";

/**
 * Act 9 — the receipts (§E16, §E16.2, F14).
 *
 * > **Deliberately boring.** A real closure with before/after, SSIM score,
 * > citizen confirmation, contractor, cost variance; a contractor ledger; a
 * > ward's allocated-vs-spent bar; the public API with a live `curl`; and
 * > **§44's REAL/SIMULATED/ROADMAP table, published**.
 *
 * **A server component, and that is the act's whole argument.** Everything here
 * is a real read of this deployment rendered into HTML — so it is present with
 * JavaScript off (§E13 Tier D), it is indexable, and none of it can be a
 * client-side flourish that happens to look like data.
 *
 * **What is here, and what is honestly not.** §44's table is here, published on
 * the marketing surface exactly as §E16.2 asks, and it is generated from the
 * two blueprints and drift-checked — so this is the product's limitations,
 * verbatim, on the page that sells it. The ward and contractor pages are here
 * as links to the real §E18 surfaces. **The before/after closure with its SSIM
 * score is not here**, because closure verification is Phase 15 and has not
 * been built: an act that staged one would be claiming a capability the table
 * two paragraphs below it says the product does not have yet. When Phase 15
 * lands, this act gains it — and the table will say so first.
 *
 * That is not a limitation of this act. It is the act working: §6 Principle #8
 * says publishing the honest label is the competitive advantage, and the first
 * place that gets tested is where a landing page would rather not.
 */
export function TheReceipts({
  strings,
  citySlug,
  zones,
  publicApiBase,
}: {
  readonly strings: Strings;
  readonly citySlug: string;
  readonly zones: readonly StoryZone[];
  /**
   * The host the public API is reachable at, or `null`.
   *
   * `null` renders the path without a host and says so. ADR-0040 keeps the
   * upstream URL on the server, and in a local checkout that URL is
   * `127.0.0.1:8000` — printing it on a landing page would publish a loopback
   * address as if it were a public endpoint. So the host is its own opt-in
   * variable (`NEMESIS_PUBLIC_API_URL`), set by a deployment that actually has
   * one, and absent everywhere else.
   */
  readonly publicApiBase: string | null;
}) {
  const path = `/api/v1/public/${citySlug}/zones`;
  const curl = publicApiBase === null ? `curl -s ${path}` : `curl -s ${publicApiBase}${path}`;

  return (
    <section
      className="receipts"
      data-act="receipts"
      data-real="true"
      aria-labelledby="act-receipts"
    >
      {/*
        The masthead. Act 9 is the document the film hands the reader, and it
        opened as a heading with a sentence under it on an undifferentiated
        sheet — correct type at every size and no composition at any of them.
        The same three-rule device the front doors use, because this is the same
        product and a reader arriving here from `/citizen` should not have to be
        told so.
      */}
      <header className="receipts__masthead">
        <div className="receipts__masthead-copy">
          <h2 id="act-receipts" className="type-display-2">
            {t(strings, "story.receipts.heading")}
          </h2>
          <p className="receipts__note type-body">{t(strings, "story.receipts.note")}</p>
        </div>

        {/*
          The plan, and it is the same drawing the staff door carries. Deliberate
          reuse rather than a second illustration: Act 9's subject is *every
          place this city publishes*, which is a survey, and the film has just
          spent nine acts at street level. One picture at two altitudes across
          the product is a design system; two unrelated drawings of a city is
          two people's work stapled together.
        */}
        <PortalPlate subject="plan" />
      </header>

      <div className="receipts__block">
        <h3 className="receipts__head type-heading">{t(strings, "story.receipts.api")}</h3>
        {/* The endpoint that backs every figure above it — unauthenticated,
            k-anonymous and versioned (§26.4). Printed as the command rather
            than described, because a `curl` somebody can paste is the only
            form of that claim a reader can check in ten seconds. */}
        <pre className="type-mono-data">{notTranslatable(curl)}</pre>
      </div>

      {/*
        `data-story-honesty` is the marker `tests/story.spec.ts` asserts on, and
        the storyboard carries it too — §E16.2's promise is about the marketing
        *surface*, not about one of its tiers.

        **The whole table used to be printed here, and it should not have
        been.** Eighty-one rows is six and a half thousand pixels: the landing
        spent more height on the honesty table than on the film, and a reader
        scrolling for the receipts arrived at a wall of rows rather than at the
        claim they came for. The table has a canonical home — `/{tenant}/honesty`
        is indexable, deep-linkable and published (ADR-0046) — and this act's job
        is to say *the record exists, here is its shape, here is the door*.

        **The counts are published, not summarised away.** Every number below is
        `HONESTY_COUNTS`, generated from §44 and §E28 by the same pipeline that
        builds the full table, so this cannot drift into a flattering rounding of
        it — and the statuses are named in full, so a reader sees the vocabulary
        that includes CUT and ROADMAP before they follow the link.
      */}
      <div data-story-honesty="true" className="receipts__honesty">
        <h3 className="receipts__head type-heading">{t(strings, "story.receipts.honesty")}</h3>
        <p className="type-body">
          {t(strings, "story.receipts.honestyCount", {
            claims: HONESTY_COUNTS.system + HONESTY_COUNTS.surface,
            real: HONESTY_COUNTS.systemReal + HONESTY_COUNTS.surfaceFinished,
          })}
        </p>
        <p className="receipts__statuses type-micro">
          {notTranslatable(HONESTY_STATUSES.join(" · "))}
        </p>
        <p className="type-body">
          <Link href={`/${citySlug}/honesty`}>{t(strings, "story.receipts.honestyLink")}</Link>
        </p>
      </div>

      {zones.length === 0 ? null : (
        <div className="receipts__places">
          <h3 className="receipts__head type-heading">{t(strings, "story.receipts.places")}</h3>
          <p className="type-caption">{t(strings, "story.receipts.placesNote")}</p>
          {/* Every published place, as the cards §E18's own index uses rather
              than as a bare column of names. The list is the last thing on the
              landing and it was thirteen unstyled list items — the shape of a
              debug dump on the page that is supposed to be the proof. */}
          <ul className="receipts__place-list">
            {zones.map((zone) => (
              <li key={zone.code}>
                <Link className="receipts__place" href={`/${citySlug}/ward/${zone.code}`}>
                  <span className="receipts__place-code type-micro">
                    {notTranslatable(zone.code)}
                  </span>
                  <span className="type-body">{notTranslatable(zone.name)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
