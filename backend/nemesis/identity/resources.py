"""Turning a domain row into something a grant can be tested against.

**This module is the join between authorization and the rest of the product**,
and keeping it in one file is what stops the same mapping being written three
slightly different ways in three routers. A complaint's *department* is a
column; a grant's scope is a *path*; the containment test is prefix containment
over paths. Somebody has to resolve one into the other, and if that somebody is
each handler then one of them will eventually compare an id to a path and
authorise nothing — or everything.

**What a complaint can and cannot be scoped by today, stated plainly.**

``taxonomy_path``
    Resolved from ``complaints.category``, which holds a taxonomy *key*. Works
    from the moment a report is classified (Phase 9).
``zone_path``
    Resolved from ``complaints.ward``, matched against ``zones.code``. Works
    wherever the tenant's ward labels match its zone codes, which is what Phase
    5's provisioning produces and what ``§23.2`` assumes.
``department_path``
    Resolved from ``complaints.department_id`` — **which nothing writes yet.**
    Routing is Phase 12. Until it lands, a complaint carries no department, so a
    *department-scoped* grant cannot prove containment and therefore denies.

That last line is the honest and uncomfortable consequence of default-deny, and
it is written here rather than discovered: in this build, a department-scoped
grant on ``review:read`` sees nothing, because there is no department on the
rows. The usable scopes today are tenant, zone, taxonomy and self. Phase 12
makes the fourth one work by writing the column the resolver already reads, with
no change here — which is the test that the seam is in the right place.

**Resolution is per request and cached nowhere.** A reparented department must
change authorization immediately, and a cache keyed on a department id is a
cache that keeps answering with where that department used to be. The cost is
two small indexed reads on an endpoint that is already loading the row.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from nemesis.db.models.complaint import Complaint
from nemesis.db.models.organisation import Department, Zone
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.identity import permissions as perms
from nemesis.identity.scopes import Resource, Visibility
from nemesis.tenancy.context import tenant_scope


async def complaint_resource(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint: Complaint
) -> Resource:
    """Describe a complaint by the attributes a grant can test.

    Takes the loaded row rather than an id: every caller has already fetched it
    (they are about to return it), and a second read to authorise what you just
    read is a wasted query on the hottest operator path.
    """
    department_path: str | None = None
    zone_path: str | None = None
    taxonomy_path: str | None = None

    with tenant_scope(tenant_id):
        if complaint.department_id is not None:
            department_path = (
                await session.execute(
                    select(Department.path).where(
                        Department.tenant_id == tenant_id,
                        Department.id == complaint.department_id,
                    )
                )
            ).scalar_one_or_none()
        if complaint.ward:
            zone_path = (
                await session.execute(
                    select(Zone.path).where(
                        Zone.tenant_id == tenant_id, Zone.code == complaint.ward
                    )
                )
            ).scalar_one_or_none()
        if complaint.category:
            taxonomy_path = (
                await session.execute(
                    select(TaxonomyNode.path).where(
                        TaxonomyNode.tenant_id == tenant_id,
                        TaxonomyNode.key == complaint.category,
                    )
                )
            ).scalar_one_or_none()

    return Resource(
        kind=perms.KIND_COMPLAINT,
        id=complaint.id,
        department_path=department_path,
        zone_path=zone_path,
        taxonomy_path=taxonomy_path,
        # A complaint is attached to nobody until Phase 14 assigns a work order.
        # An empty set rather than an omission: `self`-scoped field staff
        # therefore see nothing here, which is correct — they are attached to
        # work orders, not to reports.
        attached_user_ids=frozenset(),
    )


async def review_item_resource(
    session: AsyncSession, *, tenant_id: uuid.UUID, complaint_id: uuid.UUID
) -> Resource:
    """A review item, described by the complaint it is about.

    The item itself carries no place and no category — it carries a *reason* and
    a priority. Everything a grant can test about it belongs to the complaint
    behind it, so the resource is the complaint's attributes under the review
    item's kind. Scoping the queue by anything else would mean a reviewer's
    grant restricted which *kinds of problem* they may judge rather than which
    part of the city, and that is the taxonomy scope, which this already
    carries.
    """
    with tenant_scope(tenant_id):
        complaint = (
            await session.execute(
                select(Complaint).where(
                    Complaint.tenant_id == tenant_id, Complaint.id == complaint_id
                )
            )
        ).scalar_one_or_none()

    if complaint is None:
        # A queue item whose complaint has gone. Unreachable through this
        # system — nothing deletes a complaint — so the safe reading is that
        # nothing about it can be proved, which denies every scoped grant.
        return Resource(kind=perms.KIND_REVIEW_ITEM, id=complaint_id)

    resource = await complaint_resource(session, tenant_id=tenant_id, complaint=complaint)
    return Resource(
        kind=perms.KIND_REVIEW_ITEM,
        id=complaint_id,
        department_path=resource.department_path,
        zone_path=resource.zone_path,
        taxonomy_path=resource.taxonomy_path,
    )


def complaint_visibility_condition(
    *, tenant_id: uuid.UUID, allowed: Visibility
) -> ColumnElement[bool] | None:
    """The ``WHERE`` clause a scoped principal's complaint query must carry.

    ``None`` means "no restriction" — a tenant-wide grant. A caller that treats
    ``None`` as "match nothing" has inverted the most consequential boolean in
    the system, which is why ``Visibility`` carries ``unrestricted`` as a flag
    rather than leaving it to be inferred from an empty set.

    **Why this is a condition and not a list of ids.** The Phase 13 gate clause
    says "including pagination counts": a `COUNT(*)` is computed in the database
    before any row is seen, so the restriction has to be part of the statement.
    Fetching ids first and filtering afterwards gives a correct-looking page and
    a wrong total, which is the exact leak the clause names.
    """
    if allowed.unrestricted:
        return None
    if allowed.permits_nothing:
        # A condition that is false for every row, expressed as a predicate the
        # planner understands rather than as `1 = 0`: the statement still runs,
        # still counts, and returns nothing.
        return Complaint.id.is_(None)

    clauses: list[ColumnElement[bool]] = []
    if allowed.zone_paths:
        clauses.append(
            Complaint.ward.in_(
                select(Zone.code).where(
                    Zone.tenant_id == tenant_id,
                    _path_prefixes(Zone.path, allowed.zone_paths),
                )
            )
        )
    if allowed.taxonomy_paths:
        clauses.append(
            Complaint.category.in_(
                select(TaxonomyNode.key).where(
                    TaxonomyNode.tenant_id == tenant_id,
                    _path_prefixes(TaxonomyNode.path, allowed.taxonomy_paths),
                )
            )
        )
    if allowed.department_paths:
        clauses.append(
            Complaint.department_id.in_(
                select(Department.id).where(
                    Department.tenant_id == tenant_id,
                    _path_prefixes(Department.path, allowed.department_paths),
                )
            )
        )
    if not clauses:
        # Contractor- and self-scoped grants say nothing about which complaints
        # exist: a contractor is attached to a work order (Phase 14), not to a
        # citizen's report. Denying is the honest answer; the alternative would
        # be showing a contractor every report in the city.
        return Complaint.id.is_(None)
    return or_(*clauses)


def _path_prefixes(
    column: InstrumentedAttribute[str], paths: frozenset[str]
) -> ColumnElement[bool]:
    """``path = 'a/b' OR path LIKE 'a/b/%'`` for each granted path.

    The separator in the ``LIKE`` is what makes this correct rather than
    approximately correct — the same point ``control_plane.hierarchy.is_descendant``
    makes in Python. Without it, a grant on ``roads`` covers ``roadside_waste``,
    which is a privilege nobody granted and nobody can see.

    ``%`` and ``_`` in a path would be wildcards here. They cannot occur: both
    tree key patterns are ``[a-z0-9_-]``-shaped and exclude ``%``, and ``_`` is
    legal but matches itself plus one character — which could only ever *widen*
    a grant to a sibling whose path differs by exactly that character. That is a
    real if narrow hazard, so the pattern escapes it explicitly rather than
    relying on the key pattern staying as it is.
    """
    return or_(
        *[
            or_(
                column == path,
                column.like(f"{_escape_like(path)}/%", escape="\\"),
            )
            for path in sorted(paths)
        ]
    )


def _escape_like(value: str) -> str:
    """Escape the two SQL ``LIKE`` wildcards. See ``_path_prefixes``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: PEP 695 generics (``def f[T](...)``) would read better and are available in
#: the 3.12 the containers run. They are not used here because the host-side
#: CI checks parse this package with whatever interpreter the developer has, and
#: ``ast.parse`` on 3.11 rejects the syntax outright — reporting it as
#: "could not parse", which is indistinguishable from a syntax error in a check
#: whose whole job is to be trusted when it says "clean".
_Row = TypeVar("_Row", bound=tuple[Any, ...])


def apply_complaint_visibility(  # noqa: UP047 — see _Row above
    statement: Select[_Row], *, tenant_id: uuid.UUID, allowed: Visibility
) -> Select[_Row]:
    """Attach the visibility condition to a statement that selects complaints.

    A one-line helper, and it exists so the call site reads as one step
    ("restrict this query to what the caller may see") rather than as an
    ``if condition is not None`` that somebody will eventually write with the
    branch inverted.
    """
    condition = complaint_visibility_condition(tenant_id=tenant_id, allowed=allowed)
    return statement if condition is None else statement.where(condition)


__all__ = [
    "apply_complaint_visibility",
    "complaint_resource",
    "complaint_visibility_condition",
    "review_item_resource",
]
