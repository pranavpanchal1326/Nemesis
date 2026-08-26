"""The §22.2 notices, per locale — C7, ADR-0052.

`db.models.i18n` draws the line this module sits on:

> **Two catalogues, and only one of them lives here.** Product copy — button
> labels, error prose, the §22.1 consent text — is authored by NEMESIS,
> versioned with the code, and reviewed like code; Phase 18 owns it. What lives
> in that table is *tenant-authored* text… Mixing the two would mean a tenant
> could overwrite the wording of a legal notice, which is not a localisation
> feature.

`SYSTEM_FLAGGED_NOTICE` and `RATING_DISCLAIMER` are the second kind. They are
the sentences that keep a system-derived figure about a named commercial entity
an *observation* rather than an assertion (§22.2), and a tenant able to edit
them could publish a flag with its qualification removed. So they are not
`translations` rows, they are not client-side strings, and the frontend renders
them verbatim through `notTranslatable()`. They live here, in code, and they
change through review.

**What a review status is doing in a data structure.** C7's question was *"what
may a §22.2 disclaimer be translated by?"*, and the answer this module encodes
is: by a person who is accountable for it. Every entry carries who approved it.
`UNREVIEWED` is a real, shippable value — a locale whose text exists but whose
legal review has not happened — and it is visible in the source, in the API's
`notice_review` field, and in the ADR, rather than being the absence of a
question nobody asked. The alternative was to serve English to a Marathi reader
and call the feature done, which is the state C7 was raised about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The locale every response falls back to. English is the canonical text: it is
#: the wording §22.2 was argued in, and every translation below is a translation
#: *of it*.
DEFAULT_LOCALE: Final = "en"

#: A review status meaning "the words exist, and nobody with authority has
#: signed them off yet". Not a placeholder — see the module docstring.
UNREVIEWED: Final = "unreviewed"


@dataclass(frozen=True, slots=True)
class Notice:
    """One legal sentence in one locale, and who is accountable for it."""

    text: str
    #: Who approved this wording — a name, a role, or ``UNREVIEWED``.
    review: str


@dataclass(frozen=True, slots=True)
class LocaleNotices:
    system_flagged: Notice
    rating_disclaimer: Notice


CATALOGUE: Final[dict[str, LocaleNotices]] = {
    "en": LocaleNotices(
        system_flagged=Notice(
            text=(
                "System-computed from reported data and under human review. Figures are not "
                "verified findings and must not be presented as proven fact."
            ),
            review="NEMESIS product copy, §22.2",
        ),
        rating_disclaimer=Notice(
            text=(
                "A track record, not a score. NEMESIS does not collapse a contractor to a "
                "single rating (§16.1), and a disputed or auto-confirmed closure is counted "
                "separately rather than folded into a headline number."
            ),
            review="NEMESIS product copy, §16.1",
        ),
    ),
    "mr": LocaleNotices(
        system_flagged=Notice(
            text=(
                "नोंदवलेल्या माहितीवरून प्रणालीने काढलेले आकडे; मानवी पडताळणी सुरू आहे. "
                "हे आकडे पडताळलेले निष्कर्ष नाहीत आणि त्यांना सिद्ध तथ्य म्हणून मांडू नये."
            ),
            review=UNREVIEWED,
        ),
        rating_disclaimer=Notice(
            text=(
                "हे कामाचे रेकॉर्ड आहे, गुणांकन नाही. NEMESIS कंत्राटदाराला एकाच मानांकनात "
                "बसवत नाही (§16.1), आणि वादग्रस्त किंवा आपोआप पुष्टी झालेले काम स्वतंत्रपणे "
                "मोजले जाते, मुख्य आकड्यात मिसळले जात नाही."
            ),
            review=UNREVIEWED,
        ),
    ),
}

#: Locales this catalogue can answer in. A tenant may declare more; a locale not
#: here is served the canonical English text with `notice_locale` saying so,
#: which is honest, rather than an empty string or a machine translation made at
#: request time.
SUPPORTED_LOCALES: Final[frozenset[str]] = frozenset(CATALOGUE)


def resolve(locale: str) -> tuple[str, LocaleNotices]:
    """Return the locale actually served and its notices.

    The returned locale is not always the one asked for, and the caller is
    expected to publish it. A reader shown English text under a Marathi page
    heading should be able to tell that is what happened.
    """
    if locale in CATALOGUE:
        return locale, CATALOGUE[locale]
    # No fallback *chain* — `db.models.i18n` argues against one, and the same
    # argument holds harder here: a legal notice assembled from two locales is
    # not a notice.
    return DEFAULT_LOCALE, CATALOGUE[DEFAULT_LOCALE]
