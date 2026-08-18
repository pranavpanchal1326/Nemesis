"""Fan-out from the outbox, and the retrying dispatcher.

Two passes, deliberately separate:

``fan_out_once``
    Reads committed ``outbox_messages`` from a durable cursor and inserts one
    ``webhook_deliveries`` row per matching subscription. The cursor advances in
    the *same transaction* as the rows it produced, so a crash re-reads the
    batch rather than skipping it — and the unique constraint on (endpoint,
    event) makes that re-read a no-op rather than a duplicate.

``dispatch_once``
    Takes due deliveries, signs and sends them, and reschedules or terminates
    each one. Rows are locked ``FOR UPDATE SKIP LOCKED`` so two dispatchers are
    merely concurrent rather than a source of double delivery.

**Why the outbox and not the event log directly.** ``outbox_messages`` is
already exactly "committed events, in commit order, with the envelope fields" —
built in Phase 3 for the realtime relay. Reading it costs one indexed scan and
keeps the submission transaction free of a subscription lookup. Reading
``events`` instead would mean ordering across monthly partitions and
reimplementing the commit-order guarantee the outbox exists to provide.

**The retention interaction, stated because it is the sharp edge.** The outbox
sweep deletes dispatched rows older than the resume window. If the webhook
cursor falls behind that window, events are silently skipped — the worst
possible failure for a durable delivery promise. ``fan_out_once`` therefore
reports its lag, ``sweep_outbox_safe_below`` gives the retention task a floor it
must not delete past, and an alert fires on the gap long before it matters.

**Why the retry schedule is configuration and not a loop.** The Phase 4 gate
requires delivery to survive an hour-long outage and then drain, so the schedule
has to span more than an hour with attempts to spare. Written as
``backoff_schedule_seconds`` it is a number somebody can read and a test can
assert against; written as ``2 ** attempt`` it is a property nobody can state
without doing arithmetic, and the arithmetic is where "exponential backoff"
usually turns out to top out at four minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import signal
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.config import Settings, WebhookSettings, get_settings
from nemesis.db.models.event import Event
from nemesis.db.models.integration import WebhookCursor, WebhookDelivery, WebhookEndpoint
from nemesis.db.models.outbox import OutboxMessage
from nemesis.db.session import dispose_engine, session_scope
from nemesis.integrations import webhooks
from nemesis.integrations.errors import UnsafeTargetError
from nemesis.observability import metrics
from nemesis.observability.logging import configure_logging, get_logger
from nemesis.realtime.envelope import build_envelope

log = get_logger(__name__)

#: The one cursor row, created by the Phase 4 migration.
CURSOR_ID: Final = 1

#: HTTP status codes that mean "do not retry". A 410 is the receiver saying the
#: endpoint is gone, and a 4xx that is not a timeout or a rate limit means the
#: request is wrong in a way that resending unchanged cannot fix — retrying it
#: for ten hours is load on their infrastructure with no possible outcome.
#: 408 and 429 are explicitly *not* here: both mean "try again".
_PERMANENT_FAILURE_CODES: Final = frozenset(
    {400, 401, 403, 404, 405, 406, 410, 411, 413, 414, 415, 422, 451}
)


@dataclass(frozen=True, slots=True)
class FanOutPass:
    scanned: int
    enqueued: int
    cursor: int


@dataclass(frozen=True, slots=True)
class DispatchPass:
    delivered: int
    retrying: int
    failed: int


@dataclass(frozen=True, slots=True)
class EventRef:
    """The stored event a delivery row points at.

    Carries the entity id alongside the payload because the signed envelope
    needs both and the delivery row deliberately stores neither: duplicating the
    entity id into ``webhook_deliveries`` would be a second thing that can
    disagree with the log, for a value one join already provides.
    """

    payload: dict[str, Any]
    entity_id: uuid.UUID
    entity_type: str
    sequence: int


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


async def fan_out_once(session: AsyncSession, *, batch_size: int) -> FanOutPass:
    """Turn newly committed outbox rows into pending deliveries."""
    # tenant-scope-exempt: the cursor is a deployment singleton with no tenant
    # column. Locked rather than read so two dispatchers serialise here instead
    # of both fanning out the same window.
    cursor = (
        await session.execute(
            select(WebhookCursor).where(WebhookCursor.id == CURSOR_ID).with_for_update()
        )
    ).scalar_one()

    # tenant-scope-exempt: the outbox is the deployment-wide committed feed; the
    # rows this produces are tenant-scoped and carry the tenant from each row.
    rows = (
        (
            await session.execute(
                select(OutboxMessage)
                .where(OutboxMessage.id > cursor.last_outbox_id)
                .order_by(OutboxMessage.id)
                .limit(batch_size)
                .execution_options(nemesis_tenant_scope_exempt=True)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return FanOutPass(scanned=0, enqueued=0, cursor=cursor.last_outbox_id)

    subscriptions = await _active_subscriptions(session, tenants={row.tenant_id for row in rows})

    pending: list[dict[str, Any]] = []
    for row in rows:
        for endpoint_id, event_types in subscriptions.get(row.tenant_id, ()):
            if row.event_type not in event_types:
                continue
            pending.append(
                {
                    "tenant_id": row.tenant_id,
                    "endpoint_id": endpoint_id,
                    "event_id": row.event_id,
                    "event_recorded_at": row.event_recorded_at,
                    "event_type": row.event_type,
                    "status": "pending",
                    "attempts": 0,
                    "next_attempt_at": datetime.now(tz=UTC),
                }
            )

    if pending:
        # DO NOTHING on conflict, not DO UPDATE: a row that already exists is a
        # re-read after a crash, and its attempt count and schedule are the
        # dispatcher's state. Overwriting them would reset an endpoint that is
        # eight attempts into a ten-hour backoff back to "try immediately",
        # every time the fan-out restarted.
        await session.execute(
            insert(WebhookDelivery)
            .values(pending)
            .on_conflict_do_nothing(constraint="uq_webhook_deliveries_endpoint_event")
        )

    cursor.last_outbox_id = rows[-1].id
    return FanOutPass(scanned=len(rows), enqueued=len(pending), cursor=rows[-1].id)


async def _active_subscriptions(
    session: AsyncSession, *, tenants: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[tuple[uuid.UUID, frozenset[str]], ...]]:
    """Active subscriptions for the tenants in this batch, in one query.

    Scoped by an explicit ``IN`` over the batch's tenants rather than fetching
    every subscription in the deployment: the predicate is what the tenancy
    guard demands, and it is also the right query — a batch touching three
    tenants has no business reading the other four hundred.
    """
    if not tenants:
        return {}
    rows = (
        (
            await session.execute(
                select(
                    WebhookEndpoint.tenant_id,
                    WebhookEndpoint.id,
                    WebhookEndpoint.event_types,
                ).where(
                    WebhookEndpoint.tenant_id.in_(sorted(tenants)),
                    WebhookEndpoint.is_active.is_(True),
                )
            )
        )
        .tuples()
        .all()
    )
    grouped: dict[uuid.UUID, list[tuple[uuid.UUID, frozenset[str]]]] = {}
    for tenant_id, endpoint_id, event_types in rows:
        grouped.setdefault(tenant_id, []).append((endpoint_id, frozenset(event_types)))
    return {tenant: tuple(items) for tenant, items in grouped.items()}


async def fan_out_lag(session: AsyncSession) -> int:
    """Outbox rows committed but not yet fanned out.

    Surfaced as a metric because the failure this guards against is silent: a
    stalled fan-out looks identical to a quiet system from every other signal,
    right up until the outbox retention sweep deletes the rows it never read.
    """
    # tenant-scope-exempt: deployment-wide backlog measurement.
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(OutboxMessage)
                .where(
                    OutboxMessage.id
                    > select(WebhookCursor.last_outbox_id)
                    .where(WebhookCursor.id == CURSOR_ID)
                    .scalar_subquery()
                )
                .execution_options(nemesis_tenant_scope_exempt=True)
            )
        ).scalar_one()
    )


async def sweep_outbox_safe_below(session: AsyncSession) -> int:
    """The outbox id a retention sweep must not delete at or above.

    Exported so the Phase 3 retention task can consult it rather than assuming
    the relay is the only reader. Deleting past this point would silently drop
    events that no webhook subscriber ever received, and nothing downstream
    would report a gap — the deliveries were never created, so there is no
    failed row to find.
    """
    return int(
        (
            await session.execute(
                select(WebhookCursor.last_outbox_id).where(WebhookCursor.id == CURSOR_ID)
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def backoff_delay(
    settings: WebhookSettings, attempts: int, *, rng: random.Random | None = None
) -> timedelta | None:
    """Delay before attempt ``attempts + 1``, or ``None`` when the budget is spent.

    Jittered downward from the scheduled value, never upward: the published
    claim is that the schedule spans more than an hour, and jitter that could
    *extend* an interval would make that claim untrue by up to twenty percent at
    exactly the moment somebody checks it.
    """
    schedule = settings.backoff_schedule_seconds
    if attempts >= len(schedule):
        return None
    base = schedule[attempts]
    generator = rng or random
    jitter = base * settings.backoff_jitter_ratio * generator.random()
    return timedelta(seconds=base - jitter)


def total_retry_window(settings: WebhookSettings) -> timedelta:
    """How long the schedule spans in the worst case. Asserted by the gate test."""
    return timedelta(seconds=sum(settings.backoff_schedule_seconds))


async def dispatch_once(
    session: AsyncSession,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    now: datetime | None = None,
) -> DispatchPass:
    """Send one batch of due deliveries."""
    moment = now or datetime.now(tz=UTC)

    # tenant-scope-exempt: the dispatcher serves the deployment. Every row it
    # touches carries its own tenant and is delivered to that tenant's endpoint;
    # scoping the *selection* by tenant would mean iterating the customer list
    # and would reorder deliveries relative to how they were committed.
    rows = (
        (
            await session.execute(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status == "pending",
                    WebhookDelivery.next_attempt_at <= moment,
                )
                .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
                .limit(settings.webhooks.batch_size)
                .with_for_update(skip_locked=True)
                .execution_options(nemesis_tenant_scope_exempt=True)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return DispatchPass(delivered=0, retrying=0, failed=0)

    endpoints = await _endpoints_for(session, ids={row.endpoint_id for row in rows})
    refs = await _events_for(session, rows)

    delivered = retrying = failed = 0
    # Both tables are tenant-scoped, and this loop is legitimately cross-tenant —
    # so every write is an explicit, exempted UPDATE rather than an ORM
    # attribute assignment. That is not a workaround for the tenancy guard; it is
    # the guard doing its job. A flush emits `UPDATE ... WHERE id = ?` with no
    # tenant predicate, which is indistinguishable from the mistake the guard
    # exists to catch — and writing the intent out explicitly means the exemption
    # is auditable at the statement that needs it (ADR-0014).
    for row in rows:
        endpoint = endpoints.get(row.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            # The subscription was disabled or deleted between enqueue and now.
            # Terminal rather than pending: leaving it queued would mean a
            # re-enabled endpoint suddenly receives a flood of events from the
            # window it was switched off for, which is not what "disabled" meant
            # to the person who switched it off.
            await _update_delivery(
                session,
                row.id,
                status="failed",
                next_attempt_at=None,
                last_error="subscription inactive",
            )
            failed += 1
            continue

        outcome = await _attempt(
            client=client,
            settings=settings,
            endpoint=endpoint,
            row=row,
            ref=refs.get(row.event_id),
        )
        attempts = row.attempts + 1

        if outcome.succeeded:
            await _update_delivery(
                session,
                row.id,
                status="delivered",
                attempts=attempts,
                delivered_at=moment,
                next_attempt_at=None,
                last_status_code=outcome.status_code,
                last_error=None,
            )
            await _update_endpoint(session, endpoint.id, consecutive_failures=0)
            delivered += 1
            metrics.webhook_deliveries_total.labels(outcome="delivered").inc()
            metrics.webhook_delivery_lag_seconds.observe(
                max(0.0, (moment - row.event_recorded_at).total_seconds())
            )
            continue

        failures = endpoint.consecutive_failures + 1
        delay = None if outcome.permanent else backoff_delay(settings.webhooks, attempts)
        if delay is None:
            await _update_delivery(
                session,
                row.id,
                status="failed",
                attempts=attempts,
                next_attempt_at=None,
                last_status_code=outcome.status_code,
                last_error=outcome.error,
            )
            failed += 1
            metrics.webhook_deliveries_total.labels(outcome="failed").inc()
        else:
            await _update_delivery(
                session,
                row.id,
                attempts=attempts,
                next_attempt_at=moment + delay,
                last_status_code=outcome.status_code,
                last_error=outcome.error,
            )
            retrying += 1
            metrics.webhook_deliveries_total.labels(outcome="retrying").inc()

        endpoint_changes: dict[str, Any] = {"consecutive_failures": failures}
        if failures >= settings.webhooks.disable_after_consecutive_failures:
            endpoint_changes |= {
                "is_active": False,
                "disabled_at": moment,
                "disabled_reason": (
                    f"{failures} consecutive delivery failures; last error: {outcome.error}"
                ),
            }
            log.warning(
                "webhook_endpoint_disabled",
                endpoint_id=str(endpoint.id),
                failures=failures,
                runbook="docs/runbooks/webhook-delivery-failing.md",
            )
        await _update_endpoint(session, endpoint.id, **endpoint_changes)
    return DispatchPass(delivered=delivered, retrying=retrying, failed=failed)


async def _update_delivery(session: AsyncSession, delivery_id: int, **values: Any) -> None:
    """Write one delivery row's new state.

    tenant-scope-exempt: the dispatcher serves the deployment, and this updates
    exactly the row the exempted, locked select above returned. The primary key
    is the narrowest possible predicate — narrower than a tenant scope would be —
    so the exemption widens nothing.
    """
    await session.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(**values)
        .execution_options(nemesis_tenant_scope_exempt=True)
    )


async def _update_endpoint(session: AsyncSession, endpoint_id: uuid.UUID, **values: Any) -> None:
    """Write one endpoint's failure state. tenant-scope-exempt: as above."""
    await session.execute(
        update(WebhookEndpoint)
        .where(WebhookEndpoint.id == endpoint_id)
        .values(**values)
        .execution_options(nemesis_tenant_scope_exempt=True)
    )


