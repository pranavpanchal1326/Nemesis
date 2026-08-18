"""Tiered rate limiting on a Redis token bucket.

**Why a token bucket and not a fixed window.** A fixed window of 20 per hour
lets a client spend 20 at 10:59:59 and 20 more at 11:00:00 — 40 requests in a
second, which is exactly the burst the limit exists to prevent, and it happens
at a predictable moment so it is trivially exploitable. A token bucket refills
continuously, so the sustained rate and the allowed burst are two separate,
stated numbers rather than one number with an emergent second behaviour.

**Why one Lua script.** Read-modify-write across three round trips is not atomic,
and two concurrent submissions can both read the same token count and both
spend it. Redis executes a script atomically, so the check and the spend are one
operation. This is the same reasoning as ``EventStore``'s upsert-and-lock: the
race only appears under the concurrency the mechanism exists to survive, which
is the worst possible time to discover it.

**Fail-open, counted.** If Redis is unreachable the limiter allows the request
and increments ``failed_open``. The thing being limited is a citizen reporting a
hazard; losing real reports during a Redis outage is worse than briefly
permitting abuse, and §11.3's device-velocity clustering is a second control
that does not depend on this one. The alternative — fail closed — turns a
degraded cache into a total ingestion outage, which is §27.3's "no data loss"
promise broken by the component least able to justify it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Final

import redis.asyncio as redis

from nemesis.config import RateLimitSettings, RateLimitTier
from nemesis.observability import metrics
from nemesis.observability.logging import get_logger

log = get_logger(__name__)

_KEY_PREFIX: Final = "nemesis:ratelimit"

#: Atomic take-one-token.
#:
#: KEYS[1] bucket key
#: ARGV[1] capacity (burst)  ARGV[2] refill tokens per second
#: ARGV[3] now (float seconds, supplied by the caller)
#: ARGV[4] key TTL in seconds
#:
#: The clock comes from the caller rather than from `redis.call('TIME')` so the
#: script stays deterministic and replica-safe, and so tests can advance time
#: without sleeping through an hour-long window.
_TAKE_TOKEN = """
local capacity   = tonumber(ARGV[1])
local refill     = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local ttl        = tonumber(ARGV[4])

local state   = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens  = tonumber(state[1])
local updated = tonumber(state[2])

if tokens == nil then
  tokens = capacity
  updated = now
end

local elapsed = now - updated
if elapsed > 0 then
  tokens = math.min(capacity, tokens + (elapsed * refill))
end

local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', KEYS[1], ttl)

-- Seconds until one whole token is available again. Zero when allowed, so the
-- caller never has to reason about a Retry-After it should not send.
local retry_after = 0
if allowed == 0 then
  retry_after = math.ceil((1 - tokens) / refill)
end

return {allowed, retry_after, math.floor(tokens)}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    tier: str
    retry_after_seconds: int
    remaining: int
    #: True when the limiter could not reach Redis and allowed the request
    #: rather than rejecting it. Surfaced so a caller can log it and a test can
    #: assert on it — a fail-open that is indistinguishable from a normal allow
    #: is a control nobody can prove is working.
    failed_open: bool = False


def tier_for_plan(settings: RateLimitSettings, plan: str) -> tuple[str, RateLimitTier]:
    """Resolve a tenant plan to its tier.

    An unmapped plan falls to ``anonymous``, the most restrictive tier. The
    permissive default would be the friendlier bug and the wrong one: a plan
    somebody forgot to map would silently receive the partner allowance.
    """
    name = settings.plan_tiers.get(plan, "anonymous")
    tier: RateLimitTier | None = getattr(settings, name, None)
    if tier is None:
        log.warning(
            "rate_limit_tier_unknown",
            plan=plan,
            tier=name,
            consequence="falling back to the anonymous tier",
        )
        return "anonymous", settings.anonymous
    return name, tier


