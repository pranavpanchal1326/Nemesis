"""The media store, at unit level.

The API tests cover the happy path and the rejections through HTTP. These cover
the two things that are hard to reach from there and expensive to get wrong: the
sniffer's ordering, and the path handling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nemesis.ingest.media import (
    MEDIA_SCHEME,
    EmptyUploadError,
    MediaStore,
    UnsupportedMediaError,
    UploadError,
    UploadTooLargeError,
    sniff,
)

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


class BytesStream:
    """An ``UploadFile``-shaped reader over a bytes object."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self._offset = self._data[self._offset :], len(self._data)
            return chunk
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 28, "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24, "image/png"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav"),
        (b"OggS" + b"\x00" * 28, "audio/ogg"),
        (b"ID3\x03" + b"\x00" * 28, "audio/mpeg"),
        (b"\xff\xfb\x90\x00" + b"\x00" * 28, "audio/mpeg"),
        (b"\x1a\x45\xdf\xa3" + b"\x00" * 28, "audio/webm"),
        (b"\x00\x00\x00\x18ftypmp42", "audio/mp4"),
        (b"%PDF-1.7" + b"\x00" * 24, None),
        (b"\x00\x00", None),
    ],
)
def test_sniffing_recognises_only_the_accepted_formats(head: bytes, expected: str | None) -> None:
    assert sniff(head) == expected


def test_riff_containers_are_disambiguated_before_the_mpeg_frame_sync() -> None:
    """Ordering is the whole correctness of the sniffer.

    WAV and WebP both start with ``RIFF``; the MPEG check is a two-byte frame
    sync that matches far more loosely. Putting the loose test first would
    misidentify one of the precise ones, and the failure would be a voice note
    handed to an image decoder.
    """
    assert sniff(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"


async def test_identical_uploads_land_on_one_file(tmp_path: Path) -> None:
    """Content addressing: the store is keyed by the bytes, not by the name."""
    store = MediaStore(tmp_path)
    payload = b"\xff\xd8\xff\xe0" + b"a" * 400

    first = await store.store(
        BytesStream(payload), allowed_types=IMAGE_TYPES, max_bytes=10_000, chunk_bytes=64
    )
    second = await store.store(
        BytesStream(payload), allowed_types=IMAGE_TYPES, max_bytes=10_000, chunk_bytes=64
    )

    assert first.uri == second.uri
    assert first.sha256 == second.sha256
    assert store.resolve(first.uri).exists()
    assert len(list(store.root.rglob("*.jpg"))) == 1


async def test_a_rejected_upload_leaves_nothing_behind(tmp_path: Path) -> None:
    """A partial write must not survive as a file nothing references.

    Quarantine is the directory Phase 8 will blur and promote from; an orphan
    there is a file that is never processed and never cleaned up.
    """
    store = MediaStore(tmp_path)

    with pytest.raises(UploadTooLargeError):
        await store.store(
            BytesStream(b"\xff\xd8\xff\xe0" + b"a" * 5000),
            allowed_types=IMAGE_TYPES,
            max_bytes=100,
            chunk_bytes=32,
        )

    with pytest.raises(UnsupportedMediaError):
        await store.store(
            BytesStream(b"%PDF-1.7" + b"a" * 100),
            allowed_types=IMAGE_TYPES,
            max_bytes=10_000,
            chunk_bytes=32,
        )

    with pytest.raises(EmptyUploadError):
        await store.store(
            BytesStream(b""), allowed_types=IMAGE_TYPES, max_bytes=10_000, chunk_bytes=32
        )

    leftovers = [path for path in store.root.rglob("*") if path.is_file()]
    assert leftovers == []


async def test_the_cap_is_enforced_before_the_whole_upload_is_read(tmp_path: Path) -> None:
    """The cap stops the stream; it does not audit it afterwards.

    Asserted by counting what the reader was actually asked for. A post-hoc
    check has already spent the disk and the memory it was meant to protect.
    """
    store = MediaStore(tmp_path)

    class CountingStream(BytesStream):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.bytes_read = 0

        async def read(self, size: int = -1) -> bytes:
            chunk = await super().read(size)
            self.bytes_read += len(chunk)
            return chunk

    stream = CountingStream(b"\xff\xd8\xff\xe0" + b"a" * 100_000)
    with pytest.raises(UploadTooLargeError):
        await store.store(stream, allowed_types=IMAGE_TYPES, max_bytes=1_000, chunk_bytes=256)

    # One chunk past the limit at most, not the whole 100 KB.
    assert stream.bytes_read <= 1_000 + 256


def test_resolve_refuses_a_uri_that_escapes_the_quarantine_root(tmp_path: Path) -> None:
    """The input is a value read back out of an event payload.

    The URIs this class mints are hex digests and cannot traverse. But an event
    payload is exactly the place a value survives long enough for a future
    writer to put something else in it.
    """
    store = MediaStore(tmp_path)
    with pytest.raises(UploadError):
        store.resolve(f"{MEDIA_SCHEME}://../../etc/passwd")
    with pytest.raises(UploadError):
        store.resolve("https://example.com/evil.jpg")


async def test_the_stored_uri_is_not_a_followable_url(tmp_path: Path) -> None:
    """§22.1: nothing serves quarantine, so nothing may look like it does.

    A value that cannot be pasted into a browser cannot be leaked into one by a
    template that renders whatever it is given — and Phase 3 has no way to blur
    a face, so an unblurred image must have no path to a client.
    """
    store = MediaStore(tmp_path)
    stored = await store.store(
        BytesStream(b"\x89PNG\r\n\x1a\n" + b"b" * 200),
        allowed_types=IMAGE_TYPES,
        max_bytes=10_000,
        chunk_bytes=64,
    )
    assert stored.uri.startswith(f"{MEDIA_SCHEME}://")
    assert not stored.uri.startswith("http")
