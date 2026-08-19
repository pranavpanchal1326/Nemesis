"""Reading a submission's media, from the copy each modality is allowed to see.

Two functions, in their own module, because between them they hold the phase's
sharpest constraint: **the classifier must never see an unredacted photograph,
and the transcriber has nowhere else to look.**

**Images come from the redacted store, always.** §22.1 blurs faces before
anything downstream is allowed to resolve the artefact, and the redacted copy is
the only one that exists as far as every phase after Phase 8 is concerned. That
is not a preference — a classifier reading quarantine would be a second reader of
unredacted bytes, and ``check_media_redaction.py`` exists precisely to make that
a build failure rather than a code review someone was tired during.

**Audio comes from quarantine, because there is no other copy.** Phase 8 redacts
images; there is no redacted audio artefact, because §22.1 is about faces and
there is no face in a waveform. This module is therefore listed as a third
``QUARANTINE_READER`` in that check, and the entry is defended rather than
assumed:

- It resolves an *audio* URI only, and refuses an image one by content type
  before it touches the disk. A caller that hands it a photo URI gets an error
  naming §22.1, not a decoded JPEG.
- It returns bytes to a transcriber, which returns text. No audio ever reaches
  an HTTP response, an event payload, or a second file (ADR-0031's reasoning,
  applied to the other medium).
- The honest limitation is recorded rather than hidden: a voice recording is
  identifying in a way §22 has not yet been asked about, retention for it rides
  on the same §22.4 clock as the photograph, and the day the blueprint asks for
  voice redaction this module is where that lands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nemesis.observability.logging import get_logger
from nemesis.perception.errors import PerceptionError
from nemesis.trust.stores import media_store, redacted_store

log = get_logger(__name__)

#: ``media_redacted.media_kind`` for a photograph. The projector writes it; this
#: is the reader, and the constant is named rather than inlined so the two
#: cannot disagree about spelling in a way that silently finds no images.
IMAGE_KIND = "image"


class MediaUnavailableError(PerceptionError):
    """The artefact this stage needs is not readable.

    Distinct from "the submission has no media", which is not an error at all —
    a text-only complaint is the common case. This means media was *promised* by
    the log and is not there: the retention sweep took it, the trust stage never
    produced a redacted copy, or something outside the retention path deleted it.
    """


def redacted_image_bytes(state: Mapping[str, Any]) -> bytes | None:
    """The blurred photograph, or ``None`` when this report carries no image.

    Reads ``redacted_media`` from the projected state rather than the
    ``submission_media`` table, for the reason ``StageContext`` gives: a provider
    that reached into a table could read a column no event explains, and §9.1's
    rule that current state is derived would quietly stop being true.

    Takes the *most recent* image artefact when a report carries several. Later
    is the right choice rather than an arbitrary one — a re-redaction after a
    detector upgrade appends a new entry, and classifying the superseded copy
    would mean scoring pixels the current §22.1 posture has already replaced.
    """
    artefacts: Sequence[Mapping[str, Any]] = state.get("redacted_media") or ()
    images = [item for item in artefacts if item.get("media_kind") == IMAGE_KIND]
    if not images:
        return None

    checksum = images[-1].get("redacted_sha256")
    if not isinstance(checksum, str) or not checksum:
        raise MediaUnavailableError(
            "a media_redacted event names an image with no redacted checksum, so the "
            "blurred copy cannot be located. The unredacted original is not an "
            "alternative — §22.1 has no degraded mode."
        )

    store = redacted_store()
    try:
        path = store.resolve(store.uri_for(checksum))
    except Exception as exc:
        raise MediaUnavailableError(
            f"the redacted image for this complaint is not on disk ({exc}). Either the "
            f"§22.4 retention sweep removed it — check submission_media.raw_purged_at — "
            f"or it was deleted outside the retention path. Classification stops here "
            f"rather than reaching for quarantine."
        ) from exc
    return path.read_bytes()


def audio_bytes(state: Mapping[str, Any]) -> bytes | None:
    """The submitted audio clip, or ``None`` when this report carries none.

    The one place in the system outside Phase 8 that resolves a quarantine URI —
    see the module docstring for why that is correct here and would not be for an
    image. The URI comes from ``complaint_submitted.audio_url``, which is written
    by the ingest handler and never by a client.
    """
    uri = state.get("audio_url")
    if not isinstance(uri, str) or not uri:
        return None

    # An image URI arriving through the audio field would mean the ingest handler
    # or an event payload has the two mixed up, and the consequence of guessing
    # is that this module hands unredacted photograph bytes to a caller. Checking
    # the extension is a weak test in general and an exact one here: quarantine
    # paths are minted by ``MediaStore`` from a content type it sniffed itself.
    if uri.rsplit(".", 1)[-1].lower() in {"jpg", "jpeg", "png", "webp"}:
        raise MediaUnavailableError(
            f"the audio field holds what looks like an image URI ({uri!r}). Refusing to "
            f"read it: this is the only quarantine read outside the trust stage, and it "
            f"exists for audio, which §22.1 does not redact. An image read here would be "
            f"a second path to unblurred pixels."
        )

    try:
        path = media_store().resolve(uri)
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise MediaUnavailableError(
            f"the audio clip for this complaint is not on disk ({uri!r}). §22.4's "
            f"retention clock removes it on the same schedule as the photograph, so a "
            f"report older than the media retention window transcribes to nothing — "
            f"which is expected, and is why this is an error the stage degrades on "
            f"rather than an exception nobody planned for."
        ) from exc
    except Exception as exc:
        raise MediaUnavailableError(f"cannot read the submitted audio: {exc}") from exc


__all__ = ["IMAGE_KIND", "MediaUnavailableError", "audio_bytes", "redacted_image_bytes"]
