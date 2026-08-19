"""Phase 8 — the trust and safety spine.

§11 is described in the blueprint as the highest credibility-per-hour item in
the system, and this package is the whole of it: the deterministic safety
fail-safe, the anti-fraud checks on submission, coordinated-abuse detection, the
§22.1 face-blur obligation, and the human review queue every one of them
terminates in.

**Six capabilities, and they answer different questions:**

``safety``
    *Is this dangerous?* §11.2's deterministic fail-safe, executing the Phase 6
    ruleset as a hard rule on its own queue, bypassing the scoring pipeline
    entirely when it fires.
``exif``
    *Was this photograph taken where the report says?* §11.1's cross-check —
    and, just as importantly, the three-way distinction between confirmed,
    contradicted, and simply absent.
``phash``
    *Has this file been sent before?* §11.1's perceptual hash, tolerant of the
    re-compression and resizing every share flow applies.
``abuse``
    *Is this one of many?* §11.3's two detectors — one device many reports, and
    many devices one place — which flag and never block.
``redaction``
    *Can this be shown to anyone?* §22.1's face blur, and the only code in the
    system that writes an image anything will serve.
``review``
    *Who decides?* §11.4's queue, where every flag above terminates, and where
    every human judgement becomes a Phase 11 training label.

**The package's own division of labour** mirrors Phase 7's, because the same
property is being defended: what is *pure* is separated from what touches the
database or the disk, so the parts carrying an obligation can be tested
exhaustively. ``exif.cross_check``, ``phash.hamming`` and ``FaceBox.expanded``
are total functions of their arguments; ``verification`` is the one module that
orchestrates them against a session, a filesystem and a policy document.

**Nothing here commits**, the same contract ``policy``, ``control_plane`` and
``simulation`` state. The pipeline's transaction is the orchestrator's, and the
review queue's is the HTTP handler's.

**One thing to know before changing anything in here.** The §22.1 guarantee is
structural, not conventional: ``trust.redaction`` is the only writer of the
served media root, ``trust.verification`` is the only reader of the quarantine
root, and ``scripts/check_media_redaction.py`` fails the build if either stops
being true. If a change needs a second one of either, that is a design
conversation and an ADR, not a second call site.
"""

from __future__ import annotations

from nemesis.trust.errors import (
    MediaNotFoundError,
    RedactionError,
    RedactionFailedError,
    RedactionUnavailableError,
    ReviewConflictError,
    ReviewError,
    ReviewNotFoundError,
    ReviewValidationError,
    TrustError,
)

__all__ = [
    "MediaNotFoundError",
    "RedactionError",
    "RedactionFailedError",
    "RedactionUnavailableError",
    "ReviewConflictError",
    "ReviewError",
    "ReviewNotFoundError",
    "ReviewValidationError",
    "TrustError",
]
