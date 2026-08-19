"""§22.1 face blur — the guarantee, not the adapter.

The Phase 8 gate says *no code path can persist an unblurred image*, enforced by
a repository-level guard rather than by convention. That guard is two things:
``scripts/check_media_redaction.py``, which checks the shape of the code, and
this file, which checks the behaviour.

**What is asserted here is measurable, not incidental.** "The output bytes
differ from the input" would pass against a redactor that only re-encoded. So
the image carries a high-frequency checkerboard where the face is, and the test
asserts the *variance* inside that region collapses — which a Gaussian does and
a re-encode does not.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from nemesis.trust.detectors import FaceBox, active_detector, detector_scope
from nemesis.trust.errors import (
    MediaNotFoundError,
    RedactionFailedError,
    RedactionUnavailableError,
)
from nemesis.trust.redaction import MAX_DECODED_PIXELS, RedactedStore, redact_image
from tests.trust_fixtures import (
    FixedDetector,
    gradient_image,
    image_with_exif,
    noisy_patch_image,
    region_variance,
)

FACE = FaceBox(x=4, y=4, width=24, height=16, confidence=0.9)


@pytest.fixture
def store(tmp_path: Path) -> RedactedStore:
    return RedactedStore(tmp_path)


# ---------------------------------------------------------------------------
# The guarantee
# ---------------------------------------------------------------------------


def test_the_region_under_a_detection_is_actually_blurred(store: RedactedStore) -> None:
    """Variance collapse, not byte inequality.

    The checkerboard is the highest-frequency signal an 8-bit image can carry.
    A Gaussian destroys it; a re-encode, a crop, a copy, and every other thing a
    redactor could do instead all leave it largely intact.
    """
    source = noisy_patch_image()
    box = (FACE.x, FACE.y, FACE.x + FACE.width, FACE.y + FACE.height)
    before = region_variance(source, box)

    result = redact_image(source, store=store, detector=FixedDetector([FACE]))
    after = region_variance(store.resolve(result.uri).read_bytes(), box)

    assert before > 3000, "the fixture stopped being high-frequency; the test proves nothing"
    assert after < before / 10
    assert result.faces_detected == 1
    assert result.faces_blurred == 1


def test_a_region_outside_every_detection_survives(store: RedactedStore) -> None:
    """The other half of the claim, and the reason §22.1 asks for a blur.

    A redactor that blurred the whole frame would pass the assertion above and
    destroy the evidence the photograph exists to carry. The scene has to
    survive while the person does not.
    """
    source = noisy_patch_image()
    far = (40, 30, 60, 44)
    before = region_variance(source, far)

    result = redact_image(source, store=store, detector=FixedDetector([FACE]))
    after = region_variance(store.resolve(result.uri).read_bytes(), far)

    assert after == pytest.approx(before, rel=0.35)


def test_every_detection_is_blurred_not_just_the_first(store: RedactedStore) -> None:
    """Three faces, three blurs. §22.1 is a promise about *every* face."""
    boxes = [
        FaceBox(x=2, y=2, width=12, height=10, confidence=0.9),
        FaceBox(x=18, y=2, width=12, height=10, confidence=0.9),
        FaceBox(x=2, y=18, width=12, height=10, confidence=0.9),
    ]
    source = noisy_patch_image()
    result = redact_image(source, store=store, detector=FixedDetector(boxes))
    redacted = store.resolve(result.uri).read_bytes()

    assert result.faces_detected == 3
    assert result.faces_blurred == 3
    for box in boxes:
        region = (box.x, box.y, box.x + box.width, box.y + box.height)
        assert region_variance(redacted, region) < region_variance(source, region) / 5


def test_the_blur_extends_past_the_detected_box(store: RedactedStore) -> None:
    """The §22.1 over-blur margin, asserted where it is easy to lose.

    A detector's box is tight around the facial landmarks and leaves the
    hairline, the jaw and the ears outside it — all of which identify a person
    to somebody who knows them. The margin is the difference between blurring a
    face and blurring the middle of one.
    """
    source = noisy_patch_image()
    inner = FaceBox(x=10, y=10, width=8, height=8, confidence=0.9)
    just_outside = (inner.x - 2, inner.y - 2, inner.x, inner.y)
    before = region_variance(source, just_outside)

    result = redact_image(source, store=store, detector=FixedDetector([inner]))
    after = region_variance(store.resolve(result.uri).read_bytes(), just_outside)

    assert after < before / 2


def test_no_detector_is_a_refusal_and_never_a_silent_pass(store: RedactedStore) -> None:
    """The single most important assertion in the phase.

    The tempting alternative to raising is a detector that finds no faces. Every
    pipeline run would then succeed, ``media_redacted`` would record
    ``faces_detected: 0``, and the redacted copy would be pixel-identical to the
    original — a §22.1 breach invisible from every angle including the log.
    """
    with pytest.raises(RedactionUnavailableError):
        redact_image(gradient_image(), store=store)

    # And nothing was written. A refusal that left a file behind would be a
    # refusal with an unredacted artefact sitting in the served root.
    assert not list(store.root.rglob("*.jpg")) if store.root.exists() else True


def test_the_registry_refuses_a_second_detector() -> None:
    """Two detectors means two redaction standards inside one deployment."""
    with detector_scope(FixedDetector([])):
        assert active_detector().detector_id == "fixed-test@1"
        from nemesis.trust.detectors import register_detector

        with pytest.raises(RuntimeError, match="already registered"):
            register_detector(FixedDetector([], detector_id="other@1"))


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_the_redacted_copy_carries_no_exif(store: RedactedStore) -> None:
    """Re-encoding from pixels is the mechanism, not a step that can be skipped.

    A served image still carrying its capture GPS would re-leak the location
    §22.1 coarsens — and the embedded JPEG thumbnail, which the same metadata
    block carries, is a second unblurred copy of the whole scene.
    """
    source = image_with_exif(latitude=18.52, longitude=73.85)
    with Image.open(io.BytesIO(source)) as original:
        assert original.getexif().get_ifd(0x8825), "the fixture lost its GPS; nothing is proven"

    result = redact_image(source, store=store, detector=FixedDetector([FACE]))
    with Image.open(store.resolve(result.uri)) as redacted:
        assert not redacted.getexif()
    assert result.exif_stripped


def test_the_output_is_always_jpeg_whatever_went_in(store: RedactedStore) -> None:
    """One decoder path for everything served, whatever a citizen's phone produced."""
    result = redact_image(noisy_patch_image(), store=store, detector=FixedDetector([]))
    assert result.content_type == "image/jpeg"
    with Image.open(store.resolve(result.uri)) as image:
        assert image.format == "JPEG"


