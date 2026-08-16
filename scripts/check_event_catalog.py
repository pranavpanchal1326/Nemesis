"""CI gate: every Blueprint §9.4 event type is registered or explicitly deferred.

Standard library only, and it reads ``catalog.py`` as source rather than
importing it. That is not a stylistic preference — the api container mounts only
``backend/``, so a script that imported the application could not also read the
blueprint, and one that ran on the host could not import the application. Each
half of the original combined check now runs where it is able to:

- **this script** (host, no dependencies) — does the catalog cover §9.4?
- **``tests/test_event_schema_lock.py``** (container, imports Pydantic) — has a
  released payload schema changed shape without a version bump?

The §9.4 list is parsed *from the blueprint*, the same technique the runbook
coverage check uses. A hand-maintained copy would drift, and the drift would be
invisible precisely because both sides look tidy.

Exit code 0 clean, 1 on any finding.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = ROOT / "NEMESIS-Blueprint-v2.md"
CATALOG = ROOT / "backend" / "nemesis" / "events" / "catalog.py"


def blueprint_event_types() -> set[str]:
    """Event types named in the §9.4 catalog table."""
    text = BLUEPRINT.read_text(encoding="utf-8-sig")
    section = re.search(r"^### 9\.4 Event type catalog\s*$(.*?)^(?:###|---)", text, re.M | re.S)
    if section is None:
        raise SystemExit("[FAIL] could not locate '### 9.4 Event type catalog' in the blueprint")

    found: set[str] = set()
    for line in section.group(1).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = line.split("|")
        if len(cells) < 2:
            continue
        # A row such as `cluster_match_found` / `cluster_created` names two.
        found.update(match.group(1) for match in re.finditer(r"`([a-z0-9_]+)`", cells[1]))
    return found


def catalog_contents() -> tuple[set[str], set[str], dict[str, str]]:
    """``(registered, deferred, renamed)`` read from the catalog's AST."""
    tree = ast.parse(CATALOG.read_text(encoding="utf-8-sig"), filename=str(CATALOG))

    registered: set[str] = set()
    deferred: set[str] = set()
    renamed: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "register_event" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    registered.add(first.value)
        elif isinstance(node, ast.AnnAssign | ast.Assign):
            targets = (
                [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
            )
            names = {
                target.id for target in targets if isinstance(target, ast.Name)
            }
            if not isinstance(node.value, ast.Dict):
                continue
            if "DEFERRED_EVENT_TYPES" in names:
                deferred.update(_string_keys(node.value))
            elif "RENAMED_EVENT_TYPES" in names:
                renamed.update(_string_pairs(node.value))

    return registered, deferred, renamed


def _string_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _string_pairs(node: ast.Dict) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            pairs[key.value] = value.value
    return pairs


def main() -> int:
    catalogued = blueprint_event_types()
    registered, deferred, renamed = catalog_contents()

    failures: list[str] = []

    for event_type in sorted(catalogued - registered - deferred - set(renamed)):
        failures.append(
            f"'{event_type}' appears in Blueprint §9.4 but is neither registered in "
            f"nemesis/events/catalog.py, listed in DEFERRED_EVENT_TYPES with an owning phase, "
            f"nor recorded in RENAMED_EVENT_TYPES with its replacement."
        )

    for old_name, new_name in sorted(renamed.items()):
        if new_name not in registered:
            failures.append(
                f"RENAMED_EVENT_TYPES maps '{old_name}' to '{new_name}', which is not registered. "
                f"A rename that points nowhere hides the same gap it claims to explain."
            )

    for event_type in sorted(registered & deferred):
        failures.append(
            f"'{event_type}' is both registered and listed as deferred. Remove it from "
            f"DEFERRED_EVENT_TYPES — the deferral is stale and now reads as a gap that is not one."
        )

    if not catalogued:
        failures.append("parsed zero event types from §9.4; the check is not actually checking")

    if failures:
        print(f"[FAIL] event catalog: {len(failures)} problem(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(
        f"[ OK ] event catalog: {len(catalogued)} type(s) in §9.4 — "
        f"{len(catalogued & registered)} registered, {len(catalogued & deferred)} deferred, "
        f"{len(catalogued & set(renamed))} renamed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
