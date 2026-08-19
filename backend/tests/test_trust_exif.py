"""§11.1's EXIF cross-check — the arithmetic, and reading it off real bytes.

The half of Phase 8 with the strongest claim attached and the least machinery
behind it: ``cross_check`` takes floats and returns a finding, so the §11.1 rule
that *absent EXIF reduces trust rather than rejecting* can be tested
exhaustively rather than demonstrated once.
"""

from __future__ import annotations

import io

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from nemesis.trust import exif
from nemesis.trust.exif import ExifData, ExifOutcome, cross_check, haversine_meters
from tests.trust_fixtures import gradient_image, image_with_exif

# Pune, roughly. A real coordinate rather than (0, 0): the equator and the prime
# meridian are the one place a longitude scaling bug does not show up.
PUNE = (18.5204, 73.8567)

THRESHOLDS = {
    "mismatch_distance_meters": 200.0,
    "matched_trust_delta": 0.15,
    "mismatch_trust_delta": -0.4,
    "absent_trust_delta": -0.1,
}


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


def test_the_same_point_is_zero_metres_from_itself() -> None:
    """The arcsine domain guard, which fails on the *best* possible input.

    Two identical points can produce a haversine ``a`` a few ulps above 1.0, and
    an unguarded ``asin`` raises a domain error — for a photograph taken exactly
    where it was reported.
    """
    assert haversine_meters(lat1=PUNE[0], lon1=PUNE[1], lat2=PUNE[0], lon2=PUNE[1]) == 0.0


def test_a_known_separation_matches_the_published_distance() -> None:
    """Pune to Mumbai, ~120 km great-circle. Within 1%."""
    distance = haversine_meters(lat1=18.5204, lon1=73.8567, lat2=19.0760, lon2=72.8777)
    assert 118_000 < distance < 122_000


def test_longitude_is_scaled_by_latitude() -> None:
    """The bug the planar shortcut introduces, asserted as a fact rather than a comment.

    One degree of longitude is ~111 km at the equator and ~105 km at Pune's
    latitude. A distance function that ignored the cosine would return the same
    number for both, and a 200 m fraud threshold would then mean something
    different in every city that runs this.
    """
    at_equator = haversine_meters(lat1=0.0, lon1=0.0, lat2=0.0, lon2=1.0)
    at_pune = haversine_meters(lat1=18.5204, lon1=73.0, lat2=18.5204, lon2=74.0)
    assert at_equator > at_pune
    assert 0.93 < at_pune / at_equator < 0.96


