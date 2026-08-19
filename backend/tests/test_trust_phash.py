"""§11.1 perceptual hashing — the claim, and the search that uses it.

§11.1's claim is specific: the hash *"catches re-uploaded/screenshotted images
even after compression or resize"*. That is a testable statement about
robustness, and it is what this file spends most of its assertions on — the
interesting failure is not a wrong bit count, it is a hash that survives nothing
and therefore never matches a real re-upload.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.db.models.trust import SubmissionMedia
from nemesis.tenancy.context import tenant_scope
from nemesis.trust import phash
from nemesis.trust.phash import HASH_BITS, find_duplicates, hamming, perceptual_hash
from tests.conftest import postgres_required
from tests.test_trust_review import make_complaint
from tests.trust_fixtures import gradient_image, noisy_patch_image

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _photograph(seed: int, *, width: int = 96, height: int = 72) -> bytes:
    """A structured image whose content depends on ``seed``.

    Structured rather than random: dHash encodes horizontal gradients, so an
    image with no horizontal structure hashes to the same value whatever the
    seed, and a test built on one would prove that unrelated images collide
    rather than that the hash works.
    """
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                (x * (3 + seed) + y) % 256,
                (y * (5 + seed * 2)) % 256,
                (x * y * (1 + seed)) % 256,
            )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _recompress(data: bytes, quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _resize(data: bytes, factor: float) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        resized = image.resize(
            (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
        )
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The hash
# ---------------------------------------------------------------------------


def test_the_hash_is_deterministic() -> None:
    source = _photograph(1)
    assert perceptual_hash(source) == perceptual_hash(source)


def test_the_hash_is_sixty_four_bits() -> None:
    value = perceptual_hash(_photograph(1))
    assert value is not None
    assert 0 <= value < (1 << HASH_BITS)


def test_a_recompressed_image_stays_within_the_default_tolerance() -> None:
    """§11.1's claim about compression, as a number rather than a hope.

    Quality 40 is well below anything a phone or a share flow produces, so the
    real-world case is comfortably inside whatever margin this leaves.
    """
    original = perceptual_hash(_photograph(1))
    compressed = perceptual_hash(_recompress(_photograph(1), quality=40))
    assert original is not None and compressed is not None
    assert hamming(original, compressed) <= 8


def test_a_resized_image_stays_within_the_default_tolerance() -> None:
    """§11.1's claim about resizing. Half size and double size, both directions."""
    original = perceptual_hash(_photograph(2))
    assert original is not None
    for factor in (0.5, 2.0):
        scaled = perceptual_hash(_resize(_photograph(2), factor))
        assert scaled is not None
        assert hamming(original, scaled) <= 8, f"resize x{factor} moved the hash too far"


def test_a_brightness_shift_barely_moves_the_hash() -> None:
    """Why dHash and not aHash, asserted rather than argued — and its limit.

    dHash compares each pixel to its right-hand neighbour, so it encodes the
    *sign of a gradient*, which no monotonic brightness scaling can change. An
    average-hash thresholds against the mean and moves substantially here, which
    is the case of a screenshot taken at a different screen brightness.

    The invariance ends at **clipping**, and the fixture is built to stay below
    it: scaling an image that already contains 255 pushes whole regions flat,
    and a flat region has no gradient sign to preserve. That is a real limit of
    the technique rather than a weakness of this implementation, and it is
    stated here because a test that quietly used a 1.05x nudge would imply an
    invariance stronger than the one that exists.
    """
    from PIL import ImageEnhance

    # Channel values capped near 180, so a 1.3x scale lands under 255.
    source = io.BytesIO()
    dim = Image.new("RGB", (96, 72))
    pixels = dim.load()
    assert pixels is not None
    for y in range(72):
        for x in range(96):
            pixels[x, y] = ((x * 3) % 180, (y * 2) % 180, (x + y) % 180)
    dim.save(source, format="PNG")

    brighter_buffer = io.BytesIO()
    ImageEnhance.Brightness(dim).enhance(1.3).save(brighter_buffer, format="PNG")
    dim.close()

    original = perceptual_hash(source.getvalue())
    brighter = perceptual_hash(brighter_buffer.getvalue())
    assert original is not None and brighter is not None
    assert hamming(original, brighter) == 0


def test_unrelated_images_are_far_apart() -> None:
    """The other half of a useful hash: it must also *not* match.

    A hash tolerant enough to survive everything is one that matches everything,
    at which point every submission is a duplicate of every other and the review
    queue is useless.
    """
    first = perceptual_hash(_photograph(1))
    second = perceptual_hash(_photograph(7))
    assert first is not None and second is not None
    assert hamming(first, second) > 8


