# The §11.4 review queue is filling faster than anyone is working it

- **Severity:** warning — nothing is broken, and nothing is blocked. Every
  flagged report is still flowing through the pipeline; a human simply has not
  looked at it yet.
- **Owner:** DATA
- **Alerts:** `nemesis_review_queue_open` rising without a matching rise in
  `nemesis_review_decisions_total`.

> **Read this before doing anything.** §11.3's detectors *flag, they do not
> block* (ADR-0033), so a backlog here does not suppress a single citizen's
> report. The pipeline is unaffected. What a backlog costs is the thing §11.4
> exists to provide — that no flag is a dead end — and it costs it silently.
>
> The reflex to reach for is **not** "raise the thresholds so fewer things
> queue". A threshold change is a policy revision, it is backtestable, and doing
> it under pressure to make a graph go down is how a fraud check becomes
> decoration. Work out *what* is queueing first; it is usually one thing.

## Symptoms

- `nemesis_review_queue_open{reason=...}` climbing steadily, concentrated in one
  or two reasons.
- Operators report the queue "has thousands in it".
- `GET /api/v1/review/queue` returns a large `total` with a narrow spread of
  `reason` values.

Not this page:

- A rise in `reason="safety_trigger"` — that is §11.2 firing, it is the highest
  priority in the queue by design, and it means something dangerous is being
  reported. See `safety-path-degraded.md` only if the *latency* is the problem;
  otherwise this is the system working and the answer is more reviewers.
- Complaints stuck at `submitted` — that is the pipeline halting, not the queue.
  See `media-redaction-unavailable.md`.

## How to confirm

What is actually queueing, and in what proportion:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT reason, count(*) FROM review_queue_items WHERE tenant_id = '<TENANT>' AND status = 'open' GROUP BY reason ORDER BY 2 DESC"
```

Is it one incident or a steady rate:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT date_trunc('hour', created_at) AS hour, reason, count(*) FROM review_queue_items WHERE tenant_id = '<TENANT>' AND created_at > now() - interval '2 days' GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC LIMIT 40"
```

If `geographic_cluster` dominates and it is concentrated in a few hours, look at
where — a genuine burst water main, protest, or power cut produces exactly the
pattern the detector is built to find, and several dozen honest reporters on one
street is indistinguishable from a bot farm at the level the detector works at:

```bash
docker compose exec -T postgres psql -U nemesis -d nemesis -tAc "SELECT evidence->'centre', evidence->>'distinct_devices', count(*) FROM review_queue_items WHERE tenant_id = '<TENANT>' AND reason = 'geographic_cluster' AND status = 'open' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10"
```

## Immediate mitigation

**If it is one real-world incident** — the common case — the queue is telling
the truth and the answer is to work it, not to silence it. Decide the items in
bulk *individually*: there is deliberately no bulk-decide endpoint, because
fifty labels backed by one glance would teach Phase 11 the reviewer's fatigue
rather than their judgement.

**If a detector is genuinely misconfigured for this deployment** and is firing
constantly on normal traffic, stop it at the switch rather than by editing
thresholds under pressure:

```bash
python tasks.py flag kill trust_abuse_detection --actor "<YOU>" --reason "burst incident on <WARD>, queue unworkable"
```

That stops **both** §11.3 detectors from firing. It changes no decision, because
they never made one — the pipeline, the trust score's other contributors, and
the §11.1 checks all continue. Take effect within the flag reload interval (5s).

Restore it as soon as the incident is over:

```bash
python tasks.py flag clear trust_abuse_detection --actor "<YOU>" --reason "incident closed"
```

**Do not** kill it to reduce a steady-state rate. A permanently-killed detector
is a detector that has been deleted with extra steps, and the removal date on
the flag is what stops that becoming permanent by accident.

## Root cause investigation

**A real incident.** Check the evidence bundles: `distinct_devices` in the teens
across a 150 m radius, spread over hours, with sensible descriptions, is a
street. `distinct_devices` in the teens within minutes, with near-identical
text, is not.

**Thresholds wrong for this tenant.** The defaults are municipal conventions. A
university campus where every report comes from one compound will trip the
geographic-cluster detector constantly at a 150 m radius; a rural district may
never trip it at all. The fix is a `trust_thresholds` policy revision, drafted
and approved through the ordinary lifecycle — and, because it is a governed
document, an operator can do it without a deploy:

```bash
curl -sX POST "http://localhost:8000/api/v1/control-plane/policies/trust_thresholds" -H "X-Tenant-Id: <TENANT>" -H "X-Control-Plane-Token: <TOKEN>" -H "Content-Type: application/json" -d '{"body": {"geo_cluster": {"radius_meters": 40.0, "min_distinct_devices": 8}}, "change_reason": "Campus: every report is inside one compound"}'
```

Note what this document **cannot** do: there is no field that turns face blur
off, and no field that makes a detector block. Both are ADR-0032 and ADR-0033.

**A single device flooding.** `device_velocity` concentrated on one fingerprint
is one actor, and the evidence bundle names the other reports. That is the case
§11.3 was written for; the queue is working.

**Nobody is working the queue.** The least technical cause and the most common
one in a pilot. The queue has no owner until Phase 13 gives operators
identities and Phase 27 gives them a console; until then it is a URL somebody
has to be told to open.

## Prevention

- **Phase 7** already makes a threshold change measurable before it is live —
  but trust thresholds are explicitly *not* backtestable today
  (`DECIDABLE_KINDS`), because the corpus cannot reconstruct EXIF distances or
  device fingerprints. Phase 11's labelled decisions are what will close that.
- **Phase 11** turns the decisions in `review_decisions` into the feedback that
  tunes these thresholds from measured outcomes rather than from an operator's
  guess after a bad afternoon.
- **Phase 13** gives the queue an owner, an assignment model, and per-reviewer
  throughput — at which point "nobody is working it" becomes visible instead of
  being inferred from a rising gauge.
- **Phase 27**'s support console is where this queue is actually meant to be
  worked; the HTTP surface shipped in Phase 8 is the API it will be built on.
