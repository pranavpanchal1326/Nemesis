"""The real models, behind the ``encoders`` protocols.

Every class here is *defined* in all six images and *instantiated* in one. That
split is the same one ``trust.detectors.MediaPipeFaceDetector`` makes and it
matters for the same reason: importing this module must cost nothing, so every
heavy import sits inside a method and the constructor stores paths and settings
rather than weights.

**Loading goes through the model registry, never through a module global.** A
``@lru_cache`` on a loader function would give the same "load once" behaviour and
none of the properties the phase needs: no ceiling, no eviction, no
single-flight guard across the four Celery children that all want CLIP in the
same second, and no way for an operator to ask what is resident. See
``perception.registry``.

**Nothing here decides anything.** These classes turn bytes into vectors and
audio into text. Which categories exist, which prompts describe them, how a
similarity becomes a confidence, and when to decline are all elsewhere — in
tenant data and in governed policy — and keeping this file free of all four is
what makes swapping a checkpoint a bounded change rather than a re-tuning.
"""

from __future__ import annotations

import io
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from nemesis.observability import metrics
from nemesis.observability.logging import get_logger
from nemesis.perception.encoders import (
    EncoderKind,
    Transcript,
    encoder_is_registered,
    l2_normalise,
    register_encoder,
)
from nemesis.perception.errors import ModelLoadError
from nemesis.perception.registry import REGISTRY

log = get_logger(__name__)

#: Subdirectories under ``model_cache_dir`` that ``scripts/fetch_models.py``
#: writes into. Stated here rather than derived, for the reason
#: ``trust.detectors.MODEL_SUBDIRECTORY`` gives: a loader looking in the wrong
#: place reports "model absent" and degrades every complaint, which is correct
#: behaviour for a genuinely missing model and a confusing way to find a typo.
CLIP_SUBDIRECTORY: Final = "clip"
TEXT_SUBDIRECTORY: Final = "text"
WHISPER_SUBDIRECTORY: Final = "whisper"