class RateLimiter:
    """Token-bucket limiter over Redis."""

    def __init__(self, redis_url: str, settings: RateLimitSettings) -> None:
        self._url = redis_url
        self._settings = settings
        self._client: redis.Redis[str] | None = None
        self._script: Any | None = None

    @property
    def configuration(self) -> tuple[str, RateLimitSettings]:
        """What this limiter was built with, so a caller can tell it is stale."""
        return self._url, self._settings

    def _connect(self) -> redis.Redis[str]:
        if self._client is None:
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def check(
        self,
        *,
        tenant_id: uuid.UUID,
        plan: str,
        identity: str,
        now: float | None = None,
    ) -> RateLimitDecision:
        """Spend one token for ``identity`` under the tenant's plan tier.

        ``identity`` is the thing being limited — a device fingerprint, a client
        address, an API key. It is hashed into the key rather than stored, and
        it never becomes a metric label: §22 forbids a device fingerprint
        leaving the system, and a Prometheus label is a place it would leave to.
        """
        tier_name, tier = tier_for_plan(self._settings, plan)
        return await self.check_tier(
            tier_name=tier_name, tier=tier, namespace=str(tenant_id), identity=identity, now=now
        )

    async def check_tier(
        self,
        *,
        tier_name: str,
        tier: RateLimitTier,
        namespace: str,
        identity: str,
        now: float | None = None,
    ) -> RateLimitDecision:
        """Spend one token against an explicitly supplied tier.

        Split out from ``check`` in Phase 4 because two of this system's limits
        are not per-tenant-plan at all: §26.4's public budget is per client
        address across the whole surface, and an API key carries its own
        ``quota_per_hour`` precisely so a research partner's allowance differs
        from their tenant's. Resolving those through ``plan_tiers`` would have
        meant inventing a fake plan name per key, which is a mapping that exists
        only to satisfy a signature.

        ``namespace`` is what the bucket is partitioned by — a tenant id, an API
        key id, or the literal ``public``. It is part of the key rather than a
        second argument to the script so one identity cannot spend another
        namespace's tokens.
        """
        if not self._settings.enabled:
            return RateLimitDecision(True, tier_name, 0, tier.burst)

        key = f"{_KEY_PREFIX}:{tier_name}:{namespace}:{identity}"
        refill = tier.requests / tier.window_seconds
        timestamp = time.time() if now is None else now

        try:
            if self._script is None:
                self._script = self._connect().register_script(_TAKE_TOKEN)
            allowed, retry_after, remaining = await self._script(
                keys=[key],
                args=[tier.burst, refill, timestamp, tier.window_seconds * 2],
            )
        except Exception as exc:
            metrics.rate_limit_decisions_total.labels(tier=tier_name, outcome="failed_open").inc()
            metrics.system_degradation_total.labels(
                dependency=metrics.Dependency.REDIS.value, reason="rate_limiter_unavailable"
            ).inc()
            if not self._settings.fail_open:
                raise
            log.warning(
                "rate_limiter_unavailable",
                error_type=type(exc).__name__,
                consequence="allowing the request; see docs/runbooks/redis-unavailable.md",
            )
            return RateLimitDecision(True, tier_name, 0, 0, failed_open=True)

        permitted = bool(int(allowed))
        metrics.rate_limit_decisions_total.labels(
            tier=tier_name, outcome="allowed" if permitted else "limited"
        ).inc()
        return RateLimitDecision(
            allowed=permitted,
            tier=tier_name,
            retry_after_seconds=int(retry_after),
            remaining=int(remaining),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()  # type: ignore[attr-defined]
            self._client = None
        self._script = None


_limiter: RateLimiter | None = None


def get_limiter(redis_url: str, settings: RateLimitSettings) -> RateLimiter:
    """The process's limiter, rebuilt if the configuration it was built with changed.

    The arguments are checked rather than ignored after the first call. A plain
    ``if _limiter is None`` silently returns a limiter configured from whichever
    settings happened to arrive first — which is invisible in production, where
    there is one configuration, and wrong in exactly the case that matters:
    a process that builds a second application with different limits, such as a
    test suite, gets the first one's budgets and asserts against a configuration
    nothing is running.
    """
    global _limiter
    if _limiter is None or _limiter.configuration != (redis_url, settings):
        _limiter = RateLimiter(redis_url, settings)
    return _limiter


async def close_limiter() -> None:
    global _limiter
    if _limiter is not None:
        await _limiter.close()
    _limiter = None
