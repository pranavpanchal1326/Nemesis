"""Every committed binary file still decodes as the format its name claims.

**Why this exists, and what it is paid to catch.** At some point before
`4b31099` every tracked image in this repository was written back through a text
codec: each byte >= 0x80 became ``EF BF BD`` — U+FFFD, the Unicode replacement
character — and CRLF collapsed to LF. A PNG signature that should read
``89 50 4E 47 0D 0A 1A 0A`` read ``EF BF BD 50 4E 47 0A 1A``. Nothing failed.

That is the whole problem. Corruption of this shape is *silent at every layer
that would normally notice*:

- Git is content-agnostic. The blob changed, so it committed the change.
- The files keep their extensions, their plausible sizes, and their paths.
- ``raw.githubusercontent`` stops sniffing them as images and serves
  ``application/octet-stream`` with ``X-Content-Type-Options: nosniff``, so the
  README renders broken images while the markup is provably correct — which
  sends the investigation to the markup.
- Worst of all, **Playwright reports an undecodable baseline as a test
  failure**, not as a tooling error. 95 corrupt snapshots read as 95 failing
  visual assertions, which is indistinguishable at a glance from a renderer
  regression and invites exactly the wrong fix: re-record the baselines and
  accept whatever the current build happens to draw.

`4b31099` recovered what had intact ancestors in history and recorded, honestly,
that 95 Playwright snapshots and four product captures could not be recovered
and had to be re-made. They were not re-made for eleven commits, because nothing
in CI asked. This script is the thing that asks.

**It is a signature check, not a decode.** Reading the magic bytes is enough to
catch the failure mode that actually occurs here and costs nothing — a full
decode would need Pillow on the host, and this file deliberately runs on a
machine where the stack will not start, like the memory-budget and media
redaction checks beside it.

A zero-byte file is also a failure. It is what a half-finished `git lfs` fetch
or an interrupted write leaves behind, and it decodes as nothing at all.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent

# The ✓/✗ this family prints are not ASCII, and a Windows console hands Python a
# cp1252 stdout — so the *failure* path of this check would itself fail with a
# UnicodeEncodeError, reporting a broken file as a broken checker. Forced here
# rather than left to the locale, because a guard whose error path crashes is
# worse than no guard: it fails for a reason that looks like it is its own fault.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Where committed binaries live. Each root is listed because it holds files a
#: reader or a gate depends on, not because it merely contains images:
#:
#:   frontend/tests   the visual regression baselines — the set this check exists for
#:   frontend/public  shipped to the browser; `brand/nemesis-mark.png` is read at
#:                    build time for its intrinsic size by a static next/image import
#:   assets           the README's product photography
#:   docs/images      the same, for the documents
SEARCH_ROOTS: Final = (
    Path("frontend") / "tests",
    Path("frontend") / "public",
    Path("assets"),
    Path("docs") / "images",
)

#: Directories that hold *run output* rather than committed artefacts. A failed
#: Playwright run writes actual/diff images into `test-results/`, and a truncated
#: one there is a symptom of the run, not a defect in the repository.
SKIP_DIRS: Final = frozenset(
    {
        "node_modules",
        "test-results",
        "playwright-report",
        "storybook-static",
        ".next",
        "storybook-diff",
    }
)

#: Leading bytes each extension must begin with. One tuple per extension because
#: several formats have more than one legal preamble.
SIGNATURES: Final[dict[str, tuple[bytes, ...]]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),  # 'WEBP' follows at offset 8; checked below
    ".woff": (b"wOFF",),
    ".woff2": (b"wOF2",),
    ".ttf": (b"\x00\x01\x00\x00", b"true", b"ttcf"),
    ".otf": (b"OTTO", b"\x00\x01\x00\x00"),
    ".ico": (b"\x00\x00\x01\x00",),
    ".pdf": (b"%PDF-",),
    ".mp4": (b"\x00\x00\x00",),  # ftyp box length; 'ftyp' at offset 4
    ".webm": (b"\x1a\x45\xdf\xa3",),
    ".pptx": (b"PK\x03\x04",),
    ".zip": (b"PK\x03\x04",),
}

#: U+FFFD encoded as UTF-8. Three of these at the head of a binary file is the
#: text round-trip, and naming it in the output is the difference between a
#: fifteen-minute fix and the afternoon `4b31099` spent finding it.
REPLACEMENT_CHAR: Final = b"\xef\xbf\xbd"


def _candidates() -> Iterator[Path]:
    for root in SEARCH_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() in SIGNATURES:
                yield path


def _problem(path: Path) -> str | None:
    """Return why this file is not the format its extension claims, or None."""
    expected = SIGNATURES[path.suffix.lower()]
    head = path.read_bytes()[:16]

    if len(head) == 0:
        return "empty file"

    if head.startswith(REPLACEMENT_CHAR):
        return (
            "begins with U+FFFD — written back through a text codec, "
            "as in 4b31099; recover from an intact blob or re-make it"
        )

    if not head.startswith(expected):
        if REPLACEMENT_CHAR in head:
            return (
                f"contains U+FFFD within its first 16 bytes (head {head[:8].hex(' ')}) "
                "— a partial text round-trip"
            )
        want = " or ".join(sig.hex(" ") for sig in expected)
        return f"signature is {head[:8].hex(' ')}, expected {want}"

    if path.suffix.lower() == ".webp" and head[8:12] != b"WEBP":
        return f"RIFF container is not WEBP (bytes 8-12: {head[8:12]!r})"

    return None


def main() -> int:
    checked = 0
    findings: list[tuple[Path, str]] = []

    for path in _candidates():
        checked += 1
        problem = _problem(path)
        if problem is not None:
            findings.append((path, problem))

    if checked == 0:
        # Not a pass. Every search root being absent means this check is
        # scanning nothing and would stay green through any amount of damage.
        print("[✗] binary integrity: found no files to check — SEARCH_ROOTS is wrong")
        return 1

    if not findings:
        print(f"[✓] binary integrity: {checked} file(s), every signature intact")
        return 0

    print(f"[✗] binary integrity: {len(findings)} of {checked} file(s) do not decode\n")
    for path, problem in findings:
        print(f"  {path.relative_to(ROOT).as_posix()}")
        print(f"      {problem}")
    print(
        "\n  A Playwright baseline reports this as a FAILING VISUAL ASSERTION, not as a\n"
        "  broken file. Do not re-record a baseline to make this pass without first\n"
        "  confirming the image it replaces was a correct one.\n"
        "\n  Recover from history where an intact ancestor exists:\n"
        "      git log --all --oneline -- <path>\n"
        "      git cat-file -p <blob> > <path>\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
