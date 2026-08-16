#!/usr/bin/env python3
"""Runbook coverage.

The Phase 1a gate says: *every §27.3 scenario has a runbook page.* This is that
gate, implemented rather than asserted — per the plan's own rule that every gate
in PHASES.md is a test, not an opinion.

It checks four things:

  1. Every ``**Scenario: ...**`` heading in Blueprint §27.3 is claimed by exactly
     one runbook page, via that page's ``**Blueprint scenario:**`` line. Parsing
     the blueprint rather than keeping a copied list means a scenario added to
     §27.3 later fails this check immediately, instead of being noticed never.
  2. Every declared ``Dependency`` the system can degrade against has a page. A
     dependency with a fallback path but no recovery steps is a fallback nobody
     can act on.
  3. Every page is linked from the index. An unlinked runbook is one that will
     not be found under pressure, which is the same as not existing.
  4. Every page has the required sections. A runbook missing "How to confirm" is
     a page that tells you what to do without telling you whether to do it.

Standard library only — this runs before anything is installed.
"""

from __future__ import annotations

import re
from pathlib import Path

from _console import init, safe

OK, FAIL = init()

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = ROOT / "NEMESIS-Blueprint-v2.md"
RUNBOOKS = ROOT / "docs" / "runbooks"
INDEX = RUNBOOKS / "README.md"
METRICS_MODULE = ROOT / "backend" / "nemesis" / "observability" / "metrics.py"

SCENARIO_RE = re.compile(r"^\*\*Scenario:\s*(.+?)\*\*\s*$", re.MULTILINE)
CLAIM_RE = re.compile(r"^\*\*Blueprint scenario:\*\*\s*(.+?)\s*$", re.MULTILINE)
DEPENDENCY_RE = re.compile(r"^\*\*Dependency:\*\*\s*(.+?)\s*$", re.MULTILINE)
ENUM_MEMBER_RE = re.compile(r'^\s+[A-Z_]+\s*=\s*"([a-z_]+)"', re.MULTILINE)

REQUIRED_SECTIONS = (
    "## Symptoms",
    "## How to confirm",
    "## Immediate mitigation",
    "## Root cause investigation",
    "## Prevention",
)


def _section_27_3(text: str) -> str:
    """Just §27.3, so a `**Scenario:` written elsewhere is not mistaken for one."""
    start = text.index("### 27.3 Operational runbook")
    end = text.index("### 27.4", start)
    return text[start:end]


def _dependency_members() -> set[str]:
    source = METRICS_MODULE.read_text(encoding="utf-8")
    marker = "class Dependency("
    rest = source[source.index(marker) + len(marker) :]
    end = len(rest)
    for terminator in ("\nclass ", "\ndef ", "\n# ---"):
        found = rest.find(terminator)
        if found != -1:
            end = min(end, found)
    return set(ENUM_MEMBER_RE.findall(rest[:end]))


def main() -> int:
    problems: list[str] = []

    for path in (BLUEPRINT, INDEX):
        if not path.exists():
            print(f"error: {path} not found")
            return 1

    pages = {p for p in RUNBOOKS.glob("*.md") if p.name != "README.md"}
    if not pages:
        print("error: no runbook pages found — the check is not running")
        return 1

    page_text = {p: p.read_text(encoding="utf-8") for p in sorted(pages)}

    # -- 1. §27.3 scenario coverage -----------------------------------------
    blueprint_scenarios = set(SCENARIO_RE.findall(_section_27_3(BLUEPRINT.read_text("utf-8"))))
    claimed: dict[str, list[str]] = {}
    for path, text in page_text.items():
        for claim in CLAIM_RE.findall(text):
            claimed.setdefault(claim, []).append(path.name)

    for scenario in sorted(blueprint_scenarios):
        owners = claimed.get(scenario, [])
        if not owners:
            problems.append(
                f"Blueprint §27.3 scenario {scenario!r} has no runbook page. Add "
                f"one with a line reading exactly:  **Blueprint scenario:** {scenario}"
            )
        elif len(owners) > 1:
            problems.append(
                f"§27.3 scenario {scenario!r} is claimed by {owners} — exactly "
                f"one page must own it, or the responder has to pick."
            )

    for claim, owners in sorted(claimed.items()):
        if claim not in blueprint_scenarios:
            problems.append(
                f"{owners[0]}: claims §27.3 scenario {claim!r}, which is not in "
                f"the blueprint. Either the wording drifted or the scenario moved."
            )

    # -- 2. every degradable dependency has a page --------------------------
    covered_dependencies = {
        dep.strip()
        for text in page_text.values()
        for line in DEPENDENCY_RE.findall(text)
        for dep in line.split(",")
    }
    for dependency in sorted(_dependency_members()):
        if dependency not in covered_dependencies:
            problems.append(
                f"Dependency.{dependency.upper()} can degrade but no runbook "
                f"page declares  **Dependency:** {dependency}"
            )

    # -- 3. index completeness ----------------------------------------------
    index_text = INDEX.read_text(encoding="utf-8")
    for path in sorted(pages):
        if f"({path.name})" not in index_text:
            problems.append(f"docs/runbooks/README.md does not link {path.name}")

    # -- 4. page structure ---------------------------------------------------
    for path, text in page_text.items():
        missing = [s for s in REQUIRED_SECTIONS if s not in text]
        if missing:
            problems.append(f"{path.name}: missing section(s) {', '.join(missing)}")

    if problems:
        print(f"\nrunbook check: {len(problems)} problem(s)\n")
        for problem in problems:
            print(f"  {FAIL} {safe(problem)}")
        print()
        return 1

    print(
        f"runbook check: ok — {len(pages)} pages, "
        f"{len(blueprint_scenarios)}/{len(blueprint_scenarios)} §27.3 scenarios covered"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
