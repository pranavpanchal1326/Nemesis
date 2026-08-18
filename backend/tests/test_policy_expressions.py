"""The routing condition sandbox — the module where being wrong is a breach.

No database in this file. ``compile_condition`` and ``Condition.evaluate`` are
pure functions, and the properties that matter are properties of the language:
what it refuses, and that anything it accepts cannot raise. Both are far better
hammered directly than through three layers of async plumbing.

The escape attempts below are not decoration. Every one of them is a published
technique against a Python expression sandbox, and the reason they are here
rather than in a comment is that the defence is an *allowlist*: the failure mode
of an allowlist is somebody widening it for a plausible-sounding feature, and
these are what fail when they do.
"""

from __future__ import annotations

import contextlib

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from nemesis.policy.errors import ExpressionError, ExpressionLimitError
from nemesis.policy.expressions import (
    MAX_COLLECTION_MEMBERS,
    MAX_EXPRESSION_CHARS,
    ROUTING_FACTS,
    FactKind,
    compile_condition,
)

# ---------------------------------------------------------------------------
# What the language refuses
# ---------------------------------------------------------------------------

#: Every published route out of a Python expression sandbox, plus the constructs
#: that would let one be built. Parametrised rather than looped so a regression
#: names the exact technique that started working again.
ESCAPE_ATTEMPTS = [
    pytest.param("__import__('os').system('id')", id="import-builtin"),
    pytest.param("open('/etc/passwd')", id="open-builtin"),
    pytest.param("category.__class__", id="attribute-on-a-fact"),
    pytest.param("(1).__class__.__bases__[0].__subclasses__()", id="subclasses-walk"),
    pytest.param("().__class__.__mro__", id="mro-walk"),
    pytest.param("[x for x in tags]", id="comprehension"),
    pytest.param("{x for x in tags}", id="set-comprehension"),
    pytest.param("(x for x in tags)", id="generator"),
    pytest.param("lambda: 1", id="lambda"),
    pytest.param("f'{category}'", id="f-string"),
    pytest.param("category[0] == 'a'", id="subscript"),
    pytest.param("severity + 1 > 5", id="arithmetic"),
    pytest.param("severity ** 999999999 > 1", id="exponent-dos"),
    pytest.param("(severity := 5) > 1", id="walrus"),
    pytest.param("severity if severity else 1", id="conditional-expression"),
    pytest.param("{'a': 1} == {'a': 1}", id="dict-literal"),
    pytest.param("'x' * 10 == 'y'", id="string-repetition-dos"),
]


@pytest.mark.parametrize("source", ESCAPE_ATTEMPTS)
def test_every_known_sandbox_escape_is_refused(source: str) -> None:
    """The allowlist holds against every technique this file knows about.

    A failure here is not a style regression. It means an expression an operator
    can type into a policy document reaches something outside the evaluator, and
    the operator does not have to be malicious for that to be a breach — a
    pasted snippet is enough.
    """
    with pytest.raises(ExpressionError):
        compile_condition(source)


def test_the_refusal_names_the_construct_and_its_remedy() -> None:
    """An author who reads "invalid expression" edits at random.

    Message quality is a correctness property here: the person writing a routing
    rule is a solutions engineer, not a Python programmer, and a rejection that
    does not say what to do instead produces a support ticket rather than a
    fixed rule.
    """
    with pytest.raises(ExpressionError, match="arithmetic is not available"):
        compile_condition("severity + 1 > 5")
    with pytest.raises(ExpressionError, match="attribute access is not available"):
        compile_condition("category.upper")


def test_an_unknown_fact_is_refused_with_the_available_names() -> None:
    """A typo must fail at draft time, not become a rule that never matches.

    The silent version of this is the worst failure Phase 6 can produce: a
    complaint that is never routed is indistinguishable on a queue from one
    nobody has picked up yet, so the outage has no symptom until someone asks
    why a department is quiet.
    """
    with pytest.raises(ExpressionError) as caught:
        compile_condition("sevrity > 7")
    message = str(caught.value)
    assert "'sevrity' is not a fact" in message
    assert "severity" in message, "the message must list what was available"


def test_comparing_across_kinds_is_refused() -> None:
    with pytest.raises(ExpressionError, match="cannot compare number with string"):
        compile_condition("severity > 'high'")


def test_ordering_strings_is_refused() -> None:
    """``"b" > "a"`` is true in Python and meaningless in a routing rule."""
    with pytest.raises(ExpressionError, match="orders numbers only"):
        compile_condition("category > 'roads'")


