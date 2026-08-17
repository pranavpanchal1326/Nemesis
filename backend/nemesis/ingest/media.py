"""Uploads: sniffed, capped while streaming, and written to quarantine.

**Nothing here trusts the client.** The declared ``Content-Type`` is ignored
entirely and the type is decided by magic bytes, because §25.1 lists upload
handling as a threat surface and a header supplied by the uploader is a claim,
not a check. The declared filename is ignored too: the stored name is the
content's own SHA-256, so a path traversal has nothing to traverse with and two
identical uploads are one file.

**Why quarantine, and why Phase 3 serves nothing.** §22.1 requires face blur
*before any persistence, including temp paths*, and the blur is Phase 8's — it
needs MediaPipe, which lives in the ``worker-ml`` image. Phase 3 cannot blur, so
Phase 3 must not create a path that serves what it stores: there is deliberately
no media endpoint in this phase, and the reference recorded in the event is an
internal URI rather than a URL a browser could follow.

That is a stated constraint rather than a gap glossed over. The submission is
durable, the pipeline can read it, and the only thing missing is the one thing
Phase 3 has no way to do correctly. Phase 8 inserts blur-and-promote between
quarantine and a served store, and the guard test that phase's gate requires has
exactly one code path to police because of this.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from nemesis.observability.logging import get_logger

log = get_logger(__name__)

#: Subdirectory of ``upload_dir``. Named, not derived, so the answer to "which
#: files has a human looked at" is a directory listing.
QUARANTINE_DIRNAME: Final = "quarantine"

#: URI scheme for a stored-but-unprocessed file. Deliberately not ``http``: a
#: value that cannot be pasted into a browser cannot be leaked into one by a
#: template that renders whatever it is given.
MEDIA_SCHEME: Final = "nemesis+quarantine"

#: Bytes read before deciding the type. Every signature below fits in 16; 32
#: leaves room for the RIFF/ISO-BMFF forms whose discriminator sits at an offset.
SNIFF_BYTES: Final = 32


class UploadError(ValueError):
    """The upload cannot be accepted as sent."""


class UploadTooLargeError(UploadError):
    """The stream exceeded its cap. Raised while reading, never after."""


class UnsupportedMediaError(UploadError):
    """The sniffed content type is not in the allow-list for this field."""


class EmptyUploadError(UploadError):
    """Zero bytes. A file part with no content is a client bug, not a submission."""


@dataclass(frozen=True, slots=True)
class StoredMedia:
    uri: str
    content_type: str
    size_bytes: int
    sha256: str


class ChunkStream(Protocol):
    """What ``UploadFile`` provides, narrowed to what is used."""

    async def read(self, size: int = -1) -> bytes: ...


def sniff(head: bytes) -> str | None:
    """Content type from magic bytes, or ``None`` if unrecognised.

    Only the formats the allow-lists name. An extensible sniffer that recognises
    everything would be a liability here — recognising a format is the first
    half of accepting it, and the list of things this system should accept is
    short and closed.
    """
    if len(head) < 4:
        return None

    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        # ID3-tagged or a bare MPEG frame sync. The frame-sync form is why the
        # WAV and WebP checks come first: both begin with RIFF, neither begins
        # with 0xFF, and ordering the cheap unambiguous signatures ahead of the
        # loose one is what keeps this total.
        return "audio/mpeg"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        # EBML. WebM and Matroska share it; the doctype is deeper in the file
        # than a sniff should read, and both decode with the same tooling.
        return "audio/webm"
    if head[4:8] == b"ftyp":
        return "audio/mp4"
    return None


#: Sniffed type -> stored extension. Only for operator legibility — nothing
#: reads a file back by extension.
_EXTENSIONS: Final[dict[str, str]] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
}


class MediaStore:
    """Content-addressed quarantine storage."""

    def __init__(self, upload_dir: Path) -> None:
        self._root = Path(upload_dir) / QUARANTINE_DIRNAME

    @property
    def root(self) -> Path:
        return self._root

    async def store(
        self,
        stream: ChunkStream,
        *,
        allowed_types: tuple[str, ...],
        max_bytes: int,
        chunk_bytes: int,
    ) -> StoredMedia:
        """Stream one upload to quarantine, enforcing the cap as it goes.

        The cap is checked per chunk, so an oversized upload is refused after
        ``max_bytes`` have been read rather than after all of them have. The
        temporary file is written into the destination directory — not the
        system temp dir — so the final step is a rename within one filesystem,
        which is atomic: a reader can never observe a partially written file
        under its content address.
        """
        self._root.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        total = 0
        content_type: str | None = None
        head = b""

        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed in the finally below
            dir=self._root, prefix=".incoming-", suffix=".part", delete=False
        )
        temp_path = Path(handle.name)
        try:
            while True:
                chunk = await stream.read(chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError(f"upload exceeds the {max_bytes} byte limit")
                if len(head) < SNIFF_BYTES:
                    head += chunk[: SNIFF_BYTES - len(head)]
                digest.update(chunk)
                handle.write(chunk)

            if total == 0:
                raise EmptyUploadError("upload contained no bytes")

            content_type = sniff(head)
            if content_type is None or content_type not in allowed_types:
                raise UnsupportedMediaError(
                    f"content type {content_type or 'unrecognised'} is not accepted here; "
                    f"expected one of {', '.join(allowed_types)}"
                )

            handle.flush()
            os.fsync(handle.fileno())
            handle.close()

            checksum = digest.hexdigest()
            destination = self._path_for(checksum, content_type)
            destination.parent.mkdir(parents=True, exist_ok=True)
            # `replace` rather than `rename`: an identical file already present
            # is the content-addressing working, not a collision to fail on.
            temp_path.replace(destination)
            temp_path = destination
        except BaseException:
            if not handle.closed:
                handle.close()
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            if not handle.closed:  # pragma: no cover — every path above closes it
                handle.close()

        log.info("media_stored", content_type=content_type, size_bytes=total)
        return StoredMedia(
            uri=f"{MEDIA_SCHEME}://{destination.relative_to(self._root).as_posix()}",
            content_type=content_type,
            size_bytes=total,
            sha256=checksum,
        )

    def _path_for(self, checksum: str, content_type: str) -> Path:
        # Two-character fan-out. One directory holding every upload a city
        # produces is a directory nothing enumerates quickly, including the
        # filesystem.
        return self._root / checksum[:2] / f"{checksum}.{_EXTENSIONS[content_type]}"

    def resolve(self, uri: str) -> Path:
        """Filesystem path for a stored URI, for the phases that read the bytes.

        Rejects anything that escapes the quarantine root. The URIs are minted
        by this class from a hex digest and cannot contain traversal — but this
        function's input is a value read back out of an event payload, and an
        event payload is the one place a value survives long enough for a future
        writer to put something unexpected in it.
        """
        if not uri.startswith(f"{MEDIA_SCHEME}://"):
            raise UploadError(f"not a quarantine URI: {uri!r}")
        relative = uri[len(MEDIA_SCHEME) + 3 :]
        candidate = (self._root / relative).resolve()
        if not candidate.is_relative_to(self._root.resolve()):
            raise UploadError("resolved outside the quarantine root")
        return candidate


def new_complaint_id() -> uuid.UUID:
    """Minted by the server, never accepted from the client.

    A client-chosen id would let one citizen's submission overwrite the chain of
    another's by guessing — and the chain is the evidence.
    """
    return uuid.uuid4()
