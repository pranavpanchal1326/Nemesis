import Link from "next/link";
import type { ReactNode } from "react";

import { formatReceiptTime } from "@/lib/i18n/datetime";
import { notTranslatable, t, type Strings, type Translated } from "@/lib/i18n/strings";
import "./public.css";

/**
 * The chrome every §E18 page shares — §16.2, §E13 Tier D.
 *
 * **A server component with no interactivity at all, and that is the design.**
 * §E13's bottom rung is *"JS disabled, crawler, 2G — semantic article, all copy
 * present, public data server-rendered"*. On this surface Tier D is not a
 * fallback that degrades from something better: it **is** the page. There is no
 * client bundle to fail, no hydration to wait for, and no state that only
 * exists after JavaScript runs. Everything below is `<a>`, `<dl>`, `<table>`
 * and text.
 *
 * That is also why the locale switch is a set of links rather than a control.
 * A `<select>` that needs an `onChange` to do anything is a control that does
 * nothing at Tier D, and §E3.3's rule against showing an affordance that is not
 * one applies to the surface a crawler reads too.
 */
export function PublicShell({
  city,
  citySlug,
  strings,
  locale,
  generatedAt,
  notice,
  children,
}: {
  readonly city: string;
  readonly citySlug: string;
  readonly strings: Strings;
  readonly locale: string;
  /** The API's own `generated_at`. Stated because a cached transparency page
   *  with no timestamp is a page whose age the reader has to guess. */
  readonly generatedAt?: string;
  /**
   * `SYSTEM_FLAGGED_NOTICE`, from the response.
   *
   * §E18: *"they render as first-class UI, never as tooltips or footnotes."* So
   * it is in the header, above the figures it qualifies, at reading size — not
   * greyed out at the bottom where a screenshot crops it off.
   */
  readonly notice?: string;
  readonly children: ReactNode;
}) {
  return (
    <div data-surface="public" lang={locale} className="public">
      <header className="public__header">
        <p className="public__city type-display-2">
          <Link href={`/${citySlug}`}>{t(strings, "city.published", { city })}</Link>
        </p>
        <p className="public__what type-body">{t(strings, "city.what")}</p>
        {notice === undefined ? null : <SystemNotice notice={notice} />}
      </header>

      <main className="public__main">{children}</main>

      <footer className="public__footer">
        {generatedAt === undefined ? null : (
          <p className="type-caption">
            {t(strings, "city.generatedAt", {
              time: formatReceiptTime(generatedAt, locale),
            })}
          </p>
        )}
        <nav className="public__locales" aria-label={t(strings, "city.published", { city })}>
          {LOCALES.map((tag) => (
            <Link
              key={tag}
              href={`?locale=${tag}`}
              hrefLang={tag}
              aria-current={tag === locale ? "true" : undefined}
              className="type-caption"
            >
              {notTranslatable(tag)}
            </Link>
          ))}
        </nav>
        <p className="type-caption">
          <Link href={`/${citySlug}/honesty`}>{t(strings, "honesty.title")}</Link>
        </p>
      </footer>
    </div>
  );
}

/**
 * §22.2's notice, as a block of prose.
 *
 * The words are the server's. Not `t()`, and the reason matters: this sentence
 * is a legal position the platform takes about figures it computed, and a
 * frontend that substituted its own translation would be substituting its own
 * legal text. `notTranslatable` is the marked escape for exactly this — a value
 * that must reach the screen unaltered.
 *
 * **The cost is stated rather than hidden:** the notice is therefore English on
 * a Marathi page. That is a gap in the *backend* — the disclaimer is a constant
 * in `public/aggregates.py` and does not resolve through the Phase 5 locale
 * registry — and it is recorded as a defect rather than worked around here.
 */
function SystemNotice({ notice }: { readonly notice: string }) {
  return (
    <aside className="public__notice" data-notice="system-flagged">
      <p className="type-caption">{notTranslatable(notice)}</p>
    </aside>
  );
}

/** The locales the application can render before the control plane speaks.
 *  Imported rather than hard-coded would be better; it is `SEEDED_LOCALES`,
 *  and this module cannot import the server module without pulling
 *  `server-only` into a shared component. Kept in step by a unit test. */
const LOCALES: readonly string[] = ["en", "mr"];

export { LOCALES as SHELL_LOCALES };

/** A heading that names a place and its kind, in the institutional voice. */
export function PlaceHeading({
  name,
  kind,
  strings,
}: {
  readonly name: string;
  readonly kind: string;
  readonly strings: Strings;
}) {
  return (
    <h1 className="public__place type-display-1">
      <span className="public__place-kind type-micro">{kindLabel(kind, strings)}</span>
      {notTranslatable(name)}
    </h1>
  );
}

function kindLabel(kind: string, strings: Strings): Translated {
  // `zone_kind` is tenant-defined text, not a closed enum on the public
  // contract, so this cannot be an exhaustive switch and must not pretend to
  // be. The three the seeded templates use are named; anything else says
  // "place" rather than echoing an unrecognised token at a reader.
  switch (kind) {
    case "ward":
      return t(strings, "place.kind.ward");
    case "zone":
      return t(strings, "place.kind.zone");
    case "city":
      return t(strings, "place.kind.city");
    default:
      return t(strings, "place.kind.other");
  }
}