def test_a_bare_non_boolean_is_not_a_condition() -> None:
    """``category`` alone would match every classified complaint.

    It reads like a rule about categories and behaves like a catch-all, because
    a non-empty string is truthy. Refusing it makes the author write the
    comparison they meant.
    """
    with pytest.raises(ExpressionError, match="needs a true/false value"):
        compile_condition("category")


def test_chained_comparisons_are_refused() -> None:
    """Half of all readers parse ``a < b < c`` as ``(a < b) < c``.

    A policy language whose operators mean something other than what its readers
    think produces approved rules nobody understands, which is worse than a
    language that is missing a convenience.
    """
    with pytest.raises(ExpressionError, match="chained comparisons"):
        compile_condition("1 < severity < 10")


def test_an_empty_collection_is_refused() -> None:
    with pytest.raises(ExpressionError, match="empty collection"):
        compile_condition("category in ()")


def test_a_mixed_collection_is_refused() -> None:
    with pytest.raises(ExpressionError, match="one kind of value"):
        compile_condition("category in ('a', 1)")


def test_a_collection_built_from_facts_is_refused() -> None:
    """The document an approver read must fully determine what the rule means."""
    with pytest.raises(ExpressionError, match="literals only"):
        compile_condition("category in (category, 'roads')")


def test_a_collection_cannot_be_an_equality_operand() -> None:
    with pytest.raises(ExpressionError, match="is a collection"):
        compile_condition("tags == ('a',)")


def test_membership_needs_a_set_fact_on_the_right() -> None:
    with pytest.raises(ExpressionError, match="literal collection or one of the set facts"):
        compile_condition("'x' in category")


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_an_over_long_condition_is_refused() -> None:
    with pytest.raises(ExpressionLimitError, match="over the"):
        compile_condition("True and " * MAX_EXPRESSION_CHARS + "True")


def test_a_deeply_nested_condition_is_refused() -> None:
    """The evaluator bounds its own recursion, so the compiler must agree.

    An approved document is not a hostile input, but "trusted" is not
    "infallible", and a generated ruleset is exactly the thing that nests
    forty deep by accident.
    """
    source = "not (" * 40 + "True" + ")" * 40
    with pytest.raises(ExpressionLimitError):
        compile_condition(source)


def test_an_over_large_collection_is_refused() -> None:
    members = ", ".join(f"'c{index}'" for index in range(MAX_COLLECTION_MEMBERS + 1))
    with pytest.raises(ExpressionLimitError, match="over the"):
        compile_condition(f"category in ({members})")


def test_a_condition_at_the_node_limit_is_still_accepted() -> None:
    """The bounds must not be so tight that a realistic rule fails.

    A limit that refuses ordinary work gets raised in a hurry by whoever hits it
    at 4pm on a Friday, which is how bounds stop being bounds.
    """
    clauses = " or ".join(f"severity > {index}" for index in range(20))
    condition = compile_condition(clauses)
    assert condition.evaluate({"severity": 21.0}) is True


# ---------------------------------------------------------------------------
# What the language accepts, and what it means
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "facts", "expected"),
    [
        ("True", {}, True),
        ("False", {}, False),
        ("severity >= 8.5", {"severity": 8.5}, True),
        ("severity >= 8.5", {"severity": 8.4}, False),
        ("category == 'pothole'", {"category": "pothole"}, True),
        ("category != 'pothole'", {"category": "streetlight"}, True),
        ("category in ('a', 'b')", {"category": "b"}, True),
        ("category not in ('a', 'b')", {"category": "c"}, True),
        ("'roads' in category_ancestors", {"category_ancestors": frozenset({"roads"})}, True),
        ("'roads' in category_ancestors", {"category_ancestors": frozenset({"parks"})}, False),
        ("is_safety_triggered", {"is_safety_triggered": True}, True),
        ("not is_safety_triggered", {"is_safety_triggered": False}, True),
        (
            "severity > 8 and 'roads' in category_ancestors",
            {"severity": 9.0, "category_ancestors": frozenset({"roads"})},
            True,
        ),
        (
            "severity > 8 or report_count > 5",
            {"severity": 1.0, "report_count": 9.0},
            True,
        ),
    ],
)
def test_accepted_conditions_evaluate_as_written(
    source: str, facts: dict[str, object], expected: bool
) -> None:
    assert compile_condition(source).evaluate(facts) is expected


def test_referenced_facts_are_reported() -> None:
    """The routing stage skips computing a fact no approved rule mentions."""
    condition = compile_condition("severity > 5 and category == 'x'")
    assert condition.referenced == frozenset({"severity", "category"})


