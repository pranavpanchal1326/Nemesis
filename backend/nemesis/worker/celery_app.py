"""Celery application, split across two queues by memory profile.

Rationale (hardware-driven, not stylistic): a Celery prefork pool imports the
task module *per worker process*. With torch in that module, four workers cost
roughly 6GB of RAM — untenable on a 16GB machine also running Ollama, WSL2, and
a browser. So inference tasks are routed to a dedicated ``ml`` queue served by a
single-concurrency worker that is the only process in the system holding model
weights, while I/O-bound work (notifications, projections, SLA sweeps) runs
concurrently on the ``io`` queue in an image that has never seen torch.

Task-level policy here is deliberately strict, because Blueprint §24.2 requires
that a failing stage degrade the complaint's status rather than lose it.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import worker_process_shutdown, worker_ready

from nemesis.config import get_settings
from nemesis.observability.worker_metrics import (
    mark_child_dead,
    reset_multiprocess_dir,
    start_worker_metrics_server,
)

settings = get_settings()

QUEUE_IO = "io"
QUEUE_ML = "ml"
# Safety-flagged complaints bypass the scoring pipeline entirely (§11.2); they
# get their own queue so a backlog of routine classification work can never
# delay a danger signal.
QUEUE_SAFETY = "safety"

#: Task modules, listed explicitly rather than autodiscovered.
#:
#: `autodiscover_tasks(["nemesis.pipeline"])` looks for a module named
#: `tasks.py` inside each package and nothing else. `nemesis/pipeline/
#: integrity.py` therefore registered **zero** tasks and loaded **zero** beat
#: schedules, silently — `celery inspect registered` reported "empty" and no
#: error appeared anywhere. The integrity sweep would never have run, and
#: `NemesisEventIntegritySweepStalled` is the only thing that would eventually
#: have said so.
#:
#: An explicit list fails loudly at worker startup if a module is missing or
#: raises, which is the correct failure mode for the code that detects tampering.
TASK_MODULES = ("nemesis.pipeline.integrity", "nemesis.pipeline.tasks")

celery_app = Celery(
    "nemesis",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=TASK_MODULES,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Redelivery on worker loss. Every task must therefore be idempotent —
    # enforced by the idempotency key carried in each task payload.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Prefetching starves the safety queue and inflates per-worker memory.
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=270,
    broker_connection_retry_on_startup=True,
    # **Redis has no acknowledgement.** `task_acks_late` is implemented on this
    # transport by moving the message to an `unacked` structure and restoring it
    # once `visibility_timeout` elapses — there is no connection-drop signal that
    # returns it sooner. So a worker killed mid-task does not have its work
    # redelivered "on restart"; it has it redelivered after this many seconds.
    #
    # The default is **3600**, and it was never set. Phase 3's SIGKILL drill is
    # what surfaced that: a queued task survives a kill and runs the moment the
    # replacement worker connects, but an in-flight one sat for an hour with no
    # error anywhere, which is indistinguishable from lost until it suddenly
    # is not.
    #
    # 360 rather than something smaller: it must exceed `task_time_limit` (300),
    # or a task that is still legitimately running is redelivered and executed
    # concurrently with itself. That is survivable here — every stage is
    # idempotent by construction — but it would double the work and make the
    # logs unreadable at exactly the wrong moment.
    broker_transport_options={"visibility_timeout": 360},
    result_expires=3600,
    task_default_queue=QUEUE_IO,
    task_queues={
        QUEUE_IO: {"exchange": QUEUE_IO, "routing_key": QUEUE_IO},
        QUEUE_ML: {"exchange": QUEUE_ML, "routing_key": QUEUE_ML},
        QUEUE_SAFETY: {"exchange": QUEUE_SAFETY, "routing_key": QUEUE_SAFETY},
    },
)

# Beat schedules live here, keyed by task *name* rather than by import, so
# `beat` gets a complete schedule from this module alone. Defining them as an
# import side effect of the task module made the schedule depend on whether
# something had imported it first — which is how the schedule silently emptied.
celery_app.conf.beat_schedule = {
    "sweep-chain-integrity": {
        "task": "nemesis.integrity.sweep_chains",
        # Hourly: frequent enough that a tamper is found the same day,
        # infrequent enough that the read load is invisible next to the writes.
        "schedule": 3600.0,
        "options": {"queue": QUEUE_IO},
    },
    "maintain-event-partitions": {
        "task": "nemesis.integrity.maintain_partitions",
        # Daily, against a three-month window. Twelve weeks of missed runs would
        # have to accumulate before the DEFAULT partition ever takes a row.
        "schedule": 86400.0,
        "options": {"queue": QUEUE_IO},
    },
    "purge-dispatched-outbox": {
        "task": "nemesis.integrity.purge_outbox",
        # Hourly. Dispatched outbox rows exist only to catch a reconnecting
        # client up across a disconnect; past that window they are dead weight
        # on the index the relay reads. Deleting one destroys no history — the
        # event it pointed at is untouched — which is why this one is allowed to
        # run unattended while §22.4 retention on `events` is not.
        "schedule": 3600.0,
        "options": {"queue": QUEUE_IO},
    },
}


# --------------------------------------------------------------------------
# Metrics export (Phase 3, carried forward from Phase 1a).
#
# `worker_ready` rather than `worker_init`: init fires before the pool exists,
# and on the prefork pool the parent has not yet decided whether it is even
# going to fork. Binding the port at ready means exactly one process per
# container binds it, and it binds after the stale mmap files are cleared.
# --------------------------------------------------------------------------


@worker_ready.connect
def _start_metrics_export(**_: Any) -> None:
    from nemesis.observability.logging import get_logger

    removed = reset_multiprocess_dir()
    if removed:
        # Logged, because the alternative failure is quiet: files inherited from
        # a previous run report a pipeline that processed thousands of
        # complaints the instant the worker came up.
        get_logger(__name__).info("worker_metrics_reset", stale_files_removed=removed)
    start_worker_metrics_server()


@worker_process_shutdown.connect
def _close_child_loop(**_: Any) -> None:
    """Dispose the engine and close this child's loop before it exits.

    Without it the pool's sockets are finalised by the garbage collector at
    interpreter shutdown, which under `filterwarnings = ["error"]` is the
    intermittent `ResourceWarning` failure Phase 1a spent a day attributing to
    the wrong test.
    """
    from nemesis.worker.loop import close_loop

    close_loop()


@worker_process_shutdown.connect
def _release_child_metrics(pid: int | None = None, **_: Any) -> None:
    """Drop a recycled child's gauge files.

    `worker_max_tasks_per_child=200` makes child replacement routine, so
    without this every `livesum` gauge accumulates the contribution of every
    child that ever existed and never comes back down.
    """
    if pid is not None:
        mark_child_dead(pid)
