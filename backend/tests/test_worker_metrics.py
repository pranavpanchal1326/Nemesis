"""Worker metrics export — the Phase 1a carry-forward.

These assert the parts that are wrong in a way nobody notices: a gauge with no
declared multiprocess mode (one series per process id, forever), and stale mmap
files inherited across a restart (a pipeline that appears to have processed
thousands of complaints the instant it came up).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from prometheus_client import Gauge

from nemesis.observability import metrics
from nemesis.observability.worker_metrics import (
    MULTIPROC_ENV,
    WORKER_METRICS_PORT,
    multiprocess_dir,
    reset_multiprocess_dir,
    start_worker_metrics_server,
)


def test_every_gauge_declares_a_multiprocess_mode() -> None:
    """The default mode is ``all``, which is unbounded cardinality.

    A prefork worker recycles children routinely
    (``worker_max_tasks_per_child=200``), so ``all`` would emit a new series per
    child and never retire one — turning a bounded operational gauge into a
    growing set of time series on the metric an operator reads during an
    incident.
    """
    undeclared = [
        name
        for name, collector in vars(metrics).items()
        if isinstance(collector, Gauge) and not getattr(collector, "_multiprocess_mode", None)
    ]
    assert undeclared == []


def test_gauges_use_a_mode_prometheus_client_accepts() -> None:
    valid = {"all", "liveall", "min", "max", "livesum", "sum", "mostrecent", "livemostrecent"}
    for name, collector in vars(metrics).items():
        if isinstance(collector, Gauge):
            mode = getattr(collector, "_multiprocess_mode", None)
            assert mode in valid, f"{name} declares an unknown multiprocess_mode {mode!r}"


def test_the_export_is_disabled_rather_than_fatal_without_the_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observability is never worth an outage.

    A worker that cannot export metrics must still process complaints, which is
    the same rule ``tracing._instrument_libraries`` follows.
    """
    monkeypatch.delenv(MULTIPROC_ENV, raising=False)
    assert multiprocess_dir() is None
    assert start_worker_metrics_server(WORKER_METRICS_PORT) is False
    assert reset_multiprocess_dir() == 0


def test_stale_mmap_files_are_cleared_before_the_children_fork(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(MULTIPROC_ENV, str(tmp_path))
    (tmp_path / "counter_101.db").write_bytes(b"stale")
    (tmp_path / "gauge_livesum_102.db").write_bytes(b"stale")
    # Not an export file; must survive, because deleting arbitrary content from
    # a configured directory is a broader promise than this function makes.
    (tmp_path / "notes.txt").write_text("keep me")

    assert reset_multiprocess_dir() == 2
    assert list(tmp_path.glob("*.db")) == []
    assert (tmp_path / "notes.txt").exists()


def test_the_directory_is_created_if_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "not-yet"
    monkeypatch.setenv(MULTIPROC_ENV, str(target))
    assert reset_multiprocess_dir() == 0
    assert target.is_dir()
