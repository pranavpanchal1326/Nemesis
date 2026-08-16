"""SHA-256 hash chaining for the append-only event log (§9.3).

Every event carries the hash of its predecessor *for the same entity*, so the
history of one complaint is a chain rather than the whole table being one. That
is what makes per-entity verification cheap enough to run on read.

**This widens the Blueprint §9.3 preimage, deliberately.** The blueprint hashes::

    previous_hash ‖ event_type ‖ entity_id ‖ json.dumps(payload) ‖ created_at

which omits four fields that change the meaning of the event, so each is a
tamper an auditor would not catch (ADR-0010):

``tenant_id``
    Moving a row between tenants leaves the chain valid. In a multi-tenant
    product that is the highest-consequence edit available.
``event_version``
    Rewriting the version changes which upcaster interprets the payload, so the
    same bytes are read as a different event.
``sequence``
    Without it, appends are ordered only by ``previous_hash``. Reordering two
    events that happen to share a predecessor is undetectable.
``entity_type``
    ``entity_id`` is a UUID, but nothing in the schema forbids the same UUID
    appearing under two entity types.

The preimage is also **structured rather than concatenated**. String
concatenation is ambiguous: an ``event_type`` of ``"a"`` with an entity id
starting ``"bc"`` produces the same bytes as ``"ab"`` with ``"c"``. Building a
canonical JSON object instead makes every field delimited and length-implied,
and reuses the one canonicaliser the payload already goes through.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Final

from nemesis.domain.constants import HASH_HEX_LENGTH
from nemesis.events.canonical import JSONValue, canonicalise

#: Domain-separated genesis value, so the first link of a NEMESIS chain cannot
#: collide with the first link of any other chained log, and a chain cannot be
#: re-rooted by inserting a plausible all-zeroes predecessor.
GENESIS_HASH: Final = hashlib.sha256(b"nemesis.event-chain.genesis.v1").hexdigest()

#: Bumped only when the preimage *shape* changes. Stored in the preimage itself,
#: so a chain written under one scheme can never be silently verified under
#: another — the mismatch surfaces as a broken link at the exact transition.
CHAIN_SCHEME_VERSION: Final = 1

#: Re-exported so callers reasoning about the chain have one import, while the
#: definition stays in a leaf module that cannot create a cycle with the models.
__all__ = [
    "CHAIN_SCHEME_VERSION",
    "GENESIS_HASH",
    "HASH_HEX_LENGTH",
    "compute_event_hash",
    "format_timestamp",
]


def compute_event_hash(
    *,
    previous_hash: str,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    sequence: int,
    event_type: str,
    event_version: int,
    payload: JSONValue,
    occurred_at: datetime,
) -> str:
    """Return the hex SHA-256 link for one event.

    ``occurred_at`` must be timezone-aware; a naive datetime would hash
    differently depending on the writer's local zone, which is the same class of
    non-determinism the canonicaliser exists to remove.
    """
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware to hash deterministically")

    preimage: dict[str, JSONValue] = {
        "scheme": CHAIN_SCHEME_VERSION,
        "previous_hash": previous_hash,
        "tenant_id": str(tenant_id),
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "sequence": sequence,
        "event_type": event_type,
        "event_version": event_version,
        "occurred_at": format_timestamp(occurred_at),
        "payload": payload,
    }
    return hashlib.sha256(canonicalise(preimage)).hexdigest()


def format_timestamp(value: datetime) -> str:
    """RFC 3339 in UTC with microsecond precision, always.

    Postgres ``timestamptz`` stores microseconds and returns them in the
    session's timezone. Normalising to UTC and to a fixed number of fractional
    digits means a value hashed on write and a value read back in a different
    session timezone produce the same string — ``isoformat()`` alone does not,
    because it omits the fractional part entirely when it happens to be zero.
    """
    utc = value.astimezone(tz=UTC)
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{utc.microsecond:06d}Z"
