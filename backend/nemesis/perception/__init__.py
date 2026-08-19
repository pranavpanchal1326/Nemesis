"""Phase 9 — the perception layer and the model registry.

What this package does, in one sentence: it turns a citizen's photograph, voice
note and description into a taxonomy category, an embedding pair, and an honest
confidence — or into a refusal to guess.

**The line this package holds.** Nothing in here decides what a category *is*
(taxonomy, Phase 5), what describes one (prompt sets, Phase 5), how sure is sure
enough (``perception_calibration``, Phase 6), or what to do with a report that
cannot be classified (§24.2, Phase 3). It runs models and does arithmetic. That
line is what makes "a new tenant category is classifiable by adding prompts
alone" — the phase gate — true rather than aspirational: adding a category
touches tenant data and nothing in this directory.

The modules split along what fails independently:

``encoders``
    The three protocols and the process-wide registration seam. Holds no
    weights, imports no torch, and is what a deterministic fake implements.
``backends``
    The real CLIP, e5 and faster-whisper adapters. Defined in every image,
    instantiated in one.
``registry``
    Load once, load together, stay under a ceiling. The single-flight guard that
    stops four Celery children each allocating their own copy of CLIP.
``prompts``
    Tenant prompt sets resolved, hashed, and embedded — the *pair* half of
    "versioned model and prompt-set pairs".
``scoring``
    The zero-shot arithmetic, with no model and no database anywhere near it, so
    the decision rule is assertable exactly.
``calibration``
    The governed document flattened into what the scorer reads.
``media``
    The two reads: the redacted image, and the quarantined audio.
``embeddings``
    The only writer of the two vector columns, and the documented exception to
    the projection rule.
``stage``
    The pipeline provider that runs all of the above in order, including the
    second §11.2 safety pass that Phase 8 could not perform.
``harness``
    Per-category precision, recall and F1 over a labelled corpus, published as a
    committed artefact and reproduced by one command.

Nothing here commits a transaction, and nothing raises an HTTP error — the same
rule ``policy`` and ``simulation`` follow, for the same reason: the stage runs in
Celery, the harness runs in a script, and a package that knew about either could
not be called from the other.
"""

from __future__ import annotations

from nemesis.perception.errors import (
    CalibrationError,
    EncoderUnavailableError,
    ModelCapacityError,
    ModelLoadError,
    PerceptionError,
    PromptSetUnavailableError,
    TranscriptionUnavailableError,
)

__all__ = [
    "CalibrationError",
    "EncoderUnavailableError",
    "ModelCapacityError",
    "ModelLoadError",
    "PerceptionError",
    "PromptSetUnavailableError",
    "TranscriptionUnavailableError",
]
