# 0006 — Domain configuration is tenant-scoped data, never code

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT
- **Blueprint:** §5, §11.2, §13.3, §13.5, §14.3, §27.2

## Context

The blueprint describes NEMESIS as multi-tenant across structurally different
buyers (§5): municipalities, campuses, industrial parks, gated communities. It
also describes several tunable behaviours as if they were fixed:

- five defect categories (§43.1),
- a severity rubric with four fixed weights (§13.5),
- a dedup threshold of 0.85 (§14.3),
- a hardcoded safety keyword list (§11.2),
- fixed SLA tiers (§27.2),
- a closed role enum (§18.1).

A campus has no potholes. It has elevator faults, HVAC failures, and lab spills.
Under a hardcoded taxonomy, every new customer is a code change, a deploy, and a
release cycle — which contradicts §5's claim of a 2–4 week campus sales cycle.

§13.3 additionally promises that "the rubric improves as resolution data
accumulates". With weights compiled into the application, that promise is
unimplementable by anyone except an engineer with deploy access.

## Decision

Anything a customer could plausibly want different is **data**: tenant-scoped,
versioned, effective-dated, and auditable. Specifically the taxonomy, severity
rubrics, dedup thresholds, safety rulesets, SLA matrices, routing rules, rate
cards, roles, and locales.

Two consequences are made explicit because they are easy to get wrong:

1. **Deterministic ≠ hardcoded.** §11.2's safety fail-safe must remain
   deterministic and non-probabilistic. That property is about *predictability* —
   same input, same outcome, no model score involved — not about physical
   location in a source file. It becomes a versioned, approved, hot-reloadable
   ruleset that still executes as a hard rule ahead of all scoring.

2. **No policy takes effect unreviewed.** Configuration that changes system
   behaviour is governed: draft → review → approve → activate, with every
   transition recorded in the same hash-chained log as domain events (§17.7).
   A tenant admin editing a severity weight is an auditable act, not a
   side-channel around the audit trail.

Every decision records the exact policy version that produced it, so a scored
complaint remains explainable against the rubric that actually scored it.

## Alternatives considered

**Per-tenant configuration files deployed with the application.** Rejected:
still a deploy per change, still engineer-gated, and no audit trail.

**Environment variables per tenant.** Rejected: does not scale past a handful of
tenants, cannot be versioned or effective-dated, and cannot express a taxonomy.

**Fully dynamic scripting for rules.** Rejected: an arbitrary code-execution
surface driven by customer input, and it would make the safety path
non-auditable. Routing rules use a sandboxed, side-effect-free evaluator over a
declared condition grammar instead.

## Consequences

- Onboarding a structurally new tenant requires **no code change and no deploy**
  — the Phase 5 exit gate tests exactly this.
- §13.3's improving-rubric promise becomes implementable, and Phase 7 lets a
  change be backtested against history before activation.
- CI enforces the rule: a category, role, ward, or language literal in a domain
  module fails the build.
- Higher upfront cost — a control plane is three phases of work that a hardcoded
  system would not need.
- Every decision path must resolve and cache policy, adding a lookup that must
  not become a per-request database round trip.

## Revisit when

Never for the taxonomy — that one is foundational. Individual policy types may
be reconsidered if a category of configuration proves genuinely universal across
every tenant profile, in which case a documented default is acceptable, but the
override path stays.
