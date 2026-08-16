"""Feature flags with kill switches (Phase 1a).

    from nemesis.flags import get_flags

    if await get_flags().is_enabled("realtime_websocket_hub", tenant_id=tenant):
        ...

See ``registry.py`` for why flags are declared in code and overridden in data,
and ``service.py`` for the resolution order.
"""

from __future__ import annotations

from nemesis.config import Settings, get_settings
from nemesis.flags.registry import (
    REGISTRY,
    FlagSpec,
    UnknownFlagError,
    expired_flags,
    get_spec,
    kill_switches,
)
from nemesis.flags.service import FeatureFlags, FlagDecision
from nemesis.flags.store import FlagOverride, FlagStore, MemoryFlagStore, RedisFlagStore

__all__ = [
    "REGISTRY",
    "FeatureFlags",
    "FlagDecision",
    "FlagOverride",
    "FlagSpec",
    "FlagStore",
    "MemoryFlagStore",
    "RedisFlagStore",
    "UnknownFlagError",
    "build_flags",
    "close_flags",
    "expired_flags",
    "get_flags",
    "get_spec",
    "kill_switches",
    "reset_flags",
]

_flags: FeatureFlags | None = None


def build_flags(settings: Settings) -> FeatureFlags:
    """Construct an evaluator from settings.

    With flags disabled the store is in-memory and empty, so every flag resolves
    to its declared default. That is the correct degenerate behaviour: the code
    paths stay identical and only the ability to *deviate* is removed, which
    keeps a test or an air-gapped run from silently exercising a different
    branch than production does.
    """
    store: FlagStore
    if settings.flags.enabled:
        store = RedisFlagStore(settings.redis_url, settings.flags.redis_key)
    else:
        store = MemoryFlagStore()
    return FeatureFlags(store, reload_interval_seconds=settings.flags.reload_interval_seconds)


def get_flags() -> FeatureFlags:
    """Process-wide evaluator.

    A singleton because the TTL cache is only worth having if it is shared —
    a per-request evaluator would reload on every call and reintroduce exactly
    the Redis round trip the snapshot exists to avoid.
    """
    global _flags
    if _flags is None:
        _flags = build_flags(get_settings())
    return _flags


async def close_flags() -> None:
    """Release the store's connection and drop the singleton.

    Called from the application lifespan alongside ``dispose_engine``. Without
    it the Redis client is finalised by the garbage collector after the event
    loop has closed, which surfaces as a ``ResourceWarning`` from ``__del__``
    during shutdown — noise that trains people to ignore shutdown warnings, and
    a socket held open for as long as the process takes to exit.
    """
    global _flags
    if _flags is not None:
        store = _flags._store
        closer = getattr(store, "close", None)
        if closer is not None:
            await closer()
        _flags = None


def reset_flags() -> None:
    """Drop the singleton without closing it. Tests only."""
    global _flags
    _flags = None
