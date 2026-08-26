/**
 * Which way a locale runs — A11, §E22.
 *
 * §E22 claims *"RTL-ready layout primitives"*, and the ground under that claim
 * is real: every stylesheet in `src/` uses logical properties, and
 * `scripts/check-guards.ts` fails the build on a physical `left` / `right` /
 * `margin-left`. But logical properties only do anything once something sets a
 * direction, and until F2 nothing in this application ever did. Ready was a
 * claim; this module plus one seeded locale is what makes it a fact.
 *
 * **Why a list rather than `Intl`.** `Intl.Locale.prototype.getTextInfo()` is
 * the correct answer and is not available in every runtime this code runs in —
 * this module is imported by server components, by the client shell, by
 * Storybook and by vitest, and a feature-detected branch that silently returns
 * `"ltr"` on the runtime that lacks it would fail exactly where nobody looks.
 * The set of right-to-left written languages is small, stable, and changes on
 * the timescale of scripts rather than releases.
 */

/**
 * Language subtags written right to left.
 *
 * `ur` is the one this product is most likely to meet — Urdu is an official
 * language in several Indian states, so a NEMESIS deployment that needs RTL
 * will probably need it rather than `ar`. `ar` is what the demo tenant seeds,
 * because it is the widest-supported script on a bare test container and the
 * assertion is about direction rather than about words.
 */
const RTL_LANGUAGES: ReadonlySet<string> = new Set([
  "ar", // Arabic
  "arc", // Aramaic
  "ckb", // Central Kurdish
  "dv", // Divehi
  "fa", // Persian
  "he", // Hebrew
  "ks", // Kashmiri
  "ps", // Pashto
  "sd", // Sindhi
  "ur", // Urdu
  "yi", // Yiddish
]);

export type Direction = "ltr" | "rtl";

/**
 * The direction for a BCP-47 tag.
 *
 * Matched on the language subtag alone: `ar-EG` and `ar` run the same way, and
 * a registry keyed on language is the common case (`negotiateLocale` says the
 * same thing about matching `mr-IN` to `mr`).
 */
export function directionOf(locale: string): Direction {
  const language = locale.toLowerCase().split("-")[0] ?? "";
  return RTL_LANGUAGES.has(language) ? "rtl" : "ltr";
}
