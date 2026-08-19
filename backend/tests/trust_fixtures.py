"""Image and detector doubles the Phase 8 tests share.

**Why real image bytes and not mocks.** The §22.1 guarantee is a claim about
pixels — that the region under every detected box is different afterwards — and
a mocked encoder can only ever confirm that the code called the function it was
told to call. Pillow is a base dependency precisely so this can be checked
against actual JPEG bytes, in the image the test suite runs in.

**Why the detector is deterministic and hand-written.** BlazeFace on a
synthesised test image finds an unpredictable number of faces, which makes
"every detected face was blurred" a statistical claim. A detector that returns
exactly the boxes it was constructed with turns it into an exact one, and the
real MediaPipe adapter is exercised separately by the live gate — where a real
photograph exists.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from PIL import Image

from nemesis.trust.detectors import FaceBox


class FixedDetector:
    """Returns the boxes it was given. Nothing else.

    Implements the ``FaceDetector`` protocol structurally — no inheritance,
    which is the point of the protocol: a detector that can be written in six
    lines is a seam, and one that requires a base class is a plugin system.
    """

    def __init__(self, boxes: Sequence[FaceBox], *, detector_id: str = "fixed-test@1") -> None:
        self._boxes = tuple(boxes)
        self._detector_id = detector_id
        #: Set on every call, so a test can assert the redactor passed the
        #: buffer it claimed to — a detector handed the wrong dimensions
        #: silently finds nothing, which is the failure mode with no symptom.
        self.last_size: tuple[int, int] | None = None

    @property
    def detector_id(self) -> str:
        return self._detector_id

    def detect(self, *, width: int, height: int, rgb: bytes) -> Sequence[FaceBox]:
        assert len(rgb) == width * height * 3, (
            f"the redactor handed {len(rgb)} bytes for a {width}x{height} RGB buffer, "
            f"which should be {width * height * 3}. A detector reading a mis-sized "
            f"buffer finds faces in the wrong place, or none at all."
        )
        self.last_size = (width, height)
        return self._boxes


class ExplodingDetector:
    """Raises. For the path where the model is loaded and the inference fails."""

    detector_id = "exploding-test@1"

    def detect(self, *, width: int, height: int, rgb: bytes) -> Sequence[FaceBox]:
        raise RuntimeError("inference failed")


def gradient_image(
    width: int = 64, height: int = 48, *, fmt: str = "JPEG", quality: int = 95
) -> bytes:
    """A deterministic image with structure in both axes.

    A gradient rather than noise or a flat fill, for two reasons that both
    matter here: a flat image has no gradients, so every dHash bit is the same
    arbitrary value and two unrelated flat images hash identically; and a blur
    over a flat region changes nothing, so the redaction assertion would pass
    against a redactor that did nothing at all.
    """
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            pixels[x, y] = ((x * 4) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=quality)
    return buffer.getvalue()


def noisy_patch_image(width: int = 64, height: int = 48) -> bytes:
    """A gradient with a high-frequency checkerboard in the top-left quadrant.

    The checkerboard is what makes "this region was blurred" measurable: a
    Gaussian is a low-pass filter, so the variance of a checkerboard collapses
    under it while a smooth gradient barely moves. Asserting on variance rather
    than on "the bytes differ" is the difference between proving a blur happened
    and proving *something* happened.
    """
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            if x < width // 2 and y < height // 2:
                value = 255 if (x + y) % 2 == 0 else 0
                pixels[x, y] = (value, value, value)
            else:
                pixels[x, y] = ((x * 4) % 256, (y * 5) % 256, 128)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def region_variance(data: bytes, box: tuple[int, int, int, int]) -> float:
    """Mean per-channel variance inside ``box`` (left, top, right, bottom)."""
    with Image.open(io.BytesIO(data)) as image:
        # ``tobytes``, not ``getdata`` — deprecated in Pillow 12, and this
        # suite turns DeprecationWarning into an error.
        values = image.convert("L").crop(box).tobytes()
    if not values:  # pragma: no cover — every caller passes a non-empty box
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def image_with_exif(
    *, latitude: float, longitude: float, captured: str | None = "2026:03:14 09:15:00"
) -> bytes:
    """A JPEG carrying a GPS IFD, written through Pillow's own EXIF writer.

    Constructed rather than checked in as a binary fixture: a committed .jpg is
    a file nobody can read the intent of, and the coordinates this test needs to
    vary are exactly the thing a binary hides.
    """
    from PIL import Image as PILImage

    base = PILImage.open(io.BytesIO(gradient_image()))
    exif = base.getexif()
    gps = exif.get_ifd(0x8825)
    gps[1] = "N" if latitude >= 0 else "S"
    gps[2] = _to_dms(abs(latitude))
    gps[3] = "E" if longitude >= 0 else "W"
    gps[4] = _to_dms(abs(longitude))
    if captured is not None:
        exif.get_ifd(0x8769)[0x9003] = captured

    buffer = io.BytesIO()
    base.save(buffer, format="JPEG", exif=exif)
    base.close()
    return buffer.getvalue()


def _to_dms(value: float) -> tuple[float, float, float]:
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return (float(degrees), float(minutes), float(seconds))


__all__ = [
    "ExplodingDetector",
    "FixedDetector",
    "gradient_image",
    "image_with_exif",
    "noisy_patch_image",
    "region_variance",
]
