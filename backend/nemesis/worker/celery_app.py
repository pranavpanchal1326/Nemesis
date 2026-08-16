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

from celery import Celery

from nemesis.config import get_settings

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
TASK_MODULES = ("nemesis.pipeline.integrity",)

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
}
