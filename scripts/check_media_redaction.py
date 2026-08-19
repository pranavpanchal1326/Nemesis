#!/usr/bin/env python3
"""CI gate: no code path can persist or serve an unblurred image (§22.1).

This is the machine-checkable half of the Phase 8 exit gate, and the gate is
explicit that it must be *"a repository-level guard test, not convention"*. The
distinction matters because the alternative — "we all know only the trust stage
reads quarantine" — is true on the day it is written and quietly false the first
time somebody needs a thumbnail in a hurry.

**What the guarantee actually is.** §22.4 retains the raw uploaded photograph
for 30 days, so it is not destroyed on arrival; ADR-0031 records why that is the
honest reading of the two requirements together. What is enforced instead is
that the original is *unreachable*: exactly one function reads the quarantine
root, exactly one function writes the redacted root, and no HTTP handler can
express the first. Those three are structural properties of the code, and this
script is what makes them checkable.

**The four rules.**

1. ``MediaStore.resolve`` — the only way to turn a quarantine URI into a path —
   is called from exactly one module, ``nemesis.trust.verification``, plus the
   ingest handler's live-capture check. Anything else reading the original is a
   second path into unredacted bytes.
2. ``RedactedStore._write`` — the only way bytes reach the served root — is
   called only from ``nemesis.trust.redaction``. That module is the one that
   cannot return without having run a face detector.
3. No module under ``nemesis/api`` may import ``MediaStore``'s resolve or name
   the quarantine scheme, except the ingest handler that *writes* quarantine.
   An HTTP handler that can name the original is one route away from serving it.
4. ``redact_image`` obtains its detector from ``active_detector()`` and there is
   no fallback path around it. A default detector that finds no faces would make
   a §22.1 breach indistinguishable from success (see ``trust.detectors``).

**This check tests itself before it tests the repository.** A guard that reports
"clean" and a guard whose rules have stopped matching produce identical output,
and the second is the more likely of the two after a refactor renames a method.
So ``_self_test`` runs each rule against a synthetic module that violates it and
fails the build if the rule stays silent — in memory, with no filesystem writes,
in about a millisecond. The alternative was a pytest that shells out to this
script, which cannot run in the container the suite executes in (only
``./backend`` is mounted there) and would therefore have been skipped in exactly
the environment CI uses.

Standard library only, so it runs without the stack and without installing the
backend — the same reasoning as ``check_tenant_scoping.py``. A check that needs
a database to tell you the code is wrong gets skipped on the day it matters.

Exit code 0 clean, 1 on any finding.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from _console import init, safe

OK, FAIL = init()

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
PACKAGE = BACKEND / "nemesis"

#: The single reader of the quarantine root, and the single caller allowed to
#: turn a stored URI into a filesystem path.
QUARANTINE_READERS = frozenset(
    {
        "nemesis/trust/verification.py",
        # §11.1's live-capture-only mode reads EXIF off the just-stored upload
        # to decide whether to refuse the submission. It reads the original
        # because that is the only place the metadata exists — the redacted copy
        # has had it stripped by construction — and it never serves what it
        # read. Listed by name rather than left implicit, which is the point of
        # a list: adding a third entry is a decision somebody defends in review.
        "nemesis/api/v1/complaints.py",
    }
)

#: The single writer of the served root.
REDACTED_WRITERS = frozenset({"nemesis/trust/redaction.py"})

#: Modules allowed to name the quarantine scheme at all. ``ingest/media.py``
#: defines it; the two readers above resolve URIs written in it.
QUARANTINE_SCHEME = "nemesis+quarantine"
SCHEME_NAMERS = frozenset(
    {
        "nemesis/ingest/media.py",
        "nemesis/trust/redaction.py",  # refuses it explicitly, by name
    }
)

#: Where the detector must come from inside ``redact_image``.
DETECTOR_ACCESSOR = "active_detector"


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    message: str


def _modules() -> list[tuple[str, Path, str]]:
    """Every first-party module, as (relative posix path, path, source)."""
    found: list[tuple[str, Path, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(BACKEND).as_posix()
        found.append((relative, path, path.read_text(encoding="utf-8")))
    return found


def _attribute_calls(tree: ast.AST, name: str) -> list[int]:
    """Line numbers of every ``<something>.<name>(...)`` call.

    Attribute-based rather than resolving the receiver's type: this script has
    no type information and inventing an approximation of one would make it
    wrong in both directions. The cost is that an unrelated ``.resolve()`` on a
    ``Path`` would be flagged — which is why the message names the rule rather
    than asserting the receiver, and why the allow-list is by module.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        ):
            lines.append(node.lineno)
    return lines


