"""Structured logging with a correlation ID that survives process hops.

Blueprint §24.3 makes ``structlog`` to stdout the observability substrate (the
``events`` table is the audit record). For that to be usable, one citizen
submission must be traceable across the API request, the Celery tasks it spawns,
and any Investigation Agent tool calls — so the correlation ID lives in a
``ContextVar`` and is explicitly propagated into task payloads.
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def set_correlation_id(value: str | None) -> str:
    """Bind a correlation ID to the current context, generating one if absent."""
    resolved = value or new_correlation_id()
    _correlation_id.set(resolved)
    return resolved


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def _inject_correlation_id(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor: stamp every record with the active correlation ID."""
    cid = _correlation_id.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(level: str = "INFO", service_name: str = "nemesis") -> None:
    """Idempotent logging setup. Safe to call from API, worker, and beat."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level),
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_correlation_id,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        # Emit through the stdlib logger rather than `PrintLoggerFactory`.
        # PrintLogger binds `sys.stdout` at *construction* time, so once anything
        # replaces the stream — a process supervisor, uvicorn's reloader, or a
        # test capturing output — every subsequent log call raises
        # "I/O operation on closed file", and `cache_logger_on_first_use` makes
        # that permanent for the process. The stdlib logger resolves its handler
        # on each emit, so reconfiguration actually takes effect.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
