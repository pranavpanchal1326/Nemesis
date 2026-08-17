#!/usr/bin/env python3
"""Download and verify every model weight the pipeline needs.

Run once; the weights land in the `modelcache` Docker volume mounted at /models
and the stack then runs **fully offline** (§6.6, §38.1). Lazy first-run
downloads are rejected deliberately: they turn a demo into a coin flip on venue
WiFi, and they hide a missing dependency until the worst possible moment.

Every model is not just fetched but *loaded and executed once*, because a
present file proves nothing. A truncated download, an incompatible weight
format, or a missing companion file all only surface at inference time.

    nem models          # fetch + verify
    nem models --verify # verify only, no network
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

MODELS_DIR = Path(os.environ.get("NEMESIS_MODEL_CACHE_DIR", "/models"))

# Kept in sync with nemesis.config.ModelSettings. Imported from there rather
# than restated, so this script cannot drift from what the pipeline loads.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class Step:
    def __init__(self, name: str, detail: str, fn: Callable[[], str]) -> None:
        self.name = name
        self.detail = detail
        self.fn = fn

    def run(self) -> bool:
        print(f"\n\033[1m── {self.name}\033[0m  ({self.detail})", flush=True)
        started = time.monotonic()
        try:
            result = self.fn()
        except Exception as exc:  # reported below, then surfaced as a failed step
            print(f"\033[31m   FAILED: {type(exc).__name__}: {exc}\033[0m", file=sys.stderr)
            return False
        print(f"\033[32m   OK\033[0m  {result}  [{time.monotonic() - started:.1f}s]")
        return True


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


def fetch_clip() -> str:
    """CLIP zero-shot classifier (§8.4, §43.1) and image embeddings (§14.1)."""
    import open_clip
    import torch

    from nemesis.config import get_settings

    cfg = get_settings().models
    model, _, preprocess = open_clip.create_model_and_transforms(
        cfg.clip_model, pretrained=cfg.clip_pretrained, cache_dir=str(MODELS_DIR / "clip")
    )
    tokenizer = open_clip.get_tokenizer(cfg.clip_model)
    model.eval()

    # Smoke test: a real forward pass on both towers. Verifies the weights load,
    # the towers agree on dimensionality, and the embedding width matches the
    # `halfvec(512)` column the dedup index is built on.
    with torch.no_grad():
        image = torch.zeros(1, 3, 224, 224)
        image_features = model.encode_image(image)
        text_features = model.encode_text(tokenizer(["a photo of a pothole in a road"]))

    if image_features.shape[-1] != cfg.clip_embedding_dim:
        raise ValueError(
            f"CLIP embedding dim {image_features.shape[-1]} != configured "
            f"{cfg.clip_embedding_dim}; the pgvector column would be wrong"
        )
    if text_features.shape[-1] != cfg.clip_embedding_dim:
        raise ValueError("CLIP text tower dimensionality disagrees with the image tower")

    del model, preprocess
    return f"{cfg.clip_model}/{cfg.clip_pretrained}, dim={cfg.clip_embedding_dim}"


def fetch_text_embeddings() -> str:
    """Multilingual sentence embeddings for dedup Stage 2 (ADR-0003)."""
    from sentence_transformers import SentenceTransformer

    from nemesis.config import get_settings

    cfg = get_settings().models
    model = SentenceTransformer(cfg.text_embedding_model, cache_folder=str(MODELS_DIR / "text"))

    # The check that matters: Devanagari and Latin renderings of the *same*
    # complaint must land close together, and an unrelated complaint must not.
    # This is precisely what all-MiniLM-L6-v2 fails, and failing it silently is
    # what would break dedup for Hindi/Marathi reporters.
    prefix = cfg.text_embedding_prefix
    pothole_en = "there is a large pothole on the main road"
    pothole_hi = "मुख्य सड़क पर एक बड़ा गड्ढा है"
    unrelated = "the streetlight outside the school is not working"

    embeddings = model.encode(
        [prefix + pothole_en, prefix + pothole_hi, prefix + unrelated],
        normalize_embeddings=True,
    )
    if embeddings.shape[1] != cfg.text_embedding_dim:
        raise ValueError(
            f"embedding dim {embeddings.shape[1]} != configured {cfg.text_embedding_dim}; "
            "the vector(384) column and HNSW index would be wrong"
        )

    same = float(embeddings[0] @ embeddings[1])
    different = float(embeddings[0] @ embeddings[2])
    if same <= different:
        raise ValueError(
            f"cross-lingual check failed: same-meaning similarity {same:.3f} is not "
            f"above unrelated similarity {different:.3f}. This model cannot support "
            "Hindi/Marathi deduplication (see ADR-0003)."
        )

    del model
    return (
        f"{cfg.text_embedding_model}, dim={cfg.text_embedding_dim}, "
        f"cross-lingual {same:.3f} > unrelated {different:.3f}"
    )


def fetch_whisper() -> str:
    """Speech-to-text for voice complaints (§8.4)."""
    from faster_whisper import WhisperModel

    from nemesis.config import get_settings

    cfg = get_settings().models
    model = WhisperModel(
        cfg.whisper_model,
        device="cpu",
        compute_type=cfg.whisper_compute_type,
        download_root=str(MODELS_DIR / "whisper"),
    )

    # Transcribe 0.5s of silence. Exercises the full CTranslate2 load path
    # without needing an audio fixture; a broken conversion fails right here.
    import numpy as np

    segments, _ = model.transcribe(np.zeros(8000, dtype=np.float32), language="hi")
    list(segments)

    del model
    return (
        f"{cfg.whisper_model} ({cfg.whisper_compute_type}), "
        f"languages={','.join(cfg.whisper_prefetch_languages)}"
    )


def fetch_face_detector() -> str:
    """Face blurring before any persistence (§22.1, DPDP).

    MediaPipe 1.x removed `mp.solutions`; the Tasks API loads an explicit
    .tflite bundle, which makes the model a genuine artefact to download and
    cache rather than something bundled with the wheel.
    """
    import urllib.request

    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import FaceDetector, FaceDetectorOptions

    from nemesis.config import get_settings

    cfg = get_settings().models
    target_dir = MODELS_DIR / "mediapipe"
    target_dir.mkdir(parents=True, exist_ok=True)
    model_path = target_dir / cfg.face_detector_model_file

    if not model_path.exists():
        if os.environ.get("HF_HUB_OFFLINE") == "1":
            raise FileNotFoundError(f"{model_path} missing and running in offline mode")
        tmp = model_path.with_suffix(".partial")
        # Download to a temp name and rename only on success, so an interrupted
        # fetch cannot leave a truncated file that looks cached.
        urllib.request.urlretrieve(cfg.face_detector_model_url, tmp)
        tmp.rename(model_path)

    detector = FaceDetector.create_from_options(
        FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            min_detection_confidence=cfg.face_detector_min_confidence,
        )
    )
    detector.detect(
        mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((256, 256, 3), dtype=np.uint8))
    )
    detector.close()

    return (
        f"{cfg.face_detector_model_file} "
        f"({_human(model_path.stat().st_size)}, conf>={cfg.face_detector_min_confidence})"
    )


def check_ollama() -> str:
    """The Investigation Agent's LLM (§12.4), served from the host GPU."""
    import json
    import urllib.request

    from nemesis.config import get_settings

    cfg = get_settings().ollama
    url = cfg.base_url.rstrip("/") + "/api/tags"
    with urllib.request.urlopen(url, timeout=10) as response:
        available = {m["name"] for m in json.load(response)["models"]}

    if cfg.model not in available:
        raise ValueError(
            f"{cfg.model} not present on the Ollama host. Run: ollama pull {cfg.model}"
        )
    return f"{cfg.model} available at {cfg.base_url}"


