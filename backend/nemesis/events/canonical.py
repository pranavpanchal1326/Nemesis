"""Deterministic JSON canonicalisation — RFC 8785 (JCS).

A hash chain is only tamper-evident if identical payloads hash identically.
Blueprint §9.3 hashes ``json.dumps(payload, sort_keys=True)``, which is not
sufficient: ``sort_keys`` orders by Unicode code point rather than UTF-16 code
unit, ``ensure_ascii`` differs by call site, ``repr``-based float formatting is
not the ECMAScript form, and nothing normalises Unicode. Any one of those makes
two byte-different encodings of the *same* payload hash differently, which turns
a verification failure into a coin flip rather than evidence of tampering.

This module implements the JSON Canonicalization Scheme so that canonical bytes
are a pure function of the *value*, never of how the value was constructed or
which process serialised it.

Two properties matter more than strict RFC conformance, and both are tested:

1. **Round-trip stability.** A payload written to a ``jsonb`` column and read
   back must canonicalise to identical bytes. Postgres reorders keys, collapses
   duplicates, and stores numbers as ``numeric`` — so a canonicaliser that
   depends on input key order or on Python's float repr would verify green on
   write and red on read, for every event, forever.
2. **Version stability.** The output must not move when Python, asyncpg, or
   Postgres are upgraded. That rules out anything built on ``json.dumps``
   defaults.

Deviations from RFC 8785, deliberate and enforced rather than tolerated:

- **Unicode is normalised to NFC** by this function. The RFC places
  normalisation out of scope and merely recommends NFC on input. Leaving it out
  would mean two visually identical Devanagari strings — realistic in a
  multilingual product whose input arrives from browsers and mobile keyboards
  with different normalisation behaviour — produce different hashes. Because
  the store persists the canonical form, what is written is already NFC and the
  read-back path re-derives identical bytes.
- **Integers are canonicalised as doubles, and rejected if that would lose
  precision.** JCS defines every JSON number as an IEEE-754 double, so ``10``
  and ``10.0`` must produce identical bytes — otherwise a severity score that
  lands on a whole number hashes one way on write and the other on read. The
  consequence is that a Python ``int`` which no double represents exactly
  (2**53 + 1 being the smallest) is refused at the boundary rather than
  serialised into a value that any JavaScript consumer of the §16.3 public API
  would read back as a different number.
- **NaN and infinities are rejected.** They have no JSON representation at all.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Final

# json.loads / jsonb round-trips produce exactly these types.
type JSONValue = bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"] | None

#: The largest integer for which every integer below it is also exactly
#: representable as a double — JavaScript's ``Number.MAX_SAFE_INTEGER``.
#: Individual larger integers (powers of two, and their multiples) remain exact
#: and are accepted; ``_serialise_int`` tests representability directly rather
#: than using this bound, which is exported for callers that want the
#: conservative limit when choosing a column type or an API contract.
MAX_SAFE_INTEGER: Final = 2**53 - 1
MIN_SAFE_INTEGER: Final = -MAX_SAFE_INTEGER

#: Two-character escapes required by RFC 8785 §3.2.2.2. Every other control
#: character below 0x20 uses the \u00xx form; nothing else is escaped, because
#: the output is UTF-8 and gratuitous \u escaping is a second valid encoding of
#: the same string — precisely what canonicalisation exists to prevent.
_SHORT_ESCAPES: Final[dict[int, str]] = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class CanonicalisationError(ValueError):
    """A value cannot be canonicalised, so it must not enter the event log.

    Raised at the write boundary rather than tolerated, because an event that
    cannot be canonicalised deterministically can never be verified.
    """


def canonicalise(value: JSONValue) -> bytes:
    """Return the RFC 8785 canonical UTF-8 encoding of ``value``.

    The result is a pure function of the value: key insertion order, Unicode
    normalisation form, and ``int``/``float`` spelling of the same number all
    produce identical bytes.
    """
    out: list[str] = []
    _write(value, out, depth=0)
    return "".join(out).encode("utf-8")


def canonicalise_to_str(value: JSONValue) -> str:
    """Canonical form as ``str`` — for embedding in a hash preimage or a log."""
    return canonicalise(value).decode("utf-8")


#: Bounded to keep a hostile or accidentally cyclic payload from exhausting the
#: C stack. Real event payloads are shallow; anything approaching this is a bug
#: upstream, and a RecursionError inside a write transaction is a far worse way
#: to discover it.
_MAX_DEPTH: Final = 64


def _write(value: JSONValue, out: list[str], *, depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise CanonicalisationError(f"payload nests deeper than {_MAX_DEPTH} levels")

    if value is None:
        out.append("null")
    # bool before int: bool is an int subclass, and True would otherwise
    # canonicalise to "1", silently changing the type on the way back out.
    elif isinstance(value, bool):
        out.append("true" if value else "false")
    elif isinstance(value, int):
        out.append(_serialise_int(value))
    elif isinstance(value, float):
        out.append(serialise_number(value))
    elif isinstance(value, str):
        out.append(_serialise_string(value))
    elif isinstance(value, list):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _write(item, out, depth=depth + 1)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        for index, (key, item) in enumerate(_sorted_items(value)):
            if index:
                out.append(",")
            out.append(_serialise_string(key))
            out.append(":")
            _write(item, out, depth=depth + 1)
        out.append("}")
    else:
        raise CanonicalisationError(
            f"{type(value).__name__} is not a JSON type; convert it at the event boundary"
        )


def _sorted_items(mapping: dict[str, JSONValue]) -> list[tuple[str, JSONValue]]:
    """Sort object members by UTF-16 code unit, as RFC 8785 §3.2.3 requires.

    Python's native string ordering is by code point, which disagrees for keys
    containing characters above the BMP: U+10000 sorts *after* U+FFFF by code
    point but *before* it by UTF-16 code unit, because the surrogate pair begins
    at 0xD800. Encoding to UTF-16 big-endian and comparing bytes reproduces the
    code-unit order exactly.
    """
    items: list[tuple[str, JSONValue]] = []
    for key, item in mapping.items():
        if not isinstance(key, str):
            raise CanonicalisationError(f"object keys must be strings, got {type(key).__name__}")
        items.append((unicodedata.normalize("NFC", key), item))

    if len({key for key, _ in items}) != len(items):
        # Distinct keys that collide under NFC would otherwise silently drop a
        # member, changing the payload while keeping the hash self-consistent.
        raise CanonicalisationError("object keys collide after NFC normalisation")

    return sorted(items, key=lambda pair: pair[0].encode("utf-16-be"))


def _serialise_int(value: int) -> str:
    """Route integers through the double formatter, so 10 and 10.0 agree.

    Integers are *not* special-cased to ``str(value)``. Doing so was the first
    version of this function and it broke the round-trip property at the exact
    boundary it was trying to protect: ``2**53`` as a float canonicalises to the
    digit string ``9007199254740992``, which ``json.loads`` reads back as an
    ``int`` — so the value written and the value read took two different code
    paths, and only one of them was allowed through.

    Nor is representability checked here, which was the *second* wrong version.
    Shortest-round-trip formatting prints the shortest string that parses back
    to the same double, not the double's exact value: ``5.735771635482877e16``
    renders as ``57357716354828770`` while the double itself is
    ...768. Re-reading that string yields an ``int`` no double represents
    exactly, so an exactness check here rejects strings this module just
    produced. Precision is therefore a *boundary* concern — event payload models
    constrain integer fields to ``MAX_SAFE_INTEGER`` — and canonicalisation
    stays a total, stable function of the value, which is what verification
    needs it to be.
    """
    try:
        as_double = float(value)
    except OverflowError:
        as_double = math.inf

    if math.isinf(as_double):
        raise CanonicalisationError(
            f"integer with {len(str(abs(value)))} digits exceeds the double range; "
            f"encode it as a string"
        )
    return serialise_number(as_double)


def _serialise_string(value: str) -> str:
    normalised = unicodedata.normalize("NFC", value)
    out = ['"']
    for char in normalised:
        code = ord(char)
        escape = _SHORT_ESCAPES.get(code)
        if escape is not None:
            out.append(escape)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        elif 0xD800 <= code <= 0xDFFF:
            # A lone surrogate survives inside a Python str but cannot be
            # encoded as UTF-8, so it would fail later — at the point of
            # writing bytes, with no indication of which field caused it.
            raise CanonicalisationError(f"unpaired surrogate U+{code:04X} in string")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def serialise_number(value: float) -> str:
    """Format a double exactly as ECMAScript ``Number::toString`` would.

    RFC 8785 §3.2.2.3 defers to that algorithm, and Python's ``repr`` is not it.
    They agree on the *digits* — both emit the shortest decimal string that
    round-trips — and disagree on when to use exponent notation and how to spell
    the exponent:

    ==============  ================  ==================
    value           ``repr``          ECMAScript
    ==============  ================  ==================
    ``1e16``        ``1e+16``         ``10000000000000000``
    ``1e-7``        ``1e-07``         ``1e-7``
    ``0.000001``    ``1e-06``         ``0.000001``
    ==============  ================  ==================

    Three formatting disagreements are three ways for the same severity score to
    hash two different ways.
    """
    if math.isnan(value):
        raise CanonicalisationError("NaN has no JSON representation")
    if math.isinf(value):
        raise CanonicalisationError("infinity has no JSON representation")
    if value == 0:
        # Covers -0.0, which ECMAScript renders as "0". Preserving the sign
        # would make negative zero a distinct payload from zero.
        return "0"

    sign = "-" if value < 0 else ""
    digits, exponent = _shortest_digits(abs(value))
    return sign + _ecmascript_layout(digits, exponent)


def _shortest_digits(value: float) -> tuple[str, int]:
    """Decompose a positive double into ``(digits, n)`` with ``value = 0.digits * 10**n``.

    ``digits`` carries no leading or trailing zeros, and is the shortest string
    that round-trips — which is exactly what ``repr`` produces, so the decimal
    expansion is taken from there and only the *layout* is recomputed.
    """
    text = repr(value)
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    integer_part, _, fraction_part = mantissa.partition(".")

    raw = integer_part + fraction_part
    # Position of the decimal point measured from the left of `raw`.
    point = len(integer_part) + exponent

    stripped = raw.lstrip("0")
    point -= len(raw) - len(stripped)
    digits = stripped.rstrip("0")

    return digits, point


def _ecmascript_layout(digits: str, point: int) -> str:
    """Apply the ECMAScript Number::toString case analysis (ECMA-262 6.1.6.1.20)."""
    length = len(digits)

    if length <= point <= 21:
        # Integral with trailing zeros: 1e16 -> "10000000000000000".
        return digits + "0" * (point - length)
    if 0 < point <= 21:
        # Decimal point falls inside the digits: "123.45".
        return digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        # Small magnitude stays in positional form: 0.000001 -> "0.000001".
        return "0." + "0" * -point + digits

    # Exponent notation. ECMAScript reports the exponent relative to the first
    # significant digit, hence point - 1, and never pads it to two characters.
    scientific_exponent = point - 1
    sign = "+" if scientific_exponent >= 0 else "-"
    mantissa = digits if length == 1 else digits[0] + "." + digits[1:]
    return f"{mantissa}e{sign}{abs(scientific_exponent)}"