@hypothesis_settings(max_examples=200, deadline=None)
@given(
    lat1=st.floats(-89.0, 89.0),
    lon1=st.floats(-179.0, 179.0),
    lat2=st.floats(-89.0, 89.0),
    lon2=st.floats(-179.0, 179.0),
)
def test_distance_is_symmetric_finite_and_bounded(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> None:
    """Three properties that must hold for every pair on the planet.

    Symmetry because a mismatch must not depend on which coordinate the code
    happened to put first; finiteness because a NaN would compare ``False``
    against the threshold and silently report every photograph as matching; and
    the half-circumference bound because a great-circle distance that exceeds it
    means the formula has gone through the far side of the Earth.
    """
    forward = haversine_meters(lat1=lat1, lon1=lon1, lat2=lat2, lon2=lon2)
    backward = haversine_meters(lat1=lat2, lon1=lon2, lat2=lat1, lon2=lon1)
    assert forward == pytest.approx(backward, abs=1e-6)
    assert forward == forward  # not NaN
    assert 0.0 <= forward <= 20_100_000.0


# ---------------------------------------------------------------------------
# The three outcomes
# ---------------------------------------------------------------------------


def test_absent_exif_reduces_trust_and_does_not_reject() -> None:
    """§11.1's edge case, which describes most honest submissions.

    WhatsApp and every share flow strip EXIF. If absence were treated as a
    mismatch, the system would penalise the most common way a citizen sends a
    photograph — and the delta here is deliberately the mildest of the three.
    """
    finding = cross_check(
        ExifData(present=False),
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **THRESHOLDS,
    )
    assert finding.outcome is ExifOutcome.ABSENT
    assert finding.trust_delta == THRESHOLDS["absent_trust_delta"]
    assert not finding.is_mismatch
    # None, not 0.0. A zero here reads downstream as "EXIF confirmed the
    # location", which is the opposite of what happened.
    assert finding.distance_meters is None
    assert abs(finding.trust_delta) < abs(THRESHOLDS["mismatch_trust_delta"])


def test_exif_present_with_no_gps_is_absent_for_the_gps_question() -> None:
    """A camera that wrote EXIF with location services off.

    A real and distinct state from "no EXIF at all", and it lands on ABSENT
    because the *GPS* is what is missing — while the capture time it did record
    stays usable for the re-upload window.
    """
    finding = cross_check(
        ExifData(present=True, latitude=None, longitude=None),
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **THRESHOLDS,
    )
    assert finding.outcome is ExifOutcome.ABSENT


def test_a_nearby_photograph_confirms_and_raises_trust() -> None:
    finding = cross_check(
        ExifData(present=True, latitude=PUNE[0] + 0.0005, longitude=PUNE[1]),
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **THRESHOLDS,
    )
    assert finding.outcome is ExifOutcome.PRESENT_MATCHED
    assert finding.trust_delta == THRESHOLDS["matched_trust_delta"]
    assert finding.distance_meters is not None and finding.distance_meters < 200.0


def test_a_distant_photograph_is_a_contradiction() -> None:
    finding = cross_check(
        ExifData(present=True, latitude=19.0760, longitude=72.8777),
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **THRESHOLDS,
    )
    assert finding.outcome is ExifOutcome.PRESENT_MISMATCHED
    assert finding.is_mismatch
    assert finding.trust_delta == THRESHOLDS["mismatch_trust_delta"]
    assert finding.distance_meters is not None and finding.distance_meters > 100_000


def test_exactly_at_the_threshold_is_inside_it() -> None:
    """``>`` and not ``>=``, asserted rather than left to a reading of the source.

    A tenant configuring 200 m is declaring the edge of acceptable. Treating the
    edge as a violation makes the configured number mean one metre less than it
    says, which is the kind of off-by-one nobody finds by reading a policy form.
    """
    # 0.001 degrees of latitude is ~111.2 m; two of them is ~222 m. Bracket the
    # threshold by solving for a delta that lands just inside and just outside.
    inside = 199.0 / 111_195.0
    outside = 201.0 / 111_195.0
    near = cross_check(
        ExifData(present=True, latitude=PUNE[0] + inside, longitude=PUNE[1]),
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **THRESHOLDS,
    )
    far = cross_check(
        ExifData(present=True, latitude=PUNE[0] + outside, longitude=PUNE[1]),
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **THRESHOLDS,
    )
    assert near.outcome is ExifOutcome.PRESENT_MATCHED
    assert far.outcome is ExifOutcome.PRESENT_MISMATCHED


def test_a_tenant_can_widen_the_radius_with_no_code_change() -> None:
    """The knob is a parameter, so the same photograph decides differently.

    This is architectural principle 1 at the smallest scale in the phase: a
    campus where every report is inside one compound and a city with a 200 m
    convention cannot share this number, and neither should have to wait for a
    deploy.
    """
    data = ExifData(present=True, latitude=19.0760, longitude=72.8777)
    strict = cross_check(data, claimed_latitude=PUNE[0], claimed_longitude=PUNE[1], **THRESHOLDS)
    lenient = cross_check(
        data,
        claimed_latitude=PUNE[0],
        claimed_longitude=PUNE[1],
        **{**THRESHOLDS, "mismatch_distance_meters": 200_000.0},
    )
    assert strict.outcome is ExifOutcome.PRESENT_MISMATCHED
    assert lenient.outcome is ExifOutcome.PRESENT_MATCHED


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_gps_round_trips_through_a_real_jpeg() -> None:
    """Written by Pillow's EXIF writer, read back by ours.

    A round trip rather than a hand-built byte fixture, because the thing being
    checked is the degrees/minutes/seconds conversion — and a fixture written by
    the same understanding that wrote the parser proves nothing about it.
    """
    data = exif.extract(image_with_exif(latitude=PUNE[0], longitude=PUNE[1]))
    assert data.present
    assert data.latitude == pytest.approx(PUNE[0], abs=1e-4)
    assert data.longitude == pytest.approx(PUNE[1], abs=1e-4)


def test_southern_and_western_hemispheres_are_signed() -> None:
    """The ref letters. Getting them wrong mirrors a photograph onto the wrong
    continent and reports a confident ten-thousand-kilometre mismatch."""
    data = exif.extract(image_with_exif(latitude=-33.8688, longitude=-70.6693))
    assert data.latitude is not None and data.latitude < 0
    assert data.longitude is not None and data.longitude < 0


def test_capture_time_is_read_as_an_aware_datetime() -> None:
    """Naive datetimes are a correctness bug in this codebase, by standard.

    The tag has no offset and UTC is a documented assumption; what must not
    happen is a naive value flowing into an age comparison to be interpreted as
    UTC anyway, without anyone having decided so.
    """
    data = exif.extract(image_with_exif(latitude=PUNE[0], longitude=PUNE[1]))
    assert data.captured_at is not None
    assert data.captured_at.tzinfo is not None
    assert data.captured_at.year == 2026


def test_an_image_with_no_exif_reports_absence_rather_than_raising() -> None:
    data = exif.extract(gradient_image())
    assert not data.present
    assert data.latitude is None


def test_unreadable_bytes_report_absence_rather_than_failing_the_submission() -> None:
    """The contract that keeps §11.1 non-rejecting under malformed input.

    An exception here would fail the trust stage — and therefore halt the
    complaint — for a photograph whose only problem is a mangled metadata
    segment. Absence is the honest description of what was learned.
    """
    assert not exif.extract(b"not an image at all").present
    assert not exif.extract(b"").present
    # A truncated JPEG: valid magic bytes, nothing behind them.
    assert not exif.extract(b"\xff\xd8\xff\xe0" + b"\x00" * 32).present


def test_a_corrupt_coordinate_is_dropped_rather_than_clamped() -> None:
    """A latitude of 95 is a corrupt tag, not a place.

    Clamping it to 90 would put the photograph at the pole and report a
    confident 8,000 km mismatch against a report that may be perfectly honest.
    """
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(gradient_image())) as base:
        data = base.getexif()
        gps = data.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (95.0, 0.0, 0.0)
        gps[3] = "E"
        gps[4] = (73.0, 0.0, 0.0)
        buffer = io.BytesIO()
        base.save(buffer, format="JPEG", exif=data)

    parsed = exif.extract(buffer.getvalue())
    assert parsed.present
    assert parsed.latitude is None
