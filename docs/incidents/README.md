# Incident process

An incident is any unplanned event that degrades a stated commitment: a §27.1
processing budget, a §27.2 citizen-facing SLA, a safety guarantee, or the
integrity of the event log.

The process is deliberately small. A heavy process during an incident competes
with the incident for attention, and the loser is always the process — so what is
written here is only what has been observed to survive contact with a real
outage.

## Severity

Severity is chosen from **impact on citizens and on the accountability
guarantees**, never from how alarming the technical symptom looks. A saturated
queue that nobody notices is SEV-3. A safety fail-safe that ran late is SEV-1
even if every dashboard is green.

| | Definition | Response | Post-mortem |
|---|---|---|---|
| **SEV-1** | A safety guarantee at risk, citizen data exposed, the event chain broken, or complaints being lost | Drop everything | Required, within 5 working days |
| **SEV-2** | A stated SLA or budget breached; a core capability unavailable; a degraded path carrying full load | Same working day | Required, within 10 working days |
| **SEV-3** | Degraded but contained; a fallback is holding; no citizen-visible effect | Next working day | Optional — write one if anything was surprising |

**Two rules that override the table.** Any incident involving personal data is
at least SEV-2 regardless of scale, and stays SEV-2 even if only one record was
affected; §22 and the DPDP program treat exposure as categorical, not
proportional. And any incident where the **safety fail-safe did not fire on a
genuinely dangerous report** is SEV-1 unconditionally — §41.1 sets that
false-negative rate at a hard 0 %, described as "not a target range".

## Roles

At current team size one person usually holds all three. Naming them anyway is
what makes it possible to hand one off cleanly when there are more people, and
what makes it obvious which hat you are wearing when you are wearing all of them.

- **Incident lead** — owns the decision, not the keyboard. Their job is
  sequencing and communication. If the lead is deep in a terminal, nobody is
  leading.
- **Operator** — makes the changes. Announces each one *before* making it, so
  two people cannot restart the same service.
- **Scribe** — keeps the timeline in UTC as it happens. Reconstructing a timeline
  afterwards produces a tidy narrative rather than an accurate one, and the
  difference is exactly where the learning was.

## Flow

```
alert fires  →  runbook  →  mitigate  →  verify  →  record  →  post-mortem  →  action register
```

1. **Acknowledge.** Say out loud (in whatever channel exists) that you are
   looking at it. An unacknowledged alert gets investigated twice or not at all.
2. **Open the runbook.** Every alert carries a `runbook_url` and CI verifies the
   page exists. Read the opening note — several pages begin by telling you the
   situation is not urgent, and that is the single most valuable line on them.
3. **Mitigate before diagnosing.** Restore the commitment first. A kill switch
   pulled at minute two costs less than a root cause found at minute forty. The
   handles are in
   [feature-flag-kill-switch.md](../runbooks/feature-flag-kill-switch.md).
4. **Verify against a signal, not a feeling.** The alert clearing is the
   verification. If you cannot point at the metric that recovered, you have not
   verified anything.
5. **Record.** Copy [TEMPLATE.md](TEMPLATE.md) to
   `docs/incidents/YYYY-MM-DD-short-slug.md` while it is fresh.
6. **Post-mortem** per the severity table.
7. **Action register.** Every action gets an owner and a date in
   [action-register.md](action-register.md), or it does not exist.

## Blameless, and what that actually means

Blameless does not mean "nobody made a mistake". It means the mistake is treated
as **evidence about the system**, because a system in which a tired person can
cause an outage with one command will eventually meet a tired person.

Concretely, in a post-mortem:

- Write "the deploy proceeded without the migration having been applied", not
  "X forgot to run the migration".
- Never write "human error" as a root cause. It is a description of where the
  investigation stopped.
- **"Be more careful" is not an action item.** Neither is "add documentation",
  unless the documentation is a runbook page an alert links to.
- Ask what made the wrong action *easy* and the right action *hard*. That
  difference is the fix.

The reason this is enforced rather than encouraged: the moment a post-mortem can
attribute fault, people stop reporting near misses, and near misses are the
cheapest information available about a system.

## Post-mortem quality bar

A post-mortem is finished when someone who was not there could:

1. Reconstruct the timeline in UTC, including what was *believed* at each point
   — not just what was true. The gap between them is usually the finding.
2. Identify which signal detected it, and how long detection took. "A customer
   told us" is a legitimate and important answer.
3. Name the contributing factors, plural. Single-cause incidents are rare enough
   that finding one should raise suspicion that the investigation ended early.
4. Read the action register and see items that are specific, owned, and dated.

## What is deliberately not here

**On-call rotation and escalation routing.** They are Phase 1b, deferred until a
deploy target is chosen. An escalation policy with no production system to be
paged about would be a document describing a rotation nobody is on, and writing
it now would mean writing it twice.

Alertmanager already groups, routes, and inhibits — the routing tree exists and
works. Phase 1b adds notification integrations to receivers that are already
there, rather than building a new tree. What is missing is deliberately the
*human* half.

## Index

No incidents recorded yet. Files are added as `YYYY-MM-DD-short-slug.md` and
listed here newest first.

| Date | Severity | Title | Post-mortem |
|---|---|---|---|
| — | — | — | — |
