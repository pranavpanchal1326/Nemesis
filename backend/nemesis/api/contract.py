"""Fail CI on a breaking change to a published API version.

The counterpart of ``nemesis/events/schema_lock.json``, applied to the outward
contract rather than to the log. Phase 2 made an event payload change without an
upcaster a CI failure; this makes a *response shape* change without a version
bump one.

**What counts as breaking, and why the line is drawn there.**

Breaking — a consumer that worked yesterday fails today:
  * a response field removed
  * a response field's type changed or narrowed
  * a required response field made optional (a client that reads it
    unconditionally now gets a null it never handled)
  * a path removed
  * a request parameter added as required, or an optional one made required

Not breaking — a consumer that ignores what it does not know keeps working:
  * a response field added
  * a new path
  * a new optional request parameter
  * documentation, summaries, descriptions

Forcing a version bump for every addition is the failure mode on the other side:
it produces a v7 that nobody has migrated to and a v1 that everybody is still
on, which is worse for compatibility than having no versions at all.

**Preview versions are exempt**, and that is what ``preview`` means. A version
carrying no compatibility promise cannot break one. The exemption is read from
the version registry rather than from a list here, so promoting v2 to active
brings it under the lock automatically — the mistake this avoids is a version
that goes stable while its lock entry stays exempt because nobody edited two
files.

**Why this lives in the backend package rather than in ``scripts/``.** It has to
construct the FastAPI app to read the contract the code actually serves, which
needs the full dependency set — and the api container mounts only ``./backend``,
so a root-level script could never import it. The same reasoning put the event
schema *fingerprint* check in the test suite while its catalog half stayed
host-side. ``tests/test_api_contract.py`` runs the verification; ``nem api-lock``
runs the update.

Usage::

    python -m nemesis.api.contract            # verify
    python -m nemesis.api.contract --update   # re-lock after a legitimate change

``--update`` is deliberately a separate action. If the check rewrote the lock on
failure it would enforce nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

LOCK_PATH = Path(__file__).resolve().parent / "api_contract_lock.json"

OK, FAIL = "[ OK ]", "[FAIL]"


def _load_spec() -> dict[str, Any]:
    """The OpenAPI document, generated from the running code.

    Built by constructing the app rather than by reading a committed artefact:
    the whole point is to compare the lock against what the code *actually*
    serves, and a committed spec would be a third thing that can drift.
    """
    from nemesis.config import Settings
    from nemesis.main import create_app

    app = create_app(Settings(app_env="ci"))
    spec: dict[str, Any] = app.openapi()
    return spec


def _versioned_paths(spec: dict[str, Any], version: str) -> dict[str, Any]:
    prefix = f"/api/{version}/"
    return {path: item for path, item in spec.get("paths", {}).items() if path.startswith(prefix)}


def _resolve(schema: dict[str, Any], spec: dict[str, Any], seen: frozenset[str]) -> dict[str, Any]:
    """Flatten a schema to ``{field: type}``, following ``$ref`` once per name.

    ``seen`` guards recursive models. A model that contains itself would
    otherwise loop forever, and a taxonomy tree is exactly that shape — so this
    is a real case, not a defensive flourish.
    """
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return {"$recursive": name}
        target = spec.get("components", {}).get("schemas", {}).get(name, {})
        return _resolve(target, spec, seen | {name})

    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if isinstance(options, list):
            merged: dict[str, Any] = {}
            for option in options:
                if isinstance(option, dict):
                    merged |= _resolve(option, spec, seen)
            return merged

    if schema.get("type") == "array":
        items = schema.get("items")
        inner = _resolve(items, spec, seen) if isinstance(items, dict) else {}
        return {"$array": inner}

    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = set(schema.get("required", []))
        return {
            name: {
                "type": _type_of(prop, spec, seen),
                "required": name in required,
            }
            for name, prop in sorted(properties.items())
        }

    return {"$scalar": _type_of(schema, spec, seen)}


def _type_of(schema: dict[str, Any], spec: dict[str, Any], seen: frozenset[str]) -> str:
    if "$ref" in schema:
        return "object:" + str(schema["$ref"]).rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if isinstance(options, list):
            return "|".join(sorted(_type_of(o, spec, seen) for o in options if isinstance(o, dict)))
    declared = schema.get("type")
    if declared == "array":
        items = schema.get("items")
        inner = _type_of(items, spec, seen) if isinstance(items, dict) else "any"
        return f"array<{inner}>"
    return str(declared or "any")


def build_snapshot() -> dict[str, Any]:
    """The lockable shape of every non-preview version's surface."""
    from nemesis.api.versioning import VersionStatus, all_versions

    spec = _load_spec()
    snapshot: dict[str, Any] = {}

    for version in all_versions():
        if version.status is VersionStatus.PREVIEW:
            # No compatibility promise to break — see the module docstring.
            continue
        paths = _versioned_paths(spec, version.name)
        locked: dict[str, Any] = {}
        for path, item in sorted(paths.items()):
            for method, operation in sorted(item.items()):
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                response = (
                    operation.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                locked[f"{method.upper()} {path}"] = {
                    "response": _resolve(response, spec, frozenset()) if response else {},
                    "required_params": sorted(
                        param["name"]
                        for param in operation.get("parameters", [])
                        if param.get("required")
                    ),
                }
        snapshot[version.name] = locked
    return snapshot


def compare(locked: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Every breaking difference, as a message naming the consumer harm."""
    problems: list[str] = []

    for version, operations in locked.items():
        live = current.get(version)
        if live is None:
            problems.append(
                f"{version}: the whole version disappeared from the running app. "
                f"A published version is withdrawn through the deprecation clock "
                f"(docs/RELEASE.md), never by deleting a router."
            )
            continue

        for operation, contract in operations.items():
            now = live.get(operation)
            if now is None:
                problems.append(
                    f"{version} {operation}: removed. Every consumer calling it breaks "
                    f"today; publish it under a new version instead."
                )
                continue

            problems.extend(
                _compare_fields(
                    f"{version} {operation}",
                    contract.get("response", {}),
                    now.get("response", {}),
                )
            )

            added_required = set(now.get("required_params", [])) - set(
                contract.get("required_params", [])
            )
            if added_required:
                problems.append(
                    f"{version} {operation}: parameter(s) {sorted(added_required)} became "
                    f"required. Every existing caller omits them and now gets a 422."
                )
    return problems


def _compare_fields(where: str, locked: Any, current: Any, path: str = "") -> list[str]:
    problems: list[str] = []
    if not isinstance(locked, dict) or not isinstance(current, dict):
        return problems

    for name, spec in locked.items():
        here = f"{path}.{name}" if path else name
        if name.startswith("$"):
            continue
        live = current.get(name)
        if live is None:
            problems.append(
                f"{where}: response field '{here}' removed. A consumer reading it gets "
                f"a KeyError, not a graceful degradation."
            )
            continue
        if isinstance(spec, dict) and "type" in spec and isinstance(live, dict):
            if spec["type"] != live.get("type"):
                problems.append(
                    f"{where}: response field '{here}' changed type from "
                    f"{spec['type']} to {live.get('type')}."
                )
            if spec.get("required") and not live.get("required"):
                problems.append(
                    f"{where}: response field '{here}' became optional. A consumer that "
                    f"reads it unconditionally now receives a null it has never handled."
                )
        else:
            problems.extend(_compare_fields(where, spec, live, here))

    return problems


def main() -> int:
    update = "--update" in sys.argv
    current = build_snapshot()

    if update:
        LOCK_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sys.stdout.write(f"{OK} api_contract_lock.json rewritten from the current build.\n")
        return 0

    if not LOCK_PATH.exists():
        sys.stderr.write(
            f"{FAIL} {LOCK_PATH.name} is missing. Create it with "
            f"`python scripts/check_api_contract.py --update` and commit it.\n"
        )
        return 1

    locked = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    problems = compare(locked, current)

    if problems:
        sys.stderr.write(f"\n{FAIL} Published API contract broken:\n\n")
        for problem in problems:
            sys.stderr.write(f"  - {problem}\n")
        sys.stderr.write(
            "\n  A published version is a compatibility obligation (docs/RELEASE.md:60).\n"
            "  Ship the change under a new version instead. If this change is genuinely\n"
            "  additive and the check is wrong about it, re-lock deliberately with\n"
            "  `python scripts/check_api_contract.py --update` and say why in the commit.\n\n"
        )
        return 1

    operations = sum(len(ops) for ops in locked.values())
    sys.stdout.write(
        f"{OK} API contract intact - {operations} locked operation(s) across "
        f"{len(locked)} published version(s).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
