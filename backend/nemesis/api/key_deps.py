"""Authenticating a request with an API key, and spending its quota.

**This is authentication, unlike everything else in the API today.** ``api.deps``
is careful to say that ``X-Tenant-ID`` names a tenant and proves nothing; the
control-plane token is a shared secret that identifies no one. A key is
different in kind: it is a per-consumer credential with a scope set and a quota,
and resolving it *determines* the tenant rather than trusting a claim about one.

It is still not Phase 13's identity system, and the distinction is worth being
precise about rather than overselling: a key authenticates a *consumer
organisation*, not a person. There is no user behind it, no session, and no
audit trail naming an individual. Phase 13 brings operator identity; this brings
machine identity, and those are genuinely different problems.

**Quota is spent before the handler runs, and usage is recorded after.** The
order matters: spending afterwards means a burst of expensive requests all pass
the check before any of them has been counted, which is precisely the shape of
the abuse the quota exists to bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from nemesis.api.errors import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_429_TOO_MANY_REQUESTS,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.api.ratelimit import get_limiter
from nemesis.config import RateLimitTier, Settings, get_settings
from nemesis.db.session import session_scope
from nemesis.integrations import keys
from nemesis.observability import metrics

API_KEY_HEADER = "X-API-Key"


@dataclass(frozen=True, slots=True)
class KeyedCaller:
    """A verified consumer, as a handler needs it."""

    key: keys.ResolvedKey

    def require(self, scope: str) -> None:
        if not self.key.permits(scope):
            raise ProblemDetailError(
                status_code=HTTP_403_FORBIDDEN,
                title="Insufficient scope",
                detail=(
                    f"This key does not carry the '{scope}' scope. Scopes are fixed at "
                    f"issue time; mint a new key rather than expecting this one to widen."
                ),
                problem_type=f"{PROBLEM_BASE}/insufficient-scope",
                extra={"required_scope": scope},
            )


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> KeyedCaller:
    """Resolve, verify, and throttle a keyed request."""
    settings = _settings_of(request)

    if not x_api_key:
        raise _unauthorised("Supply an API key in the X-API-Key header.")

    async with session_scope() as session:
        resolved = await keys.resolve(session, presented=x_api_key)

    if resolved is None:
        metrics.api_key_requests_total.labels(outcome="rejected").inc()
        # One message for unknown, revoked, and expired alike. Telling a caller
        # their key is *expired* rather than *unknown* confirms it was real,
        # which is exactly the fact worth having to somebody who found it in a
        # log file.
        raise _unauthorised("The supplied API key is not valid.")

    await _spend_quota(settings=settings, resolved=resolved)
    metrics.api_key_requests_total.labels(outcome="allowed").inc()
    return KeyedCaller(key=resolved)


async def _spend_quota(*, settings: Settings, resolved: keys.ResolvedKey) -> None:
    """One token against the key's own hourly budget.

    The tier is built from ``quota_per_hour`` rather than looked up in
    ``plan_tiers``: a key's allowance is a per-consumer commercial decision, and
    routing it through the plan map would mean inventing a synthetic plan name
    for every research partner.

    The burst is a twentieth of the hourly rate, floored at one. A consumer
    granted 3600/hour can spend 180 in a burst, which covers a paginated crawl
    and stops a single client from consuming an hour's budget in three seconds
    and then complaining the API is unavailable.
    """
    limiter = get_limiter(settings.redis_url, settings.rate_limit)
    tier = RateLimitTier(
        requests=resolved.quota_per_hour,
        window_seconds=3600,
        burst=max(1, resolved.quota_per_hour // 20),
    )
    decision = await limiter.check_tier(
        tier_name="api_key",
        tier=tier,
        namespace=str(resolved.tenant_id),
        identity=str(resolved.id),
    )
    if decision.allowed:
        return

    metrics.api_key_requests_total.labels(outcome="throttled").inc()
    raise ProblemDetailError(
        status_code=HTTP_429_TOO_MANY_REQUESTS,
        title="Quota exceeded",
        detail=(
            f"This key allows {resolved.quota_per_hour} requests per hour. "
            f"Retry in {decision.retry_after_seconds} seconds."
        ),
        problem_type=f"{PROBLEM_BASE}/rate-limited",
        extra={
            "retry_after_seconds": decision.retry_after_seconds,
            "quota_per_hour": resolved.quota_per_hour,
        },
    )


def _unauthorised(detail: str) -> ProblemDetailError:
    return ProblemDetailError(
        status_code=HTTP_401_UNAUTHORIZED,
        title="Not authenticated",
        detail=detail,
        problem_type=f"{PROBLEM_BASE}/unauthenticated",
    )


def _settings_of(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


KeyDep = Annotated[KeyedCaller, Depends(require_api_key)]
