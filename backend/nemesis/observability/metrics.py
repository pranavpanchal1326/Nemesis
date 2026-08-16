"""Prometheus metrics.

Deliberately hand-rolled rather than pulled from an auto-instrumentation
package. The §41 KPI set is the reason this module exists, and those metrics are
domain metrics — dedup precision, safety-bypass latency, agent invocation rate —
which no generic HTTP instrumentation can produce. Defining them here keeps one
registry and one naming convention for both HTTP and domain signals.

Naming follows Prometheus conventions: `nemesis_<subsystem>_<name>_<unit>`.
Cardinality is guarded — route templates never raw paths, and no tenant, user,
or complaint identifier ever becomes a label.
"""

from __future__ import annotations

from enum import StrEnum

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)


class PipelineStage(StrEnum):
    """The canonical `stage` label vocabulary.

    Declared here rather than passed as free strings because the label values
    are a *contract with the dashboards and alert rules*, not an implementation
    detail. A Grafana panel querying `stage="classify"` while the code emits
    `stage="classification"` renders an empty graph that looks exactly like a
    healthy system with no traffic — the worst possible failure mode for an
    operational signal.

    `scripts/check_observability.py` asserts that every stage selector in
    `infra/observability/` names a member of this enum, so that drift fails CI
    rather than silently producing blank panels.

    The set mirrors the §27.1 budget table. ``END_TO_END`` is not a stage the
    pipeline executes — it is the span from submission to work-order creation,
    recorded once at the end so the budget can be observed directly instead of
    reconstructed by summing quantiles, which is not a valid operation.
    """

    INGEST = "ingest"
    SAFETY_CHECK = "safety_check"
    CLASSIFICATION = "classification"
    DEDUP = "dedup"
    SEVERITY_SCORING = "severity_scoring"
    ROUTING = "routing"
    AGENT_INVESTIGATION = "agent_investigation"
    END_TO_END = "end_to_end"


class StageOutcome(StrEnum):
    """Outcome vocabulary for `pipeline_stage_duration_seconds`.

    ``DEGRADED`` is deliberately distinct from ``FAILED``: under §24.2 a stage
    that takes its documented fallback path has *succeeded at its contract* and
    must not inflate the failure ratio that pages a human. Collapsing the two
    would make correct degradation indistinguishable from breakage.
    """

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class Dependency(StrEnum):
    """External dependencies that can fail, and therefore can degrade.

    Every member must have a runbook page under `docs/runbooks/`, checked by
    `scripts/check_runbooks.py`. A dependency the system can degrade against but
    that nobody wrote recovery steps for is a 2am research project.

    Only dependencies that exist today are listed. OSM enrichment (Phase 12) and
    object storage are not members yet, for the same reason no flag is declared
    for an unbuilt feature: adding one now would demand a runbook for a failure
    that cannot occur, and a runbook nobody can rehearse is decoration.
    """

    DATABASE = "database"
    REDIS = "redis"
    OLLAMA = "ollama"
    WEBSOCKET_HUB = "websocket_hub"


# --- HTTP ------------------------------------------------------------------
# `endpoint` is the route *template* (/api/v1/complaints/{id}), never the
# resolved path, or every complaint id becomes its own time series.
http_requests_total = Counter(
    "nemesis_http_requests_total",
    "HTTP requests processed.",
    labelnames=("method", "endpoint", "status"),
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "nemesis_http_request_duration_seconds",
    "HTTP request latency.",
    labelnames=("method", "endpoint"),
    # Bucket edges chosen around the §27.1 budgets: 2s submission ack, 8s
    # classification, 30s end-to-end. Default buckets would put no boundary
    # anywhere near the numbers this system is actually judged on.
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 8.0, 15.0, 30.0, 60.0),
    registry=REGISTRY,
)

http_requests_in_flight = Gauge(
    "nemesis_http_requests_in_flight",
    "Requests currently being served.",
    registry=REGISTRY,
)

# --- Pipeline (§27.1) ------------------------------------------------------
pipeline_stage_duration_seconds = Histogram(
    "nemesis_pipeline_stage_duration_seconds",
    "Duration of a single pipeline stage.",
    labelnames=("stage", "outcome"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 8.0, 10.0, 30.0, 90.0),
    registry=REGISTRY,
)

pipeline_events_total = Counter(
    "nemesis_pipeline_events_total",
    "Domain events appended to the event log, by type.",
    labelnames=("event_type",),
    registry=REGISTRY,
)

# --- Degradation (§24.2, §27.3) --------------------------------------------
# The signal that a dependency failed and the system took its fallback path.
# A pipeline that degrades silently is indistinguishable from one that works.
system_degradation_total = Counter(
    "nemesis_system_degradation_total",
    "Degraded-mode fallbacks taken, by dependency and reason.",
    labelnames=("dependency", "reason"),
    registry=REGISTRY,
)

dependency_up = Gauge(
    "nemesis_dependency_up",
    "Readiness of a downstream dependency (1 = up, 0 = down).",
    labelnames=("dependency",),
    registry=REGISTRY,
)


# --- Event store integrity (Phase 2, §17.4) --------------------------------
# The blueprint lists background chain re-verification as ROADMAP. These are the
# signals that close it: without them the sweep runs and nobody can tell whether
# it found anything, which is the same as not running it.
#
# Unlabelled on purpose. A `tenant_id` label would be unbounded cardinality on
# the one metric that must never stop being scraped, and the *identity* of a
# broken chain belongs in the structured log where the exact offset is — not in
# a time series.
event_chains_verified_total = Counter(
    "nemesis_event_chains_verified_total",
    "Entity chains recomputed and checked by the integrity sweep.",
    registry=REGISTRY,
)

event_chain_breaks_total = Counter(
    "nemesis_event_chain_breaks_total",
    "Entity chains that failed to recompute. Any non-zero value is an incident.",
    registry=REGISTRY,
)

# A gauge, not a counter: the question is "how many rows are stranded right
# now", and the answer must be able to go back to zero once the remedy is
# applied. Non-zero means attaching the month's partition now needs a scan and
# an ACCESS EXCLUSIVE lock on a hot append-only table.
event_default_partition_rows = Gauge(
    "nemesis_event_default_partition_rows",
    "Rows sitting in the events DEFAULT partition, which should always be zero.",
    registry=REGISTRY,
)


# --- Feature flags (Phase 1a) ----------------------------------------------
# `flag` is bounded by the code-declared registry, so it cannot grow
# unboundedly the way a tenant or user label would. `outcome` distinguishes a
# flag that is off from one that was *killed*, because "the feature is disabled"
# and "somebody pulled the emergency handle" call for very different responses.
feature_flag_evaluations_total = Counter(
    "nemesis_feature_flag_evaluations_total",
    "Feature flag evaluations, by flag and resolved outcome.",
    labelnames=("flag", "outcome"),
    registry=REGISTRY,
)

feature_flag_state = Gauge(
    "nemesis_feature_flag_state",
    "Current resolved state of a declared flag (1 = on, 0 = off, -1 = killed).",
    labelnames=("flag",),
    registry=REGISTRY,
)


def render() -> tuple[bytes, str]:
    """Serialise the registry for the /metrics endpoint."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
