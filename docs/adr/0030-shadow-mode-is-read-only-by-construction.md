# ADR-0030 — Shadow mode is read-only by construction, on its own transaction

**Status:** Accepted
**Date:** 2026-08-19
**Phase:** 7 — Configuration simulation & backtesting
**Owning function:** DATA

## Context

Phase 7's exit gate says *shadow mode provably cannot mutate state or emit
domain events*. The word doing the work is **provably**, and it rules out the
implementation everybody writes first: a shadow path that simply never calls a
write function.

That is a property of the code as it stands today, held in place by nothing. It
survives until somebody adds a counter, a cache warm, a metric row, or a "record
what we saw" line to a helper three frames down — and the first symptom of it
not surviving is a duplicated event on a citizen's complaint chain, which is the
one thing §9.1 forbids outright.

It also rules out the test everybody writes first: run shadow mode, assert the
complaint table is unchanged. That proves the code did not write *today*.

## Decision

**Two independent enforcement layers, on a transaction that shadow mode owns.**

**Layer one — Postgres.** `SET TRANSACTION READ ONLY`. The database refuses
`INSERT`, `UPDATE`, `DELETE` and DDL for the rest of the transaction, including
from raw `text()` SQL and including from code that does not know it is running
inside a shadow evaluation.

**Layer two — a statement guard.** A `before_execute` listener in the shape
`tenancy.guard` already uses, refusing the write *before* the database is
touched so the traceback names the offending statement instead of surfacing an
asyncpg `ReadOnlySqlTransactionError` several frames away.

Neither is redundant, and the test suite proves each **with the other absent**:
one test issues an ORM insert (the guard catches it, and Postgres never sees
it); another issues raw SQL the guard explicitly cannot parse (Postgres catches
it). Two layers only ever tested together are one layer with a spare.

**The scope runs on its own session, borrowed from the caller's engine.** This
is the part that was learned rather than designed. The first implementation
marked the *caller's* transaction read-only, and in Postgres that is a one-way
door: `SET TRANSACTION READ WRITE` after the first query fails with *"transaction
read-write mode must be set before any query"*. The caller could therefore never
write again, and the failure arrived at some unrelated statement much later. The
tests for the recording half caught it.

So `read_only(session)` opens a session on the same engine, whose transaction
begins clean — making `SET TRANSACTION READ ONLY` unambiguously legal as its
first statement — and rolls it back on the way out. The caller's session is
untouched.

**Recording is outside the scope.** `observe` returns *values*;
`record` writes them on the caller's session into `shadow_observations`, a table
no decision path reads and no projector touches. Doing it inside would require
an exemption, and an exemption is a hole with a comment next to it.

## Consequences

**Good**

- The guarantee holds over the *whole* evaluation — the bundle reads, the corpus
  fold, the calendar loads — rather than over the part somebody remembered.
- A helper added next year that writes fails loudly on its first execution
  instead of quietly becoming a second writer.
- Divergence is stored in full; agreement is counted. Storing both outcomes for
  every observation would make this the largest table in the system within a
  week, for information the digests already carry.

**Costs, accepted**

- **`observe` reads committed data.** It is not in the caller's transaction, so
  a caller that has just written the candidate must commit before observing
  against it. For shadow mode this is the correct reading — an observation
  should describe what other readers can see — but it is a real constraint and
  it is documented at both the module and the function.
- One extra pooled connection per shadow run.
- `AsyncSession.get_bind()` returns the *synchronous* `Engine`, so the scope
  re-wraps it in an `AsyncEngine` to reach the same pool. That is a small piece
  of SQLAlchemy plumbing sitting in a module about guarantees, and it is
  commented where it lives.

**Deliberately not done**

- **No sampling.** A shadow runner watching one complaint in ten would report a
  divergence rate with a confidence interval nobody computed, and the sampling
  would be invisible in the output. If shadow mode is too expensive for full
  traffic, the honest fix is fewer *candidates*, not fewer citizens' reports.
- **No feedback.** An observation that fed back into a decision would make shadow
  mode a staged rollout with no approval step. A test walks the import graph to
  assert that nothing in `policy` or `pipeline` imports `ShadowObservation`.
- **Backtest runs are not read-only-scoped.** A backtest legitimately writes its
  run row and its certificate; it is not a shadow evaluation, and wrapping it
  would mean exempting the writes that are its purpose.

## References

- `nemesis/simulation/readonly.py` — both layers, and why the scope owns its transaction
- `nemesis/simulation/shadow.py` — `observe` / `record`, and the kill switch
- `tests/test_simulation_shadow.py` — each layer tested with the other absent
- ADR-0014 — the tenancy guard, whose two-layer shape this follows
