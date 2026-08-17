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
    multiprocess_mode="livesum",
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

# The §24.2 signal. Labelled by `fallback` and not only by `stage`, because
# "dedup was skipped" and "the report is parked waiting for a human" are the
# same event to a counter and completely different to an operator.
pipeline_stage_degraded_total = Counter(
    "nemesis_pipeline_stage_degraded_total",
    "Pipeline stages that took their declared fallback path, by stage and fallback.",
    labelnames=("stage", "fallback"),
    registry=REGISTRY,
)

pipeline_stage_retries_total = Counter(
    "nemesis_pipeline_stage_retries_total",
    "Stage attempts beyond the first, by stage.",
    labelnames=("stage",),
    registry=REGISTRY,
)

# A gauge rather than a counter: the question an operator has is "how many
# complaints are parked right now", and that number must be able to come back
# down when the queue is worked, which a counter cannot express.
pipeline_dead_letters_open = Gauge(
    "nemesis_pipeline_dead_letters_open",
    "Unresolved pipeline dead letters, by stage.",
    labelnames=("stage",),
    multiprocess_mode="max",
    registry=REGISTRY,
)

# --- Ingestion (§26.1) -----------------------------------------------------
ingest_submissions_total = Counter(
    "nemesis_ingest_submissions_total",
    "Complaint submissions accepted or rejected, by outcome.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

ingest_upload_bytes = Histogram(
    "nemesis_ingest_upload_bytes",
    "Size of accepted submission media.",
    # Edges around the 15 MB cap: a phone photo lands near 2-5 MB and a voice
    # note near 100 KB, so the interesting resolution is at the small end.
    buckets=(64_000, 256_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000, 15_000_000),
    registry=REGISTRY,
)

# --- Rate limiting (§26.4) -------------------------------------------------
# `tier` is bounded by the declared plan map, never a tenant id.
rate_limit_decisions_total = Counter(
    "nemesis_rate_limit_decisions_total",
    "Rate limit decisions, by tier and outcome (allowed / limited / failed_open).",
    labelnames=("tier", "outcome"),
    registry=REGISTRY,
)

# --- Transactional outbox (Phase 3) ----------------------------------------
outbox_dispatched_total = Counter(
    "nemesis_outbox_dispatched_total",
    "Outbox rows published to the realtime transport.",
    registry=REGISTRY,
)

outbox_pending_messages = Gauge(
    "nemesis_outbox_pending_messages",
    "Undispatched outbox rows across all tenants.",
    multiprocess_mode="max",
    registry=REGISTRY,
)

# Measured from the event's `occurred_at` to the moment the relay published it.
# This is the number §26.3's "realtime" claim actually rests on, and the only
# one that would reveal a relay that is alive, healthy, and hours behind.
outbox_dispatch_lag_seconds = Histogram(
    "nemesis_outbox_dispatch_lag_seconds",
    "Delay between an event being recorded and its realtime publish.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 15.0, 60.0, 300.0),
    registry=REGISTRY,
)

# --- WebSocket hub (§26.3) -------------------------------------------------
websocket_connections = Gauge(
    "nemesis_websocket_connections",
    "Currently open pipeline-event WebSocket connections.",
    multiprocess_mode="livesum",
    registry=REGISTRY,
)

websocket_messages_sent_total = Counter(
    "nemesis_websocket_messages_sent_total",
    "Envelopes written to a client socket.",
    registry=REGISTRY,
)

# A client shed for lagging is not an error — it is the hub working as designed
# (§27.3's fallback is that the client reconnects and polls). It is counted
# separately from errors so a burst of shedding reads as "somebody's browser
# tab is throttled", not as an outage.
websocket_clients_shed_total = Counter(
    "nemesis_websocket_clients_shed_total",
    "Connections closed for failing to keep up with their own event stream.",
    labelnames=("reason",),
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
    multiprocess_mode="min",
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
    multiprocess_mode="max",
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
    multiprocess_mode="mostrecent",
    registry=REGISTRY,
)


def render() -> tuple[bytes, str]:
    """Serialise the registry for the /metrics endpoint."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
