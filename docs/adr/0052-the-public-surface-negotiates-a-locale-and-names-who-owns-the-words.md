# 0052 — The public surface negotiates a locale, and names who owns the words

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** PLT · LEGAL · PROD
- **Blueprint:** §16.2, §22.2, §26.4, §E18; `docs/FRONTEND-EXECUTION-PLAN.md` C7, C8

## Context

Two register rows, one contract change.

**C7 — the public surface cannot speak Marathi.** `SYSTEM_FLAGGED_NOTICE`,
`RATING_DISCLAIMER` and `zones.name` were English constants on every response.
`NAMESPACE_ZONE` and `NAMESPACE_TAXONOMY` have existed since Phase 5 and the
public endpoints never consulted them. A Marathi reader on a Marathi city's
transparency page read the city's places, its complaint categories and its legal
disclaimer in English — which is not a localisation gap, it is §16.2's audience
being the wrong audience.

**C8 — a tenant's display name is not published.** Only the slug was. The
frontend title-cased it, and recorded that as a defect rather than reaching for
the alternative it was tempted into: a lookup table of the cities we happen to
know about, which would be a second source of truth for a fact the platform
already holds.

C7 is a decision rather than a patch because of one question it forces: **what
may a §22.2 disclaimer be translated *by*?** Not by the client — a browser
asserting its own wording of a legal notice is exactly the failure
`notTranslatable()` exists to prevent. Not by the tenant either: `db.models.i18n`
already draws that line, and a municipality able to edit the sentence that keeps
a contractor flag an observation rather than an accusation is a municipality that
can publish an accusation.

## Decision

**1. The locale is a query parameter, not a header.** `?locale=mr`, defaulting to
`en`.

Not `Accept-Language`, for two reasons and the second decided it. These responses
are `Cache-Control: public`; negotiating on a header means `Vary:
Accept-Language`, which intermediaries implement inconsistently and some
implement by not caching. And §16.2's claim is that these URLs are *citable* — a
URL whose language depends on the reader's browser settings is not a citation.

Defaulting to `en` rather than to the tenant's primary locale is what makes this
**additive**: a consumer written before this ADR sends no parameter and receives
byte-for-byte the body it received before. The v1 contract re-locks by addition.

**2. Two catalogues, and they stay separate.**

| | Owner | Where it lives |
|---|---|---|
| The §22.2 notice, the §16.1 rating disclaimer | NEMESIS | `nemesis/public/notices.py` — code, reviewed like code |
| Zone names, taxonomy display names | The tenant | the `translations` table, via `NAMESPACE_ZONE` / `NAMESPACE_TAXONOMY` |

The frontend renders both verbatim through `notTranslatable()`. It resolves
neither.

**3. Every response says who is accountable for its legal wording.** Each entry
in the notice catalogue carries a `review` field naming who approved that
wording, and every public body now publishes four fields:

- `tenant_name` — C8;
- `locale` — which language the tenant-authored names are in;
- `notice_locale` — which language the notice *actually* is in, which is not
  always the one asked for;
- `notice_review` — who signed that wording off, or that nobody has.

`rating_disclaimer_locale` and `rating_disclaimer_review` sit beside them on the
contractor profile, separately, because the two sentences are reviewed
separately and a deployment can legitimately have one and not the other.

**4. `category_name` is additive beside `category`, never instead of it.** The
taxonomy key is what a consumer joins on and it stays identical in every
language. The display name is the human half, and it falls back to the key —
`pothole_or_road_damage` reads as an untranslated entry, which is legible;
an empty cell beside a count is the shape §E18 spends its whole argument
forbidding.

**5. The Marathi notices ship marked `unreviewed`, and the API says so.** The
wording exists, it is served, and `notice_review` reports `unreviewed` to every
caller. Legal sign-off is an outstanding item and this is where it is recorded.

## Alternatives considered

**Put the notices in the `translations` table.** Tempting — one mechanism
instead of two — and it hands a tenant the ability to edit the sentence that
qualifies a flag about a named commercial entity. `db.models.i18n` already
rejected exactly this: *"a tenant could overwrite the wording of a legal notice,
which is not a localisation feature."*

**Translate on the client.** The frontend already has a locale, a bundle loader
and a registry proxy, so this is three lines. It is also the client asserting its
own legal text, which is why `notTranslatable()` exists and why the public
surface renders the server's words verbatim.

**Ship no Marathi notice until legal review happens.** The most conservative
option and the one that leaves C7 open indefinitely: a review nobody has
scheduled does not happen, and meanwhile a Marathi page keeps carrying an English
disclaimer, which is the state C7 was raised about. Publishing the translation
with its review status attached is worse than a reviewed translation and better
than a permanent English one — and unlike either alternative it makes the
outstanding work visible on the surface where it matters, in the response itself.

**Negotiate on `Accept-Language` as well as the query.** Rejected for the
caching and citability reasons above. The *frontend* still negotiates on
`Accept-Language` for its own copy — `publicLocale()` does, and always did — and
then passes the result explicitly upstream. Negotiation happens once, at the
edge, and the API is told the answer.

## Consequences

- v1 and v2 both gain the envelope. A reader who follows a v1 link and a v2 link
  to the same ward gets the same language and the same disclaimer; a difference
  there would be a defect, not a version.
- `Content-Language` is set on every public response. No `Vary`, deliberately:
  the locale is in the URL, so two languages are two cache entries.
- Every public response body grows by four short strings. On a fifty-zone index
  that is once, in the envelope, and not per zone.
- Two extra queries per public response — one bundle per namespace, never one per
  label. A fifty-place index with a hundred category rows costs two reads.
- `cityName()` became `cityNameFallback()` in the frontend and is now reachable
  only where there is no published body to read a name from: a failed fetch, and
  the generated honesty page. Title-casing a slug is a guess, and it is confined
  to the two places a guess is still the honest answer.
- A tenant that declares a locale and translates nothing gets a page in that
  language with untranslated entries in it, not a page in two languages.
  `TranslationService.coverage()` is how that gap is measured.

## Revisit when

- A tenant needs a locale the notice catalogue does not carry. Today the fallback
  is English with `notice_locale` saying so; at three or four such tenants the
  right answer is a review workflow rather than a Python dict.
- Legal review of the Marathi wording completes — at which point `review` names a
  person and `unreviewed` disappears from the published surface.
- v2 leaves preview. The `/ward/` → `/zone/` rename moves the frontend's URLs,
  and this envelope should move with it unchanged.
