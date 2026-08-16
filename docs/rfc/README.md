# RFCs

An **ADR** records a decision that was already made, so nobody reverse-engineers
the reasoning later. An **RFC** is the argument *before* the decision, written
down so it can be had once, in one place, by everyone it affects.

The split matters because they fail differently. A codebase with no ADRs loses
its reasoning. A codebase with no RFCs makes cross-track decisions in a corner
and discovers the objection during integration, when it is expensive.

Most RFCs end by producing an ADR. That is the intended lifecycle, not a
redundancy: the RFC holds the debate and the alternatives in full, and the ADR
holds the conclusion in a form somebody will actually read in a hurry.

## When to write one

Write an RFC when a change:

- **crosses tracks** — anything touching two of Platform, Control Plane,
  Intelligence, Accountability, Experience, Data, Trust, or Commercial;
- **changes a published contract** — an event schema, an API version, a
  webhook payload, the tenancy model;
- **changes an engineering standard** in `docs/PHASES.md`, or weakens a gate;
- **is expensive to reverse** — a datastore, a framework, a protocol, a
  deployment target;
- **affects a safety, privacy, or accountability guarantee**, however small it
  looks. §11.2's fail-safe, §22's privacy controls, and §9.3's hash chain each
  carry a promise that a local optimisation can quietly break.

Do **not** write one for work that is fully inside one track and reversible by
one person in an afternoon. An RFC process applied to routine work teaches
people to route around the RFC process.

The honest test: *if someone objected to this six weeks from now, would we have
to undo work?* If yes, the objection is cheaper today.

## Lifecycle

```
Draft ──▶ Review ──▶ Accepted ──▶ Implemented
             │
             ├──▶ Rejected     (kept — the rejected option is the useful part)
             └──▶ Withdrawn    (kept — the reason it stopped mattering is data)
```

| Status | Meaning |
|---|---|
| `Draft` | Being written. Not yet asking for anyone's time |
| `Review` | Open for comment, with a stated closing date |
| `Accepted` | Agreed. Implementation may start; an ADR is written if the decision constrains future work |
| `Rejected` | Not proceeding, with the reasoning recorded |
| `Withdrawn` | The author stopped pursuing it, with the reason recorded |
| `Implemented` | Shipped. Links to the ADR and the phase that carried it |

**Nothing is ever deleted, including rejected RFCs.** The rejected alternative is
frequently the more valuable artefact — the next person to propose it arrives
with the counter-argument already in hand rather than re-running the debate.

## Review, at current team size

A review needs a **closing date** and at least one reader from each affected
track. With a small team that is often one person, and an RFC reviewed by its
own author is a diary entry rather than a review — so:

- If nobody else can review it, say so in the RFC, in the Review-notes section.
  A decision made alone and *labelled* as made alone is honest; one presented as
  reviewed is not.
- Silence is not approval. If the closing date passes with no comment, the
  status becomes `Draft` again, not `Accepted`.

## Numbering

Four digits, sequential, allocated when the file is created:
`docs/rfc/0001-short-kebab-title.md`. Numbers are never reused, including for
rejected and withdrawn RFCs — a stable identifier is the point.

## Template

Copy [0000-template.md](0000-template.md).

## Index

| # | Title | Status | Owner | ADR |
|---|---|---|---|---|
| — | *No RFCs yet.* | — | — | — |

## Related

- [ADRs](../adr/README.md) — decisions already made
- [PHASES.md](../PHASES.md) — the plan an RFC would be amending
- [Incident process](../incidents/README.md) — where an RFC sometimes originates
