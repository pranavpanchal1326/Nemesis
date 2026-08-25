# 0046 — Publishing is an act somebody takes, not a flag a template sets

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** PLT · LEGAL
- **Blueprint:** §16.2, §16.3, §22.2, §26.4 · §E18, §E25 M6

## Context

`tenants.public_api_enabled` decides whether §26.4's unauthenticated surface
answers for a tenant at all. `api/public_deps.py` states the reasoning behind its
default in the strongest terms this codebase uses anywhere:

> The risk is publishing a customer's data because the code *can*, which is a
> disclosure decision no engineer is entitled to make on their behalf.

The column defaults to false and 404s when it is false. That half is right.

The other half was never built. Grep the tree and there is exactly one writer:
`sandbox.py`, which sets it to true as a side effect of provisioning a synthetic
developer tenant. **For a real tenant the column is unreachable.** Provisioning
does not accept it, no control-plane route sets it, and the CLI does not offer
it. The only way to publish a real city's ward figures today is `UPDATE tenants
SET public_api_enabled = true`, typed into `psql` by whoever has the password.

That is not a gap in a feature. It is the disclosure decision the module docstring
refuses to let an engineer make, arriving by the one route that leaves no record
of who made it, when, or why — and it blocks M6, whose entire surface is the
rendering of data no tenant can currently agree to publish.

Three questions had to be answered.

**Does publication belong on `TenantSpec`, so a tenant can be provisioned
publishing?** **When may it be revoked, and by whom?** **What does the log say
about it?**

## Decision

**`PUT /api/v1/control-plane/tenants/{slug}/publication` — a separate,
control-plane-token-gated act, taken after the tenant exists, recorded as
`admin_action` with a required justification.**

```
PUT /api/v1/control-plane/tenants/pune-demo/publication
X-Control-Plane-Token: …
{ "enabled": true, "justification": "Council resolution 2026/114.",
  "min_aggregate": 5 }
```

### Not a field on `TenantSpec`

Provisioning applies a template: eleven departments, a taxonomy, a calendar, and
zero complaints. A tenant that publishes from the instant it exists has published
before anybody has looked at what is in it — and what is in it at that moment is
nothing, which is precisely when the k-anonymity floor has never been exercised
and the aggregates have never been read by a human being.

The stronger reason is that it collapses two decisions with different owners into
one call. Provisioning is an operations act. Publishing a municipality's figures
to the open internet is a decision made by the municipality, usually in writing,
often by resolution. A schema that accepts both in one body invites the second to
be filled in by whoever was performing the first.

So publication is a second call, and a tenant is always born unpublished. The
seam is the same one §E24 draws for the frontend: the ability to build something
is not permission to route it publicly.

### Revocable, through the same door

`enabled: false` retracts. A one-way door on a disclosure is not a control — it
is a control that has been used once. A pilot that ends, a dispute under §16.4, a
data-protection notice: each of these is a reason to stop publishing, and none of
them should require a database session.

Retraction is not erasure and does not pretend to be. Anything already fetched
and cached under `Cache-Control: public, max-age=300` stays fetched, and anything
an RTI applicant has downloaded stays downloaded. The endpoint's response says so
in `cache_seconds`, so the caller learns the window rather than assuming there
isn't one.

### The floor moves in one direction without argument

`min_aggregate` sets `tenants.public_api_min_aggregate`. It is validated against
`public_api.min_aggregate_floor` at the point of *writing* — a tenant asking for
1 is refused with a 422 naming the floor — while `clamp_suppression_threshold`
keeps clamping at read time for rows that predate this endpoint.

Refusing at write time and clamping at read time are not redundant. The write is
a person making a request that is wrong, and telling them so is the whole value
of the interaction. The read is a row that already exists, where failing would
take a live transparency page down over a historical misconfiguration —
ADR-clamping's own argument, unchanged.

### `admin_action`, and only when something changed

The event is `admin_action` with `target_entity_type: "tenant"`, not
`organisation_changed`. `organisation_changed` describes structure — departments,
zones, certifications — and a reader walking a tenant's chain for "when did this
city start publishing" should not have to filter structural noise to find it.
`AdminActionV1.justification` is required for the reason its own docstring gives:
*an audited action with no stated reason is an audit trail that answers "what"
and refuses to answer "why".* A disclosure decision is the case that reasoning was
written for.

**A call that changes nothing appends nothing** and returns `changed: false`. An
idempotent PUT is the right verb here — a deployment script re-running should not
fail — but a log that records the re-runs alongside the decisions is a log where
the decisions are harder to find. The response tells the caller which it was, so
nothing is hidden by the omission.

## Alternatives considered

**A CLI-only operation, no HTTP.** `nemesis.control_plane.__main__` already
exists and the argument for it is real: this is an operator action, not a product
feature. Rejected because the CLI runs inside the api container against the same
service layer, so an HTTP route costs one handler and buys the thing the CLI
cannot — a municipality's own IT department taking the decision from outside the
box, and `nem seed-demo` provisioning a publishing demo city over HTTP like
everything else in this repository.

**Make it a policy under the Phase 6 policy engine, with a backtest.** Over-built.
A policy is a versioned decision rule that a backtest can be replayed against;
publication is a boolean with a legal basis behind it. Forcing it through the
policy machinery would produce a certification step that certifies nothing.

**Publish by default and let a tenant opt out.** Rejected on sight, and recorded
only because it is the shape most SaaS products ship. It inverts who bears the
cost of an oversight, and §22.2's exposure lands on the party that did not act.

## Consequences

- `nem seed-demo` provisions a city that publishes, over HTTP, with a stated
  justification — so M6's surface has something to render on a clean checkout and
  the demo does not depend on a hand-run `UPDATE`.
- `sandbox.py` keeps setting the column directly. It provisions a synthetic
  tenant that exists to be a developer's playground and it is already outside the
  control-plane path; changing it would be a second decision wearing this one's
  clothes. Noted here so the duplication is deliberate rather than missed.
- The contract lock gains one path. A new path is not a breaking change
  (`api/contract.py`), so this re-locks by addition and v1 is unaffected.
- `GET /api/v1/public/{slug}/…` becomes reachable for real tenants for the first
  time, which means §E18's suppression and disclaimer rendering stops being
  theoretical. That is M6's gate, and it could not be asserted before this.

## Revisit when

- **Phase 13 lands identity.** `justification` plus a control-plane token is the
  best available attribution today; the moment there is an operator identity, the
  event should carry `actor_id` and the justification becomes corroboration
  rather than the whole record.
- **A tenant needs to publish some places and not others.** The boolean is
  deliberately coarse. Per-zone publication is a real requirement in a city with
  a sensitive installation in one ward, and it would need its own decision rather
  than a nullable column added here.
