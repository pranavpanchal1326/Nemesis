# 0019 — A taxonomy key is an immutable contract; the display name is a translation

- **Status:** Accepted
- **Date:** 2026-08-17
- **Owner:** PLT
- **Blueprint:** §9.4, §13.5, §16.3

## Context

A tenant's defect category needs a name. The obvious model is one column: the
category *is* its name, and renaming it is an `UPDATE`.

That model has a failure this system cannot absorb. `classification_scored`
records `category` in an append-only log that must remain readable for years, and
`complaints.category` is the projection of it. A rename would leave every
historical event pointing at a string that no longer resolves — and the log
cannot be rewritten, because rewriting it is exactly what the hash chain exists
to make detectable.

The reflex fix is to forbid renames. That fails for a different reason: "Pothole"
is wrong on a screen a citizen is reading in Marathi, and "we cannot fix the
label because the log would break" is not an answer anybody outside engineering
will accept.

## Decision

**Two separate things, in two separate columns.**

- **`taxonomy_nodes.key`** is the machine identifier. It is what appears in the
  event log, in a URL path segment, in a Prometheus label, and in a contractor's
  certification scope. It is immutable by convention, constrained to
  `[a-z0-9_.-]` by a `CHECK` constraint, and may not contain `/`.
- **`taxonomy_nodes.display_name`** is the fallback label, and the
  `translations` table holds it per locale.

Renaming a label is a translation edit and touches no history. Renaming a *key*
is not offered through any API.

## Why the key pattern is enforced in the database

The service validates keys through Pydantic, but the service is not the only
writer — a migration is, and so is a `psql` session during an incident. Three
consequences ride on the pattern:

- **`/` is the path separator.** The subtree query is
  `path LIKE 'roads/%'`; a key containing `/` would silently merge one node's
  subtree into another's, and both would look correct in every listing, because
  listings sort by that same path.
- **The key becomes a metric label**, where a space or a quote is a cardinality
  or an escaping bug rather than a validation error.
- **The key reaches §16.3's public API** as a URL segment.

`_` and `%` are legal in a key and are `LIKE` wildcards, so the subtree pattern
escapes them. That is not hypothetical: `_` is the recommended word separator, so
almost every real key contains one.

## There is no DELETE

A category a complaint was classified into cannot be removed without making that
complaint's history unreadable. Deactivation (`is_active = false`) is the
operation, and it is reversible in a way deletion is not. The
`contractor_certifications` foreign key is `ON DELETE RESTRICT` for the same
reason: §17's audit has to resolve what a contractor was allowed to do at the
time of an assignment, which requires the node to still exist.

Deactivated nodes still count toward the taxonomy content hash. A complaint
classified into a since-retired category is still classified into it, so a digest
that dropped inactive nodes would report two materially different taxonomies as
identical.

## Consequences

- A tenant that picks a bad key lives with it, or creates a new node and
  deactivates the old one — which leaves an honest record that the taxonomy
  changed, rather than a rewritten one claiming it was always right.
- Every surface that displays a category must resolve through the translation
  bundle and fall back to `display_name`. The fallback is deliberately a single
  step and not a chain; see `db/models/i18n.py`.
- `TranslationService.coverage()` exists so an untranslated taxonomy is a number
  somebody can be shown, rather than a discovery made by a citizen reading
  English on a Marathi interface.
