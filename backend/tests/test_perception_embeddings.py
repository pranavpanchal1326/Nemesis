"""The documented exception to the projection rule, kept the size it should be.

``projections.writer`` states that nothing outside it writes the current-state
tables, because a row the log does not explain breaks §9.1.
``perception.embeddings`` is the one exception it names, and the reason is
arithmetic: a 512-dimensional half-precision vector plus a 384-dimensional single
is about 2.5 KB per complaint, and a million complaints is 2.5 GB of an
append-only log whose whole value is that it stays small enough to replay — for
data that is regenerable from the photograph and that no human will ever read.

**An exception is only honest while it is checkable.** ``embeddings`` claims in
its own docstring that "a test walks every module's AST and fails on a second
writer". This is that test. Without it the claim is a comment, and the second
writer arrives in the phase after the one that read it.

The check is an AST walk rather than a grep for the same reason
``check_tenant_scoping`` is: a grep for ``text_embedding`` matches the column
definition, the migration, the docstring above, and this file, and a check that
matches its own explanation is a check nobody keeps passing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "nemesis"

#: The two columns only ``perception.embeddings`` may assign.
VECTOR_COLUMNS = frozenset({"text_embedding", "image_embedding"})

#: The one module allowed to write them, relative to ``nemesis/``.
SOLE_WRITER = "perception/embeddings.py"

#: Modules that legitimately *name* the columns without writing them. Declared
#: rather than inferred, so adding one is a decision somebody makes in a diff.
DECLARED_READERS = frozenset(
    {
        # The ORM model, where the columns are defined.
        "db/models/complaint.py",
        # Phase 10. Both of these are flagged by the keyword-argument rule and
        # neither writes anything: `engine` loads the two vectors off the
        # complaint row and hands them to `similarity`, which passes them into a
        # `SELECT` builder as the right-hand side of a cosine comparison. The
        # parameters are named after the columns because that is what they hold,
        # and renaming them to dodge the check would make the call sites worse
        # to read in exchange for nothing.
        #
        # Listed rather than the rule loosened: the rule catches assignment,
        # `update().values()`, `insert().values()` and keyword arguments, and the
        # last of those is what caught `dedup/harness.py` genuinely writing the
        # column direct. A narrower rule would have missed it.
        "dedup/engine.py",
        "dedup/similarity.py",
    }
)


def _modules() -> list[Path]:
    return sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)


def _relative(path: Path) -> str:
    return path.relative_to(PACKAGE).as_posix()


#: The sanctioned API. Passing a vector to *this* is not a write — it is the
#: seam, and the whole point of the seam is that callers use it. Naming it here
#: rather than special-casing ``perception/stage.py`` keeps the rule about the
#: mechanism rather than about one file: any module may call ``store``, and no
#: module may reach past it.
SANCTIONED_CALL = "store"


class _AssignmentFinder(ast.NodeVisitor):
    """Every place a vector column is written, by any of the four routes.

    All four, because each is a way the exception could be widened without the
    others noticing:

    * ``complaint.text_embedding = ...`` — the ORM assignment
      ``control_plane.taxonomy`` warns about at length, which emits an UPDATE
      with no tenant predicate at an arbitrary later autoflush.
    * ``.values(text_embedding=...)`` — the explicit-statement route.
    * ``{"text_embedding": ...}`` in a dict handed to ``values()`` or ``update()``.
    * ``values["text_embedding"] = ...`` — building that dict a key at a time,
      which is what ``embeddings.store`` itself does and which a finder written
      only for the literal form would miss entirely.

    Calls to ``store`` are exempt: handing a vector to the sanctioned writer is
    the behaviour this rule exists to produce.
    """

    def __init__(self) -> None:
        self.hits: list[tuple[str, int]] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr in VECTOR_COLUMNS:
                self.hits.append((target.attr, node.lineno))
            elif isinstance(target, ast.Subscript):
                key = target.slice
                if isinstance(key, ast.Constant) and key.value in VECTOR_COLUMNS:
                    self.hits.append((str(key.value), node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._callee(node) != SANCTIONED_CALL:
            for keyword in node.keywords:
                if keyword.arg in VECTOR_COLUMNS:
                    self.hits.append((keyword.arg, node.lineno))
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key in node.keys:
            if isinstance(key, ast.Constant) and key.value in VECTOR_COLUMNS:
                self.hits.append((str(key.value), node.lineno))
        self.generic_visit(node)

    @staticmethod
    def _callee(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        if isinstance(node.func, ast.Name):
            return node.func.id
        return None


def _writers(path: Path) -> list[tuple[str, int]]:
    finder = _AssignmentFinder()
    finder.visit(ast.parse(path.read_text(encoding="utf-8")))
    return finder.hits


def test_only_one_module_writes_the_vector_columns() -> None:
    offenders: dict[str, list[tuple[str, int]]] = {}
    for path in _modules():
        relative = _relative(path)
        if relative == SOLE_WRITER or relative in DECLARED_READERS:
            continue
        hits = _writers(path)
        if hits:
            offenders[relative] = hits

    assert not offenders, (
        f"the projection-rule exception has grown a second writer: {offenders}. "
        f"§9.1 says current state is derived from the log; these two columns are the "
        f"one documented exception, and an exception with two writers is not an "
        f"exception, it is a policy. Write through perception.embeddings.store, or "
        f"argue the case in the module docstring and add the module to DECLARED_READERS."
    )


def test_the_sole_writer_actually_writes_them() -> None:
    """Guards the guard.

    A check whose target moved is a check that passes by looking at nothing —
    the failure mode ``check_media_redaction`` self-tests against, reached here
    by asserting the one module the walk exempts is the one doing the work.
    """
    hits = {name for name, _ in _writers(PACKAGE / SOLE_WRITER)}
    assert hits == VECTOR_COLUMNS


@pytest.mark.parametrize("column", sorted(VECTOR_COLUMNS))
def test_the_walk_would_catch_a_new_writer(column: str) -> None:
    """And guards it the other way: prove the AST walk can still fail.

    Every form the finder claims to catch, exercised against source it is handed
    directly. A visitor that silently stopped matching ``.values(...)`` would
    otherwise report a clean repository forever.
    """
    for source in (
        f"complaint.{column} = vector",
        f"stmt.values({column}=vector)",
        f'session.execute(update(Complaint).values({{"{column}": vector}}))',
        f'values["{column}"] = vector',
    ):
        finder = _AssignmentFinder()
        finder.visit(ast.parse(source))
        assert finder.hits, f"the walk missed {source!r}"

    # And the sanctioned seam stays exempt, or the rule would forbid using it.
    finder = _AssignmentFinder()
    finder.visit(ast.parse(f"await embeddings.store(session, {column}=vector)"))
    assert not finder.hits
