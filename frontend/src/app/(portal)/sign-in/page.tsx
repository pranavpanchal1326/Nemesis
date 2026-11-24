import type { Metadata } from "next";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { t } from "@/lib/i18n/strings";
import { consoleAuthConfig, readOperatorSession } from "@/server/console-session";
import { loadStrings, negotiateLocale, SEEDED_LOCALES } from "@/server/strings";

/**
 * The console's door — ADR-0062.
 *
 * **In the `(portal)` group, which is a design decision and not a convenience.**
 * That layout's own words are *"an officer arriving at a door has not started a
 * shift yet"*, which is exactly what signing in is, so this page grounds on
 * paper like the other two doors rather than on §E9.3's light table. The console
 * is dark the moment they are inside it.
 *
 * It also has to live outside `(console)`, because `(console)/layout.tsx` is
 * what redirects here — a sign-in page behind its own gate is a loop.
 *
 * **A server component with a plain form.** No client bundle, no `useState`, no
 * fetch: the form posts to `/api/session` and the browser follows. §E2 already
 * requires the public doors to work with JavaScript disabled, and a sign-in page
 * has less excuse than any of them — it is the surface most likely to be opened
 * on a locked-down municipal desktop.
 *
 * **It tells an operator what this passphrase is not.** One shared passphrase,
 * no accounts, and the session records *that* somebody signed in rather than
 * who. §E3.3 is a rule about not showing a confidently wrong screen, and a
 * sign-in page that looked like a personal login would be exactly that — an
 * officer would reasonably assume their decisions are attributed to them, and
 * until Phase 13 they are not.
 */
export const metadata: Metadata = {
  title: "Sign in",
  description: "The municipal console.",
  // Same reasoning as the staff door, one step firmer: a sign-in form is the
  // single most attractive thing on this host to a credential-stuffing crawler.
  robots: { index: false, follow: false },
};

export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<{ readonly refused?: string; readonly next?: string }>;
}) {
  // Already signed in? Then this page has nothing to ask. Sending them on
  // rather than showing the form avoids the small confusion of a sign-in page
  // that appears after a successful sign-in.
  if ((await readOperatorSession()) !== null) redirect("/console");

  const { refused } = await searchParams;
  const config = consoleAuthConfig();
  const requestHeaders = await headers();
  const locale = negotiateLocale({
    acceptLanguage: requestHeaders.get("accept-language"),
    available: SEEDED_LOCALES,
  });
  const strings = await loadStrings(["common", "console"], locale);

  return (
    <main className="portal portal--sign-in" id="main">
      <header className="portal__masthead">
        <div className="portal__masthead-copy">
          <h1 className="portal__title type-display-1">{t(strings, "signIn.title")}</h1>
          <p className="portal__standfirst type-body">{t(strings, "signIn.lede")}</p>
        </div>
      </header>

      {config.kind === "unconfigured" ? (
        /*
         * Not a 404 and not a silent failure. A deployment with no passphrase
         * set has a closed console *by design* (the fail-closed rule in
         * `console-session.ts`), and saying so is the difference between an
         * operator reporting a bug and the person who deployed it reading
         * `.env.example`. The variable names are deliberately not printed here
         * — those go to the deployer through the 503 from `/api/session`, not
         * to whoever is standing at the door.
         */
        <section className="sign-in__notice" role="status">
          <p className="type-body">{t(strings, "signIn.unconfigured")}</p>
          <p className="sign-in__hint type-caption">{t(strings, "signIn.unconfigured.hint")}</p>
        </section>
      ) : (
        <form className="sign-in" method="post" action="/api/session">
          {refused === undefined ? null : (
            /*
             * `role="alert"` so a screen reader hears the refusal on the
             * reload rather than discovering it by exploring the form, and
             * `aria-describedby` ties it to the field it is about.
             */
            <p className="sign-in__refused type-body" id="refused" role="alert">
              {t(strings, "signIn.refused")}
            </p>
          )}

          <label className="sign-in__label type-micro" htmlFor="passphrase">
            {t(strings, "signIn.passphrase")}
          </label>
          <input
            className="sign-in__input type-mono-data"
            id="passphrase"
            name="passphrase"
            type="password"
            autoComplete="current-password"
            // No `autoFocus`. It was here on the argument that the field is the
            // only thing on the page worth focusing — and `jsx-a11y` is right
            // to object: moving focus on load skips the heading and the lede,
            // so a screen-reader user is dropped into an unlabelled text box
            // and a magnifier user loses the page they just opened. §E22 does
            // not get waived because a form is small.
            required
            aria-describedby={refused === undefined ? "passphrase-hint" : "refused passphrase-hint"}
          />
          <p className="sign-in__hint type-caption" id="passphrase-hint">
            {t(strings, "signIn.passphrase.hint")}
          </p>

          <button className="sign-in__submit type-micro" type="submit">
            {t(strings, "signIn.submit")}
          </button>

          <p className="sign-in__shared type-caption">{t(strings, "signIn.shared")}</p>
        </form>
      )}
    </main>
  );
}