@dataclass(frozen=True, slots=True)
class _Attempt:
    succeeded: bool
    status_code: int | None
    error: str | None
    #: True when retrying cannot possibly help — a 4xx that is not a timeout or
    #: a throttle, or a target that has become unsafe to reach.
    permanent: bool = False


async def _attempt(
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    endpoint: WebhookEndpoint,
    row: WebhookDelivery,
    ref: EventRef | None,
) -> _Attempt:
    """One HTTP delivery, with the target re-validated first."""
    try:
        # Re-checked at delivery, not only at registration. DNS answers change
        # between the two, and that gap *is* the rebinding attack — a hostname
        # that resolved publicly when the subscription was created can resolve
        # to 169.254.169.254 by the time we send.
        webhooks.assert_target_is_safe(
            endpoint.url, allow_private=settings.webhooks.allow_private_network_targets
        )
    except UnsafeTargetError as exc:
        return _Attempt(succeeded=False, status_code=None, error=str(exc), permanent=True)

    if ref is None:
        # The event the row points at could not be loaded. Permanent rather than
        # retryable: the pointer is to an append-only log, so an event that is
        # not there now will not appear later, and the only way this happens is
        # a partition detached by retention out from under a delivery that never
        # drained — which is a gap to investigate, not to retry into.
        return _Attempt(
            succeeded=False,
            status_code=None,
            error="referenced event is not readable; see the outbox retention floor",
            permanent=True,
        )

    envelope = build_envelope(
        event_type=row.event_type,
        entity_type=ref.entity_type,
        entity_id=ref.entity_id,
        sequence=ref.sequence,
        occurred_at=row.event_recorded_at,
        payload=ref.payload,
        cursor=row.id,
    )
    # The same default-deny shaper the realtime stream uses (ADR-0016). A webhook
    # is a more durable disclosure than a WebSocket frame — the receiver keeps
    # it — so publishing *more* here than on the socket would be exactly
    # backwards.
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()

    secret = webhooks.derive_secret(
        settings.webhook_signing_key.get_secret_value(), endpoint.id, endpoint.secret_version
    )
    headers = {
        "Content-Type": "application/json",
        webhooks.SIGNATURE_HEADER: webhooks.sign(secret, body),
        webhooks.DELIVERY_ID_HEADER: str(row.id),
        webhooks.EVENT_TYPE_HEADER: row.event_type,
        webhooks.ATTEMPT_HEADER: str(row.attempts + 1),
        "User-Agent": f"NEMESIS-Webhooks/{settings.service_version}",
    }

    try:
        response = await client.post(
            endpoint.url,
            content=body,
            headers=headers,
            timeout=settings.webhooks.request_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        # A transport error is retryable by definition: nothing was learned
        # about whether the receiver would have accepted the payload.
        return _Attempt(succeeded=False, status_code=None, error=type(exc).__name__)

    if 200 <= response.status_code < 300:
        return _Attempt(succeeded=True, status_code=response.status_code, error=None)

    return _Attempt(
        succeeded=False,
        status_code=response.status_code,
        # The response *body* is deliberately not recorded. It is
        # attacker-influenced content landing in a log the tenant reads back
        # through an API (§25), and the status code plus the attempt count is
        # what actually diagnoses a delivery failure.
        error=f"HTTP {response.status_code}",
        permanent=response.status_code in _PERMANENT_FAILURE_CODES,
    )


async def _endpoints_for(
    session: AsyncSession, *, ids: set[uuid.UUID]
) -> dict[uuid.UUID, WebhookEndpoint]:
    if not ids:
        return {}
    # tenant-scope-exempt: resolving subscriptions for a cross-tenant batch the
    # dispatcher select above already authorised. Each row is delivered only to
    # the endpoint its own tenant owns, which the unique constraint enforces.
    rows = (
        (
            await session.execute(
                select(WebhookEndpoint)
                .where(WebhookEndpoint.id.in_(sorted(ids)))
                .with_for_update()
                .execution_options(nemesis_tenant_scope_exempt=True)
            )
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


async def _events_for(
    session: AsyncSession, rows: Sequence[WebhookDelivery]
) -> dict[int, EventRef]:
    """Load the stored events the delivery rows point at.

    One query for the batch, with ``recorded_at`` bounds in the predicate so the
    planner prunes to the partitions involved — the same shape the outbox relay
    uses, for the same reason.
    """
    if not rows:
        return {}
    recorded = [row.event_recorded_at for row in rows]
    # tenant-scope-exempt: resolves pointers for a batch already authorised above.
    events = (
        (
            await session.execute(
                select(
                    Event.id,
                    Event.payload,
                    Event.entity_id,
                    Event.entity_type,
                    Event.sequence,
                )
                .where(
                    Event.id.in_([row.event_id for row in rows]),
                    Event.recorded_at >= min(recorded),
                    Event.recorded_at <= max(recorded),
                )
                .execution_options(nemesis_tenant_scope_exempt=True)
            )
        )
        .tuples()
        .all()
    )
    return {
        int(event_id): EventRef(
            payload=dict(payload),
            entity_id=entity_id,
            entity_type=str(entity_type),
            sequence=int(sequence),
        )
        for event_id, payload, entity_id, entity_type, sequence in events
    }


async def sweep_delivered(session: AsyncSession, *, older_than_days: int) -> int:
    """Delete delivered rows past the retention window.

    Only ``delivered`` rows. A ``failed`` one is the record of a promise this
    system did not keep, and it stays until the tenant has seen it — sweeping
    those would quietly erase the evidence of the thing the delivery log exists
    to expose.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=older_than_days)
    # tenant-scope-exempt: deployment-wide retention, on the calendar rather
    # than per customer — the same argument `archived_partitions` makes.
    result = await session.execute(
        text(
            "DELETE FROM webhook_deliveries WHERE status = 'delivered' AND delivered_at < :cutoff"
        ).bindparams(cutoff=cutoff)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def refresh_gauges(session: AsyncSession) -> None:
    """Publish the two numbers that would reveal a stalled dispatcher."""
    # tenant-scope-exempt: deployment-wide operational gauges.
    pending = (
        await session.execute(
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.status == "pending")
            .execution_options(nemesis_tenant_scope_exempt=True)
        )
    ).scalar_one()
    disabled = (
        await session.execute(
            select(func.count())
            .select_from(WebhookEndpoint)
            .where(
                and_(
                    WebhookEndpoint.is_active.is_(False),
                    WebhookEndpoint.disabled_at.is_not(None),
                )
            )
            .execution_options(nemesis_tenant_scope_exempt=True)
        )
    ).scalar_one()
    metrics.webhook_deliveries_pending.set(float(pending))
    metrics.webhook_endpoints_disabled.set(float(disabled))


# ---------------------------------------------------------------------------
# Daemon entry point
# ---------------------------------------------------------------------------


async def run_forever(settings: Settings | None = None) -> None:  # pragma: no cover — daemon
    """Fan out and dispatch continuously until signalled.

    Runs as its own process for the reasons ``outbox.relay``'s docstring gives —
    one thing, a container healthcheck for liveness, and a lag metric — with one
    addition specific to this workload: a webhook delivery blocks on somebody
    else's server for up to ten seconds, and putting that inside the API's event
    loop or a Celery worker's task slot means a slow receiver consumes capacity
    the rest of the system needs.
    """
    cfg = settings or get_settings()
    configure_logging(level=cfg.log_level, service_name="nemesis-webhooks")

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    async with httpx.AsyncClient(follow_redirects=False) as client:
        # follow_redirects=False, deliberately. A 302 to an internal address is
        # the SSRF guard being walked around one hop at a time, and re-running
        # the check on every redirect target is a more complicated way to reach
        # the same place as refusing them.
        log.info("webhook_dispatcher_starting", batch_size=cfg.webhooks.batch_size)
        try:
            while not stopping.is_set():
                try:
                    async with session_scope() as session:
                        await fan_out_once(session, batch_size=cfg.webhooks.fanout_batch_size)
                    async with session_scope() as session:
                        result = await dispatch_once(session, settings=cfg, client=client)
                    async with session_scope() as session:
                        await refresh_gauges(session)
                except Exception as exc:
                    log.error(
                        "webhook_pass_failed",
                        error_type=type(exc).__name__,
                        runbook="docs/runbooks/webhook-delivery-failing.md",
                    )
                    await _sleep_or_stop(stopping, 5.0)
                    continue

                if result.delivered == 0 and result.retrying == 0:
                    await _sleep_or_stop(stopping, 1.0)
        finally:
            await dispose_engine()
            log.info("webhook_dispatcher_stopped")


async def _sleep_or_stop(stopping: asyncio.Event, seconds: float) -> None:  # pragma: no cover
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stopping.wait(), timeout=seconds)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_forever())
