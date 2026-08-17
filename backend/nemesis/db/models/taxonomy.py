"""The tenant's defect taxonomy — the table that makes NEMESIS a product.

Critique-log defect #1 is a hardcoded domain model: five fixed categories, which
ships one vertical for one city. A campus has no potholes; it has elevator
faults, lab spills, and HVAC failures. This is where that becomes data.

**The key is the contract, not the display name.** ``taxonomy_nodes.key`` is what
appears in ``classification_scored.category``, in ``complaints.category``, and in
a contractor's certification scope. It is immutable by convention and constrained
to a machine-safe alphabet, because an event written six months ago must still
resolve — renaming a *display name* is a translation edit, and renaming a *key*
would orphan history. The two are separate columns precisely so nobody has to
choose between "the label is wrong on screen" and "the log is now unreadable".

**Why a materialised path.** Routing (§15.2), the equity rollups (§23.2), and the
Phase 6 policy resolver all ask "everything under this node". Recursive CTEs
answer that correctly and cost a join per level on the hottest read in the
control plane. ``path`` is maintained by ``control_plane.taxonomy`` on every
mutation and is a derived column: it is never the source of truth, and the
service's own tests rebuild it from ``parent_id`` alone to prove it agrees.

**What is deliberately not here: versioning.** Phase 6 owns versioned,
effective-dated, draft→approve→activate policy documents, and taxonomy structure
joins that lifecycle there. Building half of it now would fix a shape before the
phase that has to live with it has been designed — the same mistake
``events.catalog`` refuses to make with deferred payloads. What Phase 5 does
carry is ``taxonomy_prompt_sets.version``, because ``classification_scored``
already has a ``prompt_set_version`` field that has to mean something.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nemesis.db.base import (
    Base,
    OptimisticVersionMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

#: Machine-safe keys only. Enforced in the database as well as in the service
#: because the key ends up in an event payload, a URL path segment, and a
#: Prometheus label — three places where a space or a slash is a different bug
#: each time.
KEY_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,62}[a-z0-9]$|^[a-z0-9]$"

#: Depth ceiling. Not arbitrary: ``path`` is a bounded string, the Phase 6
#: resolver walks ancestors per decision, and a taxonomy nested twelve deep is a
#: data-entry accident rather than a domain that exists. A refusal at write time
#: is a better failure than an unbounded walk at score time.
MAX_TAXONOMY_DEPTH = 8

#: ``/``-joined keys, root first. Sized for ``MAX_TAXONOMY_DEPTH`` full-length
#: keys plus separators, so the constraint that bounds depth also bounds this.
PATH_MAX_LENGTH = MAX_TAXONOMY_DEPTH * 65


class TaxonomyNode(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """One defect category, as defined by one tenant."""

    __tablename__ = "taxonomy_nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_taxonomy_nodes_tenant_id_key"),
        Index("ix_taxonomy_nodes_tenant_id_parent_id", "tenant_id", "parent_id"),
        # Prefix-matched by the subtree query, so the index has to be on the
        # tenant *and* the path or every "everything under here" read is a scan.
        Index("ix_taxonomy_nodes_tenant_id_path", "tenant_id", "path"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="not_its_own_parent",
        ),
        CheckConstraint(f"depth >= 0 AND depth < {MAX_TAXONOMY_DEPTH}", name="depth_within_bound"),
        CheckConstraint(f"key ~ '{KEY_PATTERN}'", name="key_is_machine_safe"),
    )

    #: The stable identifier that reaches the event log. See the module
    #: docstring on why this is not the display name.
    key: Mapped[str] = mapped_column(String(64), nullable=False)

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # RESTRICT, not CASCADE: deleting a parent must not silently take its
        # children — and with them the meaning of every complaint classified
        # into one — with it. The service reparents or refuses.
        ForeignKey("taxonomy_nodes.id", ondelete="RESTRICT", name="fk_taxonomy_nodes_parent"),
        nullable=True,
    )

    #: Derived from ``parent_id``. Maintained by the service, never by a caller.
    path: Mapped[str] = mapped_column(String(PATH_MAX_LENGTH), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    #: The fallback label, used when no translation exists for the requested
    #: locale. Stored here rather than only in ``translations`` so a taxonomy
    #: with no translations loaded is still readable — an empty screen during
    #: onboarding is indistinguishable from a broken one.
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: A token the design system resolves (Phase 18), not a file path or a URL.
    #: A tenant-supplied URL here would be an SSRF surface and a mixed-content
    #: bug on a page §16.2 serves to the public.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    #: Whether a complaint may be classified directly into this node. An interior
    #: node is often a grouping ("Roads") whose children are the real categories,
    #: and letting the classifier land on the grouping produces a report nobody
    #: can route.
    is_selectable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    #: §13.5 semantics per node: a severity floor, a multiplier, and whether this
    #: category bypasses scoring outright (§11.2's danger path is the case that
    #: matters). JSONB rather than columns because Phase 6 replaces the *whole*
    #: structure with a versioned rubric document, and a column per knob would be
    #: a migration per knob until then.
    severity_semantics: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    #: Where work in this category tends to go. A *hint*, not a rule: Phase 6's
    #: sandboxed evaluator owns conditional routing, and this is what a
    #: solutions engineer fills in during onboarding so the tenant is useful on
    #: day one rather than after the policy phase ships.
    routing_hints: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    #: Anything else the tenant wants to carry. Open by design — the alternative
    #: is a schema change per customer, which is the defect this table exists to
    #: close.
    attributes: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


class TaxonomyPromptSet(
    UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, OptimisticVersionMixin, Base
):
    """Zero-shot prompts for one node, in one locale, for one model family.

    Phase 9's gate requires a new tenant category to be classifiable by adding
    prompts alone. This is the table that makes that true, and the reason it is
    keyed by locale as well as node: a CLIP prompt is English by convention, but
    the *text* encoder scores a Marathi description against a Marathi prompt, and
    sharing one row between them would force one of the two to be wrong.

    ``version`` is written into ``classification_scored.prompt_set_version``, so
    a classification can always be re-argued against the exact prompts that
    produced it. It is a string rather than an integer because Phase 6 will
    replace it with a policy document id, and a caller that has been treating it
    as opaque text will not need changing.
    """

    __tablename__ = "taxonomy_prompt_sets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "node_id",
            "locale",
            "encoder",
            name="uq_taxonomy_prompt_sets_tenant_id_node_id_locale_encoder",
        ),
        Index("ix_taxonomy_prompt_sets_tenant_id_node_id", "tenant_id", "node_id"),
        CheckConstraint("cardinality(prompts) > 0", name="prompt_set_is_not_empty"),
    )

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # CASCADE here, unlike the parent link: a prompt set has no meaning
        # without its node, carries no history anyone can reference, and leaving
        # orphans behind would make the next import collide on the unique key
        # for a node that no longer exists.
        ForeignKey("taxonomy_nodes.id", ondelete="CASCADE", name="fk_taxonomy_prompt_sets_node"),
        nullable=False,
    )

    #: BCP-47. Must be one of the tenant's declared locales; enforced by the
    #: service rather than by a foreign key, because ``tenants.locales`` is an
    #: array and Postgres cannot reference into one.
    locale: Mapped[str] = mapped_column(String(35), nullable=False)

    #: Which encoder these prompts are written for — 'clip' scores an image,
    #: 'text' scores a transcript. Free text, not an enum: Phase 9 may add a
    #: third, and a CHECK constraint listing two would make that a migration.
    encoder: Mapped[str] = mapped_column(String(32), nullable=False)

    prompts: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    #: Prompts that should score *against* this category. Zero-shot CLIP is a
    #: comparison, not a detector: without a contrast set every image is
    #: whichever category was listed first, at a confidence that looks credible.
    negative_prompts: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )

    prompt_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
