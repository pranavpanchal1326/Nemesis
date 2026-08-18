# 0026 — Policy rollback creates a new revision; it never re-activates an old row

- **Status:** Accepted
- **Date:** 2026-08-18
- **Owner:** PLT
- **Blueprint:** §9.1, §13.3, §27.2
- **Related:** ADR-0006 (configuration as data), ADR-0010 (event hash preimage)

## Context

Phase 6 requires "safe rollback to any prior version". The obvious
implementation is the one every configuration system reaches for first: find the
row for revision 3, set its status back to `active`, set the current one to
something else, done. One UPDATE, no new data, and the operator sees exactly the
version number they asked for.

It was rejected, and the reason only becomes visible when you ask the question
this table exists to answer.

Every scored complaint records the policy version that scored it. Six months
later a citizen disputes a severity, or an auditor asks why two identical
reports were scored differently in March. The query is *which document was
deciding at this instant* — an interval lookup over `effective_from` and
`effective_until`.

Re-activating revision 3 breaks that query and does so silently. Revision 3 now
has two live intervals with a gap, or one interval that spans a period when it
was not live at all, depending on how the timestamps are handled. Neither can be
expressed in two columns. "Which policy was live on 14 March" stops being a
query and becomes a reconstruction from the event log — which is possible, and
is exactly the kind of possible-but-nobody-will that turns an evidence trail
into a decoration.

## Decision

**Rolling back to revision *N* creates a new revision carrying revision *N*'s
body, with `rolled_back_from_id` pointing at *N*. The revision sequence only
ever increases, and no row's status is ever moved backwards.**

- `effective_from`/`effective_until` therefore form a partition of time with no
  overlaps and no gaps, and `version_effective_at()` is a single indexed query
  with exactly one answer.
- The new revision walks the **full lifecycle** — draft, submit, approve,
  activate — writing all four events, rather than being inserted directly as
  `active`. A shortcut would be a second way for a document to become live, and
  "does one exist" is the first question an auditor asks.
- Rollback is nonetheless a **single API call**, because it happens during an
  incident. The lifecycle is walked automatically; what the operator supplies is
  a target revision and a reason.

## Who approved it, and the guard that makes skipping review safe

The rollback path does not stop for a second reviewer. Requiring one at 3am,
while a rubric is scoring everything at 10 or a routing rule is sending all work
to one team, means the real remedy becomes "edit the database" — and a system
whose documented emergency procedure is worse than its undocumented one has no
emergency procedure.

Two things make that safe:

1. **The target must have been approved.** `rollback` refuses a revision with no
   `approved_at`. The content being restored was signed off once, by a human, so
   nothing enters production that no human ever reviewed. Rolling back to a
   draft is not an emergency shortcut, it is a way to ship unreviewed content
   through the emergency door, and it is refused.
2. **The approver recorded is whoever pressed rollback**, not whoever approved
   the original. They are the one putting this content into production now, and
   attributing it to someone who has not been near the system since March would
   be a comfortable fiction in the one record that exists to prevent them.
   `rolled_back_from_id` is the link to the original approval for anyone who
   needs it.

## Consequences

- **Revision numbers grow faster than "number of distinct policies".** A tenant
  that rolls back and forward three times has six revisions of two documents.
  That is the correct reading: six things were live, one after another, and each
  scored some complaints.
- **Revision numbers are never reused, including by rejected drafts.** A
  rejected draft consumes its number permanently — reusing it would make two
  different documents share a stamp, and `severity_scored.policy_version` would
  resolve to whichever survived.
- **`content_hash` is how "is this the same document as before" is answered**,
  not the revision number. Two revisions with identical hashes are byte-identical
  documents, which is exactly what a rollback produces and what a diff view
  should render as "restored" rather than as a change.
- **Storage grows with edits rather than with documents.** A severity rubric is
  a few kilobytes and a tenant revises one a handful of times a year. If a later
  phase ever finds this material, the answer is archival of superseded bodies,
  not deletion of rows — the row is the interval, and the interval is the
  evidence.