def test_the_source_is_kept_verbatim() -> None:
    """An audit screen shows what was approved, not a re-rendering of it."""
    source = "severity  >   5"
    assert compile_condition(source).source == source


# ---------------------------------------------------------------------------
# Absent facts — the documented wart
# ---------------------------------------------------------------------------


def test_a_comparison_with_an_absent_fact_is_false() -> None:
    condition = compile_condition("severity > 7")
    assert condition.evaluate({}) is False


def test_inequality_with_an_absent_fact_is_also_false() -> None:
    """The tempting alternative is wrong in the way that matters.

    If ``!=`` answered ``True`` for an absent fact, a rule reading
    ``category != "roads"`` would match a complaint the classifier never
    categorised, and route an unclassified report to a department as though it
    had been categorised.
    """
    assert compile_condition("category != 'roads'").evaluate({}) is False
    assert compile_condition("category not in ('roads',)").evaluate({}) is False


def test_rule_matches_when_unknown_negation_is_not_the_complement() -> None:
    """Pins the documented three-valued wart so nobody "fixes" it silently.

    ``not (severity > 7)`` and ``severity <= 7`` disagree when severity is
    absent. That is the cost of answering ``False`` at the comparison boundary
    instead of raising, and the trade is deliberate: raising mid-route would
    drop a citizen's report because their photo had no EXIF.
    """
    assert compile_condition("not (severity > 7)").evaluate({}) is True
    assert compile_condition("severity <= 7").evaluate({}) is False


# ---------------------------------------------------------------------------
# The property the whole module exists to provide
# ---------------------------------------------------------------------------

#: Facts of every declared kind, plus the absence of each. Hypothesis explores
#: the cross product, including the combinations a hand-written table forgets.
_FACT_VALUES = {
    FactKind.NUMBER: st.one_of(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.integers(min_value=-1000, max_value=1000),
    ),
    FactKind.STRING: st.text(max_size=40),
    FactKind.BOOLEAN: st.booleans(),
    FactKind.STRING_SET: st.frozensets(st.text(max_size=20), max_size=8),
}


@st.composite
def _fact_mappings(draw: st.DrawFn) -> dict[str, object]:
    """A fact mapping with each declared fact independently present or absent."""
    facts: dict[str, object] = {}
    for fact in ROUTING_FACTS.facts:
        if draw(st.booleans()):
            facts[fact.name] = draw(_FACT_VALUES[fact.kind])
    return facts


_CONDITIONS = [
    "True",
    "severity > 7",
    "severity <= 7 and category == 'roads'",
    "not is_safety_triggered",
    "category in ('a', 'b', 'c')",
    "'urgent' in tags",
    "'roads' in category_ancestors or report_count >= 3",
    "severity > 8 and (not (trust_score < 0.5))",
    "locale == 'hi' and submitted_via == 'whatsapp'",
    "zone_code not in ('Z1', 'Z2')",
]


@given(facts=_fact_mappings())
@hypothesis_settings(max_examples=200, deadline=None)
def test_a_compiled_condition_never_raises_for_any_facts(facts: dict[str, object]) -> None:
    """Evaluation is total. This is the property everything else rests on.

    ``evaluate_routing`` promises it cannot raise, and it can only promise that
    because compilation already refused everything that could. Routing runs
    inside a Celery task on a citizen's complaint; an exception there is not an
    error message, it is a report that stops moving.
    """
    for source in _CONDITIONS:
        result = compile_condition(source).evaluate(facts)
        assert result is True or result is False


@given(facts=_fact_mappings())
@hypothesis_settings(max_examples=100, deadline=None)
def test_evaluation_is_deterministic(facts: dict[str, object]) -> None:
    """Same facts, same answer — architectural principle 2, as a test.

    §11.2's fail-safe stays provably deterministic once it is policy data rather
    than source. That claim is only worth making if the evaluator underneath it
    has no clock, no randomness, and no dependence on iteration order.
    """
    for source in _CONDITIONS:
        condition = compile_condition(source)
        first = condition.evaluate(facts)
        assert all(condition.evaluate(facts) is first for _ in range(5))


@given(source=st.text(max_size=60))
@hypothesis_settings(max_examples=300, deadline=None)
def test_arbitrary_text_never_escapes_compilation(source: str) -> None:
    """Whatever a caller types, the outcome is a condition or an ExpressionError.

    Never a ``SyntaxError``, a ``ValueError`` from deep in ``ast``, or a
    ``RecursionError`` — each of which would reach the API as a 500 on a policy
    edit rather than as a message telling the author what is wrong.
    """
    with contextlib.suppress(ExpressionError):
        compile_condition(source)
