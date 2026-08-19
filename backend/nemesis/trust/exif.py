"""§11.1's EXIF/GPS cross-check — extraction, and the arithmetic over it.

**Split into two halves that never touch, and the split is the point.**
``extract`` reads bytes and needs Pillow; ``cross_check`` is pure arithmetic
over two coordinates and a radius. The §11.1 rule that absent EXIF *reduces
trust rather than rejecting* is a statement about the second half, and a
statement in that shape is only defensible if it can be tested exhaustively —
which it can be here, because it takes floats and returns a finding.

**Why not ``exifread``.** It was in the dependency list and is gone. It would
have lived in the ``ml`` extra, which would have put the §11.1 check in an image
the test suite does not run in — so the check with a fraud obligation attached
would have been the one with no unit tests. Pillow already parses the GPS IFD;
what was missing was the four-tag conversion below, which is twenty lines and is
now covered by property-based tests against known vectors.

**Three outcomes, never two.** ``PRESENT_MATCHED``, ``PRESENT_MISMATCHED`` and
``ABSENT`` are different facts with different consequences, and the schema, the
policy document and this module all keep them apart. Collapsing "no metadata"
into "mismatch" is exactly how §11.1's edge-case paragraph — WhatsApp and share
flows strip EXIF by default — turns into a system that penalises the most common
honest submission path.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from nemesis.observability.logging import get_logger

log = get_logger(__name__)

#: Mean Earth radius, metres (IUGG). The haversine below is accurate to about
#: 0.5% at any distance, which is three orders of magnitude better than the
#: precision of the comparison it feeds — a 200 metre threshold against a
#: consumer GPS fix that is itself ±10 metres on a good day.
EARTH_RADIUS_METERS: Final = 6_371_008.8

#: EXIF IFD pointer for the GPS block. Pillow exposes it by this tag number and
#: not by name, which is the one thing about ``getexif`` that is easy to get
#: wrong: ``getexif()[0x8825]`` returns an *offset*, and only ``get_ifd``
#: follows it.
_GPS_IFD: Final = 0x8825

_GPS_LATITUDE_REF: Final = 1
_GPS_LATITUDE: Final = 2
_GPS_LONGITUDE_REF: Final = 3
_GPS_LONGITUDE: Final = 4

#: ``DateTimeOriginal`` — when the shutter fired, as opposed to ``DateTime``,
#: which many editors rewrite on save. The §11.1 re-upload check wants the
#: former: a screenshot of last year's photograph carries this year's file dates
#: and last year's capture date, and only one of the two is evidence.
_DATETIME_ORIGINAL: Final = 0x9003
_EXIF_IFD: Final = 0x8769

#: EXIF's own format, which is not ISO 8601 and has no timezone. Parsed as UTC
#: and *documented as an assumption* rather than silently localised: the tag has
#: no offset, guessing the tenant's timezone would make the value depend on
#: configuration, and the only consumer is an age-in-hours comparison whose
#: threshold is measured in days.
_EXIF_DATETIME_FORMAT: Final = "%Y:%m:%d %H:%M:%S"


class ExifOutcome(StrEnum):
    """What the cross-check concluded. Three values, never two — see the module docstring."""

    #: EXIF GPS present, within the policy radius of the claimed location.
    PRESENT_MATCHED = "present_matched"
    #: EXIF GPS present, outside it. The only outcome that is a *contradiction*.
    PRESENT_MISMATCHED = "present_mismatched"
    #: No EXIF, or EXIF with no GPS block. §11.1's common case.
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class ExifData:
    """What was read out of the file. Every field optional, because every field is."""

    present: bool
    latitude: float | None = None
    longitude: float | None = None
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExifFinding:
    """The cross-check's conclusion, ready to become an ``exif_check_completed``.

    ``distance_meters`` stays ``None`` for ``ABSENT``. ``ExifCheckCompletedV1``
    already argues this: a default of zero would read downstream as "EXIF
    confirmed the location", which is the opposite of what happened.
    """

    outcome: ExifOutcome
    trust_delta: float
    distance_meters: float | None
    reason: str

    @property
    def present(self) -> bool:
        return self.outcome is not ExifOutcome.ABSENT

    @property
    def is_mismatch(self) -> bool:
        return self.outcome is ExifOutcome.PRESENT_MISMATCHED


def haversine_meters(*, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in metres.

    Haversine rather than a planar approximation. The planar shortcut is faster
    and is wrong in a way that matters here: it needs a cosine of latitude to
    scale longitude, and the version that omits it — the one people actually
    write — under-reports east-west distance by 15% at Pune's latitude and by
    50% at Oslo's. A fraud check whose threshold silently doubles depending on
    which city runs it is not a check.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    # `min(1.0, ...)` guards the arcsine: for two identical points floating
    # point can produce a value a few ulps above 1.0, and `asin` raises a
    # domain error rather than returning zero. The failing case is a photograph
    # taken exactly where it was reported, which is the *best* possible input.
    return 2.0 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(a)))


def cross_check(
    data: ExifData,
    *,
    claimed_latitude: float,
    claimed_longitude: float,
    mismatch_distance_meters: float,
    matched_trust_delta: float,
    mismatch_trust_delta: float,
    absent_trust_delta: float,
) -> ExifFinding:
    """§11.1, as a total function of the metadata and the tenant's thresholds.

    Every threshold is a parameter rather than a read of the policy document,
    for the reason ``simulation.engine.decide`` gives about itself: a function
    that takes its configuration by value cannot disagree with itself between
    two runs, and it can be tested against the boundary without a database.

    The comparison is ``>`` and not ``>=``: a photograph at exactly the
    threshold distance is inside it. The threshold is where a tenant declares
    the *edge of acceptable*, and treating the edge as a violation makes the
    configured number mean one metre less than it says.
    """
    if not data.present or data.latitude is None or data.longitude is None:
        return ExifFinding(
            outcome=ExifOutcome.ABSENT,
            trust_delta=absent_trust_delta,
            distance_meters=None,
            reason=(
                "no EXIF GPS on the upload. §11.1 treats this as reduced trust rather "
                "than a rejection: share flows strip metadata by default, so absence is "
                "weak evidence about the submitter and strong evidence about the app "
                "they used."
            ),
        )

    distance = haversine_meters(
        lat1=claimed_latitude,
        lon1=claimed_longitude,
        lat2=data.latitude,
        lon2=data.longitude,
    )
    if distance > mismatch_distance_meters:
        return ExifFinding(
            outcome=ExifOutcome.PRESENT_MISMATCHED,
            trust_delta=mismatch_trust_delta,
            distance_meters=distance,
            reason=(
                f"the photograph's own GPS is {distance:.0f} m from the reported "
                f"location, beyond the {mismatch_distance_meters:.0f} m the tenant "
                f"allows. Unlike absent metadata this is a contradiction, not a silence."
            ),
        )
    return ExifFinding(
        outcome=ExifOutcome.PRESENT_MATCHED,
        trust_delta=matched_trust_delta,
        distance_meters=distance,
        reason=(
            f"the photograph's own GPS is {distance:.0f} m from the reported location, "
            f"within the {mismatch_distance_meters:.0f} m the tenant allows."
        ),
    )


def extract(source: bytes) -> ExifData:
    """Read the GPS block and capture time, or report their absence.

    **Never raises for a file with no metadata, malformed metadata, or metadata
    in an encoding nobody expected.** That is the whole contract: this runs on
    submitter-controlled bytes, and an exception here would fail the trust stage
    for a photograph whose only crime is having been through a share flow — the
    §11.1 case that must reduce trust rather than reject. Anything unreadable is
    reported as absent, which is the honest description of what was learned.
    """
    from PIL import Image  # a decode dependency, not an import-time one

    try:
        with Image.open(io.BytesIO(source)) as image:
            exif = image.getexif()
            if not exif:
                return ExifData(present=False)
            gps = exif.get_ifd(_GPS_IFD)
            captured = _captured_at(exif)
            if not gps:
                # A real and distinct state: the camera wrote EXIF and had
                # location services off. Reported as ``present=True`` with no
                # coordinates so the cross-check lands on ABSENT for the GPS
                # question while the capture time is still usable.
                return ExifData(present=True, captured_at=captured)
            latitude = _coordinate(gps.get(_GPS_LATITUDE), gps.get(_GPS_LATITUDE_REF), "NS")
            longitude = _coordinate(gps.get(_GPS_LONGITUDE), gps.get(_GPS_LONGITUDE_REF), "EW")
            if latitude is None or longitude is None:
                return ExifData(present=True, captured_at=captured)
            return ExifData(
                present=True, latitude=latitude, longitude=longitude, captured_at=captured
            )
    except Exception as exc:
        # Broad, and logged rather than swallowed — the distinction Phase 7's
        # third defect turned into a rule. Pillow raises at least five unrelated
        # types for malformed metadata across formats, and enumerating them
        # would be a list that goes stale with the next release while the
        # behaviour it protects ("absent, not failed") never changes.
        log.info(
            "exif_unreadable",
            error_type=type(exc).__name__,
            note="treated as absent, which reduces trust rather than rejecting (§11.1)",
        )
        return ExifData(present=False)


def _coordinate(value: Any, ref: Any, valid_refs: str) -> float | None:
    """Degrees/minutes/seconds plus a hemisphere letter, as a signed decimal.

    Returns ``None`` rather than raising on anything unexpected — a two-element
    tuple, a ref of ``b"N"`` instead of ``"N"``, a rational with a zero
    denominator. Every one of those appears in the wild, and none of them is a
    reason to fail a citizen's submission.
    """
    if value is None or ref is None:
        return None
    try:
        degrees, minutes, seconds = (float(part) for part in tuple(value)[:3])
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    hemisphere = ref.decode("ascii", "ignore") if isinstance(ref, bytes) else str(ref)
    hemisphere = hemisphere.strip().upper()[:1]
    if hemisphere not in valid_refs:
        return None

    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if hemisphere in "SW":
        decimal = -decimal
    # Out-of-range values are rejected rather than clamped: a latitude of 95 is
    # a corrupt tag, and clamping it to 90 would place the photograph at the
    # pole and report a confident 9,000 km mismatch.
    limit = 90.0 if valid_refs == "NS" else 180.0
    if not -limit <= decimal <= limit:
        return None
    return decimal


def _captured_at(exif: Any) -> datetime | None:
    """``DateTimeOriginal``, as an aware UTC datetime, or ``None``.

    See ``_EXIF_DATETIME_FORMAT`` on why UTC is an assumption rather than a
    lookup. The alternative — a naive datetime — is forbidden by this codebase's
    own standard, and rightly: it would flow into an age comparison and be
    interpreted as UTC anyway, just without anyone having decided so.
    """
    raw = None
    try:
        ifd = exif.get_ifd(_EXIF_IFD)
        raw = ifd.get(_DATETIME_ORIGINAL) if ifd else None
    except Exception:  # pragma: no cover — Pillow raises for malformed offsets
        raw = None
    if raw is None:
        raw = exif.get(_DATETIME_ORIGINAL)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip(), _EXIF_DATETIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


__all__ = [
    "EARTH_RADIUS_METERS",
    "ExifData",
    "ExifFinding",
    "ExifOutcome",
    "cross_check",
    "extract",
    "haversine_meters",
]