def test_the_detector_is_named_in_the_result(store: RedactedStore) -> None:
    """ "Faces were blurred" with no record of what blurred them is not evidence."""
    result = redact_image(gradient_image(), store=store, detector=FixedDetector([]))
    assert result.detector_id == "fixed-test@1"


def test_the_detector_receives_a_correctly_sized_buffer(store: RedactedStore) -> None:
    """A detector handed the wrong dimensions finds nothing, silently.

    ``FixedDetector`` asserts the buffer length internally; this test exists so
    the assertion is reached for a non-square image, where a transposed
    width/height would otherwise still multiply out correctly.
    """
    detector = FixedDetector([])
    redact_image(gradient_image(width=64, height=48), store=store, detector=detector)
    assert detector.last_size == (64, 48)


def test_a_palette_image_is_converted_before_detection(store: RedactedStore) -> None:
    """A PNG with a palette has one byte per pixel until it is converted.

    Without the ``convert("RGB")`` the buffer handed to the detector would be a
    third of the expected length — and a detector that reads it finds faces in
    the wrong place, on exactly one image format.
    """
    with Image.open(io.BytesIO(noisy_patch_image())) as image:
        buffer = io.BytesIO()
        image.convert("P", palette=Image.Palette.ADAPTIVE).save(buffer, format="PNG")

    detector = FixedDetector([])
    result = redact_image(buffer.getvalue(), store=store, detector=detector)
    assert detector.last_size == (64, 48)
    assert result.content_type == "image/jpeg"


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_undecodable_bytes_are_refused_rather_than_stored(store: RedactedStore) -> None:
    with pytest.raises(RedactionFailedError):
        redact_image(b"not an image", store=store, detector=FixedDetector([]))


