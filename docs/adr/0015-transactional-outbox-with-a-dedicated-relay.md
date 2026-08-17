# 0015 — Realtime events publish from a transactional outbox, drained by a dedicated relay

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT
- **Blueprint:** §9.1, §26.3, §27.3

## Context

§26.3 promises a live event stream that drives the §19.2 cluster-merge scene and
the fallback visualisation behind it. The obvious implementation is to publish to
Redis from the code that appended the event — in the request handler, or in the
Celery task body.

That implementation has a defect that is invisible in development and permanent
in production: **it publishes before the transaction commits.** A rollback then
leaves the browser rendering a cluster merge the database never recorded, and
nothing later can retract it, because the frame is already on the screen. In a
product whose entire claim is that every visual element maps to a real pipeline
event (§6 principle #9, §20.3), an animation for an event that did not happen is
not a cosmetic bug.

The inverse ordering — commit first, publish second — trades that for a different
failure: the process dies between the two and the event is never published at
all. The map is silently missing a pin, and nothing anywhere records that it
should have had one.

## Decision

**An outbox row is written in the same transaction as the event, and a separate
process publishes committed rows.**

`outbox_messages` carries `event_id` and `event_recorded_at` — a **pointer**, not
a copy. The payload stays in `events` alone, because a denormalised copy would
double the citizen data Phase 26 has to erase and Phase 4 has to scrub, and it
could drift from the row whose hash was signed. What the row does duplicate is
the §26.3 envelope minus the payload, so the relay can route and order without
reading the log.

**The relay is its own process** (`nemesis.outbox.relay`, the `relay` compose
service). Two alternatives were rejected:

*Inside the API.* Every replica would publish every event, so a connected client
receives N copies. Solvable with a lock — at which point the request-serving
event loop is also holding a cluster-wide lease, and a slow request delays its
renewal.

*As a Celery beat task.* Beat's useful floor is seconds and its scheduling is not
designed for a 250 ms loop. A stream whose tail latency is one beat interval is
not a stream, and §26.3 exists to drive an animation.

**Single writer, enforced by Postgres.** `pg_try_advisory_lock` on a fixed key:
a second replica starts, fails to take the lock, and idles as a warm standby. The
lock is session-scoped, so a relay that is SIGKILLed releases it when its
connection drops — no lease to expire, no cleanup after a crash.

**At-least-once, deliberately.** The row is marked dispatched *after* the publish
succeeds. Crash in between and it is republished, so a client can see an event
twice. The envelope carries `sequence` and `cursor`, both monotonic, so a
duplicate is detectable by the consumer. A duplicated pin animation is a visual
hiccup; a dropped one is a complaint the map never shows.

**Dispatched rows are kept, not deleted on publish.** They are what a
reconnecting client is replayed from — `?since=<cursor>` reads the outbox, not
the partitioned log. That makes retention a real decision rather than
housekeeping: `OUTBOX_RESUME_WINDOW_HOURS` is the longest disconnect a client may
resume across, and the hourly purge is allowed to run unattended because deleting
an outbox row destroys no history. The event it pointed at is untouched, which is
exactly why §22.4 retention on `events` is *not* automated.

## Alternatives considered

**Redis Streams instead of pub/sub with an outbox.** Rejected: it puts a second
durable queue next to a durable log, with its own trimming policy and its own way
to disagree with the database about what happened. Pub/sub is fire-and-forget and
that is acceptable *here and nowhere else*, because durability already lives one
layer down.

**Postgres `LISTEN`/`NOTIFY`.** Fires on commit, which solves the ordering
problem elegantly — and is fire-and-forget, so a listener that is restarting
misses the notification entirely. It would still need the outbox as the durable
path, at which point it is an optimisation on top of this design rather than an
alternative to it. Worth revisiting to cut relay latency; not worth the second
mechanism today.

**Publish from the Celery task after `session.commit()`.** Rejected: it is the
commit-then-publish ordering above, dressed up. The window is small and the
failure is silent, which is the worst combination.

## Consequences

- One more container, ~192 MB, bringing the application stack to 6528 MB inside
  the 8192 MB WSL2 cap in `docs/HARDWARE.md`. The `obs` profile still fits.
- One more thing that can be down. Its liveness is a compose healthcheck and its
  lag is `nemesis_outbox_dispatch_lag_seconds`, which is the number §26.3's
  "realtime" claim actually rests on — and the only signal that would reveal a
  relay that is alive, healthy, and hours behind.
- The relay reads across tenants by construction and says so through the guard's
  explicit exemption, rather than by having no tenant column to check.
- A failed publish **stops the batch** rather than skipping ahead. Publishing
  later rows past a failed one would deliver an entity's events out of order, and
  arrival order is the consumer's only ordering signal.

## Revisit when

- Phase 1b picks a deploy target: a managed pub/sub may replace the Redis channel
  without touching the outbox, which is the point of the split.
- Phase 21's temporal replay needs to seek the log directly rather than the
  outbox, at which point the resume window stops being the limit on how far back
  a client can go.
