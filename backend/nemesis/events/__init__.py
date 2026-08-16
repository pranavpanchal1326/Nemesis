"""The event store — NEMESIS's system of record (Blueprint §9).

Importing this package imports the catalog for its registration side effect. A
partially-registered registry is worse than an empty one: appends for
unregistered types fail loudly, but *reads* of an event whose type is missing
would raise ``UnknownEventTypeError`` in the middle of a replay, and the reader
would have no way to tell "this build is old" from "this event is corrupt".
"""

from __future__ import annotations

from nemesis.events import catalog as catalog  # re-exported for its registration side effect
from nemesis.events.canonical import CanonicalisationError, canonicalise, canonicalise_to_str
from nemesis.events.hashing import GENESIS_HASH, compute_event_hash
from nemesis.events.registry import (
    EventRegistryError,
    UnknownEventTypeError,
    entity_type_of,
    latest_version,
    read_payload,
    register_event,
    registered_events,
    upcast,
    validate_payload,
)
from nemesis.events.store import AppendedEvent, ChainForkError, EventStore, EventStoreError
from nemesis.events.verify import (
    BreakKind,
    ChainBreak,
    ChainVerification,
    SweepResult,
    sweep_chains,
    verify_chain,
)

__all__ = [
    "GENESIS_HASH",
    "AppendedEvent",
    "BreakKind",
    "CanonicalisationError",
    "ChainBreak",
    "ChainForkError",
    "ChainVerification",
    "EventRegistryError",
    "EventStore",
    "EventStoreError",
    "SweepResult",
    "UnknownEventTypeError",
    "canonicalise",
    "canonicalise_to_str",
    "compute_event_hash",
    "entity_type_of",
    "latest_version",
    "read_payload",
    "register_event",
    "registered_events",
    "sweep_chains",
    "upcast",
    "validate_payload",
    "verify_chain",
]
