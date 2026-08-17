"""Prometheus export from Celery workers. The Phase 1a carry-forward.

Phase 1a deferred this and said why: ``nemesis/pipeline/`` was empty, so there
were exactly zero worker-side metrics to miss. Phase 3 ships the first worker
tasks, and the moment it does, every §41 pipeline KPI becomes unobservable
without this — the dashboards and alert rules already exist and would silently
show no data, which reads as "no traffic" rather than as "no export".

**Two problems, two mechanisms.**

*A Celery worker serves no HTTP port.* Prometheus scrapes; it does not receive.
So each worker container runs a tiny HTTP server on ``WORKER_METRICS_PORT``,
started once in the parent process, and ``prometheus.yml`` scrapes it like any
other target.

*A prefork worker is many processes.* Each child holds its own counters, and a
scrape served by the parent would report the parent's — which are zero, because
the parent runs no tasks. ``prometheus_client``'s multiprocess mode is the
answer: with ``PROMETHEUS_MULTIPROC_DIR`` set **before ``prometheus_client`` is
imported**, every metric value is backed by an mmap file in that directory, and
``MultiProcessCollector`` sums across all of them at scrape time.

That import-order requirement is why the variable is set in ``docker-compose.yml``
rather than by this module: by the time any Python here runs, the import has
already happened, and setting it in code would be a no-op that looks like
configuration.

**Gauges need a declared ``multiprocess_mode``.** The default, ``all``, exports
one series per process id — which turns a bounded gauge into an unbounded set of
time series that also never goes away when a child is recycled. Every gauge in
``metrics.py`` declares its mode explicitly for that reason.

**The API is deliberately not in multiprocess mode.** It runs a single uvicorn
process and serves ``/metrics`` from the in-process registry, which is simpler
and exact. Multiprocess mode trades exactness for aggregation, and there is
nothing to aggregate there.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from prometheus_client import CollectorRegistry

from nemesis.observability.logging import get_logger

log = get_logger(__name__)

#: Read by ``prometheus_client`` at import. Named here as documentation and for
#: the checks — nothing in this module sets it, because setting it after the
#: import would do nothing while looking like it did something.
MULTIPROC_ENV: Final = "PROMETHEUS_MULTIPROC_DIR"

#: Same port in every worker container. They have separate network namespaces,
#: so one number is less to remember and the Prometheus job list distinguishes
#: them by hostname.
WORKER_METRICS_PORT: Final = 9100


def multiprocess_dir() -> Path | None:
    value = os.environ.get(MULTIPROC_ENV)
    return Path(value) if value else None


def build_multiprocess_registry() -> CollectorRegistry:
    """A registry that aggregates every worker process's mmap files."""
    from prometheus_client import multiprocess

    registry = CollectorRegistry()
    # `prometheus_client` ships type information for its metric classes but
    # not for the multiprocess helpers, so these two calls are untyped in an
    # otherwise typed package. Ignored at the call site rather than by adding
    # the whole package to the mypy override list, which would silently
    # un-type every Counter and Gauge in `metrics.py` as well.
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    return registry


def reset_multiprocess_dir() -> int:
    """Clear stale mmap files left by the previous run of this container.

    Without this a restarted worker inherits the dead processes' counters and
    reports them forever: the files are keyed by process id, and nothing removes
    a file for a process that no longer exists. The symptom is a pipeline that
    appears to have processed thousands of complaints immediately after a
    deploy, which is worse than no data because it looks like data.

    Safe because it runs in the parent before any child forks, and because the
    directory holds nothing but this container's own export state.
    """
    directory = multiprocess_dir()
    if directory is None:
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    removed = 0
    for stale in directory.glob("*.db"):
        stale.unlink(missing_ok=True)
        removed += 1
    return removed


def mark_child_dead(pid: int) -> None:
    """Drop a recycled child's gauge files.

    ``worker_max_tasks_per_child`` means children are replaced routinely, not
    exceptionally. Without this call each retired child leaves a permanent
    contribution to every ``livesum`` gauge, so "requests in flight" climbs
    forever and never returns to zero.
    """
    if multiprocess_dir() is None:
        return
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(pid)  # type: ignore[no-untyped-call]


def start_worker_metrics_server(port: int = WORKER_METRICS_PORT) -> bool:
    """Serve the aggregated registry over HTTP. Returns whether it started.

    Returns ``False`` rather than raising when multiprocess mode is not
    configured. A worker that cannot export metrics must still process
    complaints: observability is not worth an outage, which is the same rule
    ``tracing._instrument_libraries`` follows.
    """
    directory = multiprocess_dir()
    if directory is None:
        log.warning(
            "worker_metrics_disabled",
            reason=f"{MULTIPROC_ENV} is not set",
            consequence="pipeline KPI panels will have no data from this worker",
        )
        return False

    from prometheus_client import start_http_server

    start_http_server(port, registry=build_multiprocess_registry())
    log.info("worker_metrics_started", port=port, multiproc_dir=str(directory))
    return True
