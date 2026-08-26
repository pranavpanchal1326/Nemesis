import { notTranslatable, type Translated } from "./strings";

/**
 * Times, in the reader's language — §E10.1, §E10.2.
 *
 * **The ledger was rendering `2026-08-25T04:21:11.120958Z`.** That is the wire
 * format, and it is exactly right on the wire: `format_timestamp` in
 * `nemesis/events/hashing.py` pins RFC 3339 with microsecond precision because
 * the value enters a hash preimage and must be byte-stable across timezones.
 * None of that is a reason to put it in front of a citizen. §E17.4 asks for *"a
 * vertical paper ledger"*, and a paper ledger says *25 Aug, 09:51*.
 *
 * **`Intl`, not a format string.** Month names, ordering, and the 12/24-hour
 * convention are all locale properties, and Marathi does not write a date the
 * way English does. A hand-rolled `${day} ${MONTHS[m]}` would be a second
 * translation table living outside the locale registry — which is the thing
 * §E10.1's whole apparatus exists to prevent.
 *
 * **The microseconds are dropped and the precision is not.** The exact value
 * stays on the `<time datetime>` attribute, so a screen reader, a crawler and
 * anybody who copies the row still gets the value the chain hashed. The visible
 * text is for reading; the attribute is the record.
 */

/** A wire timestamp that could not be parsed. Rendered as itself rather than as
 *  an empty cell — §E3.3, and a malformed timestamp is a bug worth seeing. */
function fallback(value: string): Translated {
  return notTranslatable(value);
}

/**
 * *25 Aug, 09:51* — the ledger's own format.
 *
 * Day and month because a citizen's report and its resolution are days apart,
 * not months; the time because the pipeline's six gates land seconds apart and a
 * ledger without it would show six identical rows. The year is omitted for the
 * current year and shown otherwise, which is how a person writes a date.
 */
export function formatLedgerTime(
  timestamp: string,
  locale: string,
  now: number = Date.now(),
): Translated {
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return fallback(timestamp);

  const date = new Date(parsed);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();

  return notTranslatable(
    new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "short",
      ...(sameYear ? {} : { year: "numeric" }),
      hour: "2-digit",
      minute: "2-digit",
    }).format(date),
  );
}

/**
 * *25 August 2026 at 09:51* — the receipt's format.
 *
 * Longer than the ledger's on purpose. §E17.3's receipt is *"a document:
 * saveable, shareable"*, and a document that may be printed and read a year
 * later cannot abbreviate its own date or leave off its year.
 */
export function formatReceiptTime(timestamp: string, locale: string): Translated {
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return fallback(timestamp);

  return notTranslatable(
    new Intl.DateTimeFormat(locale, {
      dateStyle: "long",
      timeStyle: "short",
    }).format(new Date(parsed)),
  );
}

/**
 * *1 June 2019* — a date that is a date.
 *
 * **Not `formatReceiptTime` with the time ignored.** `active_since` is a
 * SQL `DATE`, and it arrives as `"2019-06-01"` with no time and no zone.
 * `Date.parse` reads a bare ISO date as **UTC midnight**, and
 * `Intl.DateTimeFormat` then renders it in the reader's zone — so a contractor
 * on the register since 1 June 2019 was being published as *"June 1, 2019 at
 * 5:30 AM"* in Asia/Kolkata, and in a zone west of UTC it would have been the
 * previous day.
 *
 * A wrong date on a named commercial entity's public record is exactly the kind
 * of small inaccuracy §22.2 makes expensive, so the parts are read back out in
 * UTC rather than the value being re-localised.
 */
export function formatDateOnly(date: string, locale: string): Translated {
  const parsed = Date.parse(date);
  if (Number.isNaN(parsed)) return fallback(date);

  return notTranslatable(
    new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "long",
      year: "numeric",
      // The value carries no time, so it carries no zone either. Formatting in
      // UTC is what keeps the rendered day equal to the stored one.
      timeZone: "UTC",
    }).format(new Date(parsed)),
  );
}
