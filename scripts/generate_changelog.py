#!/usr/bin/env python3
"""Generate the Unreleased section of CHANGELOG.md from conventional commits.

Generated rather than hand-written because hand-maintained changelogs drift, and
a changelog that is wrong is worse than none: during an incident it is read as a
record of what changed, and a missing entry sends the investigation somewhere the
problem is not.

Only the block between the generated markers is rewritten. Everything below it —
including the hand-written 0.1.0 entry, which predates this script and the commit
history it reads — is preserved verbatim.

Standard library and `git`. No changelog tool is worth a dependency for eighty
lines of parsing, and adding one would put a release step behind an install step.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

START = "<!-- generated:start -->"
END = "<!-- generated:end -->"

# Keep a Changelog headings, in the order they should appear. `build`/`ci`/
# `chore` map to nothing: they do not describe a change to the product, and
# listing them trains readers to skim.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Added", ("feat",)),
    ("Fixed", ("fix",)),
    ("Changed", ("refactor", "perf")),
    ("Documentation", ("docs",)),
)

COMMIT_RE = re.compile(
    r"^(?P<type>[a-z]+)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?"
    r": (?P<subject>.+)$"
)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def _last_tag() -> str | None:
    tag = _git("describe", "--tags", "--abbrev=0")
    return tag or None


def _commits(since: str | None) -> list[tuple[str, str]]:
    """(subject, body) for each commit since `since`."""
    # \x1e between records, \x1f between fields — neither occurs in commit text,
    # unlike the newlines a naive format would break on.
    spec = f"{since}..HEAD" if since else "HEAD"
    raw = _git("log", spec, "--no-merges", "--format=%s%x1f%b%x1e")
    records = [r for r in raw.split("\x1e") if r.strip()]
    out: list[tuple[str, str]] = []
    for record in records:
        subject, _, body = record.strip().partition("\x1f")
        out.append((subject.strip(), body.strip()))
    return out


def _render(since: str | None) -> str:
    commits = _commits(since)
    if not commits:
        return (
            "## [Unreleased]\n\n"
            f"No commits since {since or 'the beginning of history'}.\n"
        )

    buckets: dict[str, list[str]] = {name: [] for name, _ in SECTIONS}
    breaking: list[str] = []
    skipped = 0

    for subject, body in commits:
        match = COMMIT_RE.match(subject)
        if match is None:
            # Not a conventional commit. Counted rather than silently dropped —
            # a rising count means the commit-msg hook is being bypassed.
            skipped += 1
            continue

        scope = match["scope"]
        entry = f"**{scope}:** {match['subject']}" if scope else match["subject"]

        if match["breaking"] or "BREAKING CHANGE:" in body:
            note = ""
            for line in body.splitlines():
                if line.startswith("BREAKING CHANGE:"):
                    note = line.removeprefix("BREAKING CHANGE:").strip()
            breaking.append(f"{entry}{f' — {note}' if note else ''}")
            continue

        for name, types in SECTIONS:
            if match["type"] in types:
                buckets[name].append(entry)
                break

    lines = ["## [Unreleased]", ""]

    if breaking:
        # First, always, and with the heading Keep a Changelog reserves for it.
        # A breaking change buried under "Added" is a breaking change somebody
        # will ship without noticing.
        lines.append("### ⚠ BREAKING CHANGES")
        lines.append("")
        lines.extend(f"- {item}" for item in breaking)
        lines.append("")

    for name, _ in SECTIONS:
        items = buckets[name]
        if not items:
            continue
        lines.append(f"### {name}")
        lines.append("")
        lines.extend(f"- {item}" for item in items)
        lines.append("")

    if len(lines) == 2:
        lines.append("Nothing user-facing since the last release.")
        lines.append("")

    if skipped:
        lines.append(
            f"> {skipped} commit(s) did not parse as conventional commits and are "
            f"not listed. If this number is not zero, the commit-msg hook is "
            f"being bypassed."
        )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    if not CHANGELOG.exists():
        print(f"error: {CHANGELOG} not found")
        return 1

    text = CHANGELOG.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"error: CHANGELOG.md is missing the {START} / {END} markers")
        return 1

    tag = _last_tag()
    body = _render(tag)

    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    CHANGELOG.write_text(f"{head}{START}\n\n{body}\n{END}{tail}", encoding="utf-8")

    print(f"CHANGELOG.md updated from commits since {tag or 'the beginning of history'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
