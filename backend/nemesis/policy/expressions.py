"""The sandboxed, side-effect-free evaluator routing rules are written in.

Phase 6 promises routing conditions "evaluated in a sandboxed, side-effect-free
evaluator". This is that evaluator, and the shape of it is a security decision
that deserves stating rather than discovering from the imports.

**Why an interpreter and not ``eval`` with empty globals.** The well-known
sandbox escape is attribute traversal: from any object reachable in the
expression you walk ``__class__`` → ``__bases__`` → ``__subclasses__`` and
arrive at something that opens a file. Every published defence is a blocklist,
and the history of blocklists in this problem is a history of them being one
construct short. So this module never builds a code object and never calls
``eval``. It parses to an AST, refuses every node type that is not on a short
allowlist, and walks the survivors itself. The evaluator's entire vocabulary is
the ``_evaluate`` dispatch below — there is no reachable Python object at all,
which is a property you can verify by reading eighty lines rather than by
trusting that a blocklist is complete.

**Why conditions are compiled against a declared fact schema.** The alternative
— accept any name, discover at evaluation time whether it exists — makes two
failures possible in production that are much better as draft-time errors:

1. A typo (``sevrity > 7``) becomes a rule that silently never matches. A
   complaint that is never routed looks exactly like one nobody has picked up,
   which is the hardest kind of outage to notice.
2. A type mismatch (``category > 5``) raises ``TypeError`` *inside routing*,
   for one complaint, hours after the rule was approved.

With a declared schema both are caught when the draft is written, by the person
who wrote it. That buys the property this module actually exists to provide:
**a compiled condition cannot raise.** Evaluation is total. It returns ``True``
or ``False`` and does nothing else — no I/O, no allocation the caller can
observe, no clock, no randomness. Same facts, same answer, forever, which is
what makes §11.2's fail-safe still provably deterministic once it is policy data
rather than source (architectural principle 2).

**Unknown facts, and the wart that comes with them.** A fact can be absent — a
complaint with no transcript has no detected language. Any comparison with an
absent operand is ``False``. That is deliberate and it has a visible
consequence: ``not (severity > 7)`` is ``True`` when severity is unknown, while
``severity <= 7`` is ``False``. The two are not complements, which is what
three-valued logic always costs somewhere. The alternative was raising mid-route
on a fact that legitimately does not exist yet, and dropping a citizen's report
because their photo had no EXIF is a worse trade than an operator having to
think about negation. ``rule_matches_when_unknown`` in the tests pins the
behaviour so nobody quietly "fixes" it into the other one.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from nemesis.policy.errors import ExpressionError, ExpressionLimitError

#: Source length ceiling. Not about memory — the parser handles far more — but
#: about reviewability: a routing condition nobody can read in one screen is a
#: condition an approver rubber-stamps, and the approval step is the control.
MAX_EXPRESSION_CHARS: Final = 800

#: AST nodes in one condition. A rule at this size wants splitting into two
#: rules, which the ordered ruleset already expresses better than a conjunction.
MAX_EXPRESSION_NODES: Final = 120

#: Nesting depth. Bounds the recursive walk in both the compiler and the
#: evaluator, so neither can be made to exhaust the C stack by an approved
#: document — a policy author is trusted, but "trusted" is not "infallible".
MAX_EXPRESSION_DEPTH: Final = 12

#: Members in a literal collection (``category in (...)``). Generous, because a
#: municipality genuinely has dozens of categories, and bounded, because the
#: membership test is linear and runs per complaint per rule.
MAX_COLLECTION_MEMBERS: Final = 64


class FactKind(StrEnum):
    """What a fact holds, and therefore what may be done to it.

    A closed set, and small on purpose. Every kind added here is a kind the
    compiler's comparison table has to have an answer for, and "what does
    ``<`` mean between these two things" must never be a question resolved at
    runtime by Python's default.
    """

    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    #: A set of strings — tags, certifications held, zones touched. Membership
    #: is the only operation: ordering a set is meaningless, and equality against
    #: a literal set is a rule that breaks whenever the set gains a member, which
    #: is not what the author meant.
    STRING_SET = "string_set"


@dataclass(frozen=True, slots=True)
class Fact:
    """One name a condition may mention, with what it means."""

    name: str
    kind: FactKind
    description: str


@dataclass(frozen=True, slots=True)
class FactSchema:
    """The closed vocabulary a family of conditions is compiled against.

    Frozen and passed explicitly rather than read from a module global, because
    the severity rubric's facts and the routing rules' facts are different
    vocabularies and sharing one namespace between them would let a routing rule
    reference a fact routing never has.
    """

    facts: tuple[Fact, ...]

    def by_name(self) -> Mapping[str, Fact]:
        return {fact.name: fact for fact in self.facts}


#: The vocabulary a routing condition sees. Every name here is something the
#: routing stage can supply for *any* tenant without knowing that tenant's
#: domain — which is the constraint that keeps this list from becoming the
#: hardcoded domain model Phase 5 removed. ``category`` is a taxonomy *key*, not
#: one of a fixed five; ``category_path`` is the materialised path, so a rule can
#: say ``"roads" in category_ancestors`` and cover a subtree the tenant defined
#: after the rule was approved.
ROUTING_FACTS: Final = FactSchema(
    facts=(
        Fact(
            "category", FactKind.STRING, "The tenant taxonomy key the complaint was classified into"
        ),
        Fact(
            "category_ancestors",
            FactKind.STRING_SET,
            "Every ancestor key of the category, including the category itself",
        ),
        Fact("severity", FactKind.NUMBER, "The rubric score, 0-10, or absent if scoring degraded"),
        Fact("severity_tier", FactKind.STRING, "The SLA tier the severity resolved to"),
        Fact("zone_code", FactKind.STRING, "The zone the complaint's location falls in, or absent"),
        Fact(
            "report_count", FactKind.NUMBER, "Reports in the complaint's cluster, 1 if unclustered"
        ),
        Fact("is_safety_triggered", FactKind.BOOLEAN, "Whether the §11.2 fail-safe fired"),
        Fact("trust_score", FactKind.NUMBER, "Submission trust after the §11.1 EXIF cross-check"),
        Fact("locale", FactKind.STRING, "The submission locale, or absent"),
        Fact("submitted_via", FactKind.STRING, "Channel the report arrived on"),
        Fact("tags", FactKind.STRING_SET, "Free tags attached by earlier stages or by an operator"),
    )
)

#: Comparison operators, split by what they need from their operands. Ordering
#: is numbers-only: ``"b" > "a"`` is true in Python and means nothing in a
#: routing rule, and accepting it would let an author write a condition that
#: reads sensibly and sorts lexicographically.
_ORDERING_OPS: Final = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_EQUALITY_OPS: Final = (ast.Eq, ast.NotEq)
_MEMBERSHIP_OPS: Final = (ast.In, ast.NotIn)

_OP_NAMES: Final[dict[type[ast.cmpop], str]] = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.In: "in",
    ast.NotIn: "not in",
}

#: Everything the walker will look at. A node whose type is absent is refused by
#: name, so the failure message tells an author what they used rather than
#: "invalid syntax" — and so adding a construct is a deliberate edit here rather
#: than something that starts working because CPython gained a node.
_ALLOWED_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Tuple,
    ast.List,
    *_ORDERING_OPS,
    *_EQUALITY_OPS,
    *_MEMBERSHIP_OPS,
)


@dataclass(frozen=True, slots=True)
class Condition:
    """A compiled, evaluable routing condition.

    Holds the validated AST rather than a code object — see the module
    docstring. ``source`` is kept because it is what an operator wrote and what
    an audit screen has to show; re-rendering it from the tree would produce
    something subtly different from the text that was approved.
    """

    source: str
    tree: ast.Expression
    schema: FactSchema
    #: Facts this condition actually reads. Useful beyond diagnostics: the
    #: routing stage uses it to avoid computing an expensive fact no approved
    #: rule mentions.
    referenced: frozenset[str]

    def evaluate(self, facts: Mapping[str, Any]) -> bool:
        """Whether this condition holds for ``facts``.

        Cannot raise for any input. Every way it could — an unknown name, an
        incomparable pair, a construct with no handler — was refused at compile
        time, which is the property the whole module is arranged to provide.
        Absent facts compare ``False``; see the module docstring.
        """
        return bool(_evaluate(self.tree.body, facts))


def compile_condition(source: str, *, schema: FactSchema = ROUTING_FACTS) -> Condition:
    """Parse and validate one condition, or refuse it with a reason.

    Every rejection names the offending construct and says why it is not
    allowed. A policy author reading "invalid expression" edits at random; one
    reading "attribute access is not available in policy conditions" writes a
    different rule.
    """
    if not source or not source.strip():
        raise ExpressionError("a routing condition cannot be empty; use 'True' to always match")
    if len(source) > MAX_EXPRESSION_CHARS:
        raise ExpressionLimitError(
            f"condition is {len(source)} characters, over the {MAX_EXPRESSION_CHARS} limit. "
            f"A condition nobody can read in one screen is one an approver cannot "
            f"meaningfully approve — split it into two rules."
        )

    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"condition is not a valid expression: {exc.msg}") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_EXPRESSION_NODES:
        raise ExpressionLimitError(
            f"condition has {len(nodes)} syntax nodes, over the {MAX_EXPRESSION_NODES} limit"
        )

    known = schema.by_name()
    referenced: set[str] = set()
    kind = _check(tree.body, known=known, referenced=referenced, depth=0)
    # The *whole* condition must be a truth value. Checking only the operands of
    # each connective would let a bare ``category`` through as a rule that
    # matches whenever the category is a non-empty string — which is every
    # classified complaint, and reads on the page like a rule about categories.
    _require_truthy(kind, context="a routing condition")
    return Condition(source=source, tree=tree, schema=schema, referenced=frozenset(referenced))


# ---------------------------------------------------------------------------
# Compile-time validation
# ---------------------------------------------------------------------------


def _check(
    node: ast.AST, *, known: Mapping[str, Fact], referenced: set[str], depth: int
) -> FactKind | None:
    """Validate one node and report the kind it produces.

    ``None`` means "a boolean-valued sub-expression" — a comparison or a
    connective — as distinct from a ``BOOLEAN`` fact, which may itself be
    compared. Keeping them separate is what stops ``(a > b) == category`` from
    compiling into a comparison between a truth value and a taxonomy key.
    """
    if depth > MAX_EXPRESSION_DEPTH:
        raise ExpressionLimitError(
            f"condition nests deeper than {MAX_EXPRESSION_DEPTH} levels; "
            f"the evaluator bounds its own recursion and will not accept it"
        )
    if not isinstance(node, _ALLOWED_NODES):
        raise ExpressionError(_refusal(node))

    if isinstance(node, ast.BoolOp):
        for value in node.values:
            kind = _check(value, known=known, referenced=referenced, depth=depth + 1)
            _require_truthy(kind, context="'and'/'or'")
        return None

    if isinstance(node, ast.UnaryOp):
        # ``ast.Not`` is the only unary operator on the allowlist, so this is
        # exhaustive — but it is checked rather than assumed, because a future
        # edit to _ALLOWED_NODES would otherwise silently widen the language.
        if not isinstance(node.op, ast.Not):
            raise ExpressionError(_refusal(node.op))
        kind = _check(node.operand, known=known, referenced=referenced, depth=depth + 1)
        _require_truthy(kind, context="'not'")
        return None

    if isinstance(node, ast.Compare):
        _check_compare(node, known=known, referenced=referenced, depth=depth)
        return None

    if isinstance(node, ast.Name):
        fact = known.get(node.id)
        if fact is None:
            raise ExpressionError(
                f"{node.id!r} is not a fact available to this policy. Available facts: "
                f"{', '.join(sorted(known))}. A name that does not resolve would be a "
                f"rule that silently never matches, which reads on a queue exactly like "
                f"a complaint nobody has picked up."
            )
        referenced.add(node.id)
        return fact.kind

    if isinstance(node, ast.Constant):
        return _constant_kind(node)

    if isinstance(node, ast.Tuple | ast.List):
        _check_collection(node, depth=depth)
        return None

    raise ExpressionError(_refusal(node))


def _check_compare(
    node: ast.Compare, *, known: Mapping[str, Fact], referenced: set[str], depth: int
) -> None:
    """Validate one comparison, including its operand kinds.

    Chained comparisons (``1 < x < 10``) are refused rather than supported.
    Python's chaining semantics are correct and widely misremembered — plenty of
    people read ``a < b < c`` as ``(a < b) < c`` — and a policy language whose
    operators mean something other than what half its readers think is a
    language that produces approved rules nobody understands.
    """
    if len(node.ops) != 1:
        raise ExpressionError(
            "chained comparisons are not available in policy conditions; write "
            "'x > 1 and x < 10' so the meaning is the same for every reader"
        )
    operator = node.ops[0]
    left_kind = _check(node.left, known=known, referenced=referenced, depth=depth + 1)
    right = node.comparators[0]
    symbol = _OP_NAMES[type(operator)]

    if isinstance(operator, _MEMBERSHIP_OPS):
        _check_membership(
            left_kind, right, symbol=symbol, known=known, referenced=referenced, depth=depth
        )
        return

    for operand, side in ((node.left, "left"), (right, "right")):
        if isinstance(operand, ast.Tuple | ast.List):
            raise ExpressionError(
                f"the {side} side of {symbol!r} is a collection. A collection can only "
                f"be used with 'in' / 'not in' — comparing one for equality would be a "
                f"rule that breaks the moment the collection gains a member."
            )

    right_kind = _check(right, known=known, referenced=referenced, depth=depth + 1)
    if left_kind is None or right_kind is None:
        raise ExpressionError(
            f"{symbol!r} needs two values, but one side is itself a comparison. "
            f"Combine comparisons with 'and'/'or' instead."
        )
    if left_kind is not right_kind:
        raise ExpressionError(
            f"cannot compare {left_kind.value} with {right_kind.value} using {symbol!r}. "
            f"A comparison across kinds has no meaning the evaluator could give it "
            f"that would still be the same meaning next release."
        )
    if isinstance(operator, _ORDERING_OPS) and left_kind is not FactKind.NUMBER:
        raise ExpressionError(
            f"{symbol!r} orders numbers only, and both sides here are "
            f"{left_kind.value}. Ordering strings would compare them "
            f"alphabetically, which reads sensibly and is almost never what a "
            f"routing rule means."
        )
    if left_kind is FactKind.STRING_SET:
        raise ExpressionError(
            f"a set cannot be compared with {symbol!r}; test membership instead, "
            f"as in '\"urgent\" in tags'"
        )


def _check_membership(
    left_kind: FactKind | None,
    right: ast.expr,
    *,
    symbol: str,
    known: Mapping[str, Fact],
    referenced: set[str],
    depth: int,
) -> None:
    """Validate ``x in y``, in the two shapes that mean something.

    ``x in (literals...)`` asks whether a value is one of a listed set, and
    ``x in set_fact`` asks whether a set fact contains it. In both shapes the
    left side is a single string or number — a fact or a literal — so the test
    is one hash lookup or one bounded scan over members an approver could read.
    What is refused is a *collection* on the left, which has no meaning here,
    and a non-set fact on the right, which would silently be a substring test.
    """
    if left_kind is None:
        raise ExpressionError(f"the left side of {symbol!r} must be a value, not a comparison")

    if isinstance(right, ast.Tuple | ast.List):
        if left_kind is FactKind.STRING_SET:
            raise ExpressionError(
                "a set cannot be a member of a list; write '\"value\" in the_set' instead"
            )
        member_kind = _check_collection(right, depth=depth)
        if member_kind is not None and member_kind is not left_kind:
            raise ExpressionError(
                f"{symbol!r} compares a {left_kind.value} against a collection of "
                f"{member_kind.value}; no member could ever match"
            )
        return

    right_kind = _check(right, known=known, referenced=referenced, depth=depth + 1)
    if right_kind is not FactKind.STRING_SET:
        set_facts = sorted(fact.name for fact in known.values() if fact.kind is FactKind.STRING_SET)
        raise ExpressionError(
            f"the right side of {symbol!r} must be a literal collection or one of the "
            f"set facts ({', '.join(set_facts)}), not "
            f"{right_kind.value if right_kind else 'a comparison'}"
        )
    if left_kind is not FactKind.STRING:
        raise ExpressionError(
            f"only a string can be a member of a set; the left side is {left_kind.value}"
        )


def _check_collection(node: ast.Tuple | ast.List, *, depth: int) -> FactKind | None:
    """Validate a literal collection and report its member kind.

    Literals only. A collection built from facts would make the document's
    meaning depend on data the approver could not see, which is the same defect
    as an unapproved draft influencing a decision — arriving by a different
    door.
    """
    if depth > MAX_EXPRESSION_DEPTH:
        raise ExpressionLimitError(f"collection nests deeper than {MAX_EXPRESSION_DEPTH} levels")
    if len(node.elts) > MAX_COLLECTION_MEMBERS:
        raise ExpressionLimitError(
            f"collection has {len(node.elts)} members, over the {MAX_COLLECTION_MEMBERS} limit"
        )
    if not node.elts:
        raise ExpressionError(
            "an empty collection makes 'in' always false and 'not in' always true; "
            "if that is the intent, write it as 'False' or 'True' so it is visible"
        )

    kinds = set()
    for element in node.elts:
        if not isinstance(element, ast.Constant):
            raise ExpressionError(
                "collections in policy conditions hold literals only. A collection "
                "built from facts would make the rule's meaning depend on data the "
                "approver never saw."
            )
        kinds.add(_constant_kind(element))
    if len(kinds) > 1:
        raise ExpressionError(
            f"a collection must hold one kind of value, not "
            f"{', '.join(sorted(kind.value for kind in kinds))}"
        )
    return next(iter(kinds))


def _constant_kind(node: ast.Constant) -> FactKind:
    """Map a literal to a fact kind, refusing the ones with no meaning here."""
    value = node.value
    # bool before int: ``isinstance(True, int)`` is true, and letting a boolean
    # answer as a number would make ``severity > True`` compile.
    if isinstance(value, bool):
        return FactKind.BOOLEAN
    if isinstance(value, int | float):
        return FactKind.NUMBER
    if isinstance(value, str):
        return FactKind.STRING
    raise ExpressionError(
        f"{value!r} is not a value a policy condition can use. Conditions compare "
        f"numbers, strings, and booleans; None in particular is refused because "
        f"'x == None' would be a test for an absent fact, which is already what "
        f"every comparison with an absent fact answers."
    )


def _require_truthy(kind: FactKind | None, *, context: str) -> None:
    """Refuse a bare non-boolean operand to a logical connective.

    ``category and severity > 5`` is legal Python and means something surprising
    (a non-empty string is truthy). Requiring an explicit comparison makes the
    author say which test they meant.
    """
    if kind is None or kind is FactKind.BOOLEAN:
        return
    raise ExpressionError(
        f"{context} needs a true/false value, but this is a {kind.value}. "
        f"Write the comparison out — 'category == \"pothole\"' rather than 'category' — "
        f"so the rule does not depend on which values Python considers truthy."
    )


def _refusal(node: ast.AST) -> str:
    """A rejection message that names the construct and its remedy."""
    remedies = {
        "Call": (
            "function calls are not available: a call is the one construct that could "
            "reach outside the evaluator"
        ),
        "Attribute": (
            "attribute access is not available: it is the standard route out of every "
            "Python sandbox that has ever been broken"
        ),
        "Subscript": ("indexing is not available; declare the value you need as a fact instead"),
        "Lambda": "lambdas are not available",
        "IfExp": (
            "conditional expressions are not available; write two rules, which the "
            "ordered ruleset already expresses"
        ),
        "ListComp": (
            "comprehensions are not available: their cost depends on runtime data "
            "rather than on the document"
        ),
        "SetComp": "comprehensions are not available",
        "DictComp": "comprehensions are not available",
        "GeneratorExp": "comprehensions are not available",
        "JoinedStr": (
            "f-strings are not available: they evaluate arbitrary expressions inside a literal"
        ),
        "NamedExpr": (
            "assignment expressions are not available: a policy condition may not have effects"
        ),
        "Starred": "unpacking is not available",
        "Await": "await is not available: a policy condition may not do I/O",
        "BinOp": (
            "arithmetic is not available; compute the value as a fact so it is named, "
            "computed once, and visible to an approver"
        ),
        "Dict": "dict literals are not available",
        "Set": 'set literals are not available; use a tuple, as in \'category in ("a", "b")\'',
    }
    name = type(node).__name__
    detail = remedies.get(name, f"{name} is not part of the policy condition language")
    return f"{detail} (in {name})"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

#: The sentinel for "this fact was not supplied". A module-private object rather
#: than ``None`` so a fact whose genuine value is ``None`` — which the fact
#: builders never produce, but a future one might — stays distinguishable from
#: one that is missing.
_ABSENT: Final = object()


def _evaluate(node: ast.AST, facts: Mapping[str, Any]) -> Any:
    """Walk one validated node. Total for every tree ``_check`` accepted.

    The ``raise`` at the bottom is unreachable by construction and stays anyway:
    if a future edit widens ``_ALLOWED_NODES`` without teaching this function
    the new node, the failure should be a loud error naming the node rather than
    a silently wrong routing decision.
    """
    if isinstance(node, ast.BoolOp):
        # Short-circuits, like Python, because an operator that reads like
        # Python's must behave like it. With a total evaluator this is a
        # performance property rather than a safety one.
        if isinstance(node.op, ast.And):
            return all(_evaluate(value, facts) for value in node.values)
        return any(_evaluate(value, facts) for value in node.values)

    if isinstance(node, ast.UnaryOp):
        return not _evaluate(node.operand, facts)

    if isinstance(node, ast.Compare):
        return _compare(node, facts)

    if isinstance(node, ast.Name):
        return facts.get(node.id, _ABSENT)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Tuple | ast.List):
        return tuple(element.value for element in node.elts if isinstance(element, ast.Constant))

    raise ExpressionError(
        f"no evaluator for {type(node).__name__}; the allowlist and the "
        f"evaluator have drifted apart"
    )


def _compare(node: ast.Compare, facts: Mapping[str, Any]) -> bool:
    """Evaluate one validated comparison.

    Absent operands answer ``False`` for every operator including ``!=`` and
    ``not in``. Making ``!=`` answer ``True`` for an absent fact is the tempting
    alternative and it is wrong in the way that matters: a rule saying
    ``category != "roads"`` would then match a complaint whose category the
    classifier never determined, and route an unclassified report to a
    department as though it had been categorised.
    """
    left = _evaluate(node.left, facts)
    right = _evaluate(node.comparators[0], facts)
    if left is _ABSENT or right is _ABSENT:
        return False

    operator = node.ops[0]
    if isinstance(operator, ast.Eq):
        return bool(left == right)
    if isinstance(operator, ast.NotEq):
        return bool(left != right)
    if isinstance(operator, ast.Lt):
        return bool(left < right)
    if isinstance(operator, ast.LtE):
        return bool(left <= right)
    if isinstance(operator, ast.Gt):
        return bool(left > right)
    if isinstance(operator, ast.GtE):
        return bool(left >= right)
    if isinstance(operator, ast.In):
        return _contains(right, left)
    return not _contains(right, left)


def _contains(container: Any, value: Any) -> bool:
    """Membership, defensive about what the container turned out to be.

    ``_check_membership`` already guaranteed the shape, so the guard is for the
    case the compiler and the evaluator disagree after a future edit — and it
    answers ``False`` rather than raising, because a routing stage that raises
    strands a complaint.
    """
    if isinstance(container, str | bytes):
        return False
    if isinstance(container, frozenset | set | tuple | list):
        return value in container
    return False


__all__ = [
    "MAX_COLLECTION_MEMBERS",
    "MAX_EXPRESSION_CHARS",
    "MAX_EXPRESSION_DEPTH",
    "MAX_EXPRESSION_NODES",
    "ROUTING_FACTS",
    "Condition",
    "Fact",
    "FactKind",
    "FactSchema",
    "compile_condition",
]
