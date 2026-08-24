"""Write the OpenAPI document the frontend generates its client from.

**Why this is a module and not a `curl`.** The same reasoning that put
``nemesis.api.contract`` here rather than in ``scripts/``: reading the contract
the code actually serves means constructing the FastAPI app, which needs the
full dependency set — and the api container mounts only ``./backend``, so a
root-level script could never import it. It also means the export works with the
stack **down**, which matters because the frontend's type generation is a build
step and a build step that requires a running database is a build step that
fails on a laptop at an airport.

**Why the frontend does not fetch `/openapi.json` at build time instead.** It
could, and that would be one fewer file. But §E24 requires *generated-client
drift fails CI*, and a drift check needs something to diff against — a committed
artefact whose change shows up in a pull request next to the backend change that
caused it. A schema fetched at build time makes the drift invisible: the client
silently regenerates and the reviewer sees nothing.

So the document is committed, exactly like ``api_contract_lock.json`` and
``events/schema_lock.json`` are, and for the same reason. The three are the same
idea applied to three surfaces: the outward contract, the event log, and the
generated client.

Usage::

    python -m nemesis.api.openapi_export            # write
    python -m nemesis.api.openapi_export --check    # fail if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

#: Matching ``nemesis.api.contract``: this is an operator-facing CLI, so
#: stdout is the interface rather than a debugging leftover.
OK: Final = "[ OK ]"
FAIL: Final = "[FAIL]"

#: Committed next to the other two lock files, and read by
#: ``frontend/scripts/generate-api-types.ts``.
OPENAPI_PATH = Path(__file__).resolve().parent / "openapi.json"


def build_document() -> dict[str, Any]:
    """The app's OpenAPI document, plus the realtime shapes it cannot express.

    **The one thing this adds, and why.** OpenAPI 3.1 has no way to describe a
    WebSocket, so ``/ws/pipeline-events`` — the stream the entire map, the
    pipeline theatre and the merge scene are driven by — contributes nothing to
    the document. A browser client would therefore have to hand-write the
    envelope, which §E24 makes a review failure and which fails silently the day
    a field moves: the client keeps type-checking and starts lying.

    So three components are merged in: the envelope, the heartbeat, and the
    enumeration of event types that are actually *shaped* for the wire. That
    last one matters more than it looks. ADR-0016 makes realtime payloads
    default-deny, so most registered event types reach the browser with an empty
    payload. A frontend that assumes every event in §9.4 arrives with data is
    wrong about the majority of them, and publishing the shaped set is what lets
    it find that out at compile time rather than on a screen.

    The components are additive. ``nemesis.api.contract`` locks *operations*,
    not components, so nothing here changes what that check protects.
    """
    # Imported lazily so `--help` works without a database driver present.
    from nemesis.main import create_app
    from nemesis.realtime.envelope import (
        RealtimeEnvelope,
        RealtimeHeartbeat,
        RealtimeResyncRequired,
        shaped_event_types,
    )

    document: dict[str, Any] = create_app().openapi()
    schemas: dict[str, Any] = document.setdefault("components", {}).setdefault("schemas", {})

    for model in (RealtimeEnvelope, RealtimeHeartbeat, RealtimeResyncRequired):
        schemas[model.__name__] = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )

    # The lifecycle vocabulary. §E26.1 requires every surface to render the
    # *complete* set — thirteen complaint statuses including
    # `pending_classification` and `flagged`, six work-order statuses including
    # `created` and `disputed` — and calls a view that omits one a defect. The
    # blueprint's §E2 defects #15 and #16 are both instances of that omission
    # having already happened once, in the main blueprint.
    #
    # The response models type these fields as `str`, so the enums never reached
    # the published contract and a client had to hard-code them. Publishing them
    # as components is documentation-only: no request is validated differently,
    # no response changes shape, and the frontend gets a generated union whose
    # switch statements stop compiling when a member is added.
    from nemesis.domain import lifecycle

    for enum_cls, why in (
        (
            lifecycle.ComplaintStatus,
            "§9.2's complaint lifecycle, complete. `pending_classification` is the "
            "§24.2 degraded path — the classifier was unavailable and the report was "
            "parked for a human rather than guessed at — and `flagged` is trust and "
            "safety routing the report out of the normal path. Both are states that "
            "only occur when something has gone wrong, which makes them the two a UI "
            "is most likely to forget and most needs to show.",
        ),
        (
            lifecycle.WorkOrderStatus,
            "§9.2's work-order lifecycle, complete. `created` is the unassigned "
            "backlog and `disputed` is a citizen rejecting a closure. A board that "
            "cannot display its own failures is a board that launders them.",
        ),
        (lifecycle.MilestoneStage, "§15.5's 30/40/30 gate strip."),
        (
            lifecycle.AssigneeType,
            "A work order has staff or a contractor, never both — so the assignment "
            "control is one control with two modes, not two independent pickers.",
        ),
        (lifecycle.DegradationFallback, "§24.2. Which fallback path a degraded stage took."),
        (
            lifecycle.EntityType,
            "What an event's `entity_id` refers to. A closed set: the hash chain is per entity.",
        ),
    ):
        schemas[enum_cls.__name__] = {
            "type": "string",
            "enum": [member.value for member in enum_cls],
            "title": enum_cls.__name__,
            "description": why,
        }

    schemas["RealtimeShapedEventType"] = {
        "type": "string",
        "enum": sorted(shaped_event_types()),
        "title": "RealtimeShapedEventType",
        "description": (
            "Event types that carry a shaped payload on /ws/pipeline-events. "
            "Every other registered event type is delivered with an empty "
            "payload — realtime payloads are default-deny (ADR-0016), so a "
            "type absent from this list tells the client that something "
            "happened and nothing about what."
        ),
    }

    return document


def serialise(document: dict[str, Any]) -> str:
    # Sorted keys and a stable indent, so a diff shows what changed rather than
    # how the dict happened to be ordered this run. The same discipline
    # ADR-0013 applies to canonical JSON on the wire, applied to an artefact.
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed document does not match the code",
    )
    args = parser.parse_args(argv)

    current = serialise(build_document())

    if args.check:
        if not OPENAPI_PATH.exists():
            sys.stderr.write(f"{FAIL} openapi.json has never been written.\n")
            return 1
        if OPENAPI_PATH.read_text(encoding="utf-8") != current:
            sys.stderr.write(
                f"{FAIL} The committed OpenAPI document does not match the code.\n"
                f"  Run `nem web-openapi`, then `nem web-types`, and commit both.\n"
                f"  A generated client that drifts from its schema is a client that\n"
                f"  type-checks against a contract the server stopped serving.\n"
            )
            return 1
        paths = len(json.loads(current).get("paths", {}))
        sys.stdout.write(f"{OK} OpenAPI document matches the code - {paths} path(s).\n")
        return 0

    OPENAPI_PATH.write_text(current, encoding="utf-8", newline="\n")
    paths = len(json.loads(current).get("paths", {}))
    sys.stdout.write(f"{OK} openapi.json written from the current build - {paths} path(s).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
