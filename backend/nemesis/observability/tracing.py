"""OpenTelemetry tracing.

Instrumented in Phase 0; the collector, Grafana, and alert routing arrive in
Phase 1. When no OTLP endpoint is configured the exporter is omitted entirely,
so local development and CI carry no cost and no dependency on a running
collector — but the spans, attributes, and context propagation are already
correct, so wiring the collector later is configuration rather than code.

Context propagation across the HTTP → Celery → agent boundary is the point.
A trace that stops at the API gateway cannot answer "why did this complaint take
90 seconds", which is the §27.1 question this exists to serve.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, TraceIdRatioBased

from nemesis.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_configured = False


def configure_tracing(settings: Settings, app: FastAPI | None = None) -> None:
    """Idempotent. Safe to call from the API, the workers, and beat."""
    global _configured
    if _configured or not settings.otel.enabled:
        return

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
            "deployment.environment": settings.app_env,
        }
    )

    # Sample everything outside production; production trades completeness for
    # cost at a configured ratio. ParentBased keeps a trace whole — sampling a
    # child independently of its parent produces broken, unreadable traces.
    sampler = (
        ParentBased(TraceIdRatioBased(settings.otel.sample_ratio))
        if settings.is_production_like
        else ALWAYS_ON
    )

    provider = TracerProvider(resource=resource, sampler=sampler)

    if settings.otel.endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel.endpoint))
        )

    trace.set_tracer_provider(provider)
    _instrument_libraries(app)
    _configured = True


def _instrument_libraries(app: FastAPI | None) -> None:
    """Best-effort auto-instrumentation.

    Each instrumentor is optional: a worker image has no FastAPI app, and the
    API has no Celery consumer. A missing or already-instrumented library must
    never prevent a service from starting — observability is not worth an
    outage.
    """
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")
        except Exception:
            pass

    for module_path, class_name in (
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.asyncpg", "AsyncPGInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.celery", "CeleryInstrumentor"),
    ):
        try:
            module = __import__(module_path, fromlist=[class_name])
            getattr(module, class_name)().instrument()
        except Exception:
            continue


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def current_trace_id() -> str | None:
    """Hex trace ID of the active span, for correlating a log line to a trace."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


def reset_for_testing() -> None:
    global _configured
    _configured = False
