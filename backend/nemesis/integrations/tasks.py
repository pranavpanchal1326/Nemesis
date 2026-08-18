"""Scheduled webhook work.

**The dispatcher runs as its own process; these tasks are the safety net.**
``integrations.delivery.run_forever`` is the primary path — a delivery blocks on
somebody else's server for up to ten seconds, and putting that in a Celery slot
means a slow receiver consumes worker capacity the pipeline needs. But a
deployment that has not started the dispatcher container must not silently stop
delivering, and the retention sweep and the gauges genuinely belong on a
schedule rather than in a hot loop.

So: beat drives the sweep, the gauges, and a bounded catch-up pass. If the
dedicated process is running, the catch-up pass finds nothing, because both take
the same ``FOR UPDATE SKIP LOCKED`` rows and whichever gets there first wins.
Two dispatchers are concurrent, never duplicative — which is a property of the
row locking, not of the deployment topology, and that is the point of testing it
rather than documenting a rule about which processes may run.

Registered in ``TASK_MODULES`` explicitly. Phase 2's ninth defect was a task
module that autodiscovery never found, contributing zero tasks and zero beat
schedules with no error anywhere.
"""

from __future__ import annotations

from typing import Any

import httpx

from nemesis.config import get_settings
from nemesis.db.session import session_scope
from nemesis.integrations import delivery
from nemesis.observability.logging import get_logger
from nemesis.worker.celery_app import QUEUE_IO, celery_app
from nemesis.worker.loop import run_async

log = get_logger(__name__)


@celery_app.task(name="nemesis.integrations.fan_out", queue=QUEUE_IO, bind=True)
def fan_out(self: Any) -> dict[str, int]:
    """Turn newly committed outbox rows into pending deliveries."""
    _ = self
    settings = get_settings()
    if not settings.webhooks.enabled:
        return {"scanned": 0, "enqueued": 0}

    async def _run() -> dict[str, int]:
        async with session_scope() as session:
            result = await delivery.fan_out_once(
                session, batch_size=settings.webhooks.fanout_batch_size
            )
        return {"scanned": result.scanned, "enqueued": result.enqueued}

    return run_async(_run())


@celery_app.task(name="nemesis.integrations.dispatch", queue=QUEUE_IO, bind=True)
def dispatch(self: Any) -> dict[str, int]:
    """Send one batch of due deliveries.

    A bounded catch-up pass, not the primary path — see the module docstring.
    """
    _ = self
    settings = get_settings()
    if not settings.webhooks.enabled:
        return {"delivered": 0, "retrying": 0, "failed": 0}

    async def _run() -> dict[str, int]:
        # follow_redirects=False, matching the dedicated dispatcher: a 302 to an
        # internal address is the SSRF guard being walked around one hop at a
        # time, and refusing redirects is simpler than re-checking each target.
        async with (
            httpx.AsyncClient(follow_redirects=False) as client,
            session_scope() as session,
        ):
            result = await delivery.dispatch_once(session, settings=settings, client=client)
        return {
            "delivered": result.delivered,
            "retrying": result.retrying,
            "failed": result.failed,
        }

    return run_async(_run())


@celery_app.task(name="nemesis.integrations.sweep", queue=QUEUE_IO, bind=True)
def sweep(self: Any) -> dict[str, int]:
    """Retention on the delivery log, plus the two operational gauges.

    Only ``delivered`` rows are swept. A ``failed`` row is the record of a
    promise this system did not keep and it stays until the tenant has seen it —
    see ``delivery.sweep_delivered``.
    """
    _ = self
    settings = get_settings()

    async def _run() -> dict[str, int]:
        async with session_scope() as session:
            removed = await delivery.sweep_delivered(
                session, older_than_days=settings.webhooks.retention_days
            )
            await delivery.refresh_gauges(session)
            lag = await delivery.fan_out_lag(session)
        if lag:
            log.info("webhook_fanout_lag", outbox_rows_behind=lag)
        return {"swept": removed, "fan_out_lag": lag}

    return run_async(_run())
