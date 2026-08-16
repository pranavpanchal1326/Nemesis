# 0013 — Event payloads are hashed through RFC 8785 canonical JSON

- **Status:** Accepted
- **Date:** 2026-08-16
- **Owner:** PLT
- **Blueprint:** §9.3

## Context

Blueprint §9.3 hashes the payload as `json.dumps(payload, sort_keys=True)`. That
looks canonical and is not. Four ways the same logical payload produces different
bytes:

1. **`sort_keys` orders by Unicode code point**, not UTF-16 code unit. The two
   disagree for keys containing characters above the BMP.
2. **`ensure_ascii` defaults to `True`**, so non-ASCII escapes to `\uXXXX` — but
   any call site that passes `ensure_ascii=False` produces a second valid
   encoding of the same string. In a product whose primary content is Devanagari
   and Marathi, that is most of the data.
3. **Float formatting follows Python's `repr`**, which is not the ECMAScript
   form. `1e16` is `1e+16` in Python and `10000000000000000` in ECMAScript;
   `1e-7` is `1e-07` and `1e-7`. Three formatting disagreements are three ways
   for the same severity score to hash two ways.
4. **Nothing normalises Unicode.** Composed and decomposed forms of the same
   Devanagari or accented Latin string are different bytes, and browsers and
   mobile keyboards produce both.

Any one of these makes verification a coin flip: a chain that reports a break on
untouched data is worse than no chain, because the third on-call woken by it
turns the check off.

There is a fifth issue specific to this design. Payloads are stored in `jsonb`,
and Postgres **reorders keys, collapses duplicates, and stores numbers as
`numeric`**. Verification recomputes the hash from the row as read back, so the
canonical form must be a function of the *value* and not of anything about how it
was originally serialised.

## Decision

Implement RFC 8785 (JSON Canonicalization Scheme) in
`nemesis.events.canonical`: keys sorted by UTF-16 code unit, minimal string
escaping with UTF-8 output, and ECMAScript `Number::toString` formatting.
Everything hashed anywhere in the system goes through it — payloads, hash
preimages, and projected state — so "identical" means one thing.

Three deviations from the RFC, each deliberate:

- **Strings are normalised to NFC.** The RFC places normalisation out of scope
  and recommends NFC on input. Leaving it to callers means two visually identical
  Devanagari strings hash differently. Since the store persists the canonical
  form, what is written is already NFC and the read-back path re-derives
  identical bytes. Keys that collide under NFC are rejected rather than silently
  deduplicated.
- **Integers are canonicalised as doubles.** JCS defines every JSON number as an
  IEEE-754 double, so `10` and `10.0` must produce identical bytes — otherwise a
  whole-numbered severity score hashes one way on write and the other on read,
  because `jsonb` returns `numeric` and `json.loads` infers `int`.
- **Exact-precision enforcement lives at the Pydantic boundary, not here.** Event
  payload models reject integers beyond `MAX_SAFE_INTEGER`; the canonicaliser
  itself accepts anything with a finite double, because it must be able to
  re-read its own output (see below).

## What the tests found

Both deviations above were arrived at by failing tests, not by design, and the
route is worth recording because both looked correct:

1. The first version bounded integers by `MAX_SAFE_INTEGER`. But `2**53` as a
   float canonicalises to the digit string `9007199254740992`, which `json.loads`
   reads back as an `int` one past that bound — so the canonicaliser rejected a
   string it had just produced.
2. The second version tested exact double representability instead. That failed
   too: shortest-round-trip formatting prints the shortest string that *parses
   back* to the same double, not the double's exact value.
   `5.735771635482877e16` renders as `57357716354828770` while the double is
   …768. Re-reading that yields an integer no double represents exactly.

The rule that survives is: canonicalisation is a **total, stable function of the
value**, and precision policy belongs one layer up where it can reject a payload
before anything is written.

## Alternatives considered

**Use a library.** No maintained Python RFC 8785 implementation with the
NFC behaviour needed, and the module is ~200 lines with a property-tested core.
A dependency here is a supply-chain surface on the one function whose output
must be byte-stable across upgrades.

**Ban floats in payloads entirely** (integers or decimal strings only). Rejected:
trivially stable, but every scoring, confidence, and SSIM event gets an awkward
encoding and every consumer needs a decode step somebody eventually forgets on
one code path.

**Hash the raw request bytes instead of the parsed payload.** Rejected: it makes
the hash depend on client formatting, so two clients submitting the identical
complaint produce different hashes, and a payload the schema rejected could still
be hashed.

## Consequences

- Canonicalisation costs a serialisation pass per append. Sub-millisecond against
  a write path already doing a row lock and two inserts.
- The output is stable across Python, asyncpg, and Postgres upgrades, which is
  the property that matters for a log that must verify in five years.
- `jsonb` round-trip stability is asserted against a **real database**, not
  reasoned about — `test_stored_payload_recanonicalises_identically`.
- Payloads cannot contain `NaN`, infinities, unpaired surrogates, or non-JSON
  types. All are rejected at the Pydantic boundary with a clear message rather
  than inside `EventStore.append` after the caller believed the event was
  accepted.

## Revisit when

- A third party needs to verify a chain independently, at which point the
  deviations above must be published as part of the specification.
- Python gains a canonical JSON implementation in the standard library.
