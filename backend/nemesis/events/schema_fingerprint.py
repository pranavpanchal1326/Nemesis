"""Fingerprints for the event schema lock.

Lives in the application rather than in a script because two callers need the
identical definition — the regeneration tool and the CI test — and a fingerprint
computed two ways is a fingerprint that eventually disagrees with itself, which
would fail the build for no reason and teach everyone to regenerate the lock
without reading the diff.

The fingerprint is the canonical hash of the model's JSON schema. Canonical,
using the same function the event chain uses, so it does not move when Pydantic
reorders its output or when fields are reordered in the source without changing
the contract. It *does* move when a field is added, removed, renamed, retyped,
or has its constraints changed — which is exactly the set of changes that
invalidate payloads already written and hashed under the old shape.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

from nemesis.events.canonical import canonicalise
from nemesis.events.registry import registered_events

LOCK_PATH: Final = Path(__file__).resolve().parent / "schema_lock.json"
LOCK_FORMAT_VERSION: Final = 1


def fingerprint(schema: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalise(schema)).hexdigest()


def current_entries() -> dict[str, dict[str, Any]]:
    """Every registered ``(type, version)`` and its fingerprint."""
    return {
        f"{event.event_type}@{event.version}": {
            "entity_type": str(event.entity_type),
            "model": event.model.__name__,
            "fingerprint": fingerprint(event.model.model_json_schema()),
            "has_upcaster": event.upcaster_from_previous is not None,
        }
        for event in registered_events()
    }


def load_lock() -> dict[str, dict[str, Any]]:
    if not LOCK_PATH.exists():
        return {}
    data: dict[str, Any] = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, Any]] = data.get("events", {})
    return entries


def write_lock(entries: dict[str, dict[str, Any]]) -> None:
    LOCK_PATH.write_text(
        json.dumps(
            {
                "lock_format_version": LOCK_FORMAT_VERSION,
                "events": dict(sorted(entries.items())),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def compare(current: dict[str, dict[str, Any]], locked: dict[str, dict[str, Any]]) -> list[str]:
    """Differences that would break replay, phrased as what to do about them."""
    problems: list[str] = []

    for key, entry in sorted(current.items()):
        previous = locked.get(key)
        if previous is None:
            problems.append(
                f"{key} is registered but absent from schema_lock.json. Run "
                f"`python -m nemesis.events.schema_fingerprint --write` and commit the diff, "
                f"so a new event schema is a reviewable change rather than a silent one."
            )
        elif previous.get("fingerprint") != entry["fingerprint"]:
            event_type, version = key.rsplit("@", 1)
            problems.append(
                f"{key} ({entry['model']}) changed shape without a version bump. Every "
                f"{event_type} v{version} already in the log was validated and hashed under "
                f"the old schema. Register v{int(version) + 1} with an upcaster from v{version} "
                f"instead of editing this model."
            )

    for key in sorted(set(locked) - set(current)):
        problems.append(
            f"{key} is in schema_lock.json but is no longer registered. Removing an event type "
            f"makes existing history unreadable, and a reader cannot tell an obsolete event "
            f"from a corrupt one — deprecate it with a new version instead."
        )

    return problems


def _main() -> int:  # pragma: no cover — operator tool
    # `sys.stdout` resolved per write rather than bound once, matching
    # `nemesis.flags.__main__`: a stream replaced after import must not silence
    # the tool permanently.
    import sys

    def emit(line: str) -> None:
        sys.stdout.write(line + "\n")

    current = current_entries()
    if "--write" in sys.argv:
        write_lock(current)
        emit(f"schema lock written: {len(current)} event schema(s)")
        return 0

    problems = compare(current, load_lock())
    for problem in problems:
        emit(f"  - {problem}")
    emit(f"{'FAIL' if problems else 'OK'}: {len(current)} event schema(s)")
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
