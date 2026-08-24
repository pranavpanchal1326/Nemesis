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
    #: Phase 8. EXIF cross-check, perceptual hashing, coordinated-abuse
    #: detection, and §22.1 face blur. Between safety and classification because
    #: the classifier reads the *redacted* copy — putting it later would mean a
    #: model consuming an image §22.1 says must not exist outside quarantine.
    TRUST_VERIFICATION = "trust_verification"
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

# --- Trust & safety (§11) --------------------------------------------------
# `reason` and `decision` are bounded by the `ReviewReason` and
# `ReviewDecisionKind` enums — never a complaint id, never a tenant id. The same
# cardinality rule the route-template labels follow.
review_queue_items_total = Counter(
    "nemesis_review_queue_items_total",
    "Items raised into the §11.4 human review queue, by reason.",
    labelnames=("reason",),
    registry=REGISTRY,
)

review_decisions_total = Counter(
    "nemesis_review_decisions_total",
    "Human review decisions, by reason and decision. Every one is a Phase 11 label.",
    labelnames=("reason", "decision"),
    registry=REGISTRY,
)

review_queue_open = Gauge(
    "nemesis_review_queue_open",
    "Open items in the §11.4 review queue, by reason. A queue nobody works is a dead end.",
    labelnames=("reason",),
    multiprocess_mode="max",
    registry=REGISTRY,
)

