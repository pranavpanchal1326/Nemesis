"""Console output that survives a Windows terminal.

The Windows console defaults to cp1252, which cannot encode U+2713 or U+2717.
Left unhandled, a check script crashes *while printing the failure it found* —
so the one message that mattered is replaced by a UnicodeEncodeError traceback,
and the actual problem is invisible.

`tasks.py` already solves this for the task runner; these scripts run standalone
(and from CI, and from pre-commit) so they need it too. Same approach: try UTF-8,
fall back to ASCII rather than assuming a modern terminal.
"""

from __future__ import annotations

import sys


def init() -> tuple[str, str]:
    """Return (ok, fail) glyphs this console can actually encode."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass

    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in encoding:
        return "✓", "✗"
    return "ok", "XX"


def safe(text: str) -> str:
    """Drop characters the console cannot encode, rather than raising.

    Applied to interpolated content — file paths, metric names, quoted config —
    which can contain anything. A check that crashes on its own error message is
    worse than one that prints an approximation of it.
    """
    encoding = (getattr(sys.stdout, "encoding", "") or "utf-8")
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
