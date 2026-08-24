# 0042 — Next.js 16 ships; the TypeScript 7 native compiler waits

- **Status:** Accepted
- **Date:** 2026-08-24
- **Owner:** PROD
- **Blueprint:** §E15 · §E24, §E25 Phase 18

## Context

§E15 is a locked section and it pins **Next.js 15**. It does not pin a
TypeScript version; it says "TS strict".

Both pins need re-deciding at the moment Track E actually begins, because the
field moved between the blueprint being written and the first line of frontend
code being committed:

- **Next.js 16** is the current stable major. App Router, React 19.2, Turbopack
  as the default bundler rather than an opt-in flag.
- **TypeScript 7.0** is out, and it is not an ordinary major. It is the native
  Go port of the compiler — a different implementation of `tsc`, with an
  order-of-magnitude speed claim and a correspondingly new surface area for
  differences in behaviour, plugin support, and language-service integration.

The temptation is to treat these as one decision — "take the current version of
everything" or "take what the blueprint said" — and both of those are wrong,
for opposite reasons.

## Decision

**Next.js 16 ships, amending §E15. TypeScript is pinned at 5.9.**

The asymmetry is the decision, not an inconsistency in it.

**Why the framework major is taken.** §E2 defect #6 is a record of exactly this
mistake: §8.1 specified hand-written GLSL because that was correct when it was
written, and shipping it in 2026 would have forfeited WebGPU and the compute
stage the press needs. The lesson recorded there is that a locked stack choice
written before the work starts should be re-read against the field when the work
starts. Applying that lesson to §E15's own framework pin is not a deviation from
the blueprint; it is the blueprint's own reasoning applied one level up. There
is no deployment deadline and no legacy surface to migrate — Track E is
greenfield — so the cost of taking the current major is a day of scaffolding,
and the cost of not taking it is a migration later against a real application.

**Why the compiler major is declined.** §E25's Phase 18 gate says *zero `any` in
application source* and *generated-client drift fails CI*, and §E24 adds *a
hand-written colour literal in application source fails CI*. Every one of those
is enforced by tooling that runs on top of the compiler: the ESLint
type-aware rule set, the Next lint integration, the Storybook and Vitest
transform chains, and the editor language service. A compiler those tools have
not fully caught up with does not buy us a gate — it puts three gates at risk in
exchange for typecheck speed on a codebase that does not yet exist and will
never be large enough for compiler speed to be the binding constraint. The
binding constraint on this project is VRAM shared with Ollama (§E23), not
`tsc` wall time.

This is deliberately **not** the safe-by-default position of holding both.
Holding Next at 15 would be the safe position, and it is refused.

## Alternatives considered

**Next.js 15 as §E15 literally states.** Rejected. It is still
security-supported, so nothing breaks — but it commits a greenfield application
to a generation-behind framework on its first commit, and it books a migration
that will be paid later with a real application on top of it. §E15 is locked
against *drift*, not against *argued amendment*; this ADR is the argument, which
is the mechanism the repository already uses.

**TypeScript 7 native alongside Next 16.** Rejected on gate risk, above. It is
attractive and it will be taken — the trigger is recorded in
`docs/UPGRADES.md`: when the type-aware ESLint rule set, Next's lint
integration and the Storybook transform chain all publish support, TypeScript 7
is a one-line change plus a green `nem web-check`, and nothing in application
source depends on which implementation compiled it.

**Taking neither and revisiting after M4.** Rejected because the framework
choice is the one decision in Track E that gets *more* expensive with every
milestone, and M0 is the only cheap moment to make it.

## Consequences

- §E15's framework row is amended in place, and the amendment is recorded in
  §E2's own-errors table, because a locked section that changes silently is the
  defect §E2 exists to prevent.
- `frontend/package.json` pins the TypeScript minor exactly, not with a caret,
  so a transitive bump cannot move us onto 7 without the deliberate act this
  ADR describes.
- `docs/UPGRADES.md` carries the TypeScript 7 upgrade with its trigger condition
  and its cost, so declining it now is a scheduled decision rather than a
  forgotten one.
- Next 16's Turbopack-by-default removes the need for the webpack config the
  blueprint never specified, which is one fewer place for a CDN URL or a colour
  literal to hide from the CI greps in §E24.
