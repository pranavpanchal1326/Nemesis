"""§11.1 perceptual hashing — the re-upload check, and its search.

**dHash, not aHash and not pHash.** The three differ in what they survive:

* *aHash* (mean threshold) is trivial to implement and collapses under a
  brightness or contrast change, which is the first thing any share flow or
  screenshot does.
* *pHash* (DCT of a 32x32 grey image) is the most robust and needs a discrete
  cosine transform. Without SciPy that is a hand-rolled DCT in the hot path of
  every submission, and a hand-rolled DCT is exactly the kind of code that is
  subtly wrong for years.
* *dHash* compares each pixel to its right-hand neighbour, so it encodes
  **gradients**. Gradients are invariant to any monotonic brightness or contrast
  change by construction, and the downscale to 9x8 destroys the detail that
  JPEG re-compression and resizing alter. That is precisely §11.1's claim —
  "catches re-uploaded/screenshotted images even after compression or resize" —
  and it is twenty lines with no transform in it.

**Why the hash is computed from the original bytes, before redaction.** A
redacted copy differs from its source wherever a face was blurred, and two
submissions of the same photograph would produce hashes that differ by however
much of the frame was faces. The fraud question is *"was this file sent
before"*, which is a question about the file that was sent.

**Why the search is a scan and not an index.** Hamming distance has no B-tree
ordering, and the extensions that would index it (``bktree``, ``pg_similarity``)
are not in this deployment. What bounds the cost instead is the query's own
shape: one tenant, one time window from the policy, and a partial index over
rows that have a hash at all. At a city's submission rate that is thousands of
rows, and ``bit_count(a # b)`` is a single instruction per row. It is stated
here rather than discovered later, because the honest fix at ten million rows is
an inverted index over hash *bands*, and that is a Phase 23 shape.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import BIT
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from nemesis.db.models.trust import SubmissionMedia
from nemesis.observability.logging import get_logger

log = get_logger(__name__)

#: Hash side. 8 gives 8 rows of 8 comparisons = 64 bits, which is why the
#: resize target is 9 wide: each row of 9 pixels yields 8 gradient bits.
HASH_SIDE: Final = 8
HASH_BITS: Final = HASH_SIDE * HASH_SIDE

#: The largest value ``perceptual_hash`` can return, as an unsigned 64-bit
#: integer. Postgres has no unsigned type, so ``SubmissionMedia.perceptual_hash``
#: is a signed ``BIGINT`` and the conversion happens in ``to_signed`` /
#: ``from_signed`` — in one place, because a two's-complement round trip applied
#: in two places is applied inconsistently in one of them.
_UNSIGNED_MASK: Final = (1 << HASH_BITS) - 1
_SIGN_BIT: Final = 1 << (HASH_BITS - 1)


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """A prior submission whose image is within the tenant's tolerance.

    ``age_hours`` is computed against the two artefacts' *capture* times, not
    their submission times — see ``SubmissionMedia.captured_or_reported_at``. A
    re-upload of a photograph taken a year ago is the case §11.1 is looking for,
    and ordering on submission time would put it inside every window.
    """

    complaint_id: uuid.UUID
    media_sha256: str
    hamming_distance: int
    age_hours: float


def perceptual_hash(source: bytes) -> int | None:
    """A 64-bit dHash of the image, or ``None`` if it will not decode.

    ``None`` rather than an exception. The caller is the trust stage, which has
    already decoded this image once for redaction and will therefore have failed
    earlier if the bytes are unreadable; reaching a decode failure *here* means
    something rarer, and losing the duplicate check for one submission is not a
    reason to halt a citizen's report.
    """
    from PIL import Image  # a decode dependency, not an import-time one

    try:
        with Image.open(io.BytesIO(source)) as image:
            # Greyscale first, then resize. The other order resizes three
            # channels and throws two away — same result, three times the work,
            # on every submission.
            small = image.convert("L").resize((HASH_SIDE + 1, HASH_SIDE), _resample_filter(Image))
            # ``tobytes`` rather than ``getdata``: the latter is deprecated in
            # Pillow 12 and removed in 14, and this codebase runs its tests
            # under ``filterwarnings = ["error"]`` — so the deprecation would
            # surface as a failure of the §11.1 check rather than as a warning.
            # For an ``L`` image the buffer is one byte per pixel, row-major,
            # which is exactly what the indexing below already assumes.
            pixels = small.tobytes()
    except Exception as exc:
        log.info(
            "perceptual_hash_skipped",
            error_type=type(exc).__name__,
            note="the duplicate check is skipped for this artefact; the report is not",
        )
        return None

    value = 0
    for row in range(HASH_SIDE):
        offset = row * (HASH_SIDE + 1)
        for column in range(HASH_SIDE):
            value <<= 1
            # Strictly greater. A flat region — a whitewashed wall, an
            # overexposed sky — produces equal neighbours, and `>=` would set
            # every bit of it to 1 while `>` sets them to 0. Either is arbitrary;
            # what matters is that it is the *same* arbitrary choice for the
            # original and the re-upload, which only holds if it is stated once.
            if pixels[offset + column] > pixels[offset + column + 1]:
                value |= 1
    return value


def _resample_filter(image_module: Any) -> Any:
    """``LANCZOS``, named through the enum Pillow actually exposes.

    Pillow 10 moved the constants from the module to ``Image.Resampling`` and
    kept aliases; Pillow 12 is expected to drop them. Reading through
    ``Resampling`` when it exists means this does not become a deprecation
    warning that ``filterwarnings = ["error"]`` turns into a test failure.
    """
    resampling = getattr(image_module, "Resampling", None)
    return resampling.LANCZOS if resampling is not None else image_module.LANCZOS


def hamming(left: int, right: int) -> int:
    """Bits that differ. The distance the §11.1 threshold is expressed in."""
    return int((left ^ right) & _UNSIGNED_MASK).bit_count()


def to_signed(value: int) -> int:
    """Unsigned 64-bit hash as the signed ``BIGINT`` Postgres stores.

    Postgres has no unsigned integer type and this is a bit pattern rather than
    a quantity, so the two's-complement reinterpretation is lossless and the
    XOR the search runs is unaffected by it — ``a # b`` is a bitwise operation
    and does not care which end the sign bit came from.
    """
    masked = value & _UNSIGNED_MASK
    return masked - (1 << HASH_BITS) if masked & _SIGN_BIT else masked


def from_signed(value: int) -> int:
    """The inverse of ``to_signed``."""
    return value & _UNSIGNED_MASK


def _distance_to(stored: int) -> ColumnElement[int]:
    """Hamming distance from every stored hash to ``stored``, as SQL.

    ``#`` is bitwise XOR on Postgres integers, and ``bit_count`` is defined for
    ``bit`` and ``bytea`` but **not** for ``bigint`` — so the cast is load-bearing
    rather than decorative. Without it the statement fails at execution with
    "function bit_count(bigint) does not exist", which is a runtime error on the
    submission path rather than something the type checker would have caught.

    Computed in the database on purpose. Fetching the window's hashes and
    comparing them in Python would move every candidate row across the wire on
    every submission, to compute something Postgres does in one instruction.
    """
    xor = SubmissionMedia.perceptual_hash.op("#")(stored)
    return func.bit_count(cast(xor, BIT(HASH_BITS)))


async def find_duplicates(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    complaint_id: uuid.UUID,
    image_hash: int,
    captured_at: datetime,
    max_distance: int,
    lookback_hours: int,
    limit: int = 5,
) -> tuple[DuplicateMatch, ...]:
    """Prior artefacts of this tenant within ``max_distance`` bits.

    **Scoped by tenant in the query, not by a caller's discipline.** One
    customer's photographs must never be compared against another's — it would
    leak the existence of a submission across a tenant boundary through a
    similarity score, which is the subtlest form the isolation failure takes and
    the one a scoping check is written to catch.

    **Excludes the complaint's own rows.** A report carrying the same photograph
    twice, or a redelivered stage, would otherwise match itself and flag every
    submission as a duplicate of itself.

    Ordered by distance ascending: the closest match is the one a reviewer
    should see, and truncating an unordered result would show them an arbitrary
    one of several.
    """
    window_start = captured_at - timedelta(hours=lookback_hours)
    stored = to_signed(image_hash)
    distance_expression = _distance_to(stored)
    statement = (
        select(
            SubmissionMedia.complaint_id,
            SubmissionMedia.quarantine_sha256,
            SubmissionMedia.captured_or_reported_at,
            distance_expression.label("distance"),
        )
        .where(
            SubmissionMedia.tenant_id == tenant_id,
            SubmissionMedia.complaint_id != complaint_id,
            SubmissionMedia.perceptual_hash.is_not(None),
            SubmissionMedia.captured_or_reported_at >= window_start,
            SubmissionMedia.captured_or_reported_at <= captured_at,
            distance_expression <= max_distance,
        )
        .order_by(distance_expression.asc(), SubmissionMedia.captured_or_reported_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return tuple(
        DuplicateMatch(
            complaint_id=row.complaint_id,
            media_sha256=row.quarantine_sha256,
            hamming_distance=int(row.distance),
            age_hours=max(
                0.0,
                (captured_at - row.captured_or_reported_at).total_seconds() / 3600.0,
            ),
        )
        for row in rows
    )


__all__ = [
    "HASH_BITS",
    "HASH_SIDE",
    "DuplicateMatch",
    "find_duplicates",
    "from_signed",
    "hamming",
    "perceptual_hash",
    "to_signed",
]
