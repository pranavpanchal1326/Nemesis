"""The three encoders, behind one seam, with the weights loaded lazily.

**Why a seam rather than direct imports.** ``open_clip``,
``sentence_transformers`` and ``faster_whisper`` live only in the ``ml`` extra,
which only ``worker-ml`` carries (§0's memory split). Every other process — the
API, ``worker-io``, the test suite — imports this module and finds nothing
registered, and that is a fact about the deployment rather than a problem to
route around. The same argument ``trust.detectors`` makes, reached independently
for the same reason: an import that pulls 600 MB of torch into the API is not a
dependency mistake, it is a decision to run inference in the wrong process.

**Why the protocols speak in bytes and floats rather than in library types.**
``ImageEncoder`` takes encoded image bytes and returns tuples of floats;
``TextEncoder`` takes strings and returns tuples of floats. Nothing in either
signature names torch, PIL, or a tokenizer, which is what makes a deterministic
fake implementable in ten lines — and a deterministic fake is the only way to
assert what the *scoring* does exactly, rather than statistically. A seam only
one implementation can pass through is not a seam.

**Why every encoder carries a ``model_id`` and a ``footprint_bytes``.** The id
is written into ``classification_scored.model_id`` and is the only thing that
makes "this report was classified by that model" a checkable claim after an
upgrade — the same argument ``FaceDetector.detector_id`` makes. The footprint is
what the registry's ceiling is enforced against; it is a *declared* number
rather than a measured one, because resident set size per model inside one
process is not something the OS will report truthfully, and a declared number
that is 20% wrong still stops the third 600 MB model from loading into a
container capped at 2 GB.

**Normalisation happens here, not at the call site.** Every vector this module
returns is L2-normalised, so a cosine similarity is a dot product and the
pgvector columns hold comparable magnitudes. Leaving it to callers means one of
them eventually forgets, and the symptom is a dedup threshold that behaves
differently for text than for images with nothing in the log to explain it.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from nemesis.observability.logging import get_logger
from nemesis.perception.errors import EncoderUnavailableError, ModelLoadError

log = get_logger(__name__)


class EncoderKind(StrEnum):
    """What a registered encoder does.

    A closed set, unlike the taxonomy's ``encoder`` column, which is deliberately
    free text so a third prompt family needs no migration. These are not the same
    vocabulary: that column names which *prompts* apply, this names which
    *capability* a process has. A tenant can invent the first; only a release can
    add the second.
    """

    IMAGE = "image"
    TEXT = "text"
    TRANSCRIBE = "transcribe"


#: The e5 family requires asymmetric prefixes and measurably degrades without
#: them. Two constants rather than one setting, because the asymmetry is the
#: point: a stored complaint is a passage, an incoming comparison is a query, and
#: embedding both as ``"query: "`` is the mistake that looks like it works.
QUERY_PREFIX: Final = "query: "
PASSAGE_PREFIX: Final = "passage: "


@dataclass(frozen=True, slots=True)
class Transcript:
    """What a transcriber heard, and how sure it was about the language.

    ``language_confidence`` is carried separately from the text because the two
    fail independently: faster-whisper will happily transcribe Marathi audio as
    phonetically-plausible Hindi at 0.4 confidence, and a reader that sees only
    ``language`` has no way to know it was a coin flip. §8.4's promise is that a
    voice complaint in the citizen's own language works; measuring whether it did
    requires the number, not just the label.

    ``duration_seconds`` is the audio's length, not the inference time. It is
    what the §27.1 budget is normalised against — a 4-second clip and a 90-second
    one are not comparable latencies, and a p95 over both mixed together tracks
    the length distribution rather than the model.
    """

    text: str
    language: str
    language_confidence: float
    duration_seconds: float
    model_id: str


class ImageEncoder(Protocol):
    """CLIP's image tower, plus the text tower for the prompts it scores against.

    Both towers live on one protocol because they are one model and must stay
    one: scoring an image embedding against prompts embedded by a *different*
    CLIP checkpoint produces numbers in the right range that mean nothing. Two
    protocols would make that mistake expressible.
    """

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def footprint_bytes(self) -> int: ...

    def encode_image(self, data: bytes) -> tuple[float, ...]:
        """One L2-normalised embedding for encoded image bytes."""
        ...

    def encode_prompts(self, prompts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """One L2-normalised embedding per prompt, in the order given."""
        ...


class TextEncoder(Protocol):
    """multilingual-e5-small — dedup Stage 2's text side, and text-side scoring."""

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def footprint_bytes(self) -> int: ...

    def encode(self, texts: Sequence[str], *, prefix: str) -> tuple[tuple[float, ...], ...]:
        """One L2-normalised embedding per text, prefixed as e5 requires."""
        ...


class Transcriber(Protocol):
    """faster-whisper, across whichever locales the tenant declared."""

    @property
    def model_id(self) -> str: ...

    @property
    def footprint_bytes(self) -> int: ...

    def transcribe(self, data: bytes, *, locales: Sequence[str]) -> Transcript:
        """Text and detected language for encoded audio bytes.

        ``locales`` is a *hint*, not a filter. Whisper detects language on its
        own, and constraining it to the tenant's declared set would silently
        mistranscribe a citizen speaking something the tenant did not list —
        which is exactly the population §8.4 exists for. What the hint does is
        break the tie: where detection is close between a declared locale and an
        undeclared one, the declared one wins, and the confidence records that it
        was close.
        """
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# One encoder per kind per process, exactly like ``trust.detectors``. Not per
# tenant: two tenants classified by two different CLIP checkpoints would produce
# confidences on two different scales, and every threshold in every calibration
# document would silently mean something different for each of them.

