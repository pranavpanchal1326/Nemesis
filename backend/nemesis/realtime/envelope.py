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
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

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


def _exif_check_completed(payload: Mapping[str, Any]) -> dict[str, Any]:
    """§E16.1 gate 2 — *EXIF INTACT* — and precisely nothing else (ADR-0045).

    One boolean, because one boolean is the whole caption. The three fields left
    behind are each withheld for a different reason, and the reasons are not
    interchangeable:

    ``distance_meters``
        The metres between the citizen's stated location and their camera's. On
        a stream whose every published coordinate is coarsened to ~110 m
        (``GPS_DECIMALS``), a metre-precise distance from a coarse pin is a
        second constraint on the same point — and two constraints is how a
        coarsening gets undone. It is published to the *holder of the complaint
        id* instead (``events/disclosure.py``), where it describes the reader's
        own report rather than a stranger's.
    ``trust_delta``
        The §11.3 gradient. Publishing what each behaviour costs publishes the
        surface an abuser optimises against.
    ``reason``
        System-authored prose that embeds the distance.
    """
    return {"exif_present": payload.get("exif_present")}


def _media_redacted(payload: Mapping[str, Any]) -> dict[str, Any]:
    """§E16.1 gate 5 — *a face visibly blurs* — as two counts (ADR-0045).

    **Why a count and not a boolean, on an unauthenticated stream.** §22.1's
    promise is about *every* face, and the catalog keeps ``faces_detected`` and
    ``faces_blurred`` apart precisely so a redaction that blurred some of them
    is distinguishable from one that blurred all of them. A single
    ``redacted: true`` cannot express failing that promise, and the surface where
    the failure most needs to be visible is the citizen's own phone, watching
    their own submission go through in real time.

    **What that exposes, stated rather than waved past.** A reader of the whole
    stream learns "a photograph attached to some report contained *n* faces".
    The envelope for this type carries no position at all; associating it with a
    place requires correlating on ``entity_id`` with a ``cluster_created``, whose
    centroid is already coarsened to ~110 m. The photograph is never published,
    the capture time is not in this payload, and *n* identifies nobody. That
    residual is accepted, and it is strictly smaller than the alternative —
    which is a product that claims to blur every face and publishes no number
    anyone could use to check.

    Withheld: both SHA-256s, because ``redacted_sha256`` resolves to an image on
    ``/api/v1/review/media/{sha}`` and ``source_sha256`` addresses the unblurred
    original (ADR-0031); and ``detector_id``, which names the model to evade and
    is published to the id-holder instead.
    """
    return {
        "media_kind": payload.get("media_kind"),
        "faces_detected": payload.get("faces_detected"),
        "faces_blurred": payload.get("faces_blurred"),
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
    # ADR-0045. §E16.1's pipeline theatre stages six gates and two of them —
    # the EXIF check and the face blur — reached the browser with an empty
    # payload, so the captions the blueprint specifies could not be driven from
    # the stream at all. Added as a decision rather than a patch: the question
    # "what may a browser learn about an EXIF check, or about where a face was"
    # has an answer, and it is written down in the ADR and in the two shapers.
    "exif_check_completed": _exif_check_completed,
    "media_redacted": _media_redacted,
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


# --------------------------------------------------------------------------
# The wire shape, published
# --------------------------------------------------------------------------
#
# OpenAPI 3.1 cannot describe a WebSocket, so nothing in this module reaches
# `/openapi.json` by the ordinary route. That is a problem for exactly one
# consumer, and it is a real one: the frontend's Law 2
# (docs/FRONTEND-EXECUTION-PLAN.md) forbids a hand-written interface describing
# a backend contract, and §E24 makes it a review failure. Without a published
# shape, every browser client would have to invent one — and then the day a
# field moves, the client keeps type-checking and starts lying.
#
# So the shape is declared as a model here, `nemesis.api.openapi_export` merges
# it into the exported document as a component, and
# `tests/test_realtime_envelope.py` asserts the model accepts what
# `build_envelope` actually produces. The model cannot drift from the builder
# without a test failing, which is the same arrangement `schema_lock.json` has
# with the event catalog.


class RealtimeEnvelope(BaseModel):
    """§26.3's envelope, exactly as ``build_envelope`` emits it."""

    model_config = ConfigDict(frozen=True)

    #: One of ``shaped_event_types()``. Any other registered type reaches the
    #: browser with an empty payload — default deny, ADR-0016.
    event_type: str
    entity_type: str
    entity_id: str
    sequence: int
    timestamp: str
    #: The outbox position the client has now seen. Reconnect with
    #: ``?since=<cursor>`` to be replayed from here rather than from nothing.
    cursor: int
    #: Shaped per event type. Coordinates are already coarsened to
    #: ``GPS_DECIMALS``; the client never receives an exact position.
    payload: dict[str, Any]


class RealtimeHeartbeat(BaseModel):
    """The liveness envelope, sent on the same queue as everything else.

    It carries no cursor because it advances nothing. A client that stops
    receiving these has a socket that is open and dead, which is the failure
    §E14.3 requires the client to be able to detect.
    """

    model_config = ConfigDict(frozen=True)

    event_type: Literal["heartbeat"]
    timestamp: str


class RealtimeResyncRequired(BaseModel):
    """Sent when a reconnecting client's gap exceeds the replay window.

    A replay that takes longer than a page reload is worse than a page reload,
    so past ``MAX_REPLAY`` the server says so instead of handing the client ten
    thousand animations to play.
    """

    model_config = ConfigDict(frozen=True)

    event_type: Literal["resync_required"]
    timestamp: str