class OpenClipEncoder:
    """CLIP ViT-B-32, both towers, through ``open_clip``.

    **Both towers on one object because they are one model.** Scoring an image
    embedding against prompts embedded by a different checkpoint produces numbers
    in exactly the right range that mean nothing at all, and the cheapest way to
    make that unrepresentable is to never hand out one tower without the other.

    **``torch.no_grad`` and a thread cap, both non-negotiable here.** Gradients
    would allocate an autograd graph per forward pass on a worker with the
    tightest memory cap in the deployment, for a backward pass that never comes;
    and torch defaults to one thread per core, which on this host means inference
    starves Postgres and Redis of exactly the CPU the rest of the pipeline needs.
    ``ModelSettings.torch_num_threads`` is the cap and it is applied at load
    rather than per call, because setting it per call is a documented way to
    deadlock torch's own thread pool.
    """

    def __init__(
        self,
        *,
        model_name: str,
        pretrained: str,
        cache_dir: Path,
        dimensions: int,
        threads: int,
    ) -> None:
        self._model_name = model_name
        self._pretrained = pretrained
        self._cache_dir = cache_dir
        self._dimensions = dimensions
        self._threads = threads
        self._loaded: tuple[Any, Any, Any] | None = None

    @property
    def model_id(self) -> str:
        return f"open_clip:{self._model_name}/{self._pretrained}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def footprint_bytes(self) -> int:
        from nemesis.config import get_settings

        return get_settings().perception.clip_footprint_mb * 1024 * 1024

    def _ensure(self) -> tuple[Any, Any, Any]:
        if self._loaded is not None:
            return self._loaded

        def _load() -> tuple[Any, Any, Any]:
            import open_clip
            import torch

            torch.set_num_threads(self._threads)
            model, _, preprocess = open_clip.create_model_and_transforms(
                self._model_name,
                pretrained=self._pretrained,
                cache_dir=str(self._cache_dir),
            )
            tokenizer = open_clip.get_tokenizer(self._model_name)
            model.eval()
            width = int(model.text_projection.shape[-1]) if hasattr(model, "text_projection") else 0
            if width and width != self._dimensions:
                raise ModelLoadError(
                    f"{self.model_id} embeds at {width} dimensions but the "
                    f"halfvec({self._dimensions}) column and its HNSW index expect "
                    f"{self._dimensions}; storing these would corrupt dedup Stage 2"
                )
            return model, preprocess, tokenizer

        self._loaded = REGISTRY.get(
            self.model_id, footprint_bytes=self.footprint_bytes, load=_load, kind="clip"
        )
        return self._loaded

    def encode_image(self, data: bytes) -> tuple[float, ...]:
        import torch
        from PIL import Image

        model, preprocess, _ = self._ensure()
        started = time.monotonic()
        with Image.open(io.BytesIO(data)) as image:
            # ``convert("RGB")`` before preprocessing, always. A greyscale or
            # palettised JPEG has one or zero channels and the transform stack
            # fails deep inside torchvision on the first real photograph rather
            # than in any test — and a CMYK scan silently embeds inverted.
            tensor = preprocess(image.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            features = model.encode_image(tensor)
        metrics.perception_inference_seconds.labels(operation="encode_image").observe(
            time.monotonic() - started
        )
        return l2_normalise(features[0].tolist())

    def encode_prompts(self, prompts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        import torch

        model, _, tokenizer = self._ensure()
        if not prompts:
            return ()
        started = time.monotonic()
        with torch.no_grad():
            features = model.encode_text(tokenizer(list(prompts)))
        metrics.perception_inference_seconds.labels(operation="encode_prompts").observe(
            time.monotonic() - started
        )
        return tuple(l2_normalise(row.tolist()) for row in features)


class E5TextEncoder:
    """multilingual-e5-small through ``sentence_transformers``.

    ADR-0003's model, and the prefixes are not optional decoration: e5 is trained
    asymmetrically and omitting them measurably degrades retrieval — which for
    this system means dedup Stage 2 quietly working less well for exactly the
    Hindi and Marathi reporters ADR-0003 chose this model to serve.
    """

    def __init__(self, *, model_name: str, cache_dir: Path, dimensions: int) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._dimensions = dimensions
        self._model: Any | None = None

    @property
    def model_id(self) -> str:
        return f"sentence_transformers:{self._model_name}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def footprint_bytes(self) -> int:
        from nemesis.config import get_settings

        return get_settings().perception.text_encoder_footprint_mb * 1024 * 1024

    def _ensure(self) -> Any:
        if self._model is not None:
            return self._model

        def _load() -> Any:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(self._model_name, cache_folder=str(self._cache_dir))
            width = int(model.get_sentence_embedding_dimension() or 0)
            if width != self._dimensions:
                raise ModelLoadError(
                    f"{self.model_id} embeds at {width} dimensions but the "
                    f"vector({self._dimensions}) column and its HNSW index expect "
                    f"{self._dimensions}"
                )
            return model

        self._model = REGISTRY.get(
            self.model_id, footprint_bytes=self.footprint_bytes, load=_load, kind="text"
        )
        return self._model

    def encode(self, texts: Sequence[str], *, prefix: str) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        model = self._ensure()
        started = time.monotonic()
        vectors = model.encode(
            [f"{prefix}{text}" for text in texts],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        metrics.perception_inference_seconds.labels(operation="encode_text").observe(
            time.monotonic() - started
        )
        # Normalised again rather than trusted. ``normalize_embeddings`` is a
        # keyword argument on a library this project pins but does not control,
        # and a silently un-normalised vector turns every cosine threshold in
        # Phase 10 into a number about magnitudes.
        return tuple(l2_normalise(list(row)) for row in vectors)


class WhisperTranscriber:
    """faster-whisper ``small`` at int8, on CPU (ADR-0002).

    **Language detection is the model's, and the tenant's locales only break
    ties.** Constraining detection to declared locales would mistranscribe the
    citizen who speaks something the tenant did not list — the exact population
    §8.4 exists for. What the hint does is decide a close call in favour of a
    language the tenant has prompts for, and record the confidence so a reviewer
    can see it was close.
    """

    def __init__(self, *, model_name: str, compute_type: str, cache_dir: Path) -> None:
        self._model_name = model_name
        self._compute_type = compute_type
        self._cache_dir = cache_dir
        self._model: Any | None = None

    @property
    def model_id(self) -> str:
        return f"faster_whisper:{self._model_name}/{self._compute_type}"

    @property
    def footprint_bytes(self) -> int:
        from nemesis.config import get_settings

        return get_settings().perception.transcriber_footprint_mb * 1024 * 1024

    def _ensure(self) -> Any:
        if self._model is not None:
            return self._model

        def _load() -> Any:
            from faster_whisper import WhisperModel

            return WhisperModel(
                self._model_name,
                device="cpu",
                compute_type=self._compute_type,
                download_root=str(self._cache_dir),
            )

        self._model = REGISTRY.get(
            self.model_id, footprint_bytes=self.footprint_bytes, load=_load, kind="whisper"
        )
        return self._model

    def transcribe(self, data: bytes, *, locales: Sequence[str]) -> Transcript:
        model = self._ensure()
        started = time.monotonic()
        # A file-like object rather than a temporary file: faster-whisper hands
        # it to av/ffmpeg, which reads a stream perfectly well, and a temp file
        # would put a citizen's unredacted audio on a second disk location with
        # a second retention story to get wrong (ADR-0031).
        segments, info = model.transcribe(
            io.BytesIO(data),
            beam_size=1,
            vad_filter=True,
            # Whichever language the model detects. The hint below is applied
            # *after* detection rather than as a constraint — see the docstring.
            task="transcribe",
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        elapsed = time.monotonic() - started
        metrics.perception_inference_seconds.labels(operation="transcribe").observe(elapsed)

        language = str(getattr(info, "language", "") or "und")
        confidence = float(getattr(info, "language_probability", 0.0) or 0.0)
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        language, confidence = _prefer_declared(language, confidence, locales)
        return Transcript(
            text=text,
            language=language,
            language_confidence=confidence,
            duration_seconds=duration,
            model_id=self.model_id,
        )


def _prefer_declared(language: str, confidence: float, locales: Sequence[str]) -> tuple[str, float]:
    """Map a detected language onto a declared locale when one matches.

    Whisper returns a bare ISO-639-1 code (``mr``); a tenant declares BCP-47
    (``mr-IN``). Without this, a tenant that declared ``mr-IN`` would never find
    its Marathi prompt set, and the failure would look like the prompts being
    missing rather than the tags disagreeing. The confidence is passed through
    untouched — matching a tag is a naming fact and says nothing about how sure
    the model was.
    """
    if not locales:
        return language, confidence
    for declared in locales:
        if declared == language or declared.split("-", 1)[0] == language:
            return declared, confidence
    return language, confidence


def install_perception_encoders() -> bool:
    """Register the real encoders, if this image has them. Returns whether it did.

    Called from the ``worker-ml`` startup path. Returns a bool rather than
    raising, for the reason ``install_mediapipe_detector`` gives: *not* having
    torch is the correct and expected state in four of the six images, and the
    caller needs to tell "this image is not supposed to classify" apart from
    "this image is supposed to classify and cannot".

    **The import check comes first and the order is the point.** Checking the
    cache directory first would make every worker without the ml extra log a
    warning about weights it has no use for, on every pool child, which is how a
    genuinely important warning stops being read.
    """
    try:
        import open_clip  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError:
        log.info(
            "perception_encoders_not_installed",
            note="this image does not carry the ml extra; expected outside worker-ml",
        )
        return False

    from nemesis.config import get_settings

    settings = get_settings()
    cache = settings.model_cache_dir
    models = settings.models

    if not encoder_is_registered(EncoderKind.IMAGE):
        register_encoder(
            EncoderKind.IMAGE,
            OpenClipEncoder(
                model_name=models.clip_model,
                pretrained=models.clip_pretrained,
                cache_dir=cache / CLIP_SUBDIRECTORY,
                dimensions=models.clip_embedding_dim,
                threads=models.torch_num_threads,
            ),
        )
    if not encoder_is_registered(EncoderKind.TEXT):
        register_encoder(
            EncoderKind.TEXT,
            E5TextEncoder(
                model_name=models.text_embedding_model,
                cache_dir=cache / TEXT_SUBDIRECTORY,
                dimensions=models.text_embedding_dim,
            ),
        )

    # Whisper is registered separately and its absence is survivable: a
    # deployment that never receives voice complaints does not need it, and a
    # missing transcriber degrades only submissions carrying audio. CLIP and e5
    # are not optional in the same way — without them nothing can be classified
    # or deduplicated at all.
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        log.warning(
            "transcriber_not_installed",
            consequence="voice complaints will park as pending_classification",
            remedy="install the ml extra's faster-whisper, or accept text-only intake",
        )
        return True

    if not encoder_is_registered(EncoderKind.TRANSCRIBE):
        register_encoder(
            EncoderKind.TRANSCRIBE,
            WhisperTranscriber(
                model_name=models.whisper_model,
                compute_type=models.whisper_compute_type,
                cache_dir=cache / WHISPER_SUBDIRECTORY,
            ),
        )
    return True


def warm_encoders() -> dict[str, str]:
    """Force every registered encoder to load now. Returns model id → outcome.

    **Why warming is a separate call from registering.** Registration is cheap
    and says what this process *can* do; warming is forty seconds of weight
    loading and says it is *ready to*. Collapsing them would make worker startup
    block on three cold loads before the process could report anything at all —
    including the fact that one of them failed.

    Failures are collected rather than raised. A worker whose transcriber will
    not load can still classify photographs, and taking the container down for it
    would turn a partial capability loss into a total one.
    """
    from nemesis.perception.encoders import (
        active_image_encoder,
        active_text_encoder,
        active_transcriber,
    )

    outcomes: dict[str, str] = {}
    for label, accessor in (
        ("image", active_image_encoder),
        ("text", active_text_encoder),
        ("transcribe", active_transcriber),
    ):
        encoder: Any
        try:
            encoder = accessor()
        except Exception as exc:  # not registered in this image — expected
            outcomes[label] = f"absent: {type(exc).__name__}"
            continue
        try:
            # The smallest call that forces the weights: embedding one prompt for
            # the two encoders, and nothing at all for the transcriber, whose
            # loader is reachable only through a real audio buffer. Whisper is
            # warmed by ``_ensure`` on its first use instead, and that asymmetry
            # is recorded here rather than papered over with a synthetic clip
            # that would exercise ffmpeg rather than the model.
            probe = getattr(encoder, "encode_prompts", None)
            if probe is not None:
                probe(["warm"])
            elif hasattr(encoder, "encode"):
                encoder.encode(["warm"], prefix="passage: ")
            else:
                encoder._ensure()
            outcomes[label] = f"warm: {encoder.model_id}"
        except Exception as exc:
            outcomes[label] = f"failed: {type(exc).__name__}: {exc}"
            log.error(
                "perception_encoder_warm_failed",
                kind=label,
                error_type=type(exc).__name__,
                runbook="docs/runbooks/perception-model-unavailable.md",
            )
    return outcomes


__all__ = [
    "CLIP_SUBDIRECTORY",
    "TEXT_SUBDIRECTORY",
    "WHISPER_SUBDIRECTORY",
    "E5TextEncoder",
    "OpenClipEncoder",
    "WhisperTranscriber",
    "install_perception_encoders",
    "warm_encoders",
]
