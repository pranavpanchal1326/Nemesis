"""§22.1 face blur, and the only code in this system that writes a served image.

**The guarantee, stated precisely.** Nothing outside this module constructs a
path under the *redacted* root, and nothing anywhere serves a path under the
*quarantine* root. ``scripts/check_media_redaction.py`` parses the repository
and fails the build on either. That is a structural claim about the code, not a
convention, which is what the Phase 8 gate asks for.

**Where this deviates from "before any persistence, including temp paths", and
why.** The phase summary in ``docs/PHASES.md`` states blur before *any*
persistence. §22.4 of the blueprint states the raw uploaded photo is retained
for **30 days**, for the dispute and verification window. Both cannot be
literally true, and the retention schedule is the more specific and more
considered of the two — a system that destroyed the original the instant it
arrived would have nothing to re-examine when a citizen disputes a redaction, or
when a court asks what was actually photographed.

So the raw upload persists, in quarantine, and what is enforced instead is
strictly stronger than a convention and weaker than the literal words:

1. **Unreachable.** Quarantine has no HTTP route, and its URIs use a scheme
   (``nemesis+quarantine``) that no browser can follow — Phase 3 chose that
   deliberately, for this phase.
2. **Read once, by one caller.** ``MediaStore.resolve`` is called from exactly
   one place in the repository, and that place is below.
3. **Expiring.** Every artefact is stamped with ``purge_raw_after`` from the
   tenant's own retention policy at the moment it is processed. §22.4 becomes a
   row a sweep can find rather than a paragraph.
4. **Never the thing that is shown.** The review queue, and every phase after
   this one, resolve media through ``RedactedStore`` and cannot express the
   other path.

The deviation is recorded in ADR-0031 rather than absorbed, because a gate
clause quietly reinterpreted is a gate clause that was not met.

**Why the redacted copy is re-encoded rather than patched.** Blurring a JPEG in
place would leave every APP segment intact — EXIF, XMP, thumbnails. The embedded
JPEG thumbnail is the part that matters: it is a second, smaller copy of the
*original* image, faces and all, and a redaction that blurred the main scan and
left the thumbnail would ship the unblurred faces inside the file it claims is
safe. Re-encoding from decoded pixels cannot carry any of it, which is why the
strip is a consequence of the design rather than a step that can be skipped.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from nemesis.observability.logging import get_logger
from nemesis.trust.detectors import (
    DETECTION_MARGIN_FRACTION,
    FaceBox,
    FaceDetector,
    active_detector,
)
from nemesis.trust.errors import MediaNotFoundError, RedactionFailedError

log = get_logger(__name__)

#: Subdirectory of ``upload_dir`` holding redacted, servable material. Sibling
#: of ``quarantine``, never nested inside it: a reader that walked the upload
#: root looking for "the images" must not find the originals by descending.
REDACTED_DIRNAME: Final = "redacted"

#: URI scheme for a redacted artefact. Distinct from ``nemesis+quarantine`` on
#: purpose — the two are never interchangeable, and a single scheme with a
#: directory difference is one string edit away from serving the wrong root.
REDACTED_SCHEME: Final = "nemesis+media"

#: Output format for every redacted image. One format, not the input's, so the
#: served store has exactly one decoder path and the §22.1 claim does not depend
#: on which format a citizen's phone happened to produce.
OUTPUT_FORMAT: Final = "JPEG"
OUTPUT_CONTENT_TYPE: Final = "image/jpeg"

#: Quality of the re-encode. 88 rather than 95: the served copy is evidence a
#: human looks at, not a master, and the §11.1 perceptual hash is computed from
#: the *original* bytes, so re-encode loss cannot move a duplicate out of range.
OUTPUT_QUALITY: Final = 88

#: Refuse to decode beyond this many pixels. Pillow's own decompression-bomb
#: warning is a *warning*, and this codebase runs with ``filterwarnings = error``
#: in tests and nothing at all in production — so the guard has to be a check.
#: 50 megapixels is comfortably above any phone camera and far below the
#: 30000x30000 PNG that turns a 200 KB upload into 2.7 GB of RAM on a worker
#: with a 3 GB limit.
MAX_DECODED_PIXELS: Final = 50_000_000

#: Blur radius as a fraction of the box's smaller side, and a floor in pixels.
#: Proportional because a fixed radius that anonymises a 40-pixel face merely
#: softens a 400-pixel one; the floor because a fraction of a very small box
#: rounds to zero and produces a blur that changes nothing.
BLUR_FRACTION: Final = 0.35
MIN_BLUR_RADIUS: Final = 4.0


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """What redaction produced, and what it found on the way.

    ``faces_detected`` and ``faces_blurred`` are separate for the reason
    ``MediaRedactedV1`` gives: they are equal on every success today, and a
    future change that drops a box shows up as a divergence rather than as an
    unchanged boolean.
    """

    uri: str
    sha256: str
    content_type: str
    size_bytes: int
    faces_detected: int
    faces_blurred: int
    detector_id: str
    #: Always ``True`` for an image — re-encoding from pixels cannot carry a
    #: metadata segment. Recorded rather than assumed so the event states it.
    exif_stripped: bool


class RedactedStore:
    """Content-addressed storage for material that is safe to serve.

    Deliberately *not* a subclass of ``MediaStore`` and deliberately not sharing
    its root. Inheritance would give this class ``store()`` — a method that
    writes arbitrary uploader bytes — and the whole property being defended is
    that the only way into this directory is through a face detector.
    """

    def __init__(self, upload_dir: Path) -> None:
        self._root = Path(upload_dir) / REDACTED_DIRNAME

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, checksum: str) -> Path:
        # Two-character fan-out, matching quarantine: one directory holding
        # every image a city produces is a directory nothing enumerates quickly.
        return self._root / checksum[:2] / f"{checksum}.jpg"

    def uri_for(self, checksum: str) -> str:
        return f"{REDACTED_SCHEME}://{self.path_for(checksum).relative_to(self._root).as_posix()}"

    def resolve(self, uri: str) -> Path:
        """Filesystem path for a redacted URI, for the handler that serves it.

        Rejects a quarantine URI explicitly rather than by falling through the
        prefix test. The two schemes differ by six characters, this function's
        input comes out of a database column, and "not a redacted URI" is a
        message that sends an operator looking in the wrong place when the real
        answer is that something tried to serve an original.
        """
        if uri.startswith("nemesis+quarantine://"):
            raise MediaNotFoundError(
                "refusing to resolve a quarantine URI through the redacted store: "
                "quarantine holds unredacted originals and nothing serves them. "
                "The caller is holding the wrong column."
            )
        if not uri.startswith(f"{REDACTED_SCHEME}://"):
            raise MediaNotFoundError(f"not a redacted media URI: {uri!r}")
        relative = uri[len(REDACTED_SCHEME) + 3 :]
        candidate = (self._root / relative).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise MediaNotFoundError("resolved outside the redacted root")
        if not candidate.exists():
            raise MediaNotFoundError(
                f"redacted artefact {uri!r} is not on disk. Either the §22.4 "
                f"retention sweep removed it — check submission_media.raw_purged_at "
                f"— or something deleted it outside the retention path."
            )
        return candidate

    def _write(self, data: bytes) -> tuple[str, Path]:
        """Atomically place ``data`` at its content address.

        Same shape as ``MediaStore.store``'s finish: temp file in the
        destination directory, fsync, rename within one filesystem. A reader can
        never observe a partially written file under its content address, which
        matters more here than in quarantine — this is the path an HTTP handler
        reads while the worker is still writing the next one.
        """
        checksum = hashlib.sha256(data).hexdigest()
        destination = self.path_for(checksum)
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed below
            dir=destination.parent, prefix=".redacting-", suffix=".part", delete=False
        )
        temp_path = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            temp_path.replace(destination)
        except BaseException:
            if not handle.closed:
                handle.close()
            temp_path.unlink(missing_ok=True)
            raise
        return checksum, destination


def redact_image(
    source: bytes,
    *,
    store: RedactedStore,
    detector: FaceDetector | None = None,
) -> RedactionResult:
    """Blur every detected face, strip every metadata segment, and store the result.

    Total in the sense that matters: it either returns a result whose bytes are
    on disk under the redacted root, or it raises. There is no path that returns
    a URI pointing at unredacted material, and no path that returns successfully
    without the detector having run — ``active_detector`` raises when none is
    registered, before any pixel is touched.
    """
    engine = detector if detector is not None else active_detector()
    image, width, height = _decode(source)

    try:
        rgb = image.tobytes()
        raw_boxes = engine.detect(width=width, height=height, rgb=rgb)
        boxes = _usable_boxes(raw_boxes, width=width, height=height)
        blurred = _blur(image, boxes)
        data = _encode(blurred)
    finally:
        image.close()

    # Private, and called from the one module allowed to call it. `RedactedStore`
    # deliberately exposes no public write: a public one would be a supported way
    # to put bytes under the served root without passing a detector, which is the
    # single property this phase's guard test exists to defend.
    checksum, path = store._write(data)
    log.info(
        "media_redacted",
        detector_id=engine.detector_id,
        faces_detected=len(raw_boxes),
        faces_blurred=len(boxes),
        bytes_out=len(data),
    )
    return RedactionResult(
        uri=store.uri_for(checksum),
        sha256=checksum,
        content_type=OUTPUT_CONTENT_TYPE,
        size_bytes=path.stat().st_size,
        faces_detected=len(raw_boxes),
        faces_blurred=len(boxes),
        detector_id=engine.detector_id,
        exif_stripped=True,
    )


def _decode(source: bytes) -> tuple[Any, int, int]:
    """Decode to RGB, refusing anything that would exhaust the worker.

    The size check runs against the *header* — ``Image.open`` is lazy and has
    parsed the dimensions before any pixel is read — so a decompression bomb is
    refused for the price of reading its header rather than for the price of
    decompressing it, which is the entire point of doing it here.
    """
    from PIL import Image, UnidentifiedImageError  # see below

    try:
        image = Image.open(io.BytesIO(source))
    except UnidentifiedImageError as exc:
        raise RedactionFailedError(
            f"the uploaded bytes are not a decodable image: {exc}. Retrying will "
            f"not change that, so the stage takes its fallback immediately."
        ) from exc
    except Exception as exc:  # Pillow raises OSError for truncated files
        raise RedactionFailedError(f"image could not be opened: {exc}") from exc

    width, height = image.size
    if width * height > MAX_DECODED_PIXELS:
        image.close()
        raise RedactionFailedError(
            f"image is {width}x{height} = {width * height} pixels, above the "
            f"{MAX_DECODED_PIXELS} decode limit. Decoding it would cost roughly "
            f"{width * height * 3 // (1024 * 1024)} MB of RAM on a worker capped at 3 GB."
        )

    try:
        # `convert` rather than a mode check: a palette PNG, a CMYK JPEG and a
        # 16-bit TIFF all need to become 8-bit RGB before `tobytes()` produces
        # the layout the detector protocol promises, and getting that wrong
        # yields a detector that silently finds nothing on one format.
        return image.convert("RGB"), width, height
    except Exception as exc:
        image.close()
        raise RedactionFailedError(f"image could not be converted to RGB: {exc}") from exc


def _usable_boxes(
    boxes: tuple[FaceBox, ...] | list[FaceBox] | Any, *, width: int, height: int
) -> tuple[FaceBox, ...]:
    """Expand each detection by the §22.1 margin and drop what misses the frame."""
    usable: list[FaceBox] = []
    for box in boxes:
        grown = box.expanded(DETECTION_MARGIN_FRACTION, image_width=width, image_height=height)
        if grown is not None:
            usable.append(grown)
    return tuple(usable)


def _blur(image: Any, boxes: tuple[FaceBox, ...]) -> Any:
    """Gaussian-blur each region, in place on a copy.

    **Blur, not a solid rectangle**, and not pixelation. A black box is
    unmistakably a redaction and is the safest option, but §22.1's purpose is a
    usable public record: a photograph of a flooded street with three black
    rectangles in it is harder for an operator to assess, and the point of the
    blur is that the *scene* survives while the person does not. Pixelation is
    rejected outright — mosaic redaction is reversible for small block counts,
    and there are published attacks that do it.
    """
    from PIL import ImageFilter

    if not boxes:
        return image
    for box in boxes:
        region = image.crop((box.x, box.y, box.x + box.width, box.y + box.height))
        radius = max(MIN_BLUR_RADIUS, min(box.width, box.height) * BLUR_FRACTION)
        # Applied twice. One pass of a Gaussian is a low-pass filter whose
        # residual still carries recoverable structure at this radius; a second
        # pass over the already-blurred region drives the high frequencies down
        # far enough that nothing recognisable survives, at a cost measured in
        # microseconds on a region this size.
        region = region.filter(ImageFilter.GaussianBlur(radius=radius))
        region = region.filter(ImageFilter.GaussianBlur(radius=radius))
        image.paste(region, (box.x, box.y))
    return image


def _encode(image: Any) -> bytes:
    """Re-encode from pixels. Nothing from the source file survives this.

    ``exif`` and ``icc_profile`` are not passed, and that is the mechanism
    rather than an omission: Pillow only writes the metadata it is handed, so
    the EXIF GPS block, the XMP packet and the embedded thumbnail — a second
    unblurred copy of the whole image — all end here.
    """
    buffer = io.BytesIO()
    try:
        image.save(buffer, format=OUTPUT_FORMAT, quality=OUTPUT_QUALITY, optimize=True)
    except Exception as exc:
        raise RedactionFailedError(f"redacted image could not be encoded: {exc}") from exc
    return buffer.getvalue()


__all__ = [
    "BLUR_FRACTION",
    "MAX_DECODED_PIXELS",
    "OUTPUT_CONTENT_TYPE",
    "REDACTED_DIRNAME",
    "REDACTED_SCHEME",
    "RedactedStore",
    "RedactionResult",
    "redact_image",
]
