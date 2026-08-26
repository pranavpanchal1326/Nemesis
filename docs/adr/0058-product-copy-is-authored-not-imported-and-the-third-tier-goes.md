# 0058 — Product copy is authored, not imported, and the third string tier goes

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD · PLT · LEGAL
- **Blueprint:** §E10.1, §E22, §E25 Phase 18; `docs/FRONTEND-EXECUTION-PLAN.md` register row **A17**; ADR-0052
- **Taken at:** F18 / M12

## Context

`loadStrings` resolved a namespace in three tiers, merged in order of authority:

1. the source-language bundle compiled into the artefact,
2. the shipped seed for the requested locale,
3. **the Phase 5 locale registry**, fetched from
   `GET /api/v1/control-plane/translations/{namespace}/{locale}` for whichever
   of `common`, `citizen`, `console` and `public` the surface asked for.

The third tier cannot resolve, and never could. `db/models/i18n.py` registers
four namespaces — `taxonomy`, `organisation`, `zone`, `calendar` — and the
importer refuses anything else with a readable error. The *reader*
(`get_translations`) does not validate its path parameter, so a request for
`public` is answered `200 {}` rather than `404`, which is why nothing ever
failed and nothing ever will: the merge is a no-op over an empty object, the
seed bundles carry the words, and every page renders correctly.

That is precisely the problem. §E3.3 and §6 Principle #8 are rules about a
product claiming a capability it does not have, and a tier that can only ever
return `{}` is one. Read from the frontend, the code says *product copy is
tenant-importable*. It is not. The line it was really tracing — Phase 18's
*"a locale added in the control plane appears in the UI with no code change"* —
is met by a different mechanism entirely, and `nem gate-phase18-locale` proves
it against a running stack using `zone`, not `public`.

Found by writing A2's gate, which is the argument for writing gates. Left wired
rather than deleted at the time, deliberately: which way it should go is a
decision with an argument on each side, and deleting it quietly would have
settled the question by attrition.

## Decision

**The frontend stops implying it can. The third tier is removed.**

`loadStrings` resolves two tiers — base, then the shipped seed — and makes no
network call. The unused BFF proxy at `frontend/src/app/api/i18n/[namespace]/[locale]/`
is deleted with it; it had no consumer in `src/` or `tests/`, which is its own
small piece of evidence about how live this path was.

A ninth guard in `frontend/scripts/check-guards.ts` —
**`no-product-copy-from-the-registry`** — fails the build on any reference to
the control-plane translations endpoint under `frontend/src/`. The tier is not
merely gone; it cannot return by somebody re-adding three plausible lines.

**Who owns which words is now stated in one place and enforced in two.**

| | Owner | Where it lives | How a locale is added |
|---|---|---|---|
| Product copy — button labels, error prose, the §22.1 consent text, §E26's contract strings | NEMESIS | `src/i18n/base/*.json`, versioned with the code and reviewed like code | a release |
| Tenant-authored text — a ward's name, a taxonomy node's display name, an organisation, a calendar | the tenant | the `translations` table (`NAMESPACE_ZONE`, `NAMESPACE_TAXONOMY`, `NAMESPACE_ORGANISATION`, `NAMESPACE_CALENDAR`) | **an import — no code change** |
| The §16.1 rating disclaimer and the §22.2 system-flagged notice | NEMESIS, with a named reviewer | `nemesis/public/notices.py`, rendered verbatim through `notTranslatable()` | a release, plus review (ADR-0052) |

Phase 18's gate is unaffected and is not weakened: it governs the middle row,
`nem gate-phase18-locale` asserts the middle row end to end over HTTP, and the
first row was never what it was about.

## Alternatives considered

**Widen the registry to carry product copy.** The other honest answer, and the
one that would have kept the code's claim true. It reopens what
`db/models/i18n.py` settled in a sentence — *"a tenant could overwrite the
wording of a legal notice, which is not a localisation feature"* — and the
sentence has not stopped being right. ADR-0052 weakened it in exactly one
direction by moving the §22.2 notice and the §16.1 disclaimer into
`notices.py`, out of reach of any tenant; it did not answer it, because the
§22.1 consent text, every refusal sentence and every word on a receipt are
still product copy, and all of them are strings a municipality would have a
motive to soften. Widening the registry to make a tier resolve is relaxing a
standing rule to satisfy a mechanism, which is Law 4's shape one level up.

**Validate the namespace on the reader and let the tier 404.** Half a fix. It
makes the failure visible to whoever reads the logs and leaves the frontend
still asking a question whose answer is always no — one round trip per
namespace per non-source-locale render, to be told what the code already knows.

**Leave it, and document it.** What M9 through M11 did, and it was the right
call while the decision was genuinely open. It stops being right at F18: this
is the phase whose entire subject is a claim the code makes and cannot support.

**Keep the proxy route, remove only the call.** A route with no caller is a
capability with no owner. It would sit in `src/app/api/` reading like a
supported seam and would be the obvious thing to reach for next time somebody
wants to translate a button.

## Consequences

- **One fewer network call per non-source-locale render, per namespace.** A
  Marathi public page rendered `common` + `public`: two upstream requests that
  each returned `{}`. Now zero.
- **`loadStrings` no longer touches the network at all**, which removes the last
  reason for the `try`/`catch` F12 added — the one that stopped an unreachable
  control plane 500ing every non-source locale. The catch goes with the fetch;
  the behaviour it protected is now structural rather than defended. Its test
  moves to asserting the stronger property: *no upstream call is made.*
- **A locale still arrives with no code change for everything a tenant owns**,
  and now needs one for everything NEMESIS owns. That is the true statement, and
  it is the one §E28 and the published honesty table will carry.
- **A locale NEMESIS ships no bundle for renders in the source language** with
  the tenant's own words — ward names, categories — in the new locale. That is
  the `ar` case §E22 and `bundles.ts` already describe, unchanged.
- The four registry namespaces stay exactly as they were. Nothing about the
  backend changes; this ADR removes a caller, not a capability.

## Revisit when

- A deployment needs product copy in a language NEMESIS does not ship and cannot
  wait for a release. The answer then is a **review workflow** — a proposed
  bundle, a named reviewer, a published review status, the shape ADR-0052
  already set for the notices — and not the unvalidated import this ADR removed.
- `get_translations` grows namespace validation. It should: answering `200 {}`
  for a namespace the writer refuses is the asymmetry that let this live for
  three milestones. Out of scope here because it is a backend contract change
  and F18 is not the phase that takes those.
