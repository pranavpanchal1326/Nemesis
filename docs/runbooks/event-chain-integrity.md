# Runbook — Event chain integrity break

**Alerts:** `NemesisEventChainBroken`, `NemesisEventIntegritySweepStalled`
**Severity:** critical / warning
**Owning function:** PLT (Platform Engineering)
**Blueprint:** §9.3 hash chaining, §17.4 (the gap this closes)

---

## Symptoms

- `NemesisEventChainBroken` firing — `nemesis_event_chain_breaks_total`
  increased, meaning the scheduled sweep recomputed an entity's chain and got a
  different answer than the rows store.
- `NemesisEventIntegritySweepStalled` firing — no chain has been verified in six
  hours. Different failure, same consequence: tamper detection is off.
- `event_chain_integrity_break` lines in the `worker-io` log.
- A support or audit request that cannot be answered because a complaint's
  history does not explain its current state.

Every event written by `EventStore.append` had a consistent hash **at the moment
it was written**. A mismatch therefore means something changed the row
afterwards.

## How to confirm

The alert is deliberately unlabelled — a `tenant_id` label would be unbounded
cardinality on the metric that must never stop being scraped. The identity is in
the structured log:

```bash
docker compose logs worker-io | grep event_chain_integrity_break | tail -20
```

Each line carries `tenant_id`, `entity_type`, `entity_id`, `first_break_kind`,
and `first_break_sequence`. That last field is the point: everything before that
sequence verified, so the blast radius is bounded and stated.

Re-verify one chain directly, which also confirms the finding is current rather
than a stale metric:

```bash
docker compose exec api python -m nemesis.events.inspect --tenant <TENANT_ID> --entity-type complaint --entity-id <ENTITY_ID>
```

Sweep more broadly to learn whether this is one chain or many:

```bash
docker compose exec api python -m nemesis.events.inspect --sweep --limit 2000
```

| Break kind | What changed | Most likely cause |
|---|---|---|
| `content_altered` | A row's fields no longer hash to its stored `event_hash` | An `UPDATE` against `events` |
| `link_broken` | A row's `previous_hash` does not match its predecessor | A row inserted, deleted, or reordered |
| `sequence_gap` | Sequence numbers skip | A `DELETE` |
| `sequence_duplicate` | Two rows share a sequence | Chain head lock bypassed, or a restore from an inconsistent backup |
| `head_mismatch` | `event_chain_heads` disagrees with the last event | A partial restore, or a write outside `EventStore` |

## Immediate mitigation

**Do not repair the chain.** A chain that can be repaired proves nothing,
because the repair is indistinguishable from the tamper. There is no `--fix`
flag and there will not be one. Do not delete the broken rows — they are the
evidence. Do not restart the workers hoping it clears; nothing about this is
transient.

1. **Preserve.** Snapshot the affected partition before anything else:
   `pg_dump -t events_YYYY_MM`. Do this even if you are confident it was an
   engineer with a bad `UPDATE` — confidence is not evidence.
2. **Freeze direct database access** for the affected environment until the
   cause is known. If the cause *was* direct access, this also prevents the
   well-intentioned second edit that destroys the trail.
3. **Stop deriving public claims** from the affected entity. §6.1's "prove,
   don't log" cuts both ways: a chain that cannot be verified must not be cited
   as proof, so suppress it from public contractor metrics and RTI exports until
   resolved.
4. **Open an incident** — SEV-1 for a confirmed tamper of unknown origin, SEV-3
   for a self-reported bad `UPDATE` with a named author.

## Root cause investigation

**Order of suspicion, from experience rather than from drama:** a well-meaning
engineer "fixing" a stuck complaint with direct SQL is far more common than an
attacker. A restore that replayed one table and not another is next. Treat it as
a genuine tamper until the first two are ruled out — but look for them first.

Establish **when** it changed. Postgres keeps no row history, so the usable
signals are ordering anomalies:

```bash
docker compose exec postgres psql -U nemesis -d nemesis -c "SELECT sequence, event_type, recorded_at, occurred_at FROM events WHERE tenant_id = '<TENANT_ID>' AND entity_id = '<ENTITY_ID>' ORDER BY sequence"
```

A row whose `recorded_at` is out of order with its neighbours' `sequence` was
almost certainly inserted by hand.

The *shape* of the break list is itself evidence. One `content_altered` is a
fat-fingered statement; forty consecutive ones are not. `verify_chain` collects
every break rather than stopping at the first, precisely so that distinction is
visible.

Then choose a resolution, in order of preference:

1. **Accept and annotate.** If the cause is known and benign, leave the break in
   place and record an `admin_action` event describing it. The log now says
   "this was altered, here is who and why", which is a stronger statement than a
   clean chain.
2. **Restore the partition** from the pre-break backup if the altered data is
   itself wrong and correct values are recoverable. Re-run the sweep; expect a
   `head_mismatch` until `event_chain_heads` is reconciled.
3. **Quarantine.** If neither is possible, mark the chain disputed and keep it
   out of any public claim permanently.

### `NemesisEventIntegritySweepStalled`

```bash
docker compose ps beat worker-io
docker compose logs beat | tail -30
docker compose exec worker-io celery -A nemesis.worker.celery_app:celery_app inspect scheduled
```

Usual causes: `beat` is down (its healthcheck is process liveness, so check the
container first), the `io` queue is saturated, or a deploy dropped
`nemesis.pipeline.integrity` from autodiscovery. Confirm recovery by watching
`nemesis_event_chains_verified_total` increase, **not** by the absence of the
alert — it clears on a 6h window and will lag a fix by hours.

## Prevention

- Direct `UPDATE`/`DELETE` on `events` should not be reachable from any
  application role. Phase 25's Row-Level Security work is where this becomes a
  database-enforced guarantee rather than a convention.
- Phase 27's support console exists so a customer question does not become an
  engineer running SQL against production — the root cause this runbook keeps
  describing.
- `python -m nemesis.events.inspect` is read-only and answers most questions
  that would otherwise be answered with improvised SQL. Point people at it
  before they need it.

## Related

- [`docs/adr/0010-widened-event-hash-preimage.md`](../adr/0010-widened-event-hash-preimage.md)
- [`event-partition-maintenance.md`](event-partition-maintenance.md)
- [`docs/incidents/TEMPLATE.md`](../incidents/TEMPLATE.md)