def test_a_decompression_bomb_is_refused_from_its_header(store: RedactedStore) -> None:
    """Refused for the price of reading a header, not of decompressing it.

    A 15 MB PNG can declare dimensions that decode to gigabytes. ``Image.open``
    is lazy and has parsed the size before any pixel is read, which is the only
    place this check is cheap.
    """
    side = int(MAX_DECODED_PIXELS**0.5) + 500
    buffer = io.BytesIO()
    Image.new("RGB", (side, side)).save(buffer, format="PNG")

    with pytest.raises(RedactionFailedError, match="decode limit"):
        redact_image(buffer.getvalue(), store=store, detector=FixedDetector([]))


def test_a_box_entirely_outside_the_frame_is_dropped(store: RedactedStore) -> None:
    """Detected but not blurrable, and the two counters say so.

    BlazeFace returns relative coordinates that extend past the frame for a face
    at the edge. A zero-area blur that still incremented ``faces_blurred`` would
    destroy the only signal those two numbers exist to carry.
    """
    outside = FaceBox(x=500, y=500, width=20, height=20, confidence=0.9)
    result = redact_image(gradient_image(), store=store, detector=FixedDetector([outside]))
    assert result.faces_detected == 1
    assert result.faces_blurred == 0


def test_a_box_overlapping_the_edge_is_clamped_not_dropped(store: RedactedStore) -> None:
    edge = FaceBox(x=-10, y=-10, width=30, height=30, confidence=0.9)
    result = redact_image(noisy_patch_image(), store=store, detector=FixedDetector([edge]))
    assert result.faces_blurred == 1
    assert region_variance(store.resolve(result.uri).read_bytes(), (0, 0, 18, 18)) < 2000


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_identical_bytes_are_one_file(store: RedactedStore) -> None:
    """Content addressing, and the reason the media route checks a row.

    The redacted root is shared across tenants — deduplication is the point —
    so a handler that resolved a path straight from a URL would let any tenant
    fetch any other's photograph by observing a hash.
    """
    source = noisy_patch_image()
    first = redact_image(source, store=store, detector=FixedDetector([FACE]))
    second = redact_image(source, store=store, detector=FixedDetector([FACE]))
    assert first.sha256 == second.sha256
    assert len(list(store.root.rglob("*.jpg"))) == 1


def test_the_redacted_store_refuses_a_quarantine_uri(store: RedactedStore) -> None:
    """Refused by name, with a message that says what actually went wrong.

    The two schemes differ by six characters and the input comes out of a
    database column. "Not a redacted URI" would send an operator looking in the
    wrong place when the real answer is that something tried to serve an
    original.
    """
    with pytest.raises(MediaNotFoundError, match="quarantine"):
        store.resolve("nemesis+quarantine://ab/abc.jpg")


def test_the_redacted_store_refuses_a_traversal(store: RedactedStore) -> None:
    with pytest.raises(MediaNotFoundError, match="outside"):
        store.resolve("nemesis+media://../../etc/passwd")


def test_a_missing_artefact_names_retention_as_the_likely_cause(
    store: RedactedStore,
) -> None:
    """§22.4's sweep removing a file is expected; a 500 would send the operator
    hunting a broken deployment instead of an expired one."""
    with pytest.raises(MediaNotFoundError, match="retention"):
        store.resolve("nemesis+media://ab/" + "a" * 64 + ".jpg")
