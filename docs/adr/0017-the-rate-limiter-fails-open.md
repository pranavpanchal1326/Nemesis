# 0017 — The submission rate limiter fails open, and counts every time it does

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT · SEC
- **Blueprint:** §11.3, §26.4, §27.3

## Context

Submissions are rate limited per tenant plan. The limiter's state lives in Redis,
which means there is a state it must have an answer for: **Redis is unreachable
and the limiter cannot know whether this request is over budget.**

Failing closed is the reflex. It is also, here, the wrong answer.

## Decision

**Allow the request, and count it as `failed_open`.**

The thing being rate limited is a citizen reporting a hazard. §27.3's stated
posture for every dependency failure is that no submission is lost; a limiter
that converts a degraded cache into a total ingestion outage breaks that promise
via the component least able to justify it. Rate limiting exists to stop
automated flooding, and §11.3's device-velocity and geographic-clustering
detection is a second control on that which does not depend on Redis at all.

The counting is the other half and it is not optional. A fail-open that is
indistinguishable from a normal allow is a control nobody can prove is working:

- `RateLimitDecision.failed_open` is returned to the caller, so a test can assert
  on it and a handler can log it.
- `nemesis_rate_limit_decisions_total{outcome="failed_open"}` and
  `nemesis_system_degradation_total{dependency="redis"}` both increment, so the
  window is visible in Prometheus rather than inferred afterwards.

`fail_open` is configuration, not a constant. A deployment with a different risk
posture can set it to `false`, in which case the Redis error propagates and the
caller decides — which is the point of having the option rather than an
unreachable branch.

Two related decisions recorded here because they are part of the same mechanism:

**A token bucket, not a fixed window.** A fixed window of 20/hour lets a client
spend 20 at 10:59:59 and 20 more at 11:00:00 — forty in a second, at a moment
that is trivially predictable. A bucket states the sustained rate and the allowed
burst as two separate numbers instead of one number with an emergent second
behaviour.

**One Lua script.** Read-modify-write across three round trips is not atomic, and
two concurrent submissions can both read the same token count and both spend it.
Redis executes a script atomically. This is the same reasoning as `EventStore`'s
upsert-and-lock: the race appears only under the concurrency the mechanism exists
to survive, which is the worst possible time to discover it.

**The clock is supplied by the caller**, not read with `redis.call('TIME')`. It
keeps the script deterministic and replica-safe, and it lets a test prove refill
behaviour without sleeping through an hour-long window — or shortening the window
to make sleeping cheap, which would be testing a configuration nothing runs.

## Alternatives considered

**Fail closed.** Rejected above. The failure mode is "the city cannot report a
gas leak because a cache restarted".

**Fall back to an in-process limiter.** Rejected: with N API replicas the
effective limit becomes N times the configured one, which is neither the
configured behaviour nor a documented degradation — it is a third behaviour
nobody chose, appearing only during an incident.

**Queue requests until Redis returns.** Rejected: it converts a cache outage into
unbounded latency on the §27.1 two-second acknowledgment budget, and the queue is
memory in the process least able to spare it.

## Consequences

- A Redis outage is a window in which submission abuse is unthrottled. It is
  bounded by the outage, visible in two metrics, and covered by §11.3's
  independent detection.
- The plan-to-tier map is typed configuration today, so changing a customer's
  tier is a config change rather than a deploy — but not yet a control-plane
  change. Phase 6 makes it governed, effective-dated policy data, and this ADR is
  one of the inputs to that work.
- An unmapped plan resolves to `anonymous`, the most restrictive tier. The
  permissive default would be the friendlier bug and the wrong one: a plan
  somebody forgot to map would silently receive the partner allowance.

## Revisit when

- Phase 6 lands the policy engine and these limits become versioned,
  effective-dated tenant data.
- Phase 4 issues API keys, which need a per-key budget distinct from the
  per-device one this bucket keys on.