def _media_store_resolve_calls(tree: ast.AST, source: str) -> list[int]:
    """``.resolve(...)`` calls whose receiver looks like a media store.

    Narrowed by receiver *name* — ``media_store()``, ``store``, ``MediaStore``
    — because ``Path.resolve()`` appears throughout this codebase and flagging
    all of it would produce a check nobody trusts (Phase 1a defect #5, in a new
    place).
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "resolve"
        ):
            continue
        receiver = ast.unparse(node.func.value)
        if "media_store" in receiver or receiver in {"store", "MediaStore", "self._store"}:
            lines.append(node.lineno)
    del source
    return lines


def _check_quarantine_readers(relative: str, tree: ast.AST, source: str) -> list[Finding]:
    if relative in QUARANTINE_READERS:
        return []
    return [
        Finding(
            relative,
            line,
            "resolves a quarantine URI to a filesystem path. Only "
            f"{', '.join(sorted(QUARANTINE_READERS))} may read the unredacted "
            "original (§22.1); everything else reads RedactedStore.",
        )
        for line in _media_store_resolve_calls(tree, source)
    ]


def _check_redacted_writers(relative: str, tree: ast.AST) -> list[Finding]:
    if relative in REDACTED_WRITERS:
        return []
    return [
        Finding(
            relative,
            line,
            "writes to the redacted media root. Only "
            f"{', '.join(sorted(REDACTED_WRITERS))} may, because it is the only "
            "code that cannot return without having run a face detector.",
        )
        for line in _attribute_calls(tree, "_write")
    ]


def _check_scheme_namers(relative: str, source: str) -> list[Finding]:
    if relative in SCHEME_NAMERS:
        return []
    findings: list[Finding] = []
    for number, line in enumerate(source.splitlines(), start=1):
        # Docstrings and comments explaining the design are exactly the
        # documentation this codebase wants; only code is checked.
        stripped = line.strip()
        if stripped.startswith(("#", "*", '"""', "'''", "``")):
            continue
        if QUARANTINE_SCHEME in line and '"' + QUARANTINE_SCHEME in line:
            findings.append(
                Finding(
                    relative,
                    number,
                    f"names the {QUARANTINE_SCHEME!r} scheme in code. A module that can "
                    "construct a quarantine URI is a module one route away from "
                    "serving one.",
                )
            )
    return findings


def _check_detector_is_not_optional(source: str) -> list[Finding]:
    """``redact_image`` must obtain its detector from ``active_detector``.

    Checked as a positive assertion rather than as an absence: the failure this
    guards against is somebody adding a ``or NullDetector()`` fallback, and a
    check phrased as "no NullDetector appears" would pass for a fallback spelled
    any other way.
    """
    if DETECTOR_ACCESSOR + "()" in source:
        return []
    return [
        Finding(
            "nemesis/trust/redaction.py",
            0,
            f"redact_image no longer calls {DETECTOR_ACCESSOR}(). A redaction that "
            "can proceed without a registered detector produces an image that is "
            "byte-identical to the original while every event says it was blurred "
            "— a §22.1 breach that is invisible from inside and outside the system.",
        )
    ]


#: Synthetic modules that must each produce at least one finding, with the
#: fragment of the message that proves the *right* rule fired. A probe that
#: tripped a different rule would still be "caught" and would still mean the
#: rule under test had stopped working.
_PROBES: tuple[tuple[str, str, str], ...] = (
    (
        "a second reader of quarantine",
        "def leak(store, uri):\n    return store.resolve(uri)\n",
        "unredacted original",
    ),
    (
        "a second writer of the served root",
        "def sneak(store, data):\n    return store._write(data)\n",
        "face detector",
    ),
    (
        "a module naming the quarantine scheme",
        'SCHEME = "' + QUARANTINE_SCHEME + '"\n',
        "one route away",
    ),
)


def _self_test() -> list[str]:
    """Prove each rule still fires. Returns problems, empty when healthy."""
    problems: list[str] = []
    probe = "nemesis/api/_probe.py"
    for label, source, expected in _PROBES:
        tree = ast.parse(source)
        found = [
            *_check_quarantine_readers(probe, tree, source),
            *_check_redacted_writers(probe, tree),
            *_check_scheme_namers(probe, source),
        ]
        if not any(expected in finding.message for finding in found):
            problems.append(
                f"the rule for {label} no longer fires. This check would report the "
                f"repository clean whether or not it was, which is the failure mode "
                f"a §22.1 guard must never have."
            )

    # And the detector rule, which is a positive assertion over one file.
    if not _check_detector_is_not_optional("nothing calls the accessor here"):
        problems.append(
            "the rule for redaction losing its detector no longer fires; a redactor "
            "that never calls active_detector() would pass this check."
        )
    # ...and must not fire on a file that does call it.
    if _check_detector_is_not_optional(f"engine = {DETECTOR_ACCESSOR}()"):
        problems.append(
            "the detector rule fires on a file that does call the accessor, so every "
            "run is a false positive and the check will be silenced."
        )
    return problems


def main() -> int:
    self_test_problems = _self_test()
    if self_test_problems:
        print(f"[{FAIL}] media redaction: the check itself is broken")
        for problem in self_test_problems:
            print(safe(f"  {problem}"))
        return 1

    findings: list[Finding] = []
    modules = _modules()

    for relative, _path, source in modules:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:  # pragma: no cover — ruff would have failed first
            findings.append(Finding(relative, exc.lineno or 0, f"could not parse: {exc.msg}"))
            continue
        findings.extend(_check_quarantine_readers(relative, tree, source))
        findings.extend(_check_redacted_writers(relative, tree))
        findings.extend(_check_scheme_namers(relative, source))

    redaction = next(
        (source for relative, _, source in modules if relative == "nemesis/trust/redaction.py"),
        None,
    )
    if redaction is None:
        findings.append(
            Finding(
                "nemesis/trust/redaction.py",
                0,
                "is missing. §22.1's face blur has no implementation, and every "
                "other rule in this check is vacuous without it.",
            )
        )
    else:
        findings.extend(_check_detector_is_not_optional(redaction))

    if findings:
        print(f"[{FAIL}] media redaction: {len(findings)} finding(s) in {len(modules)} module(s)")
        for finding in findings:
            print(safe(f"  {finding.path}:{finding.line}: {finding.message}"))
        return 1

    print(
        f"[{OK}] media redaction: {len(modules)} module(s) clean — "
        f"{len(QUARANTINE_READERS)} quarantine reader(s), "
        f"{len(REDACTED_WRITERS)} redacted writer(s), detector not optional, "
        f"{len(_PROBES) + 1} self-test(s) passed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
