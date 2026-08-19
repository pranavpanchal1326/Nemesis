"""CI gate: no domain module may hardcode a category, role, ward, or language.

This is the machine-checkable half of the Phase 5 exit gate, and the standing
defence against critique-log defect #1 — "domain model hardcoded: five fixed
categories, a closed role enum, safety keywords in source, languages pinned to
hi/mr/en". Phase 5 moves all four into tenant data. Without a check, the fifth
one someone adds under deadline pressure goes in unnoticed, and the control
plane becomes a thing that exists next to the hardcoding rather than instead
of it.

Standard library only, so it runs without the stack and without installing the
backend — the same reasoning as ``check_tenant_scoping.py``. A check that needs
a database to tell you the code is wrong gets skipped on the day it matters.

**What it looks for.** String literals in domain modules that match a known
category name, a known role name, a ward-shaped label, or a BCP-47 language tag
used as a value rather than as a parameter name. The category and role lists are
read **from the seeded templates**, not restated here: the failure this catches
is somebody copying a template value into source, so reading the templates makes
the check automatically cover every category the library ships.

**What it deliberately does not flag**, each with a reason:

- *Anything outside ``DOMAIN_PACKAGES``.* ``config.py`` declares platform
  defaults and ``scripts/`` fetches model weights. Neither decides what a
  complaint is about. The boundary is stated rather than assumed — see
  ``EXEMPT_MODULES`` for the two files inside a domain package that are allowed
  literals and why.
- *Docstrings and comments.* Explaining that a pothole is an example of a
  category is exactly the documentation this codebase wants; a check that
  punished it would teach people to stop writing it.
- *The lifecycle enums.* ``domain/lifecycle.py`` argues at length that pipeline
  *stages* are platform structure rather than tenant data, and that argument is
  accepted here rather than re-fought by a grep.

Exit code 0 clean, 1 on any finding.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
TEMPLATE_DIR = BACKEND / "nemesis" / "control_plane" / "templates"

#: Packages whose code decides what happens to a complaint. A literal here is a
#: customer's vocabulary compiled into the artefact.
#:
#: ``control_plane`` is *not* listed, and that is the point rather than an
#: oversight: it is the module whose job is to move these values into data, so
#: it necessarily names the columns and namespaces they live in.
DOMAIN_PACKAGES = (
    "nemesis/api",
    "nemesis/db",
    "nemesis/events",
    "nemesis/ingest",
    "nemesis/pipeline",
    "nemesis/policy",
    "nemesis/projections",
    "nemesis/realtime",
    "nemesis/simulation",
    # Phase 8. Listed from its first commit, for the reason `policy` teaches by
    # having been missed: a package added to the codebase but not to this tuple
    # is a package this check reports "clean" for without reading.
    "nemesis/trust",
    # Phase 9. Listed from its first commit, for the same reason: the perception
    # layer is the package most tempted to name a category — it is the one
    # deciding which category a photograph is — so a package that scanned clean
    # by not being scanned would be the worst possible omission here.
    "nemesis/perception",
)

#: Files inside a domain package that may carry an otherwise-flagged literal,
#: each with the reason. Membership is not a bypass — it is a claim somebody has
#: to defend in review, which is the same standard ``UNSCOPED_TABLES`` applies.
EXEMPT_MODULES: dict[str, str] = {
    "nemesis/policy/baselines.py": (
        "the platform *baseline* documents — the starting rubric, bands, SLA "
        "matrix and safety ruleset a tenant is provisioned with and may revise "
        "without a deploy. Critique-log defect #1 is safety keywords in source "
        "*deciding* things; these decide nothing a tenant cannot override, and "
        "they are the same class of artefact as control_plane/templates, which "
        "this check exempts by leaving the whole package out. Listing the one "
        "file instead means the rest of nemesis/policy is checked"
    ),
    "nemesis/db/models/i18n.py": (
        "declares the translation namespaces themselves; naming 'taxonomy' as a "
        "namespace is not the same as naming a category"
    ),
}

#: Ward-shaped labels: 'Ward 4', 'ward-12', 'W-03'. Matched as whole literals so
#: a URL path or a docstring fragment containing the word does not trip it.
WARD_PATTERN = re.compile(r"^(ward[\s_-]?\d+|w-?\d{1,3})$", re.IGNORECASE)

#: A language *set*, not a language mention.
#:
#: The first version of this check matched any two- or three-letter lowercase
#: literal as a possible BCP-47 tag. It reported twenty findings on a clean
#: codebase — ``'pk'`` and ``'fk'`` from the constraint naming convention,
#: ``'lat'`` and ``'lng'`` from the realtime envelope, ``'ok'`` from a health
#: response, ``'jpg'`` from the upload allow-list. Every one was wrong, and a
#: check that is wrong twenty times out of twenty is a check people learn to
#: silence rather than read (Phase 1a defect #5, in a new place).
#:
#: What critique-log defect #1 actually describes is *languages pinned to
#: hi/mr/en* — a collection of tags in source, standing in for the tenant's
#: locale set. So the finding is a tuple, list, or set literal of two or more
#: recognised language codes. A lone ``"en"`` as a fallback default is not that,
#: and neither is a dict key that happens to be two letters long.
MIN_TAGS_FOR_A_LANGUAGE_SET = 2

#: ISO 639-1 codes for the languages this product plausibly encounters, plus
#: the ones §8.4 names. A curated list rather than a pattern: the pattern is
#: what produced the false positives above, and the cost of a missed exotic
#: tag is far below the cost of a check nobody trusts.
LANGUAGE_CODES = frozenset(
    {
        "en", "hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "ur", "as", "or",
        "ne", "si", "sd", "ks", "kok", "mai", "sat", "doi", "mni", "brx",
        "fr", "es", "de", "pt", "ar", "zh", "ja", "ko", "ru", "id", "ms", "th", "vi",
    }
)

#: Role names the blueprint's §18.1 matrix lists. A closed role enum is
#: critique-log defect #7; Phase 13 composes roles from data.
KNOWN_ROLES = frozenset(
    {
        "citizen",
        "field_staff",
        "department_head",
        "contractor",
        "auditor",
        "super_admin",
        "municipal_admin",
    }
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    literal: str
    kind: str

    def render(self) -> str:
        return (
            f"{self.path.relative_to(ROOT)}:{self.line}: {self.kind} literal "
            f"{self.literal!r} in a domain module. This is tenant data (Phase 5) — "
            f"resolve it from the control plane, or move the code out of the "
            f"domain packages."
        )


def template_categories() -> frozenset[str]:
    """Every taxonomy key the seeded library ships.

    Read from the templates rather than restated, so a category added to a
    template is automatically covered. Zero categories means the template
    directory is missing or unreadable, which is reported rather than silently
    turning the check into a no-op.
    """
    keys: set[str] = set()
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for node in document.get("taxonomy", []):
            key = node.get("key")
            if isinstance(key, str):
                keys.add(key)
    return frozenset(keys)


class LiteralVisitor(ast.NodeVisitor):
    """Collects string constants that are *values*, not documentation."""

    def __init__(self, path: Path, categories: frozenset[str]) -> None:
        self.path = path
        self.categories = categories
        self.findings: list[Finding] = []
        self._docstrings: set[int] = set()

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802 — ast API
        self._note_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 — ast API
        self._note_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 — ast API
        self._note_docstring(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 — ast API
        self._note_docstring(node)
        self.generic_visit(node)

    def _note_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            self._docstrings.add(id(first.value))

    def visit_Expr(self, node: ast.Expr) -> None:  # noqa: N802 — ast API
        # A bare string expression is an attribute docstring (the `#:`-style
        # documentation this codebase uses after a field). Never a value.
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self._docstrings.add(id(node.value))
        self.generic_visit(node)

    def visit_Tuple(self, node: ast.Tuple) -> None:  # noqa: N802 — ast API
        self._check_language_set(node.elts, node.lineno)
        self.generic_visit(node)

    def visit_List(self, node: ast.List) -> None:  # noqa: N802 — ast API
        self._check_language_set(node.elts, node.lineno)
        self.generic_visit(node)

    def visit_Set(self, node: ast.Set) -> None:  # noqa: N802 — ast API
        self._check_language_set(node.elts, node.lineno)
        self.generic_visit(node)

    def _check_language_set(self, elements: list[ast.expr], lineno: int) -> None:
        """A collection made entirely of language codes is a pinned locale set."""
        if len(elements) < MIN_TAGS_FOR_A_LANGUAGE_SET:
            return
        values = [
            element.value
            for element in elements
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(values) != len(elements):
            return
        if all(value in LANGUAGE_CODES for value in values):
            self.findings.append(
                Finding(self.path, lineno, ", ".join(values), "pinned language set")
            )

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802 — ast API
        if not isinstance(node.value, str) or id(node) in self._docstrings:
            return
        value = node.value
        kind = self._classify(value)
        if kind is not None:
            self.findings.append(Finding(self.path, node.lineno, value, kind))

    def _classify(self, value: str) -> str | None:
        if value in self.categories:
            return "defect category"
        if value in KNOWN_ROLES:
            return "role"
        if WARD_PATTERN.match(value):
            return "ward"
        return None


def scan_file(path: Path, categories: frozenset[str]) -> list[Finding]:
    # utf-8-sig for the same reason check_tenant_scoping.py uses it: a file
    # written by a Windows editor carries a BOM, which ast.parse rejects as a
    # non-printable character.
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover — lint fails first
        print(f"{path}: could not parse: {exc.msg}")
        return []
    visitor = LiteralVisitor(path, categories)
    visitor.visit(tree)
    return visitor.findings


def main() -> int:
    categories = template_categories()
    if not categories:
        print(
            f"[FAIL] domain literals: read zero taxonomy keys from "
            f"{TEMPLATE_DIR.relative_to(ROOT)} — the check would pass everything"
        )
        return 1

    findings: list[Finding] = []
    scanned = 0
    for package in DOMAIN_PACKAGES:
        directory = BACKEND / package
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative = path.relative_to(BACKEND).as_posix()
            if relative in EXEMPT_MODULES:
                continue
            scanned += 1
            findings.extend(scan_file(path, categories))

    if findings:
        print(f"[FAIL] domain literals: {len(findings)} finding(s) in {scanned} module(s)")
        for finding in findings:
            print(f"  {finding.render()}")
        return 1

    print(
        f"[ OK ] domain literals: {scanned} domain module(s) clean against "
        f"{len(categories)} seeded categor(y/ies), {len(KNOWN_ROLES)} role name(s), "
        f"and ward/language patterns"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
