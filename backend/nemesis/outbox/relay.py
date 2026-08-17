"""The outbox relay: committed rows in, Redis publishes out.

Runs as its own process (``python -m nemesis.outbox.relay``, the ``relay``
compose service). Three placements were considered and two rejected:

*Inside the API* — every replica would publish every event, so a client
connected to any of them receives N copies. Solvable with a lock, but then the
API's request-serving loop is also holding a cluster-wide lease, and a slow
request delays the lease renewal.

*As a Celery beat task* — beat's useful floor is seconds and its scheduling
granularity is not designed for a 250 ms loop. A realtime stream whose tail
latency is one beat interval is not realtime, and §26.3 exists to drive an
animation.

*Its own process* — what this is. It does one thing, its liveness is a container
healthcheck, and its lag is a metric.

**Single writer, enforced by Postgres.** ``pg_try_advisory_lock`` on a fixed key
means a second replica starts, fails to take the lock, and idles as a warm
standby rather than double-publishing. The lock is session-scoped, so a relay
that is SIGKILLed releases it when its connection drops — no lease to expire and
no manual cleanup after a crash.

**At-least-once, deliberately.** A row is marked dispatched *after* the publish
succeeds. Crash in between and the row is republished on restart, so a client
can see an event twice. The envelope carries ``sequence`` and ``cursor``, both
monotonic per entity, so a duplicate is detectable by the consumer — which is
the correct trade: a duplicated pin animation is a visual hiccup, a dropped one
is a complaint the map never shows.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.config import Settings, get_settings
from nemesis.db.models.event import Event
from nemesis.db.models.outbox import OutboxMessage
from nemesis.db.session import dispose_engine, session_scope
from nemesis.observability import metrics
from nemesis.observability.logging import configure_logging, get_logger
from nemesis.realtime.channel import RedisEventChannel
from nemesis.realtime.envelope import build_envelope
from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

log = get_logger(__name__)

#: Advisory lock key. An arbitrary constant that must never collide with another
#: advisory lock in this database; recorded here as the only user of it.
RELAY_ADVISORY_LOCK_KEY: Final = 0x4E454D_524C59  # "NEM_RLY"

#: Rows per pass. Bounded so a large backlog is drained in steady increments
#: rather than in one long transaction that holds a connection and delays every
#: newly committed event behind it.
BATCH_SIZE: Final = 200

#: Sleep when a pass found nothing. Short enough that the tail latency of a
#: quiet system is imperceptible, long enough that an idle deployment is not
#: issuing four indexed queries a second forever.
IDLE_POLL_SECONDS: Final = 0.25

#: Sleep after a failed pass. Longer than the idle poll: if the database is
#: refusing connections, retrying four times a second helps nobody.
ERROR_BACKOFF_SECONDS: Final = 2.0


@dataclass(frozen=True, slots=True)
class RelayPass:
    """What one drain pass did — returned so tests can assert on it."""

    published: int
    failed: int


async def drain_once(session: AsyncSession, channel: RedisEventChannel) -> RelayPass:
    """Publish one batch of undispatched rows.

    Rows are locked ``FOR UPDATE SKIP LOCKED`` as well as being behind the
    advisory lock. Belt and braces on purpose: the advisory lock is what makes
    one relay the writer, and ``SKIP LOCKED`` is what makes a *second* relay
    that somehow acquired it — an operator running the module by hand to clear a
    backlog — merely slow rather than a source of duplicates.
    """
    # tenant-scope-exempt: the relay serves the deployment, not a customer.
    # There is no tenant in context here by design; introducing one would mean
    # looping over the tenant registry and republishing in customer order, which
    # would reorder events relative to how they were committed.
    rows = (
        (
            await session.execute(
                # tenant-scope-exempt: deployment-wide relay, see above.
                select(OutboxMessage)
                .where(OutboxMessage.dispatched_at.is_(None))
                .order_by(OutboxMessage.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
                .execution_options(**{TENANT_SCOPE_EXEMPT: True})
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return RelayPass(published=0, failed=0)

    payloads = await _load_payloads(session, rows)

    published: list[int] = []
    failed = 0
    for row in rows:
        envelope = build_envelope(
            event_type=row.event_type,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            sequence=row.sequence,
            occurred_at=row.occurred_at,
            payload=payloads.get(row.event_id, {}),
            cursor=row.id,
        )
        try:
            await channel.publish(row.tenant_id, envelope)
        except Exception as exc:
            failed += 1
            log.warning(
                "outbox_publish_failed",
                outbox_id=row.id,
                event_type=row.event_type,
                error_type=type(exc).__name__,
                runbook="docs/runbooks/outbox-relay-stalled.md",
            )
            # Stop at the first failure rather than skipping ahead. Publishing
            # later rows past a failed one would deliver an entity's events out
            # of order, and the consumer's only ordering signal is the order
            # they arrive in.
            break
        published.append(row.id)
        metrics.outbox_dispatch_lag_seconds.observe(
            max(0.0, (datetime.now(tz=UTC) - row.occurred_at).total_seconds())
        )

    if published:
        # tenant-scope-exempt: same reason as the select above — this marks the
        # rows that batch just published, whoever owns them.
        await session.execute(
            # tenant-scope-exempt: marks the rows this batch just published.
            update(OutboxMessage)
            .where(OutboxMessage.id.in_(published))
            .values(dispatched_at=datetime.now(tz=UTC))
            .execution_options(**{TENANT_SCOPE_EXEMPT: True})
        )
        metrics.outbox_dispatched_total.inc(len(published))

    return RelayPass(published=len(published), failed=failed)


async def _load_payloads(
    session: AsyncSession, rows: Sequence[OutboxMessage]
) -> dict[int, dict[str, Any]]:
    """Fetch the stored payloads the outbox rows point at.

    One query for the batch, and the ``recorded_at`` values go into the
    predicate so the planner prunes to the partitions actually involved instead
    of scanning every month of history.
    """
    if not rows:
        return {}
    ids = [row.event_id for row in rows]
    recorded = [row.event_recorded_at for row in rows]
    # tenant-scope-exempt: resolving pointers for a cross-tenant batch that the
    # outbox select already authorised.
    events = (
        (
            await session.execute(
                # tenant-scope-exempt: resolves pointers for a batch the
                # outbox select above already authorised.
                select(Event.id, Event.payload)
                .where(
                    Event.id.in_(ids),
                    Event.recorded_at >= min(recorded),
                    Event.recorded_at <= max(recorded),
                )
                .execution_options(**{TENANT_SCOPE_EXEMPT: True})
            )
        )
        .tuples()
        .all()
    )
    return {int(event_id): dict(payload) for event_id, payload in events}


async def _try_lock(session: AsyncSession) -> bool:
    held = (
        await session.execute(
            text("SELECT pg_try_advisory_lock(:key)").bindparams(key=RELAY_ADVISORY_LOCK_KEY)
        )
    ).scalar_one()
    return bool(held)


async def run_forever(settings: Settings | None = None) -> None:  # pragma: no cover — daemon loop
    """Drain continuously until signalled. The entry point of the relay service."""
    cfg = settings or get_settings()
    configure_logging(level=cfg.log_level, service_name="nemesis-relay")

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    channel = RedisEventChannel(cfg.redis_url)
    log.info("relay_starting", batch_size=BATCH_SIZE, poll_seconds=IDLE_POLL_SECONDS)

    try:
        while not stopping.is_set():
            try:
                async with session_scope() as session:
                    if not await _try_lock(session):
                        # Warm standby. Logged at info once per interval rather
                        # than silently, so "the relay is running but not
                        # relaying" is visible instead of being inferred from a
                        # growing backlog.
                        log.info("relay_standby", reason="advisory lock held by another relay")
                        await _sleep_or_stop(stopping, ERROR_BACKOFF_SECONDS)
                        continue
                    result = await drain_once(session, channel)
            except Exception as exc:
                log.error(
                    "relay_pass_failed",
                    error_type=type(exc).__name__,
                    runbook="docs/runbooks/outbox-relay-stalled.md",
                )
                await _sleep_or_stop(stopping, ERROR_BACKOFF_SECONDS)
                continue

            if result.published == 0:
                await _sleep_or_stop(stopping, IDLE_POLL_SECONDS)
    finally:
        await channel.close()
        await dispose_engine()
        log.info("relay_stopped")


async def _sleep_or_stop(stopping: asyncio.Event, seconds: float) -> None:  # pragma: no cover
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stopping.wait(), timeout=seconds)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_forever())
