"""Phase 9 — the perception calibration policy kind.

Revision ID: dcbda80e87e6
Revises: 25e8d619c085
Create Date: 2026-08-19 18:30:00.000000

**One change, and it is not additive: five CHECK constraints are widened.**
``PolicyKind`` gains ``perception_calibration``, and ``kind IN (...)`` is written
into ``policy_versions``, ``simulation_runs``, ``evaluation_sets``,
``policy_certificates`` and ``shadow_observations``. Each is dropped and
recreated with the eighth value, exactly as Phase 8 did for the seventh.

That cost is the constraint working rather than a design mistake, and Phase 8's
migration already made the argument: an open ``kind`` column would accept
``perception_calibrations`` (plural) as readily as the real name and produce a
policy nothing ever resolves. The recreation is safe in both directions — the
constraint is a predicate over existing rows, no row can contain the new value
before this migration, and the downgrade re-narrows it after removing the rows
that could hold it.

**Phase 9 adds no tables, and that is the interesting part.** The perception
layer is the phase that produces the most *data* in the system — two embeddings
per complaint, a transcript, a category, a confidence — and every one of those
already has somewhere to live: ``complaints.text_embedding`` and
``complaints.image_embedding`` were created in Phase 2 with their HNSW indexes,
``classification_scored`` and ``media_transcribed`` are events, and the
per-category F1 numbers are a committed report artefact rather than a table
nobody would query twice. A phase that ships no schema is not a phase that
shipped nothing; it is one whose predecessors modelled the domain correctly.

**What this migration deliberately does not do: give any tenant a calibration
document.** ``PolicyKind.PERCEPTION_CALIBRATION`` is baselined, so every tenant —
existing and new — resolves the platform defaults from ``policy.baselines`` with
no row at all, and ``seed_baselines`` writes one the next time it runs. A
migration that inserted a policy version per tenant would be writing governed
documents outside the draft → review → approve → activate lifecycle Phase 6
exists to enforce, and would do it without an event on any tenant's chain.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "dcbda80e87e6"
down_revision: str | None = "25e8d619c085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Tables carrying a ``kind IN (...)`` CHECK over ``PolicyKind``, and the
#: constraint name on each. Listed rather than discovered, for the reason the
#: Phase 8 migration gives: a loop over ``information_schema`` would silently do
#: nothing if a name ever changed, and a migration that silently does nothing is
#: the worst kind.
_KIND_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("policy_versions", "ck_policy_versions_kind_is_known"),
    ("simulation_runs", "ck_simulation_runs_kind_is_known"),
    ("evaluation_sets", "ck_evaluation_sets_kind_is_known"),
    ("policy_certificates", "ck_policy_certificates_kind_is_known"),
    ("shadow_observations", "ck_shadow_observations_kind_is_known"),
)

_KINDS_BEFORE = (
    "severity_rubric",
    "dedup_thresholds",
    "safety_ruleset",
    "sla_matrix",
    "routing_rules",
    "rate_card",
    "trust_thresholds",
)
_KINDS_AFTER = (*_KINDS_BEFORE, "perception_calibration")


def _rewrite_kind_checks(kinds: tuple[str, ...]) -> None:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    for table, constraint in _KIND_CONSTRAINTS:
        # ``op.f`` on both, and it is not optional on either — see the Phase 8
        # migration, where omitting it produced a doubly-prefixed constraint name
        # the *next* migration could not find.
        op.drop_constraint(op.f(constraint), table, type_="check")
        op.create_check_constraint(op.f(constraint), table, f"kind IN ({values})")


def upgrade() -> None:
    _rewrite_kind_checks(_KINDS_AFTER)


def downgrade() -> None:
    # Rows first, then the constraint. Re-narrowing a CHECK over a table that
    # still holds the value being removed fails on the ALTER — with an error
    # about a constraint rather than about the data, which sends whoever is
    # running the downgrade looking in the wrong place at the worst time.
    #
    # Deleting policy versions is destructive and is the correct behaviour here:
    # a ``perception_calibration`` document cannot exist in a schema that does
    # not admit the kind, and leaving orphaned rows behind would make the
    # *next* upgrade resurrect documents whose history the downgrade discarded.
    for table in ("shadow_observations", "policy_certificates", "simulation_runs"):
        op.execute(f"DELETE FROM {table} WHERE kind = 'perception_calibration'")
    op.execute("DELETE FROM evaluation_sets WHERE kind = 'perception_calibration'")
    op.execute("DELETE FROM policy_versions WHERE kind = 'perception_calibration'")
    _rewrite_kind_checks(_KINDS_BEFORE)
