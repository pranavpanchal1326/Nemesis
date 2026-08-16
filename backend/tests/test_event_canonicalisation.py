"""Canonicalisation is the foundation of the hash chain, so it is tested as one.

Every property here is a way the chain could produce a false verification
result — either a green verification over tampered data, or a red one over
untouched data. The second is the more dangerous failure: an integrity check
that cries wolf gets disabled by the third on-call who is woken by it.

Non-ASCII appears only as ``\\uXXXX`` escapes. Several of these tests turn on
the difference between two byte sequences that render identically, and a
literal would be silently rewritten by any editor or pre-commit hook that
normalises the source file — deleting the test while leaving it green.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from nemesis.events.canonical import (
    MAX_SAFE_INTEGER,
    CanonicalisationError,
    canonicalise,
    canonicalise_to_str,
    serialise_number,
)

# ---------------------------------------------------------------------------
# ECMAScript Number::toString — the formatting RFC 8785 §3.2.2.3 defers to.
# Each of these is a case where Python's repr() disagrees with ECMAScript, or a
# boundary of the case analysis in the spec's step 5.
# ---------------------------------------------------------------------------

NUMBER_VECTORS = [
    (0.0, "0"),
    (-0.0, "0"),
    (1.0, "1"),
    (-1.0, "-1"),
    (100.0, "100"),
    (0.1, "0.1"),
    (1.5, "1.5"),
    (-2.25, "-2.25"),
    # repr gives "1e+16"; positional notation runs to 1e21 in ECMAScript.
    (1e16, "10000000000000000"),
    (1e20, "100000000000000000000"),
    # The exact point the spec switches to exponent form.
    (1e21, "1e+21"),
    # repr gives "1e-06"; positional notation runs down to 1e-6.
    (0.000001, "0.000001"),
    # repr gives "1e-07"; ECMAScript never zero-pads the exponent.
    (1e-7, "1e-7"),
    (1.23e-7, "1.23e-7"),
    # Denormal minimum and double maximum.
    (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"),
    # Largest exactly representable integer-valued double.
    (9007199254740992.0, "9007199254740992"),
    (0.30000000000000004, "0.30000000000000004"),
]


@pytest.mark.parametrize(("value", "expected"), NUMBER_VECTORS)
def test_numbers_use_ecmascript_formatting(value: float, expected: str) -> None:
    assert serialise_number(value) == expected


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_number_formatting_round_trips(value: float) -> None:
    """Formatting must never lose a bit.

    A shorter-but-lossy rendering would make two distinct severity scores hash
    identically — a collision manufactured by our own serialiser.
    """
    assert float(serialise_number(value)) == value


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_number_formatting_is_valid_json(value: float) -> None:
    """Compared as a double, because an integral rendering parses as ``int``.

    ``json.loads("57357716354828770")`` is an ``int`` that no double represents
    exactly, so an ``==`` against the original float is false even though the
    formatting is correct — the comparison, not the code, was the bug here.
    """
    assert float(json.loads(serialise_number(value))) == value


def test_nan_and_infinity_are_rejected() -> None:
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(CanonicalisationError):
            serialise_number(value)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_key_order_does_not_affect_output() -> None:
    a = canonicalise({"b": 1, "a": 2, "c": {"z": 1, "y": 2}})
    b = canonicalise({"c": {"y": 2, "z": 1}, "a": 2, "b": 1})
    assert a == b
    assert a == b'{"a":2,"b":1,"c":{"y":2,"z":1}}'


def test_keys_sort_by_utf16_code_unit_not_code_point() -> None:
    """The one case where the two orderings disagree.

    U+10000 encodes as the surrogate pair D800 DC00, so by UTF-16 code unit it
    sorts *before* U+FFFF — the opposite of code-point order, which is what
    Python's ``sorted`` and ``json.dumps(sort_keys=True)`` both use.
    """
    high_bmp = "￿"
    astral = "\U00010000"
    assert astral > high_bmp  # Python's own ordering, for contrast.

    result = canonicalise_to_str({high_bmp: 1, astral: 2})
    assert result.index(astral) < result.index(high_bmp)


def test_unicode_normalisation_is_applied() -> None:
    """Composed and decomposed forms of the same text are the same payload.

    Devanagari and accented Latin arrive in either form depending on the
    client's input method; without this, the same complaint description hashes
    two ways.
    """
    composed = "Nîmes"  # LATIN SMALL LETTER I WITH CIRCUMFLEX
    decomposed = "Nîmes"  # i + COMBINING CIRCUMFLEX ACCENT
    assert composed != decomposed

    assert canonicalise({"city": composed}) == canonicalise({"city": decomposed})
    assert canonicalise({composed: 1}) == canonicalise({decomposed: 1})


def test_keys_colliding_under_normalisation_are_rejected() -> None:
    """Silently dropping one member would keep the hash self-consistent while
    changing the payload — a tamper the chain would sign rather than catch."""
    with pytest.raises(CanonicalisationError, match="collide"):
        canonicalise({"Nîmes": 1, "Nîmes": 2})


def test_booleans_are_not_serialised_as_integers() -> None:
    assert canonicalise({"flagged": True}) == b'{"flagged":true}'
    assert canonicalise([True, 1, False, None]) == b"[true,1,false,null]"


def test_control_characters_use_the_required_escapes() -> None:
    assert canonicalise('a\nb\tc\x01d\\e"f') == b'"a\\nb\\tc\\u0001d\\\\e\\"f"'


def test_non_ascii_is_not_escaped() -> None:
    r"""\u escaping would be a second valid encoding of the same string."""
    text = "नमस्ते"  # Devanagari "namaste"
    assert canonicalise(text) == b'"' + text.encode("utf-8") + b'"'


def test_unpaired_surrogate_is_rejected() -> None:
    with pytest.raises(CanonicalisationError, match="surrogate"):
        canonicalise("bad \ud800 string")


def test_integers_beyond_the_double_range_are_rejected() -> None:
    assert canonicalise(MAX_SAFE_INTEGER) == str(MAX_SAFE_INTEGER).encode()
    assert canonicalise(2**53) == canonicalise(float(2**53))

    with pytest.raises(CanonicalisationError, match="exceeds the double range"):
        canonicalise(10**400)


def test_large_integers_and_their_float_spelling_agree() -> None:
    """Both must take the double path, or the same value hashes two ways
    depending on whether it arrived from Python or from a ``jsonb`` read."""
    assert canonicalise(2**200) == canonicalise(float(2**200))


def test_non_json_types_are_rejected_at_the_boundary() -> None:
    with pytest.raises(CanonicalisationError, match="not a JSON type"):
        canonicalise({"when": object()})


def test_non_string_keys_are_rejected() -> None:
    with pytest.raises(CanonicalisationError, match="must be strings"):
        canonicalise({1: "one"})


def test_excessive_nesting_is_rejected_rather_than_overflowing_the_stack() -> None:
    deep: Any = 1
    for _ in range(80):
        deep = [deep]
    with pytest.raises(CanonicalisationError, match="nests deeper"):
        canonicalise(deep)


def test_integer_and_float_spellings_of_one_value_agree() -> None:
    """Postgres ``jsonb`` stores both as ``numeric`` and hands back whichever
    Python type ``json.loads`` infers, so 10 and 10.0 must canonicalise alike or
    verification fails on read for every event carrying a whole-numbered score.
    """
    assert canonicalise({"score": 10}) == canonicalise({"score": 10.0})


# ---------------------------------------------------------------------------
# The property that actually matters: stability across a JSON round trip.
# ---------------------------------------------------------------------------

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**80), max_value=2**80)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=40),
    lambda children: (
        st.lists(children, max_size=6) | st.dictionaries(st.text(max_size=20), children, max_size=6)
    ),
    max_leaves=25,
)


@given(json_values)
@settings(max_examples=300)
def test_canonical_form_survives_a_json_round_trip(value: Any) -> None:
    """Encode, decode, canonicalise again — the bytes must not move.

    This is the cheap proxy for the ``jsonb`` round trip the store performs on
    every verification; the live-database version of the same assertion is in
    ``test_event_store.py``.
    """
    try:
        first = canonicalise(value)
    except CanonicalisationError:
        # Generated keys that collide under NFC are rejected by design; that
        # behaviour has its own test above.
        assume(False)
        raise

    reloaded = json.loads(first.decode("utf-8"))
    assert canonicalise(reloaded) == first
