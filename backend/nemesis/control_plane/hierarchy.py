"""Materialised-path arithmetic, shared by all three trees.

The taxonomy, the department tree, and the zone tree are structurally identical:
a tenant-scoped self-referencing table with a ``key``/``code``, a ``parent_id``,
a ``path``, and a ``depth``. Writing the same six operations three times would
guarantee they drift, and the way they drift is the dangerous way — one tree
detects cycles and another does not, and nobody notices until an import wedges a
recursive read.

**Why a materialised path rather than a recursive CTE.** Both are correct.
``path`` costs a bounded rewrite on the rare operation (reparenting a subtree)
and makes the frequent one (every descendant of X) a single index-backed prefix
scan. Phase 6 resolves policy by walking a node's ancestors on *every scored
complaint*, and §27.1 budgets that whole stage at eight seconds including CLIP
inference — so the read side is where the budget has to be spent.

**Why the separator is not part of a key.** ``path`` is ``a/b/c``, and the
prefix query for the subtree of ``a/b`` is ``LIKE 'a/b/%'`` plus the node
itself. That is only sound if no key may contain ``/``, which is why both key
patterns exclude it and why the check is in the database rather than only here:
a key with a slash would make one node's subtree silently include another's.
"""

from __future__ import annotations

from typing import Final

from nemesis.control_plane.errors import HierarchyError

#: The one character that may not appear in any tree key. See the module
#: docstring — this is a correctness constraint on the prefix query, not a
#: formatting preference.
PATH_SEPARATOR: Final = "/"


def build_path(ancestor_keys: list[str], key: str) -> str:
    """``a/b/c`` for a node ``c`` whose ancestors are ``a`` then ``b``."""
    return PATH_SEPARATOR.join([*ancestor_keys, key])


def parent_path(path: str) -> str | None:
    """The path of ``path``'s parent, or ``None`` for a root."""
    head, separator, _ = path.rpartition(PATH_SEPARATOR)
    return head if separator else None


def path_keys(path: str) -> list[str]:
    return path.split(PATH_SEPARATOR)


def depth_of(path: str) -> int:
    """Ancestor count. A root is depth 0."""
    return path.count(PATH_SEPARATOR)


def is_descendant(candidate_path: str, ancestor_path: str) -> bool:
    """Whether ``candidate_path`` is at or below ``ancestor_path``.

    Inclusive of the ancestor itself, because every caller asking this question
    is asking "may I move X under Y", and moving a node under itself is exactly
    as broken as moving it under its own child.

    The separator in the prefix is what makes this correct rather than
    approximately correct: ``roads`` is not an ancestor of ``roadside_waste``,
    and a bare ``startswith`` would say it is.
    """
    return candidate_path == ancestor_path or candidate_path.startswith(
        ancestor_path + PATH_SEPARATOR
    )


def reparented_path(current_path: str, old_prefix: str, new_prefix: str) -> str:
    """``current_path`` with ``old_prefix`` replaced by ``new_prefix``.

    Used to rewrite a whole subtree in one pass. Refuses rather than silently
    returning the input when the prefix does not match, because a no-op here
    would leave half a subtree pointing at a path that no longer exists — and
    the resulting tree reads as intact until somebody queries a descendant.
    """
    if not is_descendant(current_path, old_prefix):
        raise HierarchyError(
            f"path {current_path!r} is not under {old_prefix!r}; refusing to rewrite it"
        )
    return new_prefix + current_path[len(old_prefix) :]


def escape_like(value: str) -> str:
    """Escape a literal for use inside a ``LIKE`` pattern.

    A key cannot contain ``/`` but the *pattern* is built from a path, and a
    key containing ``%`` or ``_`` would otherwise make the subtree query match
    unrelated siblings. Both characters are legal in the key patterns, so this
    is not hypothetical — ``_`` is the recommended word separator.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def subtree_pattern(path: str) -> str:
    """A ``LIKE`` pattern matching every strict descendant of ``path``."""
    return f"{escape_like(path)}{PATH_SEPARATOR}%"


def assert_within_depth(path: str, *, maximum: int, label: str) -> None:
    """Refuse a tree deeper than the schema's CHECK constraint permits.

    Checked here as well as in the database so a bulk import fails with the
    offending key named, rather than with a constraint violation that says only
    that some row in a batch of four hundred was too deep.
    """
    if depth_of(path) >= maximum:
        raise HierarchyError(
            f"{label} {path!r} would sit at depth {depth_of(path)}, beyond the "
            f"limit of {maximum - 1}. A tree this deep is a data-entry accident "
            f"more often than a domain, and the ancestor walk that resolves "
            f"policy per complaint has to stay bounded."
        )
