"""Face detection, behind a seam — and a registry that fails closed.

**Why a seam at all.** MediaPipe lives only in the ``worker-ml`` image (§0's
memory split), so every other process in the system — the API, ``worker-io``,
the test suite — imports this module and finds no detector. That is a fact about
the deployment, not a problem to route around, and the whole design question is
what happens next.

**What happens next is a refusal.** ``active_detector`` raises when nothing is
registered. The tempting alternative — return a detector that finds no faces —
is the single worst line of code this phase could contain: every pipeline run
would succeed, ``media_redacted`` would record ``faces_detected: 0``, the
redacted copy would be pixel-identical to the original, and the §22.1 breach
would be invisible from every angle including the event log. A privacy control
whose failure mode is *silence* is not a control.

So the null case is loud, the stage's declared fallback parks the report for a
human, and no served image exists for a complaint whose detector was missing.

**Why the protocol takes raw pixels rather than a Pillow image.** Two reasons,
and the second is the load-bearing one. Pillow is a base dependency now and
MediaPipe is not, so a protocol phrased in MediaPipe's types would drag the ml
extra into every import; and a detector that receives ``(width, height, rgb)``
can be implemented in a test in four lines with no image library at all, which
is what makes the redaction *guarantee* testable rather than only the MediaPipe
adapter. A seam that only one implementation can pass through is not a seam.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final, Protocol

from nemesis.observability.logging import get_logger
from nemesis.trust.errors import RedactionUnavailableError

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FaceBox:
    """One detection, in pixels, with the origin at the top left.

    Integers, and clamped by the caller rather than by the detector. A box that
    runs off the edge of the image is normal — BlazeFace returns relative
    coordinates that extend past 1.0 for a face at the frame edge — and the
    honest place to fix that is where the pixels are, not by asking every
    detector to remember.

    ``confidence`` is carried but the redactor does not read it: the threshold
    is applied inside the detector, where the model's own scale is known.
    Recording it means a later question — "were the missed faces low-confidence
    or absent from the output entirely" — has an answer.
    """

    x: int
    y: int
    width: int
    height: int
    confidence: float

    def clamped(self, *, image_width: int, image_height: int) -> FaceBox | None:
        """This box intersected with the image, or ``None`` if it misses entirely.

        Returning ``None`` rather than a zero-area box: a zero-area blur is a
        no-op that still increments ``faces_blurred``, and the whole value of
        that counter is that it can disagree with ``faces_detected``.
        """
        left = max(0, self.x)
        top = max(0, self.y)
        right = min(image_width, self.x + self.width)
        bottom = min(image_height, self.y + self.height)
        if right <= left or bottom <= top:
            return None
        return FaceBox(
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
            confidence=self.confidence,
        )

    def expanded(self, fraction: float, *, image_width: int, image_height: int) -> FaceBox | None:
        """Grown by ``fraction`` on each side, then clamped.

        §22.1 biases toward over-blurring — the same reasoning that puts
        ``face_detector_min_confidence`` below the usual 0.5. A detector's box
        is tight around the facial landmarks and leaves the hairline, the jaw
        and the ears outside it, all of which identify a person to somebody who
        knows them. The margin is the difference between blurring a face and
        blurring the middle of one.
        """
        margin_x = round(self.width * fraction)
        margin_y = round(self.height * fraction)
        grown = FaceBox(
            x=self.x - margin_x,
            y=self.y - margin_y,
            width=self.width + 2 * margin_x,
            height=self.height + 2 * margin_y,
            confidence=self.confidence,
        )
        return grown.clamped(image_width=image_width, image_height=image_height)


class FaceDetector(Protocol):
    """What ``redaction`` needs, and nothing else.

    ``detector_id`` is not decoration. It is written into ``media_redacted`` and
    into ``submission_media.detector_id``, and it is the only thing that makes
    "this photograph was redacted" a checkable claim years later — a model
    upgrade that started missing a class of faces is findable by that id and by
    nothing else.
    """

    @property
    def detector_id(self) -> str: ...

    def detect(self, *, width: int, height: int, rgb: bytes) -> Sequence[FaceBox]:
        """Boxes for every face found in a packed RGB buffer.

        ``rgb`` is ``width * height * 3`` bytes, row-major, no padding. Chosen
        over an encoded image because the caller has already decoded — handing
        back the JPEG would mean decoding twice, and the second decode is where
        a truncated file fails in a stack trace nobody expects.
        """
        ...


#: Where ``scripts/fetch_models.py`` writes the pinned ``.tflite`` bundle,
#: relative to ``model_cache_dir``. Stated here rather than derived, and asserted
#: by the Phase 8 gate: a detector that looks in the wrong directory reports
#: "model absent" and halts every complaint carrying a photograph, which is
#: correct behaviour for a genuinely missing model and a very confusing way to
#: discover a path typo.
MODEL_SUBDIRECTORY: Final = "mediapipe"

#: Margin added around every detection before blurring. See ``FaceBox.expanded``.
#: A module constant rather than a policy field: §22.1 is a legal obligation,
#: and how tightly the blur hugs the detection is an implementation detail of
#: discharging it, not a knob a tenant should be able to turn down to zero.
DETECTION_MARGIN_FRACTION: Final = 0.25


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# One detector per process, not per tenant. A tenant-selectable detector would
# mean two customers' photographs redacted to different standards, which is not
# a configuration choice — it is a compliance posture, and there is one.

_lock = threading.Lock()
_detector: FaceDetector | None = None


def register_detector(detector: FaceDetector) -> None:
    """Bind the process's detector. Called once, at worker startup.

    Refuses to replace an existing registration, for the reason
    ``pipeline.stages.register_provider`` gives: two registrations means two
    parts of the system each assumed they owned it, and import order is the
    worst available tiebreaker. Use ``detector_scope`` in tests.
    """
    global _detector
    with _lock:
        if _detector is not None:
            raise RuntimeError(
                f"a face detector is already registered ({_detector.detector_id!r}); "
                f"the process has exactly one, because two would mean two redaction "
                f"standards inside one deployment"
            )
        _detector = detector
    log.info("face_detector_registered", detector_id=detector.detector_id)


def active_detector() -> FaceDetector:
    """The registered detector, or a refusal.

    Never returns a no-op stand-in. See the module docstring: a redaction that
    silently finds nothing is indistinguishable from a redaction that worked,
    from inside the system and from outside it.
    """
    detector = _detector
    if detector is None:
        raise RedactionUnavailableError(
            "no face detector is registered in this process, so §22.1 face blur "
            "cannot be discharged. The trust stage runs on the `ml` queue, which "
            "is served by the only image carrying MediaPipe; a worker on another "
            "queue reaching this line means the stage was routed wrongly."
        )
    return detector


def detector_is_registered() -> bool:
    """For readiness reporting and for the guard test. Never for a fallback."""
    return _detector is not None


@contextmanager
def detector_scope(detector: FaceDetector) -> Iterator[None]:
    """Register a detector for the duration of a block.

    Exists for tests and is named so that is obvious — the same honesty
    ``pipeline.stages.provider_scope`` applies. The §22.1 guarantee is tested
    through this seam with a *deterministic* detector, which is the only way to
    assert "the pixels under every returned box changed" as an exact fact rather
    than as a statistical one about a real model's output.
    """
    global _detector
    with _lock:
        previous = _detector
        _detector = detector
    try:
        yield
    finally:
        with _lock:
            _detector = previous


# ---------------------------------------------------------------------------
# MediaPipe
# ---------------------------------------------------------------------------


class MediaPipeFaceDetector:
    """BlazeFace short-range, through the MediaPipe Tasks API.

    **The Tasks API, not ``mp.solutions``.** MediaPipe 1.x removed the legacy
    namespace entirely — Phase 0's gate caught the blueprint pointing at a
    deleted API, and then caught it again at *runtime* over ``libEGL.so.1``,
    because the Tasks runtime links a GPU delegate at load even for CPU
    inference. Both are settled; the note stays because the `.tflite` bundle is
    a fetched, pinned artefact rather than something the library carries, and
    that is easy to forget when adding a second model.

    Constructed lazily. Importing ``mediapipe`` costs seconds and a few hundred
    megabytes, and this class is *defined* in every image — including the API,
    where it must never be instantiated.
    """

    def __init__(self, *, model_path: str, min_confidence: float) -> None:
        self._model_path = model_path
        self._min_confidence = min_confidence
        self._detector: Any | None = None
        self._version = "1"

    @property
    def detector_id(self) -> str:
        return f"mediapipe:blaze_face_short_range@{self._version}"

    def _ensure(self) -> Any:
        if self._detector is not None:
            return self._detector
        import mediapipe as mp  # see the class docstring

        base = mp.tasks.BaseOptions(model_asset_path=self._model_path)
        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=base,
            min_detection_confidence=self._min_confidence,
        )
        self._detector = mp.tasks.vision.FaceDetector.create_from_options(options)
        return self._detector

    def detect(self, *, width: int, height: int, rgb: bytes) -> Sequence[FaceBox]:
        import mediapipe as mp
        import numpy as np

        detector = self._ensure()
        array = np.frombuffer(rgb, dtype=np.uint8).reshape((height, width, 3))
        # `.copy()` because `frombuffer` gives a read-only view over `rgb`, and
        # MediaPipe writes into the buffer it is handed. Without it the call
        # fails deep inside the C++ layer with an error about a non-writable
        # array, on the first real photograph rather than in any test.
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=array.copy())
        result = detector.detect(image)

        boxes: list[FaceBox] = []
        for detection in result.detections:
            box = detection.bounding_box
            score = float(detection.categories[0].score) if detection.categories else 0.0
            boxes.append(
                FaceBox(
                    x=int(box.origin_x),
                    y=int(box.origin_y),
                    width=int(box.width),
                    height=int(box.height),
                    confidence=score,
                )
            )
        return tuple(boxes)


def install_mediapipe_detector() -> bool:
    """Register the real detector, if this image has one. Returns whether it did.

    Called from the ``worker-ml`` startup path. Returns a bool rather than
    raising because *not* having MediaPipe is the correct, expected state in
    four of the six images — and returns it rather than swallowing it silently
    so the caller can log the difference between "this image is not supposed to
    redact" and "this image is supposed to redact and cannot".
    """
    from nemesis.config import get_settings  # avoids an import cycle

    # The import check comes first, and the order is the whole point. Four of
    # the six images in this deployment do not carry MediaPipe and are never
    # asked to redact — for them this is a calm INFO about an expected state.
    # Checking the model path first would have made every one of those workers
    # log a WARNING about a missing model it has no use for, on every pool
    # child, which is how a genuinely important warning stops being read.
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        log.info(
            "face_detector_not_installed",
            note="this image does not carry the ml extra; expected outside worker-ml",
        )
        return False

    settings = get_settings()
    # ``<cache>/mediapipe/<file>``, matching where ``scripts/fetch_models.py``
    # puts it. The subdirectory is not decoration: the cache holds five model
    # families and a flat layout would make "which of these is the face
    # detector" a question you answer by recognising a filename.
    model_path = (
        settings.model_cache_dir / MODEL_SUBDIRECTORY / settings.models.face_detector_model_file
    )
    if not model_path.exists():
        # A warning, unlike the branch above: this image *is* supposed to
        # redact and cannot, so every complaint carrying a photograph will halt.
        log.warning(
            "face_detector_model_absent",
            path=str(model_path),
            consequence="the trust stage will halt every complaint carrying a photo",
            remedy="run `nem models` to fetch the pinned .tflite bundle",
        )
        return False

    register_detector(
        MediaPipeFaceDetector(
            model_path=str(model_path),
            min_confidence=settings.models.face_detector_min_confidence,
        )
    )
    return True


__all__ = [
    "DETECTION_MARGIN_FRACTION",
    "MODEL_SUBDIRECTORY",
    "FaceBox",
    "FaceDetector",
    "MediaPipeFaceDetector",
    "active_detector",
    "detector_is_registered",
    "detector_scope",
    "install_mediapipe_detector",
    "register_detector",
]
