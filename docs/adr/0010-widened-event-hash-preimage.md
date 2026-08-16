# 0010 — The event hash preimage is widened and structured, not the blueprint's concatenation

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT
- **Blueprint:** §9.3, §17.4

## Context

Blueprint §9.3 specifies the chain link as:

```python
raw = f"{previous_hash}{event_type}{entity_id}{json.dumps(payload, sort_keys=True)}{created_at}"
```

This is the right idea — cheap, per-entity, checkable — and Phase 2 implements
the idea. But taken literally it has two defects that only matter once somebody
is actually trying to detect tampering, which is to say exactly when it matters.

**It omits fields that change the meaning of the event.** Four of them, and each
omission is an edit an auditor would not catch:

| Omitted | The undetectable edit |
|---|---|
| `tenant_id` | Move a row between tenants. In a multi-tenant product this is the highest-consequence edit available, and the chain stays valid |
| `event_version` | Rewrite the version, and a different upcaster interprets the same bytes as a different event |
| `sequence` | Without it, order is implied only by `previous_hash`; two events sharing a predecessor can be swapped undetectably |
| `entity_type` | `entity_id` is a UUID, but nothing forbids the same UUID appearing under two entity types |

**String concatenation is ambiguous.** An `event_type` of `"a"` followed by an
entity id beginning `"bc"` produces the same bytes as `"ab"` followed by `"c"`.
Unlikely to occur by accident; trivial to arrange on purpose, which is the threat
model a tamper-evidence mechanism is built for.

Separately, `json.dumps(sort_keys=True)` is not a canonical form — see
[ADR-0013](0013-rfc-8785-canonical-json.md).

## Decision

The preimage includes `previous_hash`, `tenant_id`, `entity_type`, `entity_id`,
`sequence`, `event_type`, `event_version`, `occurred_at`, and `payload`, **built
as a JSON object and canonicalised** rather than concatenated. Every field is
therefore delimited and length-implied, and the payload goes through the same
canonicaliser as everything else.

The preimage also carries `scheme: 1` (`CHAIN_SCHEME_VERSION`). A chain written
under one scheme can never be silently verified under another — the mismatch
surfaces as a broken link at the exact transition point rather than as a
whole-chain failure with no explanation.

`GENESIS_HASH` is domain-separated (`sha256("nemesis.event-chain.genesis.v1")`)
rather than a string of zeroes, so a chain cannot be re-rooted by inserting a
plausible-looking all-zeroes predecessor.

## Alternatives considered

**Implement §9.3 exactly.** Rejected: it would ship a mechanism that *claims*
tamper evidence and does not detect the single most damaging tamper available in
a multi-tenant system. A verification that returns green on a cross-tenant row
move is worse than no verification, because it will be cited as proof.

**Keep concatenation but add separators.** Rejected: it needs an escaping rule
for the separator, and an escaping rule is a second serialisation format to keep
correct forever. Canonical JSON already solves this and is already needed for the
payload.

**Hash the entire row including `recorded_at`.** Rejected: `recorded_at` is
assigned by the database `DEFAULT now()`, so the writer cannot know it before the
insert. Including it would require an update-after-insert, and updating an event
row is exactly what this design forbids.

## Consequences

- **A chain written by this build cannot be verified by a §9.3-literal reader**,
  and vice versa. That is intended and versioned rather than accidental.
- The preimage is more expensive to compute — a canonical JSON serialisation
  instead of an f-string. Measured at well under a millisecond per event, against
  a write path that is already doing a row lock and two inserts.
- Cross-tenant row moves, version rewrites, reordering, and entity-type confusion
  all become detectable, at the exact offset (`verify_chain` reports which
  sequence and which component disagreed).
- **The integrity sweep is what makes any of this real** — write-path hashing
  proves only that the writing process was consistent at the time. That sweep
  runs hourly (`nemesis.integrity.sweep_chains`), closing the gap §17.4 lists as
  ROADMAP.

## Revisit when

- An external party needs to verify a NEMESIS chain independently. That requires
  publishing the preimage construction as a specification, and would be the
  moment to reconsider whether it should follow an existing standard rather than
  our own.
- `CHAIN_SCHEME_VERSION` needs to change. Any such change requires a migration
  plan for existing chains, because rewriting stored hashes destroys the
  property they exist to provide.
