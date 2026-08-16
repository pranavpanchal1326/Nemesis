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

celery_app = Celery(
    "nemesis",
    broker=settings.redis_url,
    backend=settings.redis_url,
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

celery_app.autodiscover_tasks(["nemesis.pipeline"])
