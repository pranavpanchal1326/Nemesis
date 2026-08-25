# 0044 — The receipt carries the chain head; the polled read deliberately does not

- **Status:** Accepted
- **Date:** 2026-08-25
- **Owner:** PLT · PROD
- **Blueprint:** §9.3, §26.1, §26.2, §27.3 · §E17.3, §E26

## Context

§E17.3 specifies the receipt a citizen is handed after submitting:

> A document, deckled, carrying the complaint id and chain hash and the
> append-only sentence. **Nobody reads the hash. Everybody feels that this system
> keeps records.**

The second sentence is the design and the first is the honest admission that goes
with it. The hash is not a feature a citizen uses; it is the thing that makes the
document's claim — *this record cannot be edited* — a statement about the system
rather than about the typography.

Neither half existed. `ComplaintSubmissionResponse` published `complaint_id`,
`status` and `estimated_processing_time_seconds`, and **no endpoint exposed an
entity's chain head at all**. `<Receipt>` was built to take the hash as an
optional prop and to render *nothing* where it would go rather than a placeholder
(§E3.3), so the omission was visible — but it was still an omission.

Two questions had to be answered, and only the first is obvious.

**Which hash?** The event's, or the entity's chain head? The receipt's claim is
about the record as a whole, so it wants the head.

**Where does it live?** This is the one that decides the design, and the obvious
answer is wrong.

## Decision

**The chain head goes on the 202 submission response and on the history endpoint.
It does not go on `GET /complaints/{id}`.**

### Why not on the read path, which is where a field like this belongs

`GET /complaints/{id}` carries an ETag, and the ETag is the projection's
`version` — *"the log position the row reflects"*. That is a deliberate choice
with a stated reason: §27.3 turns this endpoint into a five-second poll per
client whenever the socket is unavailable, and a conditional request is what
keeps that affordable.

The head and the version do not advance together.

`version` advances when the **projection** changes. The head advances on **every
append**, including events that leave the projection untouched — `review_queued`,
`abuse_pattern_flagged`, `exif_check_completed`, a degradation record. So there
is a routine, ordinary window in which the chain has moved and `version` has not.

A client polling with `If-None-Match` gets a 304 in that window. If the head were
a field on that response, the client would be holding a hash that is already
wrong, served under a validator asserting it is current, on a document whose
whole claim is that the record is intact. **A stale hash on a document that says
"this cannot be edited" is worse than no hash**, because it fails in the exact
direction the hash exists to make impossible: it looks like proof and isn't.

The fixes available were each worse than not putting it there:

- Derive the ETag from the head instead. That defeats the conditional request —
  every append re-renders a full projection body for every polling client, which
  is the cost §27.3's five-second fallback cannot afford.
- Carry two validators. There is one `If-None-Match`.
- Serve it uncached on the hot path. That is the five-second poll paying an
  extra `event_chain_heads` lookup per client, forever, for a value the receipt
  needs exactly once.

### Where it does go

**`POST /complaints` → `chain_hash`.** At that instant the complaint's chain is
exactly one event long, so the head *is* `complaint_submitted`'s own
`event_hash` — a value `EventStore.append` already computed under the head lock
and already returns. The receipt costs no query.

On an idempotent replay the value is the head as it stood when the **original**
submission landed. That is correct rather than a limitation: a client retrying
after a timeout must be handed the same receipt it would have received the first
time, or the retry has produced a second document claiming to attest one report.

**`GET /complaints/{id}/events` → `chain_head` + `chain_head_sequence`.** The live
head, uncached (`Cache-Control: no-store`), beside the rows it summarises. This
is where a citizen — or an auditor holding the receipt — checks that the document
in their hand still describes the record.

It is returned **as its own field rather than read off the last row**, because
those are equal only while the whole history fits in one page. Inferring it from
the tail is correct until a complaint has more events than `limit`, which is the
worst kind of wrong: it works in every test and fails on the complaint that
mattered enough to accumulate a history.

## Alternatives considered

**Publish the event hash of `complaint_submitted` and call it the receipt hash.**
Rejected: it attests one row. The receipt's sentence is about the record. A
system that edited event four would leave that hash untouched.

**Sign the receipt rather than hashing it.** A signature proves the platform
issued the document; the chain proves the platform cannot have rewritten what the
document points at. They answer different questions and the second is the one
§E17.3 asks. A signature is worth revisiting when there is a key management
story; the chain needs none.

**Expose a dedicated `GET /complaints/{id}/chain-head`.** Rejected as a route
whose only caller would immediately also want the rows. It is one field on the
history response instead.

## Consequences

- `SubmissionReceipt.chain_hash` is required, not optional. A submission path
  that cannot state the head is a submission path that cannot issue a receipt.
- `<Receipt>` (§E26) renders the hash for real. Its no-placeholder behaviour
  stays, because Phase 22's offline queue will produce receipts before a round
  trip has happened.
- The polled read is unchanged, which was the point. Nothing about the five-second
  fallback got more expensive.
- `api_contract_lock.json` gains a required field on the 202 and a new locked
  path; both were re-locked deliberately, per ADR-0022's standard.

## Revisit when

- **The projection ever advances `version` on every append.** It does not today
  and should not — most events change nothing a reader sees — but if it did, the
  argument above dissolves and the head belongs on the read path.
- **Retention detaches a partition** (§22.4). A chain whose early events have been
  archived still verifies from the head down to the archive boundary, and the
  history endpoint will need to say where that boundary is rather than returning
  a short chain that looks complete.
