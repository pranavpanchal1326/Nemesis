"""Projectors — rebuilding current state by replaying the log (§9.1).

``complaints``, ``complaint_clusters``, and ``work_orders`` are *derived*. The
log is what happened; those tables are a cache of the answer, maintained so the
API does not replay four hundred events to render one row.

Two design rules make that claim true rather than aspirational:

**A projector is a pure function of (state, event).** No clock, no random, no
database read, no network. Replaying the same events in the same order must
produce the same state on a laptop in 2026 and in a CI runner three years later,
or the "replay reproduces current state byte-identically" gate is measuring
nothing. Anything a handler needs must come from the event payload — which is
also why the payloads in the catalog record their inputs rather than just their
conclusions.

**State is JSON, not an ORM object.** Comparing two projections then becomes a
hash equality over canonical bytes instead of a recursive object diff whose
failure output nobody can read. It also means a snapshot is storable as-is, and
that the projector layer has no opinion about SQL.

Unhandled events are skipped, deliberately and loudly-in-aggregate: an
``admin_action`` has no place in a complaint's projected state, and requiring a
no-op handler for every combination would be ceremony that hides the real gaps.
``unhandled_event_types`` reports what was skipped so a genuinely missing
handler is discoverable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Final

from nemesis.events.canonical import JSONValue, canonicalise

#: The projected state of one entity. Plain JSON so it canonicalises, hashes,
#: and snapshots without a conversion step.
ProjectedState = dict[str, Any]

#: A handler receives the state so far and one event's *upcasted* payload, plus
#: the event envelope fields it may legitimately use (sequence, occurred_at).
#: It returns the new state. Mutating and returning the same dict is fine — the
#: caller never shares state between entities.
Projector = Callable[[ProjectedState, "ProjectionEvent"], ProjectedState]


class ProjectionError(RuntimeError):
    """A projection could not be built from the events supplied."""


class ProjectionEvent:
    """The subset of an event a projector is allowed to see.

    Narrow on purpose. Exposing the full row would let a handler reach for
    ``recorded_at`` — wall-clock time on the writing machine — and produce a
    projection that differs between the original run and the replay, which is
    exactly the bug the byte-identical gate exists to catch.
    """

    __slots__ = ("event_type", "occurred_at", "payload", "sequence", "version")

    def __init__(
        self,
        *,
        event_type: str,
        version: int,
        sequence: int,
        occurred_at: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.event_type = event_type
        self.version = version
        self.sequence = sequence
        #: ISO-8601 string, not a datetime: the projected state must serialise
        #: identically whichever timezone the reader's session happens to use.
        self.occurred_at = occurred_at
        self.payload = payload


#: Registered per (entity_type, event_type). The same event type never appears
#: under two entity types — the event registry already forbids that — so the
#: entity key is here for lookup clarity rather than for disambiguation.
_projectors: dict[tuple[str, str], Projector] = {}

#: Bumped when a handler changes in a way that alters the state it produces.
#: Snapshots record it, and a snapshot from an older version is discarded rather
#: than trusted — otherwise a projector bug fix would never reach any entity
#: that already had a snapshot, which is precisely the entities with history.
PROJECTOR_VERSION: Final = 1


def projector(entity_type: str, event_type: str) -> Callable[[Projector], Projector]:
    def decorator(handler: Projector) -> Projector:
        key = (entity_type, event_type)
        if key in _projectors:
            raise ProjectionError(f"a projector for {entity_type}/{event_type} already exists")
        _projectors[key] = handler
        return handler

    return decorator


def has_projector(entity_type: str, event_type: str) -> bool:
    return (entity_type, event_type) in _projectors


def project(
    entity_type: str,
    events: Iterable[ProjectionEvent],
    *,
    initial: ProjectedState | None = None,
) -> ProjectedState:
    """Fold events into state, starting from ``initial`` (or empty).

    ``initial`` is how snapshotting works: pass the snapshot's state and only
    the events after it, and the result is identical to folding from sequence 1.
    That equivalence is asserted by test, not assumed — it is the whole basis for
    seeking being cheap in Phase 21.
    """
    state: ProjectedState = dict(initial) if initial else {}
    for event in events:
        handler = _projectors.get((entity_type, event.event_type))
        if handler is None:
            continue
        state = handler(state, event)
    return state


def unhandled_event_types(entity_type: str, events: Iterable[ProjectionEvent]) -> set[str]:
    """Event types skipped during projection — for diagnosing a missing handler."""
    return {
        event.event_type for event in events if (entity_type, event.event_type) not in _projectors
    }


def state_hash(state: Mapping[str, Any]) -> str:
    """Canonical hash of a projected state.

    Uses the same canonicaliser as the event chain, so "byte-identical" means
    the same thing for a projection as it does for a payload — one definition of
    equality across the system rather than two that can disagree.
    """
    payload: JSONValue = dict(state)
    return hashlib.sha256(canonicalise(payload)).hexdigest()
