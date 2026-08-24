# 0039 — An unverified flag is fluorescent pink and hatched, and is never rendered in red

- **Status:** Accepted
- **Date:** 2026-08-24
- **Owner:** PROD · SEC
- **Blueprint:** §16.1, §16.4, §17, §22.2 · §E9.4, §E19.6, §E26

## Context

Phase 17's detectors produce statements about **named commercial entities** —
cost-variance outliers, award concentration, repeat defects, shared directors.
§16.1 requires every one of them to display as "system-flagged, unverified, under
human review", and §22.2 explains why: a system-derived figure about a named
entity, published without its provenance stated, is an assertion, and an
assertion about a named business is a defamation surface.

The compliance requirement is therefore about *text*. This ADR is about the
thing the compliance requirement does not cover, which is that **colour is read
before text is**.

The obvious colour for a flag is red. It is what every dashboard reaches for. Two
things make it the wrong answer here, and neither is obvious enough to survive a
future contributor "fixing" the palette:

1. **Red is already taken.** §E9.4 assigns oxide red to `sev-critical`. If a detector output is also red, a critical pothole and an unproven fraud signal look the same at a glance, and the vocabulary means nothing (§E3.4).
2. **Red asserts.** Colour-coding an unproven allegation as an emergency communicates certainty that the disclaimer text immediately below it then denies. The screenshot that circulates will be of the colour, not the caption.

## Decision

**Flagged and unverified content renders in `riso-flu-pink` (#FF48B0) on a 45°
hatch, and in nothing else.**

- No detector output may render in any severity colour, ever.
- `<FlaggedNotice>` takes `disclaimer` and `responseHref` as **required props** and cannot be constructed without them (§E26). The disclaimer and the appeal path are not optional decoration on the component; they are the component.
- The hatch is a fill pattern, not only a colour, so the distinction survives grayscale printing and colour-blind reading.
- Red remains reserved exclusively for `sev-critical`.

## Alternatives considered

**Red, like every other dashboard.** Rejected for the two reasons above. The
collision with severity alone would be sufficient; the §22.2 exposure makes it
disqualifying.

**Neutral grey, so the UI asserts nothing.** Accurate, and rejected as unusable.
An officer scanning a hundred rows will not find a grey signal, and a fairness
mechanism nobody sees is not a fairness mechanism. §E3.3 requires honesty to be
*rendered*, which means visible.

**Amber, as a middle position between neutral and alarming.** Rejected because
amber is `sev-high`. The same collision, one step down.

**Colour alone, without the hatch.** Rejected because §E19.7 establishes that
officers print reports, and a printed case file is frequently grayscale. A flag
that disappears in a photocopy is a flag that gets missed in exactly the setting
where it matters most.

**Let the tenant configure the flag colour, per the configuration-over-code
principle.** Rejected. §22.2 is a legal posture, not a preference, and the
argument for configurability does not extend to a control whose purpose is to
prevent the product from making an assertion it cannot defend. A tenant may
configure detector thresholds; it may not configure away the visual distinction
between an allegation and a fact.

## Consequences

**Easy:** a flag and a severity are never confusable — on screen, in print, or to
a colour-blind reader. §16.4's requirement that the appeal path ship alongside the
accountability feature becomes structurally impossible to omit, because the
component will not compile without it.

**Hard:** fluorescent pink is unusual in a government interface and will be
questioned by every new reviewer. This ADR is the answer to that question, and it
is why the answer must be written down rather than defended from memory.

**Commits us to:** keeping fluorescent pink out of every other role in the
palette. The moment it decorates something, it stops meaning "unverified".

## Revisit when

Legal review changes §22.2's posture on system-derived statements about named
entities; or usability testing shows the hatch is being read as decorative rather
than as provisional — in which case the treatment changes, but the prohibition on
red does not.
