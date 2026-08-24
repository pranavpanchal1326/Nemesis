"""Phase 10: admit the ambiguous-dedup review reason.

Revision ID: 6f2a41c9b7d3
Revises: dcbda80e87e6
Create Date: 2026-08-24 06:00:00.000000

§14.1's middle band routes a report the engine will not guess about into the
§11.4 review queue, under a reason of its own. ``ReviewReason`` is a closed set
enforced in two places — the Python enum, and a ``CHECK`` constraint on each of
``review_queue_items`` and ``review_decisions`` whose allowed list was written
out literally by the Phase 8 migration.

**This migration exists because the second place does not follow the first.**
The models build their constraint from ``REVIEW_REASONS``, so adding an enum
member updates the model, passes ``ruff``, passes ``mypy --strict``, and passes
every test that does not actually insert a row — and then fails at runtime
against a migrated database with a check-constraint violation. That is a
genuinely nasty failure mode: the code says the value is legal, the database
says it is not, and nothing in between notices. It was caught here by an
integration test inserting a real review item, which is the only place it could
have been caught.

Both tables are altered, not just the queue. ``review_decisions`` records the
reason a decided item carried, so a reason the queue can hold and the decision
table cannot is a queue item that can be raised and never resolved.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6f2a41c9b7d3"
down_revision: str | None = "dcbda80e87e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept as literals rather than imported from ``nemesis.db.models.trust``. A
#: migration that imports the model describes whatever the model says *today*,
#: which means replaying this revision after a later enum change would apply a
#: constraint from the future. Migrations state history; models state now.
_BEFORE = (
    "safety_trigger",
    "exif_mismatch",
    "perceptual_duplicate",
    "device_velocity",
    "geographic_cluster",
    "low_trust",
)
_AFTER = (*_BEFORE, "ambiguous_dedup")

_TABLES = ("review_queue_items", "review_decisions")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _reset(allowed: tuple[str, ...]) -> None:
    """Swap the allowed list on both tables.

    The bare constraint name, not the full one. Alembic applies this project's
    metadata naming convention (``ck_%(table_name)s_%(constraint_name)s``) to
    both ``drop_constraint`` and ``create_check_constraint``, so passing
    ``ck_review_queue_items_reason_is_known`` asks it to drop
    ``ck_review_queue_items_ck_review_queue_items_reason_is_known``, which does
    not exist.
    """
    for table in _TABLES:
        op.drop_constraint("reason_is_known", table, type_="check")
        op.create_check_constraint("reason_is_known", table, f"reason IN ({_quoted(allowed)})")


def upgrade() -> None:
    _reset(_AFTER)


def downgrade() -> None:
    # Rows carrying the new reason are deleted rather than left to violate the
    # narrowed constraint. Downgrading past the phase that introduced a reason
    # means those queue items describe a decision path the code no longer has,
    # and a constraint that cannot be created is a downgrade that fails halfway
    # — which is worse than losing review items on a deliberate rollback.
    op.execute("DELETE FROM review_decisions WHERE reason = 'ambiguous_dedup'")
    op.execute("DELETE FROM review_queue_items WHERE reason = 'ambiguous_dedup'")
    _reset(_BEFORE)