dedup_decisions_total = Counter(
    "nemesis_dedup_decisions_total",
    "§14 dedup outcomes, by band. An `investigate` rate of zero means the ambiguous "
    "band has collapsed and dedup has silently become a binary merge/no-merge decision.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

dedup_candidates = Histogram(
    "nemesis_dedup_stage1_candidates",
    "Clusters surviving Stage 1 per report. The denominator of the §14.1 elimination "
    "ratio: if this tracks the tenant's open-incident count, Stage 1 has stopped filtering.",
    buckets=(0, 1, 2, 5, 10, 25, 50, 100),
    registry=REGISTRY,
)

dedup_confidence = Histogram(
    "nemesis_dedup_confidence",
    "Combined similarity of the best candidate, whether or not it merged. Watching the "
    "distribution rather than the merges is how a threshold drifting away from the data "
    "becomes visible before it becomes a complaint.",
    buckets=(0.0, 0.25, 0.5, 0.65, 0.75, 0.85, 0.9, 0.95, 1.0),
    registry=REGISTRY,
)

dedup_truncations_total = Counter(
    "nemesis_dedup_truncations_total",
    "Reports whose Stage 1 candidate cap bound. A non-zero rate means some `no match` "
    "decisions were made over a subset of the neighbourhood rather than all of it.",
    registry=REGISTRY,
)

dedup_merge_reversions_total = Counter(
    "nemesis_dedup_merge_reversions_total",
    "§14.3 compensating reversals. The measured false-positive merge rate in production, "
    "as opposed to the one the fixture set predicts.",
    registry=REGISTRY,
)

safety_triggers_total = Counter(
    "nemesis_safety_triggers_total",
    "§11.2 deterministic safety triggers that fired, by rule and detection source.",
    labelnames=("rule_id", "detection_source"),
    registry=REGISTRY,
)

media_redactions_total = Counter(
    "nemesis_media_redactions_total",
    "§22.1 face-blur outcomes, by result (redacted / failed / unavailable).",
    labelnames=("outcome",),
    registry=REGISTRY,
)

media_faces_blurred = Histogram(
    "nemesis_media_faces_blurred",
    "Faces blurred per redacted image.",
    # Zero is the common case and is its own bucket: a deployment whose
    # distribution suddenly collapses to zero has either stopped receiving
    # photographs of people or stopped detecting them, and only this
    # distinction distinguishes the two.
    buckets=(0, 1, 2, 3, 5, 10, 25),
    registry=REGISTRY,
)

abuse_patterns_total = Counter(
    "nemesis_abuse_patterns_total",
    "§11.3 coordinated-abuse detections that fired, by pattern. Flags, never blocks.",
    labelnames=("pattern",),
    registry=REGISTRY,
)

perceptual_duplicates_total = Counter(
    "nemesis_perceptual_duplicates_total",
    "§11.1 near-duplicate images found against submission history.",
    registry=REGISTRY,
)

# --- Perception (Phase 9, §8.4 / §43.1) ------------------------------------
#
# Labels name *model families and outcomes*, never a tenant, a category, or a
# complaint. A per-category label here would be unbounded by construction —
# categories are tenant data (Phase 5) — and the per-category numbers that
# matter are the F1 report's, which is an artefact rather than a time series.

perception_model_loads_total = Counter(
    "nemesis_perception_model_loads_total",
    "Model registry loads, by artefact kind and outcome. A cold start, not a use.",
    labelnames=("kind", "outcome"),
    registry=REGISTRY,
)

perception_model_load_seconds = Histogram(
    "nemesis_perception_model_load_seconds",
    "Wall time of one model registry load.",
    labelnames=("kind",),
    # Reaching to 120 s because a cold Whisper load from an unwarmed page cache
    # takes tens of seconds, and a histogram whose top bucket is 10 s reports
    # every cold start as "+Inf" — which is the exact case worth measuring.
    buckets=(0.05, 0.25, 1.0, 5.0, 15.0, 30.0, 60.0, 120.0),
    registry=REGISTRY,
)

perception_model_cache_total = Counter(
    "nemesis_perception_model_cache_total",
    "Registry lookups by result: hit, miss, or coalesced onto another thread's load.",
    labelnames=("kind", "result"),
    registry=REGISTRY,
)

perception_model_evictions_total = Counter(
    "nemesis_perception_model_evictions_total",
    "Entries evicted to stay under the resident ceiling. Sustained non-zero means "
    "the ceiling is below the working set and every complaint is paying a reload.",
    registry=REGISTRY,
)

perception_models_resident = Gauge(
    "nemesis_perception_models_resident",
    "Artefacts currently held by the model registry in this process.",
    registry=REGISTRY,
)

perception_resident_bytes = Gauge(
    "nemesis_perception_resident_bytes",
    "Declared footprint of everything the model registry holds resident.",
    registry=REGISTRY,
)

perception_inference_seconds = Histogram(
    "nemesis_perception_inference_seconds",
    "One inference pass, by operation (encode_image / encode_text / transcribe).",
    labelnames=("operation",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

perception_classifications_total = Counter(
    "nemesis_perception_classifications_total",
    "Classification outcomes: classified, abstained, or degraded. Abstentions are "
    "a correct outcome (§24.2), not a failure — they are counted apart so a rising "
    "abstention rate is visible without inflating the error ratio that pages a human.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

perception_confidence = Histogram(
    "nemesis_perception_confidence",
    "Calibrated confidence of the winning category, over every scored submission.",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95),
    registry=REGISTRY,
)

perception_transcriptions_total = Counter(
    "nemesis_perception_transcriptions_total",
    "Voice-complaint transcriptions, by detected language and outcome. The language "
    "label is a BCP-47 tag from a fixed model vocabulary, not tenant data.",
    labelnames=("language", "outcome"),
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

# --- Public API & integrations (Phase 4, §16.3 / §26.4) --------------------
# `endpoint` is the route template and `outcome` is a bounded vocabulary. No
# tenant slug label, deliberately: the public surface is per tenant by
# construction, so a slug label would grow with the customer list on the one
# metric family that is scraped on every unauthenticated request.
public_api_requests_total = Counter(
    "nemesis_public_api_requests_total",
    "Requests to the §26.4 public transparency API, by endpoint and outcome.",
    labelnames=("endpoint", "outcome"),
    registry=REGISTRY,
)

# The k-anonymity floor doing its job. A sustained rise means either a quiet
# deployment or a threshold set too high to publish anything — both are
# questions worth being able to ask, and neither is answerable from a request
# count alone.
public_api_suppressed_buckets_total = Counter(
    "nemesis_public_api_suppressed_buckets_total",
    "Aggregate buckets withheld for falling below the suppression floor.",
    registry=REGISTRY,
)

# `outcome` is allowed / throttled / rejected. No key id label — a key
# identifies a named commercial consumer, and per-key numbers belong in the
# `api_key_usage` rollup the tenant can query, not in a metric with unbounded
# cardinality that anybody scraping /metrics can read.
api_key_requests_total = Counter(
    "nemesis_api_key_requests_total",
    "Authenticated API requests, by outcome.",
    labelnames=("outcome",),
    registry=REGISTRY,
)

webhook_deliveries_total = Counter(
    "nemesis_webhook_deliveries_total",
    "Webhook delivery attempts, by outcome (delivered / retrying / failed).",
    labelnames=("outcome",),
    registry=REGISTRY,
)

# Measured from the event's `occurred_at` to a successful delivery. The number
# a tenant's integration actually experiences, and the only one that would show
# a dispatcher that is alive, healthy, and hours behind — the same failure the
# outbox lag histogram exists to catch, one hop further out.
webhook_delivery_lag_seconds = Histogram(
    "nemesis_webhook_delivery_lag_seconds",
    "Delay between an event being recorded and its successful webhook delivery.",
    buckets=(0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 1800.0, 3600.0, 21600.0),
    registry=REGISTRY,
)

webhook_deliveries_pending = Gauge(
    "nemesis_webhook_deliveries_pending",
    "Undelivered webhook rows across all tenants.",
    multiprocess_mode="max",
    registry=REGISTRY,
)

# A gauge because an endpoint disabled for repeated failure can be re-enabled,
# and "how many subscriptions are we no longer delivering to" must be able to
# come back down.
webhook_endpoints_disabled = Gauge(
    "nemesis_webhook_endpoints_disabled",
    "Webhook endpoints disabled after exhausting their consecutive-failure budget.",
    multiprocess_mode="max",
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