_lock = threading.Lock()
_encoders: dict[str, Any] = {}


def register_encoder(kind: EncoderKind, encoder: Any) -> None:
    """Bind this process's encoder for a kind. Called once, at worker startup.

    Refuses to replace an existing registration, for the reason
    ``pipeline.stages.register_provider`` gives: two registrations means two
    parts of the system each assumed they owned it, and import order is the worst
    available tiebreaker. Use ``encoder_scope`` in tests.
    """
    with _lock:
        existing = _encoders.get(kind.value)
        if existing is not None:
            raise RuntimeError(
                f"an encoder for {kind.value!r} is already registered "
                f"({getattr(existing, 'model_id', existing)!r}); the process has exactly "
                f"one, because two would mean two confidence scales in one deployment"
            )
        _encoders[kind.value] = encoder
    log.info(
        "perception_encoder_registered",
        kind=kind.value,
        model_id=getattr(encoder, "model_id", "unknown"),
    )


def active_image_encoder() -> ImageEncoder:
    """The registered image encoder, or a refusal. Never a stand-in."""
    return _require(EncoderKind.IMAGE)  # type: ignore[no-any-return]


def active_text_encoder() -> TextEncoder:
    """The registered text encoder, or a refusal. Never a stand-in."""
    return _require(EncoderKind.TEXT)  # type: ignore[no-any-return]


def active_transcriber() -> Transcriber:
    """The registered transcriber, or a refusal. Never a stand-in."""
    return _require(EncoderKind.TRANSCRIBE)  # type: ignore[no-any-return]


def _require(kind: EncoderKind) -> Any:
    encoder = _encoders.get(kind.value)
    if encoder is None:
        raise EncoderUnavailableError(
            f"no {kind.value} encoder is registered in this process, so nothing here "
            f"can say what this submission contains. The classification stage runs on "
            f"the `ml` queue, served by the only image carrying the ml extra; a worker "
            f"on another queue reaching this line means the stage was routed wrongly."
        )
    return encoder


def registered_kinds() -> frozenset[str]:
    """For readiness reporting and for the gate. Never for a fallback."""
    return frozenset(_encoders)


def encoder_is_registered(kind: EncoderKind) -> bool:
    return kind.value in _encoders


@contextmanager
def encoder_scope(kind: EncoderKind, encoder: Any) -> Iterator[None]:
    """Register an encoder for the duration of a block.

    Exists for tests and is named so that is obvious — the same honesty
    ``pipeline.stages.provider_scope`` and ``trust.detectors.detector_scope``
    apply. The scoring guarantees are asserted through this seam with
    deterministic encoders, which is the only way to make "the winning category
    is the one whose prompts scored highest" an exact claim rather than a
    statistical one about a real model's output.
    """
    with _lock:
        previous = _encoders.get(kind.value)
        _encoders[kind.value] = encoder
    try:
        yield
    finally:
        with _lock:
            if previous is None:
                _encoders.pop(kind.value, None)
            else:
                _encoders[kind.value] = previous


def reset_encoders() -> None:
    """Drop every registration. For worker shutdown and for test teardown."""
    with _lock:
        _encoders.clear()


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------


def l2_normalise(vector: Sequence[float]) -> tuple[float, ...]:
    """The unit vector, or a refusal for the zero vector.

    A zero vector has no direction, so its cosine similarity against everything
    is either undefined or — if the norm is quietly clamped to 1 — exactly zero
    against every category, which reads downstream as "the model was certain this
    is nothing" rather than "the model produced nothing". Only reachable from a
    broken model load, which is why it raises instead of returning zeros.
    """
    norm = math.sqrt(sum(float(component) * float(component) for component in vector))
    if norm <= 0.0 or not math.isfinite(norm):
        raise ModelLoadError(
            "an encoder returned a zero or non-finite vector, which has no direction "
            "and would score identically against every category. That is a broken "
            "model load, not a low-confidence result."
        )
    return tuple(float(component) / norm for component in vector)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """The dot product of two vectors this module normalised.

    Named ``cosine`` because that is what it means here, and it validates the
    widths rather than trusting them: comparing a 512-wide CLIP embedding against
    a 384-wide e5 embedding truncates to the shorter one and returns a plausible
    number, which is the most expensive silent bug available in this package.
    """
    if len(left) != len(right):
        raise ValueError(
            f"cannot compare a {len(left)}-dimensional vector with a "
            f"{len(right)}-dimensional one; these are different encoders, and the "
            f"result would be a plausible number with no meaning"
        )
    return float(sum(a * b for a, b in zip(left, right, strict=True)))


__all__ = [
    "PASSAGE_PREFIX",
    "QUERY_PREFIX",
    "EncoderKind",
    "ImageEncoder",
    "TextEncoder",
    "Transcriber",
    "Transcript",
    "active_image_encoder",
    "active_text_encoder",
    "active_transcriber",
    "cosine",
    "encoder_is_registered",
    "encoder_scope",
    "l2_normalise",
    "register_encoder",
    "registered_kinds",
    "reset_encoders",
]
