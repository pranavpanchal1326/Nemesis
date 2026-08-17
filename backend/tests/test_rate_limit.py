"""Tiered rate limiting — the gate clause "verified per tenant plan".

Against real Redis, because the whole mechanism *is* the atomicity of the Lua
script. A fake would let the token bucket be tested as arithmetic, which is the
half that was never in doubt.

The clock is injected. A test that proved refill behaviour by sleeping through a
window would take an hour, and one that shortened the window to make sleeping
cheap would be testing a configuration nothing runs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from redis.exceptions import RedisError

from nemesis.api.ratelimit import RateLimiter, tier_for_plan
from nemesis.config import RateLimitSettings, RateLimitTier, get_settings


@pytest.fixture
def limiter_settings() -> RateLimitSettings:
    return RateLimitSettings(
        anonymous=RateLimitTier(requests=10, window_seconds=3600, burst=2),
        authenticated=RateLimitTier(requests=100, window_seconds=3600, burst=5),
        partner=RateLimitTier(requests=1000, window_seconds=3600, burst=20),
        plan_tiers={"trial": "anonymous", "pilot": "authenticated", "partner": "partner"},
    )


@pytest.fixture
async def limiter(limiter_settings: RateLimitSettings) -> AsyncIterator[RateLimiter]:
    instance = RateLimiter(get_settings().redis_url, limiter_settings)
    try:
        yield instance
    finally:
        await instance.close()


def test_an_unmapped_plan_resolves_to_the_most_restrictive_tier(
    limiter_settings: RateLimitSettings,
) -> None:
    """The permissive default would be the friendlier bug and the wrong one."""
    name, tier = tier_for_plan(limiter_settings, "a-plan-nobody-mapped")
    assert name == "anonymous"
    assert tier == limiter_settings.anonymous


@pytest.mark.parametrize(
    ("plan", "expected_tier", "expected_burst"),
    [("trial", "anonymous", 2), ("pilot", "authenticated", 5), ("partner", "partner", 20)],
)
async def test_each_plan_gets_its_declared_budget(
    limiter: RateLimiter, plan: str, expected_tier: str, expected_burst: int
) -> None:
    """The gate clause: rate limit tiers verified per tenant plan."""
    tenant_id, identity = uuid.uuid4(), uuid.uuid4().hex

    allowed = 0
    for _ in range(expected_burst + 3):
        decision = await limiter.check(tenant_id=tenant_id, plan=plan, identity=identity)
        assert decision.tier == expected_tier
        if decision.allowed:
            allowed += 1

    # Exactly the declared burst, then refusal. Refill during the loop is not a
    # factor: a 3600-second window refills far slower than the loop runs.
    assert allowed == expected_burst


async def test_a_refused_request_carries_a_usable_retry_after(limiter: RateLimiter) -> None:
    tenant_id, identity = uuid.uuid4(), uuid.uuid4().hex
    for _ in range(6):
        decision = await limiter.check(tenant_id=tenant_id, plan="pilot", identity=identity)

    assert decision.allowed is False
    assert decision.retry_after_seconds > 0


async def test_tokens_refill_over_the_window(limiter: RateLimiter) -> None:
    """Continuous refill, which is the reason for a bucket over a fixed window.

    A fixed window lets a client spend its whole allowance at 10:59:59 and the
    whole next one at 11:00:00 — twice the burst in a second, at a predictable
    moment.
    """
    tenant_id, identity = uuid.uuid4(), uuid.uuid4().hex
    now = 1_000_000.0

    for _ in range(5):
        await limiter.check(tenant_id=tenant_id, plan="pilot", identity=identity, now=now)
    exhausted = await limiter.check(tenant_id=tenant_id, plan="pilot", identity=identity, now=now)
    assert exhausted.allowed is False

    # 100 requests per 3600s is one token every 36 seconds.
    recovered = await limiter.check(
        tenant_id=tenant_id, plan="pilot", identity=identity, now=now + 40
    )
    assert recovered.allowed is True


async def test_buckets_do_not_leak_across_tenants_or_identities(limiter: RateLimiter) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    identity = uuid.uuid4().hex

    for _ in range(5):
        await limiter.check(tenant_id=first, plan="pilot", identity=identity)
    assert (await limiter.check(tenant_id=first, plan="pilot", identity=identity)).allowed is False

    # Same identity string, different tenant: an untouched bucket.
    assert (await limiter.check(tenant_id=second, plan="pilot", identity=identity)).allowed is True
    # Same tenant, different identity: likewise.
    assert (
        await limiter.check(tenant_id=first, plan="pilot", identity=uuid.uuid4().hex)
    ).allowed is True


async def test_an_unreachable_redis_fails_open_and_says_so(
    limiter_settings: RateLimitSettings,
) -> None:
    """The decision worth arguing about, asserted rather than assumed.

    A citizen reporting a hazard must not be refused because a cache is down.
    The choice is counted and flagged, so "we allowed it because we could not
    check" stays distinguishable from "we allowed it because it was within
    budget".
    """
    broken = RateLimiter("redis://127.0.0.1:1/0", limiter_settings)
    try:
        decision = await broken.check(tenant_id=uuid.uuid4(), plan="pilot", identity="anything")
    finally:
        await broken.close()

    assert decision.allowed is True
    assert decision.failed_open is True


async def test_fail_closed_propagates_when_configured(
    limiter_settings: RateLimitSettings,
) -> None:
    """The other side of the policy is available, and it is not the default."""
    strict = RateLimiter(
        "redis://127.0.0.1:1/0", limiter_settings.model_copy(update={"fail_open": False})
    )
    try:
        # A connection error, not a swallowed one: fail-closed means the
        # caller sees the failure and decides, which is the point of the option.
        with pytest.raises(RedisError):
            await strict.check(tenant_id=uuid.uuid4(), plan="pilot", identity="anything")
    finally:
        await strict.close()


async def test_disabling_the_limiter_allows_everything(
    limiter_settings: RateLimitSettings,
) -> None:
    disabled = RateLimiter(
        get_settings().redis_url, limiter_settings.model_copy(update={"enabled": False})
    )
    try:
        tenant_id, identity = uuid.uuid4(), uuid.uuid4().hex
        for _ in range(20):
            assert (
                await disabled.check(tenant_id=tenant_id, plan="trial", identity=identity)
            ).allowed is True
    finally:
        await disabled.close()


def test_the_process_limiter_is_rebuilt_when_its_configuration_changes(
    limiter_settings: RateLimitSettings,
) -> None:
    """A plain ``if _limiter is None`` silently keeps the first configuration.

    Invisible in production, where there is one configuration, and wrong in
    exactly the case that matters: a process that builds a second application
    with different limits gets the first one's budgets and asserts against a
    configuration nothing is running.
    """
    from nemesis.api.ratelimit import get_limiter

    url = get_settings().redis_url
    first = get_limiter(url, limiter_settings)
    assert get_limiter(url, limiter_settings) is first

    stricter = limiter_settings.model_copy(
        update={"anonymous": RateLimitTier(requests=1, window_seconds=60, burst=1)}
    )
    rebuilt = get_limiter(url, stricter)
    assert rebuilt is not first
    assert rebuilt.configuration == (url, stricter)

    # And a different broker is a different limiter too.
    assert get_limiter("redis://elsewhere:6379/0", stricter) is not rebuilt