STEPS = [
    Step("CLIP", "zero-shot classification + image embeddings", fetch_clip),
    Step("Text embeddings", "multilingual, dedup Stage 2", fetch_text_embeddings),
    Step("faster-whisper", "Hindi/Marathi/English transcription", fetch_whisper),
    Step("MediaPipe", "face blur before storage", fetch_face_detector),
    Step("Ollama", "Investigation Agent LLM (host GPU)", check_ollama),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="load models from cache and run smoke tests without downloading",
    )
    args = parser.parse_args()

    if args.verify:
        # Force the HF stack offline so a cache miss fails loudly instead of
        # quietly reaching for the network. This is the air-gap rehearsal.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    mode = "VERIFY (offline)" if args.verify else "FETCH"
    print(f"\033[1mNEMESIS model weights — {mode}\033[0m")
    print(f"cache: {MODELS_DIR}")

    usage = shutil.disk_usage(MODELS_DIR)
    print(f"disk:  {_human(usage.free)} free")

    before = _dir_size(MODELS_DIR)
    failures = [step.name for step in STEPS if not step.run()]
    after = _dir_size(MODELS_DIR)

    print(f"\ncache size: {_human(after)}", end="")
    if after > before:
        print(f"  (+{_human(after - before)} this run)")
    else:
        print()

    if failures:
        print(f"\033[31m\nFAILED: {', '.join(failures)}\033[0m", file=sys.stderr)
        return 1
    print("\033[32m\nAll models present and verified.\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
