# 0025 — Policy conditions run in a hand-written AST interpreter, never `eval`

- **Status:** Accepted
- **Date:** 2026-08-18
- **Owner:** PLT · SEC
- **Blueprint:** §15.2, §11.2
- **Related:** ADR-0006 (configuration as data), ADR-0020 (control-plane token)

## Context

Phase 6 makes routing rules tenant data: a condition, authored by a solutions
engineer through the control plane, evaluated against every complaint. The
phase's own wording is "evaluated in a sandboxed, side-effect-free evaluator",
which names the requirement without naming the mechanism.

Three mechanisms were considered.

**`eval` with `{"__builtins__": {}}`.** The obvious one, and the one with a
twenty-year history of being broken. The escape does not need builtins: from any
object reachable in the expression you walk `__class__` → `__bases__` →
`__subclasses__` until you reach something that opens a file. Every published
defence against it is a blocklist of attribute names or AST nodes, and the
history of blocklists in this specific problem is a history of them being one
construct short — a new one arrives with every Python release that adds syntax.

**An off-the-shelf expression library.** Several exist and several are good.
They were rejected on a narrower ground than quality: this expression language
has to refuse a *typo*, not only an attack. `sevrity > 7` must fail when the
draft is written, because a routing rule that silently never matches produces
complaints that are never routed, and an unrouted complaint on a queue is
indistinguishable from one nobody has picked up yet. That requires compiling
against a declared fact schema, which no general-purpose library does.

**A hand-written interpreter over a validated AST.** More code, and the code is
the point: the whole evaluator is one dispatch function that can be read in
eighty lines.

## Decision

**`nemesis/policy/expressions.py` parses with `ast.parse(mode="eval")`, refuses
every node type not on a short allowlist, and walks the survivors itself. It
never builds a code object and never calls `eval`, `exec`, or `compile`.**

The allowlist is boolean connectives, `not`, single comparisons, names,
literals, and literal collections. Notably absent, each for a stated reason the
error message repeats back to the author:

- **Calls and attribute access** — the two constructs that could reach anything
  outside the evaluator at all.
- **Arithmetic** — `severity ** 999999999` is a denial of service, and a value
  worth computing is worth naming as a fact so an approver can see it.
- **Comprehensions** — their cost depends on runtime data rather than on the
  document somebody reviewed.
- **Chained comparisons** — `a < b < c` is correct Python and is misread by a
  large fraction of readers. A policy language whose operators mean something
  other than what its readers think produces approved rules nobody understands.

Conditions are compiled against a **declared fact schema** (`ROUTING_FACTS`),
which fixes the name *and the type* of every value a condition may mention.

## The property this buys, which is the actual reason for the design

**A compiled condition cannot raise.** Evaluation is total: it returns `True` or
`False`, does no I/O, reads no clock, and consults no randomness.

That is not a nice-to-have. Routing runs inside a Celery task, on a citizen's
complaint; an exception there is not an error message, it is a report that stops
moving. Every way evaluation could fail — an unknown name, a comparison between
a string and a number, a construct with no handler — is refused at compile time
instead, which is to say at *draft* time, by the person who wrote it, while they
still have the document open.

It is also what lets §11.2's fail-safe stay "provably deterministic under policy
control", which the Phase 6 gate requires. Deterministic does not mean hardcoded
(architectural principle 2) — but it does mean the evaluator underneath has no
source of variation, and this one demonstrably has none.

## Consequences

- **The language is small, and requests to widen it will arrive.** Each one must
  be weighed against the allowlist's only real failure mode: it is not broken by
  an attacker, it is widened by a well-meaning engineer implementing a
  plausible-sounding feature. `tests/test_policy_expressions.py` carries every
  published escape technique as a parametrised case, so a widening that reopens
  one fails CI by name rather than by a general "sandbox test".
- **Absent facts compare `False`, and that has a visible wart.**
  `not (severity > 7)` is `True` when severity is unknown while `severity <= 7`
  is `False`; the two are not complements. This is the cost of collapsing
  three-valued logic at the comparison boundary, and the alternative — raising
  mid-route on a fact that legitimately does not exist yet — would drop a
  citizen's report because their photo had no EXIF. A named test pins the
  behaviour so it cannot be quietly "fixed" into the other one.
- **A second fact schema is cheap.** `FactSchema` is passed explicitly rather
  than read from a module global, so when a later phase wants conditions over a
  different vocabulary it gets its own namespace instead of inheriting routing's.
- **No regular expressions anywhere in the policy path**, including the safety
  ruleset's term matching. A tenant-authored regex is a catastrophic-backtracking
  denial of service against the stage with the *highest* retry budget in the
  pipeline — the safety check would become the thing that takes the system down.
  Whole-word and substring matching cover what a keyword list needs and run in
  linear time on input the submitter controls.
