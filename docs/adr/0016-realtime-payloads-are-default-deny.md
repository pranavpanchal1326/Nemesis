# 0016 — A published event payload is empty unless a shape is declared for it

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT · SEC
- **Blueprint:** §22.1, §26.3, §26.4

## Context

The §26.3 WebSocket stream is, today, unauthenticated: it is scoped to a tenant
by a query parameter, and Phase 13 is what will make that a claim about *who* is
asking. Meanwhile the events it carries include `complaint_submitted`, whose
payload holds the citizen's exact GPS, the text they typed, the media references,
and the `device_fingerprint` §11.3 collects for abuse detection and §22 forbids
from leaving the system at all.

The natural implementation is to forward the stored payload and strip the
sensitive fields.

## Decision

**The opposite: forward nothing, and declare per event type what may be
published.** An event type with no declared shape publishes `{}`.

The reason is not tidiness. Strip-the-bad-fields fails on the *next* field
somebody adds: the new field is published by default, and nobody finds out until
it is already on a screen. Declare-what-is-allowed fails safe on exactly the same
change — a new field is invisible until someone decides it should not be, and
that decision is a diff in one file with a test next to it.

The scenes need almost nothing. §19.2's cluster merge needs a centroid, a
confidence, and a severity. A pin appearing needs a coarse position and a status.

Two supporting decisions:

- **Coordinates are coarsened at the source**, not at the client. `GPS_DECIMALS
  = 3` is roughly 110 metres — enough to place a pin on a street, not enough to
  place it at a house. Phase 4's public API inherits the same function rather
  than reimplementing the rounding, so there is one definition of "coarse".
- **§26.3's own example is not followed literally.** The blueprint's
  `cluster_match_found` sample includes `merged_complaint_ids`. A complaint id is
  an opaque handle to one citizen's report, and §26.4 forbids citizen identifiers
  on the public surface; the scene needs to know a merge happened and what the
  cluster now looks like. The deviation is recorded here rather than absorbed
  silently.

Chain selection is an allow-list for the same reason: `PUBLISHED_ENTITY_TYPES`
names complaint, cluster, and work order. `admin_action` and `system_degradation`
are operational history, and broadcasting them would put an outage's internals on
a citizen's phone.

## Alternatives considered

**Deny-list the sensitive fields.** Rejected above — it is safe for the fields
that exist when it is written and unsafe for every field added afterwards.

**Publish envelopes only, with no payload at all**, and make the client fetch
what it needs. Rejected: it is safest and it defeats the purpose. The merge scene
interpolates instance positions toward a centroid on receipt of the event; a
round trip per event turns a shader animation into a request waterfall.

**Wait for Phase 13 and publish full payloads to authenticated clients.**
Rejected on sequencing: the transport has to exist before the identity layer that
protects it, and shipping the permissive version first means the restrictive
version is a migration rather than a default.

## Consequences

- Each phase that adds an event type must decide, explicitly, whether it is
  publishable and in what shape. That is one more step and it is the step this
  ADR exists to force.
- A client cannot render everything from the stream alone. It receives "something
  changed on this entity, here is the coarse shape of it" and fetches the detail
  from §26.2 when it needs it, under whatever authorization Phase 13 establishes.
- `test_realtime.py` asserts the property directly on the pure function, so it is
  a unit test rather than an integration test with fixtures — which is what makes
  it cheap enough to extend on every new event type.

## Revisit when

- Phase 13 ships authorization, at which point an authenticated department user
  may legitimately receive more than an anonymous map viewer. That is a second
  shape per event type, not a removal of the first.
- Phase 4 builds the public API, which should consume these shapers rather than
  growing its own.
