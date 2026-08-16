# Incident action register

Every action item from every post-mortem, in one place, until it is done.

This file exists because of a specific, well-documented failure mode: teams
write excellent post-mortems, agree on excellent actions, and then complete
roughly none of them — because the actions live inside a document nobody opens
again. A register that is reviewed on a schedule is the only mechanism that has
been observed to change that.

## Rules

1. **An action with no owner and no due date is not an action.** It is a wish,
   and it does not go in this table.
2. **Actions are closed by evidence**, not by assertion. A link to the merged
   change, the passing test, or the runbook page — the same "prove, don't log"
   standard (§6.1) the product applies to itself.
3. **Overdue is a status, not a failure.** An action that has slipped three
   times is telling you it was mis-scoped or is not actually important; both are
   useful and both call for a decision rather than a fourth extension.
4. **Actions are typed**, because the balance matters more than the count:
   - `detect` — we would find out sooner
   - `mitigate` — we would recover faster
   - `prevent` — it would not happen again

   A register that is entirely `prevent` usually means the team is not
   investing in detection, which is what determines how bad the *next* unknown
   failure gets.

## Review cadence

Reviewed whenever a post-mortem is written, and otherwise monthly. Both numbers
are provisional and should be revisited once there is an on-call rotation to
attach the review to (Phase 1b).

## Open

| # | Incident | Action | Type | Owner | Due | Status |
|---|---|---|---|---|---|---|
| — | — | *No incidents recorded yet.* | — | — | — | — |

## Closed

| # | Incident | Action | Closed | Evidence |
|---|---|---|---|---|
| — | — | — | — | — |