def test_undecodable_bytes_skip_the_check_rather_than_failing_the_report() -> None:
    """Losing the duplicate check for one submission is not a reason to halt a
    citizen's report — which is why this returns ``None`` rather than raising."""
    assert perceptual_hash(b"not an image") is None
    assert perceptual_hash(b"") is None


def test_hamming_is_symmetric_and_zero_on_identity() -> None:
    first = perceptual_hash(_photograph(4))
    assert first is not None
    assert hamming(first, first) == 0
    second = perceptual_hash(_photograph(5))
    assert second is not None
    assert hamming(first, second) == hamming(second, first)


@pytest.mark.parametrize("value", [0, 1, (1 << 63), (1 << 64) - 1, 0xDEADBEEFCAFEBABE])
def test_the_signed_round_trip_is_lossless(value: int) -> None:
    """Postgres has no unsigned type, so the hash is stored two's-complement.

    A round trip that lost the top bit would silently halve the hash space and
    make every image whose 64th bit is set collide with its own complement.
    """
    assert phash.from_signed(phash.to_signed(value)) == value


def test_the_signed_form_fits_a_bigint() -> None:
    """The column is BIGINT; a value outside its range fails at insert time,
    which is a runtime error on the submission path rather than a type error."""
    for value in (0, (1 << 64) - 1, 1 << 63):
        stored = phash.to_signed(value)
        assert -(2**63) <= stored < 2**63


def test_a_flat_image_and_a_gradient_do_not_collide() -> None:
    """The ``>`` versus ``>=`` choice, at the one input where it is visible.

    A flat region produces equal neighbours everywhere. ``>`` sets those bits to
    zero and ``>=`` sets them to one; either is arbitrary, but the choice must
    be the same for the original and the re-upload — and it must not make every
    overexposed sky hash identically to every whitewashed wall.
    """
    flat = io.BytesIO()
    Image.new("RGB", (96, 72), (200, 200, 200)).save(flat, format="PNG")
    flat_hash = perceptual_hash(flat.getvalue())
    gradient_hash = perceptual_hash(gradient_image(96, 72))
    assert flat_hash is not None and gradient_hash is not None
    assert flat_hash == 0
    assert hamming(flat_hash, gradient_hash) > 8


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------

#: The database half of this file. Applied per test rather than at module
#: level, because the hash itself is pure and must stay runnable with no
#: Postgres — losing the §11.1 robustness assertions to an unrelated missing
#: dependency is exactly the outcome the split exists to prevent.


async def _insert(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    image_hash: int,
    captured_at: datetime,
    sha: str,
) -> None:
    session.add(
        SubmissionMedia(
            tenant_id=tenant_id,
            complaint_id=complaint_id,
            kind="image",
            content_type="image/jpeg",
            quarantine_uri=f"nemesis+quarantine://{sha[:2]}/{sha}.jpg",
            quarantine_sha256=sha,
            perceptual_hash=phash.to_signed(image_hash),
            captured_or_reported_at=captured_at,
            purge_raw_after=captured_at + timedelta(days=30),
            purge_exif_after=captured_at + timedelta(days=90),
        )
    )
    await session.flush()


@postgres_required
@pytest.mark.integration
async def test_the_search_finds_a_near_duplicate_and_ignores_a_distant_one(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    with tenant_scope(tenant_id):
        async with maker() as session:
            earlier = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            distant = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE + timedelta(hours=1)
            )
            current = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE + timedelta(hours=2)
            )

            base_hash = perceptual_hash(_photograph(1))
            assert base_hash is not None
            # Two bits flipped: a re-upload after compression, well inside the
            # default tolerance of eight.
            near_hash = base_hash ^ 0b11
            far_hash = base_hash ^ ((1 << 40) - 1)

            await _insert(
                session,
                tenant_id=tenant_id,
                complaint_id=earlier,
                image_hash=near_hash,
                captured_at=BASE,
                sha="a" * 64,
            )
            await _insert(
                session,
                tenant_id=tenant_id,
                complaint_id=distant,
                image_hash=far_hash,
                captured_at=BASE + timedelta(hours=1),
                sha="b" * 64,
            )
            await session.commit()

            matches = await find_duplicates(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                image_hash=base_hash,
                captured_at=BASE + timedelta(hours=2),
                max_distance=8,
                lookback_hours=720,
            )

    assert [match.media_sha256 for match in matches] == ["a" * 64]
    assert matches[0].hamming_distance == 2
    assert matches[0].age_hours == pytest.approx(2.0)


