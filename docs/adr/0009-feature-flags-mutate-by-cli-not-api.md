# 0009 — Feature flags mutate through the CLI, not an HTTP API

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** SRE
- **Blueprint:** §11.2, §18, §25.1

## Context

Phase 1a ships feature flags with kill switches. A kill switch is only useful if
it can be pulled quickly, which argues for an admin HTTP endpoint — the obvious
design, and the one most flag systems have.

The obstacle is that **authentication and authorization do not exist until Phase
13.** There is no identity, no role model, no default-deny decision point.

An unauthenticated mutation endpoint would therefore let any request that
reaches the API disable a shipped capability. Two of the declared flags gate
paths with real consequences — `pipeline_agent_investigation` and
`realtime_websocket_hub` — and the same mechanism would eventually gate more.
"We will add auth later" is precisely how an unauthenticated write path becomes
permanent: it works, nothing visibly depends on fixing it, and it is invisible
in a diff six months later.

## Decision

**Mutation is CLI-only.** `python -m nemesis.flags` (reached as `nem flag`)
writes overrides to Redis. Using it requires shell access to the container,
which is a real authorization boundary that exists today rather than a promised
one.

**Reading is exposed over HTTP** at `GET /ops/flags`, unauthenticated. A flag's
name, owner, state, and description are not secrets, and being able to see which
kill switches are pulled without shelling into a container is worth having
during an incident — which is when the person looking is least likely to have a
terminal ready.

`--actor` and `--reason` are **required** for `kill` and optional elsewhere. A
kill switch entry with no attribution is a mystery during the post-mortem, and
the post-mortem is guaranteed.

## Alternatives considered

**Admin endpoint behind a shared secret header.** Rejected: a static shared
secret is a credential with no rotation story, no per-actor attribution, and no
revocation — it would create exactly the kind of thing `docs/SECRETS.md` exists
to prevent, in order to avoid waiting for the real mechanism.

**Admin endpoint bound to localhost only.** Rejected as a boundary that looks
stronger than it is: it depends on network topology remaining what it is today,
and Phase 1b changes network topology by definition.

**Wait for Phase 13 and ship no flags at all.** Rejected. The kill switches are
useful now, and the plan explicitly requires every risky path to ship behind a
flag with a documented kill switch. Deferring the whole capability to get a nicer
interface would trade a real control for an interface.

**Ship the endpoint disabled by a config flag.** Rejected as circular, and
because a disabled-by-default endpoint is one commit away from being enabled by
someone who does not know why it was disabled.

## Consequences

**Easier:** no unauthenticated write path exists to be found. Attribution is
mandatory where it matters most. The read path is available to anyone who needs
situational awareness.

**Harder:** pulling a kill switch needs a shell, which is slower than a button
and unavailable from a phone. This is a genuine cost during an incident and is
accepted deliberately — the runbook
(`docs/runbooks/feature-flag-kill-switch.md`) records the exact command so the
delay is seconds of typing rather than minutes of recall.

**Committed to:** revisiting this in Phase 13. When ABAC lands with a
default-deny decision point and audited actions, the admin endpoint becomes
straightforward and this ADR is superseded. The flag *service* is unchanged by
that — only the transport in front of it.

## Revisit when

Phase 13 ships identity and authorization. At that point an authenticated,
audited admin endpoint should replace the CLI as the primary path, with the CLI
retained as the break-glass mechanism for when the API itself is the thing that
is broken.
