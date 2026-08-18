# 0021 — The public API is opt-in per tenant, and its aggregates are k-anonymous

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT · SEC
- **Blueprint:** §16.2, §16.3, §26.4, §22.1

## Context

§26.4 specifies a read-only, no-auth, rate-limited API over "privacy-scrubbed"
aggregates, written for a single-tenant deployment. Building it here raised two
questions the blueprint does not answer, and both are disclosure decisions
rather than engineering ones.

**Which tenant's data does an unauthenticated request address?** Every other
endpoint resolves a tenant from `X-Tenant-ID`, which is a UUID and an internal
handle. Requiring a journalist to obtain one before they can call a public API
defeats the purpose of it being public.

**What does "privacy-scrubbed" mean for a small number?** A ward summary over
two complaints is not an aggregate. It is one citizen's report with a category, a
place, and a time attached — every field individually scrubbed, and the row
still identifies a person to anyone who was on that street. Scrubbing fields is
necessary and it is not sufficient.

A third question surfaced while answering the first: the code being *capable* of
publishing a customer's figures is not the customer having *agreed* to publish
them. Those are different statements, and defaulting the surface on would make
the first silently mean the second for every tenant already provisioned.

## Decision

**The tenant slug is in the path, publication is opt-in and defaults to false,
and every aggregate bucket below a per-tenant floor is suppressed and says so.**

- `GET /api/v1/public/{tenant_slug}/...`. A slug is the organisation's own
  public name — readable, shareable, and citable, which is what §16.2's
  "bookmark-able by journalists" requires.
- `tenants.public_api_enabled` defaults to `false`. A tenant that has not
  decided its disclosure posture has not decided yes.
- `tenants.public_api_min_aggregate` defaults to 5 and is **clamped up** to a
  deployment floor (`public_api.min_aggregate_floor`), never down.
- A suppressed response carries `"suppressed": true` and the threshold, rather
  than omitting the figures.

## Consequences

**A tenant not publishing is 404, not 403.** Distinguishing them would let
anyone compile the deployment's customer list and — worse — learn which public
bodies have declined to publish, which is a statement about them that this
system has no business making on their behalf. The same three-states-one-answer
discipline `api.deps` applies to tenant lookup.

**Suppression is applied at the query boundary, not in the response model.** A
model that receives a count of 2 and renders `null` has already had the count in
memory, in a traceback, and in whatever an error reporter captured.

**"Suppressed" and "empty" stay distinguishable, and this is the part that is
easy to get wrong.** Zero reports is publishable and useful — it is a real fact
about a ward. Between one and the floor is not. A consumer that cannot tell a
withheld bucket from an absent one will read the second from the first, and a
transparency API that misleads while technically disclosing nothing is worse
than one that discloses less. The withheld-bucket *count* is published for the
same reason: forty reports across three visible categories and forty across nine
with six withheld are different pictures.

**The floor is clamped rather than rejected.** A tenant configured at 1 has
turned an aggregate endpoint into a per-complaint feed, which §26.4 forbids
whoever asked for it — and failing the request instead would take a public
transparency page offline over a configuration mistake. Degrading toward *more*
privacy is the direction that is safe to do silently.

**Budgets are deliberately not suppressed.** A budget allocation is a published
public-finance figure about a municipality, not an observation about a citizen.
Withholding a line because only one scheme funded a ward would hide precisely
what an RTI applicant came for.

**Contractor profiles are suppressed, and for a sharper reason than the wards.**
A contractor with two completed jobs and one dispute has a published "33%
dispute rate" that is statistical noise presented as a finding about a named
commercial entity. §16.4 requires the appeal path to ship alongside the
accountability feature; the honest first line of that defence is not publishing
a figure that cannot mean anything.

**k-anonymity here is a floor, not a proof.** Suppressing small cells does not
defend against an adversary who queries the same ward daily and differences the
counts. Real differential privacy is a Phase 23 conversation with the analytics
platform, and pretending this is that would be the overclaim §17's own framing
warns against.

## Alternatives rejected

**A UUID in the path.** Unguessable, and it makes every URL uncitable and every
integration dependent on an out-of-band lookup.

**Publication on by default with an opt-out.** Faster to launch, and it means
the first a customer hears about their data being public is when somebody links
to it.

**A single global suppression threshold.** Five is defensible for a city ward
and far too low for a campus building with nine occupants. Population is a
tenant property, so the threshold is too — with a deployment floor beneath it,
because a per-tenant knob with no floor is a per-tenant way to disable the
control.
