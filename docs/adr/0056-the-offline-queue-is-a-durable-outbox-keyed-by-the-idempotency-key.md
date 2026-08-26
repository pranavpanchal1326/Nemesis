# 0056 — The offline queue is a durable outbox keyed by the idempotency key

- **Status:** Accepted
- **Date:** 2026-08-26
- **Owner:** PROD · PLT
- **Blueprint:** §E21, §E14.1, §E17.1, §E25 Phase 22 · §26.1
- **Builds on:** ADR-0040 (the browser talks to a BFF), the A3 closure (a key minted with the draft)
- **Consumed by:** F17

## Context

§E21 is one paragraph and two of its sentences are load-bearing:

> Field staff work in basements, back lanes, and dead zones. The people expected
> to upload closure evidence have the worst connectivity in the system.
> … Conflict-free sync on reconnect, made safe by server-side idempotency.

The Phase 22 gate sharpens it into two properties that are not the same
property:

1. *A complaint and a closure photo captured fully offline sync correctly on
   reconnect.*
2. **A killed app mid-upload resumes without duplicating or losing the
   submission.**

The first needs a queue. The second needs a queue that **survives process
death**, which rules out most of what a web app would reach for first.

Two prior decisions in this repository already did the hard half. `nemesis/ingest/service.py`
established that §26.1 has no natural key — *"two citizens photographing the same
pothole from the same corner within a second are two genuine reports"* — so a
repeat is recognised **only when the client says it is one**. And
`lib/api/idempotency.ts` established that the key belongs to the *submission*
rather than to the request: it is minted when the shutter fires, and every
attempt to send that draft carries the same one. The A3 closure records why the
mutation is a route handler rather than a server action, and says so in exactly
these terms: *"M11 replays a queued submission from IndexedDB after a restart,
and a server action's payload is an opaque encoding bound to a build id."*

What is left for F17 is where the draft lives between the shutter and the 202.

## Decision

**Queued submissions live in IndexedDB, in an object store keyed by the
idempotency key, and the key is the queue's primary key rather than a column
on it.**

Four consequences follow from that one sentence, and they are the design:

1. **Enqueueing twice is idempotent locally, for the same reason sending twice
   is idempotent remotely.** `put` on a keyed store is an upsert. A field hand
   who taps send twice on a dying connection writes one row, not two, and the
   row already carries the key the server will deduplicate on.
2. **The photograph is stored as a `Blob`, not as a data URL.** IndexedDB stores
   structured clones, and a `Blob` survives one — so the bytes are held by the
   browser's blob store rather than base64-inflated by a third into a string in
   the object store. On the device §E23 budgets a 2.0 s LCP for, that difference
   is the feature.
3. **A row carries its own attempt state and its own error**, so §E21's
   *"background upload with visible per-item state, so nothing fails silently"*
   is a read of the queue rather than a second structure beside it.
4. **The queue is the only writer of a submission.** The online path enqueues
   and then drains, rather than posting directly and falling back to the queue
   on failure. A code path that only executes when the network is down is a code
   path nobody tests; making it the *sole* path means the Phase 22 gate exercises
   the same code an ordinary submission takes.

**Nothing is retried automatically without a bound.** Attempts are counted and
a row that has failed non-retriably — `ApiError.retriable` is already the
distinction, and 415 will never succeed — is *parked* with its reason rather
than retried forever. §E3.3: a queue that hides a permanent failure behind an
optimistic spinner is lying to somebody standing in a basement.

## Alternatives considered

**The Background Sync API.** The obvious answer, and genuinely designed for
this. Rejected on availability: it is Chromium-only, and Safari on iOS — which
is a large share of the field devices this is for — has never shipped it. A
capability that silently does nothing on a third of the fleet is worse than no
capability, because the failure is invisible until somebody's evidence is
missing. The service worker still helps (it serves the shell offline); it is
not trusted with the queue.

**`localStorage`.** Synchronous, string-only, and about 5 MB. A closure
photograph is 1–3 MB before compression. Rejected on all three counts.

**The Cache API.** Stores `Response` objects durably and would hold the bytes
well. Rejected because a queue needs ordering, per-item state and a key that
means something, and the Cache API is a URL-keyed store with none of the three —
we would be building an index beside it and keeping the two in step.

**An in-memory queue with `beforeunload` persistence.** Rejected outright by
the gate's second clause: *a killed app*. `beforeunload` does not fire when the
operating system kills a backgrounded tab, which on a low-memory Android phone
in a basement is the *expected* way this app ends.

**Keying the store on a generated row id, with the idempotency key as a field.**
Rejected as the near-miss it is. It works, and it permits two rows for one
submission — which is precisely the state the whole idempotency argument exists
to make unrepresentable. Making the key the key makes it unrepresentable.

## Consequences

**Easy:** the gate's second clause is nearly free — a killed app reopens, reads
the store, and re-sends rows with the keys they already had; the server answers
the ones it has seen with `Idempotent-Replay: true` and the original complaint,
which the citizen surface already renders as *"your earlier report was already
filed"* rather than as a new one. Offline stops being an error state, which is
§E17.1's actual wording.

**Hard:** IndexedDB is an event-based API from another era and every call has to
be wrapped; the wrapper is `src/lib/offline/db.ts` and it exists so nothing else
has to know. Private-browsing modes and storage-pressure eviction can lose the
store, and the honest consequence is stated in the UI rather than hidden — a
queue that cannot persist says so before somebody walks into a basement relying
on it.

**Commits us to:** the idempotency key as the identity of a submission
everywhere in the system — draft, queue row, request header, server dedup key.
Four places, one value, and if any of them mints its own the property is gone.

## Revisit when

Background Sync ships in Safari and the fleet has moved, at which point the
queue does not change — only what wakes it does.
