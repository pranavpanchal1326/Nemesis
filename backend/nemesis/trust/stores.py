"""The two media roots, resolved from settings in one place each.

Small, and separate from both ``ingest.media`` and ``trust.redaction`` on
purpose. ``check_media_redaction.py`` asserts that ``RedactedStore`` is
constructed in exactly one place and ``MediaStore`` in exactly two — here and in
the ingest handler that writes quarantine. A constructor scattered across six
call sites is a guarantee that has to be re-checked at each of them; a
constructor with one call site is a guarantee you can read.

Cached per process. A ``Path`` join is cheap, but ``get_settings`` is not free
and this runs on the submission path — and more importantly, one instance means
one root, so a test that redirects ``upload_dir`` cannot end up with two halves
of the pipeline pointing at different directories.
"""

from __future__ import annotations

from functools import lru_cache

from nemesis.config import get_settings
from nemesis.ingest.media import MediaStore
from nemesis.trust.redaction import RedactedStore


@lru_cache(maxsize=1)
def media_store() -> MediaStore:
    """Quarantine — unredacted originals, never served. See ``trust.redaction``."""
    return MediaStore(get_settings().upload_dir)


@lru_cache(maxsize=1)
def redacted_store() -> RedactedStore:
    """The served root. Written only by ``trust.redaction.redact_image``."""
    return RedactedStore(get_settings().upload_dir)


def reset() -> None:
    """Drop the cached stores.

    For tests that point ``upload_dir`` somewhere else, and named ``reset``
    rather than exposed as a settings hook because production never calls it:
    the upload root is fixed at boot, and a path that could change under a
    running worker is a path where half the redacted images end up unreachable.
    """
    media_store.cache_clear()
    redacted_store.cache_clear()


__all__ = ["media_store", "redacted_store", "reset"]
