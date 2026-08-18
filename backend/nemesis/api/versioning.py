"""API versions, and the deprecation clock as executable data.

Critique-log defect #12: §16.3 promises journalists and civil society a public
API and the previous plan had no versioning story, so the contract would break
silently on the next deploy. ``docs/RELEASE.md`` fixed the *policy* — 12 months'
notice for a public API version — and a policy nobody can execute is prose.

This module is what makes it mechanical:

* **One registry.** A version's status, its release date, and the two dates that
  matter are declared here once. The headers, the discovery endpoint, and the
  developer portal all read the same rows, so they cannot disagree about whether
  v1 is deprecated — which is the failure that turns a published clock into a
  surprise.
* **RFC 9745 ``Deprecation`` and RFC 8594 ``Sunset``.** Standard headers rather
  than an invented ``X-`` pair, because the point of announcing a deprecation in
  a header is that generic tooling notices it without being taught our
  vocabulary. ``Link rel="deprecation"`` points at the portal page explaining
  what to do about it, which is the part a header cannot carry.
* **The clock is checked, not trusted.** ``_validate`` refuses a registration
  whose sunset is closer than the policy's notice period, at import time. The
  mistake this prevents is the realistic one: somebody deprecating v1 with a
  three-month sunset because a v2 is ready and the twelve months feels
  theoretical. It is not theoretical to the newsroom that integrated last year.

**What a "version" is here.** A path prefix (``/api/v1``) and a promise about
response *shape*. Adding an optional response field is not a new version —
consumers that ignore it keep working, and forcing a version bump for every
addition would produce a v7 nobody has migrated to. Removing a field, renaming
one, narrowing a type, or making a request parameter required *is* a new
version, and ``scripts/check_api_contract.py`` is what decides which of those
happened rather than leaving it to a reviewer's judgement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final

#: ``docs/RELEASE.md``'s published notice period for a public API version.
#: Imported by the validator below rather than restated, so the document and the
#: enforcement cannot drift the way a commented constant would.
PUBLIC_API_NOTICE_DAYS: Final = 365

#: Where a consumer is told what a deprecation means for them. A header can say
#: "this goes away on a date"; it cannot say "here is the field that replaced
#: yours", and a deprecation notice without a migration path is an eviction.
DEPRECATION_DOC_PATH: Final = "/developers#versions"


class VersionStatus(StrEnum):
    """Where a version sits on its lifecycle.

    ``PREVIEW`` exists so a version can be published for integration *without*
    inheriting the twelve-month promise. The alternative is shipping v2 straight
    to ``ACTIVE`` and discovering its shape is wrong after the compatibility
    obligation has already attached — at which point fixing it costs a v3.
    A preview version says plainly that it may change, and the contract lock
    treats it accordingly.
    """

    PREVIEW = "preview"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUNSET = "sunset"


class VersionRegistryError(RuntimeError):
    """A version declaration that would break the published promise."""


@dataclass(frozen=True, slots=True)
class ApiVersion:
    name: str
    status: VersionStatus
    released_on: date
    #: The day the deprecation was *announced*. Present iff deprecated or sunset;
    #: it is what makes the notice period a checkable interval rather than an
    #: assertion that one was given.
    deprecated_on: date | None = None
    #: The day the version stops being served. Requests after it get 410.
    sunset_on: date | None = None
    #: Which version a consumer should move to. Required for a deprecation,
    #: because "this is going away" with no successor is not a migration path.
    successor: str | None = None
    description: str = ""

    @property
    def is_served(self) -> bool:
        return self.status is not VersionStatus.SUNSET

    def is_expired(self, today: date) -> bool:
        """Past its own sunset date, whatever the registry still says.

        Checked against the date rather than only against ``status`` so a
        deployment nobody has updated stops serving an expired version on
        schedule. The promise was a date, not a promise to remember.
        """
        return self.sunset_on is not None and today >= self.sunset_on


def _validate(version: ApiVersion) -> None:
    if version.status in (VersionStatus.DEPRECATED, VersionStatus.SUNSET):
        if version.deprecated_on is None or version.sunset_on is None:
            raise VersionRegistryError(
                f"{version.name} is {version.status} but does not carry both a "
                f"deprecated_on and a sunset_on; a clock with no dates is an "
                f"announcement nobody can act on"
            )
        if version.successor is None:
            raise VersionRegistryError(
                f"{version.name} is deprecated with no successor; telling a consumer "
                f"their integration ends without telling them what replaces it is an "
                f"eviction, not a deprecation"
            )
        notice = (version.sunset_on - version.deprecated_on).days
        if notice < PUBLIC_API_NOTICE_DAYS:
            raise VersionRegistryError(
                f"{version.name} gives {notice} days of notice; docs/RELEASE.md "
                f"publishes {PUBLIC_API_NOTICE_DAYS}. §16.3's consumers are newsrooms "
                f"and civil society, not engineering organisations that can "
                f"re-integrate on a quarter's notice — shorten this and the promise "
                f"was conditional in a way it was never stated to be"
            )
    elif version.sunset_on is not None:
        raise VersionRegistryError(
            f"{version.name} is {version.status} but carries a sunset date; a version "
            f"with a removal date is deprecated by definition, and leaving the status "
            f"active means no consumer is ever told"
        )


#: Every version this build serves.
#:
#: **v2 is not a placeholder.** The Phase 4 gate is that a v1 consumer keeps
#: working after v2 ships, and that cannot be proven against a v2 which does not
#: exist — so v2 ships with a genuinely breaking change to one response shape
#: (see ``api.v2.public``) and the contract test pins v1 against it.
_VERSIONS: Final[tuple[ApiVersion, ...]] = (
    ApiVersion(
        name="v1",
        status=VersionStatus.ACTIVE,
        released_on=date(2026, 8, 17),
        description=(
            "The published surface: complaint submission and retrieval, the control "
            "plane, and the §26.4 public transparency endpoints."
        ),
    ),
    ApiVersion(
        name="v2",
        status=VersionStatus.PREVIEW,
        released_on=date(2026, 8, 17),
        description=(
            "Preview. Reshapes the public aggregate responses: counts move under a "
            "'totals' object and 'ward_id' becomes 'zone_code', which the two "
            "hierarchies of ADR-0018 make the accurate name. Breaking, which is "
            "precisely why it is a new version and why v1 is unchanged by it."
        ),
    ),
)

for _version in _VERSIONS:
    _validate(_version)

_BY_NAME: Final[dict[str, ApiVersion]] = {version.name: version for version in _VERSIONS}


def all_versions() -> tuple[ApiVersion, ...]:
    return _VERSIONS


def get_version(name: str) -> ApiVersion:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise VersionRegistryError(f"'{name}' is not a declared API version") from None


def current_version() -> ApiVersion:
    """The version a new integration should start on.

    The newest ``ACTIVE`` one, never a ``PREVIEW``: pointing new consumers at a
    version that is explicitly allowed to change would hand them the breakage the
    whole registry exists to prevent.
    """
    active = [v for v in _VERSIONS if v.status is VersionStatus.ACTIVE]
    if not active:  # pragma: no cover — the registry always has one
        raise VersionRegistryError("no active API version is declared")
    return active[-1]


def version_headers(version: ApiVersion, *, today: date | None = None) -> dict[str, str]:
    """The response headers announcing this version's position on the clock.

    ``Deprecation`` carries an HTTP-date (RFC 9745) rather than the ``true``
    token some implementations send: a consumer's tooling can compare a date
    against now and warn; it can do nothing useful with a boolean.
    """
    headers = {"X-API-Version": version.name}
    if version.deprecated_on is not None:
        headers["Deprecation"] = _http_date(version.deprecated_on)
    if version.sunset_on is not None:
        headers["Sunset"] = _http_date(version.sunset_on)
    if version.status in (VersionStatus.DEPRECATED, VersionStatus.SUNSET):
        headers["Link"] = f'<{DEPRECATION_DOC_PATH}>; rel="deprecation"; type="text/html"'
        if version.successor is not None:
            headers["Link"] += (
                f', <{DEPRECATION_DOC_PATH}#{version.successor}>; rel="successor-version"'
            )
    if version.status is VersionStatus.PREVIEW:
        # Not a standard header, and deliberately not dressed up as one. It says
        # the shape may change, which is the whole content of "preview".
        headers["X-API-Stability"] = "preview"
    _ = today
    return headers


def _http_date(day: date) -> str:
    """An IMF-fixdate at midnight UTC.

    Built by hand rather than through ``email.utils`` so the weekday and month
    names are always the C locale's, whatever the process locale happens to be —
    a Hindi-locale container emitting a Hindi weekday in an HTTP date produces a
    header no client can parse, and it would only happen in the deployment
    §5 exists to support.
    """
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    return (
        f"{weekdays[day.weekday()]}, {day.day:02d} {months[day.month - 1]} {day.year} 00:00:00 GMT"
    )


def sunset_notice_remaining(version: ApiVersion, today: date) -> int | None:
    """Days left before removal, or ``None`` if no removal is scheduled.

    Negative when a version is past its date and still being served, which is a
    condition the discovery endpoint surfaces rather than hides — an expired
    version still answering requests is a broken promise in the *other*
    direction, and consumers who did migrate deserve to know the old one is
    still up.
    """
    if version.sunset_on is None:
        return None
    return (version.sunset_on - today).days


def next_sunset_date(deprecated_on: date) -> date:
    """The earliest sunset a deprecation announced today may declare."""
    return deprecated_on + timedelta(days=PUBLIC_API_NOTICE_DAYS)
