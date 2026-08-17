"""The §26.3 event envelope, and what a payload is allowed to contain.

**Default deny.** An event type with no declared public shape publishes an empty
payload. The alternative — forward the stored payload and strip the fields that
look sensitive — fails the moment someone adds a field, because the new field is
published by default and nobody finds out until it is already on a screen.

The events this stream exists to drive need almost nothing. §19.2's cluster-merge
scene needs a centroid, a confidence, and a severity. A pin appearing needs a
coarse position and a status. None of that requires the citizen's exact GPS, the
description they typed, the photo URL, or the device fingerprint §11.3 collects
for abuse detection and §22 forbids from leaving the system.

**Coordinates are coarsened here, not at the client.** ``GPS_DECIMALS = 3`` is
roughly 110 metres — enough to place a pin on a street, not enough to place it at
a house. Phase 4's public API inherits this function rather than reimplementing
the rounding, so there is one definition of "coarse" in the system.

This module is deliberately free of imports from the database or the event store:
it converts a row's fields into a dict, which is what makes the "no public field
carries citizen data" test a pure-function assertion rather than an integration
test with fixtures.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final

from nemesis.events.hashing import format_timestamp

#: Decimal places kept on a published latitude or longitude. Three places is
#: ~110 m at the equator and less further from it. §22.1 treats an exact
#: complaint location as personal data; a street-level pin is the product.
GPS_DECIMALS: Final = 3

#: Builds the public payload for one event type from its stored payload.
PayloadShaper = Callable[[Mapping[str, Any]], dict[str, Any]]


def coarsen(value: float | None) -> float | None:
    """Round a coordinate to the published precision."""
    return None if value is None else round(float(value), GPS_DECIMALS)


def _cluster_created(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cluster_centroid": {
            "lat": coarsen(payload.get("latitude")),
            "lng": coarsen(payload.get("longitude")),
        },
        "report_count": 1,
    }


def _cluster_match_found(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The §26.3 worked example, and the one that drives the hero scene.

    ``merged_complaint_ids`` in the blueprint's example is deliberately *not*
    published. A complaint id is an opaque handle to a citizen's report, and
    §26.4 already forbids citizen identifiers on the public surface — the scene
    needs to know a merge happened and what the cluster now looks like, not which
    reports were folded in.
    """
    return {
        "new_confidence": payload.get("combined_confidence"),
        "report_count": payload.get("report_count_after"),
        "geo_distance_meters": payload.get("geo_distance_meters"),
    }


def _classification_scored(payload: Mapping[str, Any]) -> dict[str, Any]:
    # Category and confidence only. `transcript` is the citizen's own words and
    # `alternatives` is model diagnostics; neither belongs on a broadcast.
    return {
        "category": payload.get("category"),
        "confidence": payload.get("confidence"),
    }


def _severity_scored(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"new_severity": payload.get("score")}


def _safety_trigger_fired(payload: Mapping[str, Any]) -> dict[str, Any]:
    # The §20 bloom pulse needs to know the fail-safe fired. `matched_terms`
    # would republish the citizen's text verbatim, which is the one part of a
    # safety trigger that is certain to be sensitive.
    return {"detection_source": payload.get("detection_source")}


def _work_order_created(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"cluster_id": _as_str(payload.get("cluster_id"))}


def _citizen_confirmed(payload: Mapping[str, Any]) -> dict[str, Any]:
    # §44: an auto-confirmed closure must stay distinguishable from a real one
    # everywhere it surfaces, and this is one of the places it surfaces.
    return {"auto_confirmed": payload.get("auto_confirmed")}


def _pipeline_stage_degraded(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stage": payload.get("stage"),
        "fallback_taken": payload.get("fallback_taken"),
    }


#: Event type -> public payload shape. Absence means an empty payload, not an
#: unfiltered one.
_SHAPERS: Final[dict[str, PayloadShaper]] = {
    "cluster_created": _cluster_created,
    "cluster_match_found": _cluster_match_found,
    "classification_scored": _classification_scored,
    "severity_scored": _severity_scored,
    "safety_trigger_fired": _safety_trigger_fired,
    "work_order_created": _work_order_created,
    "citizen_confirmed": _citizen_confirmed,
    "pipeline_stage_degraded": _pipeline_stage_degraded,
}


def public_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The publishable subset of a stored payload, or ``{}`` if none is declared."""
    shaper = _SHAPERS.get(event_type)
    if shaper is None:
        return {}
    return shaper(payload)


def shaped_event_types() -> frozenset[str]:
    """Event types with a declared public shape — for the serialiser test."""
    return frozenset(_SHAPERS)


def build_envelope(
    *,
    event_type: str,
    entity_type: str,
    entity_id: uuid.UUID,
    sequence: int,
    occurred_at: datetime,
    payload: Mapping[str, Any],
    cursor: int,
) -> dict[str, Any]:
    """One §26.3 envelope.

    ``cursor`` is this repository's addition to the blueprint's shape: the
    outbox id the client has now seen. Without it a reconnecting client can only
    say "send me everything again" or "start from now", and the second one
    silently drops whatever arrived during the disconnect — which on a 3D map
    means a pin that never appears and no way to notice.
    """
    return {
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "sequence": sequence,
        "timestamp": format_timestamp(occurred_at),
        "cursor": cursor,
        "payload": public_payload(event_type, payload),
    }


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)
