import Link from "next/link";

import { notTranslatable, t, type Strings } from "@/lib/i18n/strings";
import { HonestyTable } from "@/public/HonestyTable";

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
      <h2 id="act-receipts" className="type-display-2">
        {t(strings, "story.receipts.heading")}
      </h2>
      <p className="receipts__note type-body">{t(strings, "story.receipts.note")}</p>

      <div>
        <h3 className="type-heading">{t(strings, "story.receipts.api")}</h3>
        {/* The endpoint that backs every figure above it — unauthenticated,
            k-anonymous and versioned (§26.4). Printed as the command rather
            than described, because a `curl` somebody can paste is the only
            form of that claim a reader can check in ten seconds. */}
        <pre className="type-mono-data">{notTranslatable(curl)}</pre>
      </div>

      {/* `data-story-honesty` is the marker `tests/story.spec.ts` asserts on,
          and the storyboard carries it too — §E16.2's promise is about the
          marketing *surface*, not about one of its tiers. */}
      <div data-story-honesty="true">
        <h3 className="type-heading">{t(strings, "story.receipts.honesty")}</h3>
        <HonestyTable strings={strings} />
      </div>

      {zones.length === 0 ? null : (
        <ul>
          {zones.map((zone) => (
            <li key={zone.code}>
              <Link href={`/${citySlug}/ward/${zone.code}`} className="type-body">
                {notTranslatable(zone.name)}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
