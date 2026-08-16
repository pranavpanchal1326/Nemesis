# RFC-NNNN — <short title>

- **Status:** Draft | Review | Accepted | Rejected | Withdrawn | Implemented
- **Author:**
- **Owning function:** PLT | DATA | PROD | SEC | SRE | BIZ
- **Affected tracks:** <every track this touches — this is what makes it an RFC>
- **Opened:** YYYY-MM-DD
- **Review closes:** YYYY-MM-DD
- **Blueprint:** §<sections>
- **Phase:** <phase this lands in, or amends>
- **Resulting ADR:** <NNNN, once accepted>

## Summary

Three sentences, readable by someone from another track. If it cannot be
summarised in three sentences, the proposal is probably two proposals.

## Problem

What is broken, missing, or about to become expensive. **Evidence, not
assertion** — a metric, an incident, a gate that cannot be met, a customer
requirement. "It would be cleaner" is not a problem statement; it is a
preference, and preferences do not need an RFC.

State the cost of doing nothing. Sometimes that cost is acceptable, and
establishing it honestly is how an RFC gets correctly rejected.

## Constraints

What the solution must respect. At minimum, check these against the
architectural principles in `docs/PHASES.md`:

- Configuration over code — could a solutions engineer do this without an editor?
- Multi-tenant from row zero — does this work for two tenants with conflicting
  configurations?
- Every human decision is training data — is a decision here being discarded?
- Evolvability — does this version and upcast, or does it freeze a payload?
- Hardware — 16 GB, 8 GB WSL2, CPU inference, 8 GB VRAM reserved for Ollama.

## Proposal

What we do, in enough detail that someone else could implement it. Include the
data model, the contract, and the failure behaviour — not just the happy path.

## Alternatives considered

Each with the reason it was rejected. **An RFC with no rejected alternative is a
proposal, not a decision**, and it will be re-litigated by the next person who
thinks of the alternative you did not write down.

Include "do nothing" explicitly. It is a real option and often the right one.

## Impact

| Area | Effect |
|---|---|
| Blast radius | What breaks if this is wrong |
| Migration | What has to change, and can it be done incrementally |
| Reversibility | How we undo it, and how long we have before we cannot |
| Gates | Which existing gates this affects — **weakening one requires saying so here, explicitly** |
| Performance | Against the §27.1 budgets, measured or estimated (say which) |
| Security / privacy | Any effect on §22 or §25 controls |
| Tenancy | Does isolation still hold |

## Risks and open questions

Including the ones with no answer yet. An RFC that claims no open questions is
usually one where they have not been looked for.

## Rollout

Behind which flag, in which order, verified by which signal. If this ships
behind a flag, name it and give it a `remove_by` date — flags are declared in
`backend/nemesis/flags/registry.py` with a removal clock that CI enforces.

## Review notes

Who reviewed, what they objected to, and how it was resolved. Objections are
recorded even when overruled — an RFC that records only agreement is not
evidence that there was any.

If this was decided by one person because no second reviewer was available, say
so here plainly.