@postgres_required
@pytest.mark.integration
async def test_the_search_never_crosses_a_tenant_boundary(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The subtlest form the isolation failure takes.

    A cross-tenant match would leak the *existence* of another customer's
    submission through a similarity score — no row is returned to the caller,
    but the flag on their complaint says somebody, somewhere, sent this picture.
    """
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    base_hash = perceptual_hash(_photograph(1))
    assert base_hash is not None

    with tenant_scope(other_tenant_id):
        async with maker() as session:
            theirs = await make_complaint(session, tenant_id=other_tenant_id, reported_at=BASE)
            await _insert(
                session,
                tenant_id=other_tenant_id,
                complaint_id=theirs,
                image_hash=base_hash,
                captured_at=BASE,
                sha="c" * 64,
            )
            await session.commit()

    with tenant_scope(tenant_id):
        async with maker() as session:
            mine = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE + timedelta(hours=1)
            )
            await session.commit()
            matches = await find_duplicates(
                session,
                tenant_id=tenant_id,
                complaint_id=mine,
                image_hash=base_hash,
                captured_at=BASE + timedelta(hours=1),
                max_distance=8,
                lookback_hours=720,
            )
    assert matches == ()


@postgres_required
@pytest.mark.integration
async def test_the_lookback_window_bounds_the_search(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """An identical image outside the window is not a match.

    The window is what keeps the scan bounded as a deployment ages, and a search
    that ignored it would get slower every day the system stayed up.
    """
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    base_hash = perceptual_hash(_photograph(6))
    assert base_hash is not None
    old = BASE - timedelta(days=400)

    with tenant_scope(tenant_id):
        async with maker() as session:
            ancient = await make_complaint(session, tenant_id=tenant_id, reported_at=old)
            current = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            await _insert(
                session,
                tenant_id=tenant_id,
                complaint_id=ancient,
                image_hash=base_hash,
                captured_at=old,
                sha="d" * 64,
            )
            await session.commit()

            inside = await find_duplicates(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                image_hash=base_hash,
                captured_at=BASE,
                max_distance=8,
                lookback_hours=24 * 500,
            )
            outside = await find_duplicates(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                image_hash=base_hash,
                captured_at=BASE,
                max_distance=8,
                lookback_hours=720,
            )

    assert len(inside) == 1
    assert outside == ()


@postgres_required
@pytest.mark.integration
async def test_a_complaint_is_never_its_own_duplicate(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A redelivered stage, or a report carrying the same photograph twice,
    would otherwise flag every submission as a duplicate of itself."""
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    base_hash = perceptual_hash(_photograph(8))
    assert base_hash is not None

    with tenant_scope(tenant_id):
        async with maker() as session:
            complaint = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            await _insert(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint,
                image_hash=base_hash,
                captured_at=BASE,
                sha="e" * 64,
            )
            await session.commit()
            matches = await find_duplicates(
                session,
                tenant_id=tenant_id,
                complaint_id=complaint,
                image_hash=base_hash,
                captured_at=BASE,
                max_distance=8,
                lookback_hours=720,
            )
    assert matches == ()


@postgres_required
@pytest.mark.integration
async def test_matches_are_ordered_closest_first(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The closest match is the one a reviewer should see, and the event records
    only that one — so an unordered result would put an arbitrary match on the
    chain."""
    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    base_hash = perceptual_hash(_photograph(9))
    assert base_hash is not None

    with tenant_scope(tenant_id):
        async with maker() as session:
            far = await make_complaint(session, tenant_id=tenant_id, reported_at=BASE)
            near = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE + timedelta(minutes=5)
            )
            current = await make_complaint(
                session, tenant_id=tenant_id, reported_at=BASE + timedelta(minutes=10)
            )
            await _insert(
                session,
                tenant_id=tenant_id,
                complaint_id=far,
                image_hash=base_hash ^ 0b1111,
                captured_at=BASE,
                sha="1" * 64,
            )
            await _insert(
                session,
                tenant_id=tenant_id,
                complaint_id=near,
                image_hash=base_hash ^ 0b1,
                captured_at=BASE + timedelta(minutes=5),
                sha="2" * 64,
            )
            await session.commit()
            matches = await find_duplicates(
                session,
                tenant_id=tenant_id,
                complaint_id=current,
                image_hash=base_hash,
                captured_at=BASE + timedelta(minutes=10),
                max_distance=8,
                lookback_hours=720,
            )

    assert [match.hamming_distance for match in matches] == [1, 4]


def test_the_fixture_images_are_not_accidentally_identical() -> None:
    """Guards the file above from becoming vacuous.

    Every robustness assertion here compares two hashes. If ``_photograph``
    ever stopped depending on its seed, all of them would pass trivially.
    """
    hashes = {perceptual_hash(_photograph(seed)) for seed in range(6)}
    assert len(hashes) == 6
    assert perceptual_hash(noisy_patch_image()) not in hashes
