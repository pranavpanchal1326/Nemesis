"""Composing roles, granting them on scopes, and delegating a subset of them.

**The invariant that makes delegation safe is enforced here and nowhere else:
nobody may delegate authority they do not hold.** It is stated once, in
``assert_within_delegator_authority``, and proved by a property test over
generated grant matrices — because it is the rule most likely to be broken by a
convenience somebody adds later ("let a department head cover any ward during
an emergency"), and the breakage would be silent and permanent: the delegated
grant outlives the emergency and nothing in the system can tell it apart from
one that was always legitimate.

The rule has three parts, and each one is a way the obvious implementation gets
it wrong:

1. **Every permission** in the delegated role must be held by the delegator.
   Checking "the delegator holds *some* of these" would let a role with one
   shared permission carry nine others across.
2. On a scope that **contains** the delegated scope. Holding ``review:decide``
   on Ward 4 does not let you delegate it on Ward 7, and — less obviously —
   does not let you delegate it tenant-wide, which is the direction a
   ``is_descendant`` call written the wrong way round permits.
3. For a **bounded window**. A delegation with no end is a standing grant that
   arrived through the side door, so ``valid_until`` is required and capped.

**Scope references have no foreign key, and this module is why that is
tolerable.** ``role_grants.scope_ref`` points at one of four tables depending on
``scope_kind`` (the column comment argues the alternatives), so referential
integrity is enforced on write here — resolve the row, in this tenant, or refuse
— and again at decision time, where a scope that no longer resolves denies
rather than defaults.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.control_plane.hierarchy import is_descendant
from nemesis.db.models.identity import Role, RoleGrant
from nemesis.db.models.organisation import Contractor, Department, User, Zone
from nemesis.db.models.taxonomy import TaxonomyNode
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.identity import permissions as perms
from nemesis.identity.errors import ConflictError, NotFoundError, ValidationError
from nemesis.identity.scopes import Grant
from nemesis.tenancy.context import tenant_scope

#: The longest a delegation may run without being renewed. Leave cover is
#: measured in weeks; a grant that outlives the leave is the thing this cap
#: exists to prevent. Renewal is deliberately a second deliberate act.
MAX_DELEGATION_DAYS: Final = 90


@dataclass(frozen=True, slots=True)
class ScopeRef:
    """A resolved scope: what kind, which row, and its materialised path."""

    kind: str
    ref: uuid.UUID | None
    path: str | None


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def normalise_permissions(names: Iterable[str], *, tenant_id: uuid.UUID) -> list[str]:
    """Validate, deduplicate and sort a permission set.

    Sorted so two roles that permit the same things store the same array — the
    column is compared in tests and rendered in a UI, and "the same role in a
    different order" is a diff nobody wants to read.

    The platform-only check is here rather than in the API layer because the CLI
    and the provisioning templates write roles too, and a rule enforced at one
    of three entry points is a rule with two ways around it.
    """
    requested = sorted({name.strip() for name in names if name.strip()})
    if not requested:
        raise ValidationError(
            "a role with no permissions permits nothing; it would present to its "
            "holder as an account that cannot sign in to anything"
        )

    unknown = [name for name in requested if not perms.exists(name)]
    if unknown:
        raise ValidationError(
            f"unknown permission(s) {unknown}. A permission names a code path, so a new "
            f"one arrives with the endpoint it guards. Declared: {sorted(perms.PERMISSIONS)}"
        )

    if tenant_id != SYSTEM_TENANT_ID:
        forbidden = [name for name in requested if perms.get(name).platform_only]
        if forbidden:
            raise ValidationError(
                f"permission(s) {forbidden} may only be held in the reserved platform "
                f"tenant. A customer role carrying impersonation:start would let a "
                f"tenant administrator grant themselves the support path"
            )
    return requested


async def create_role(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    name: str,
    permissions: Iterable[str],
    description: str | None = None,
    is_system: bool = False,
) -> Role:
    """Compose a role. ``code`` is unique per tenant."""
    validated = normalise_permissions(permissions, tenant_id=tenant_id)
    with tenant_scope(tenant_id):
        existing = (
            await session.execute(
                select(func.count())
                .select_from(Role)
                .where(Role.tenant_id == tenant_id, Role.code == code)
            )
        ).scalar_one()
        if existing:
            raise ConflictError(f"a role with code {code!r} already exists in this tenant")

        row = Role(
            tenant_id=tenant_id,
            code=code,
            name=name,
            description=description,
            permissions=validated,
            is_system=is_system,
        )
        session.add(row)
        await session.flush()
    return row


async def update_role(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    role_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
    permissions: Iterable[str] | None = None,
    is_active: bool | None = None,
) -> Role:
    """Amend a role. Every live grant of it sees the change on the next decision.

    That immediacy is the point of roles being separate from grants, and it is
    also the sharpest edge in this module: removing a permission from a role
    removes it from everybody holding that role, instantly, with no list of who
    was affected. The API layer answers that by returning the grant count, so the
    administrator reads "this will change access for 41 people" before pressing
    the button rather than after.
    """
    row = await get_role(session, tenant_id=tenant_id, role_id=role_id)
    if row.is_system:
        raise ValidationError(
            "this role ships with the platform and is not editable. Copy it and edit "
            "the copy — an edited system role would be silently different between two "
            "deployments running the same version"
        )

    # An explicit ``update()`` rather than mutating the loaded row: an ORM
    # flush emits ``UPDATE roles WHERE id = ?`` with no tenant predicate, and
    # ``tenancy.guard`` refuses a write nothing can prove was scoped.
    changes: dict[str, object] = {}
    if name is not None:
        changes["name"] = name
    if description is not None:
        changes["description"] = description
    if permissions is not None:
        changes["permissions"] = normalise_permissions(permissions, tenant_id=tenant_id)
    if is_active is not None:
        changes["is_active"] = is_active

    if not changes:
        return row

    with tenant_scope(tenant_id):
        await session.execute(
            update(Role).where(Role.tenant_id == tenant_id, Role.id == role_id).values(**changes)
        )
    return await get_role(session, tenant_id=tenant_id, role_id=role_id)


async def get_role(session: AsyncSession, *, tenant_id: uuid.UUID, role_id: uuid.UUID) -> Role:
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(Role).where(Role.tenant_id == tenant_id, Role.id == role_id)
            )
        ).scalar_one_or_none()
    if row is None:
        # Never "exists but not yours" — see `identity.errors.NotFoundError`.
        raise NotFoundError(f"no role {role_id} in this tenant")
    return row


async def list_roles(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Role]:
    with tenant_scope(tenant_id):
        return list(
            (
                await session.execute(
                    select(Role).where(Role.tenant_id == tenant_id).order_by(Role.code)
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def _scope_statement(kind: str, *, tenant_id: uuid.UUID, ref: uuid.UUID) -> Select[tuple[str]]:
    """The one-row path lookup for each scope kind.

    A dict of (table, path column) would be shorter and would also mean the
    tenant predicate is written once and applied by a loop — which is exactly
    the shape ``check_tenant_scoping.py`` cannot read, because the table is no
    longer a literal in the statement. Four explicit statements stay visible to
    the static check.
    """
    if kind == perms.SCOPE_DEPARTMENT:
        return select(Department.path).where(
            Department.tenant_id == tenant_id, Department.id == ref
        )
    if kind == perms.SCOPE_ZONE:
        return select(Zone.path).where(Zone.tenant_id == tenant_id, Zone.id == ref)
    if kind == perms.SCOPE_TAXONOMY:
        return select(TaxonomyNode.path).where(
            TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.id == ref
        )
    # Contractor is deliberately absent: it is not a tree, so there is no path
    # to select and matching is equality on the id. ``resolve_scope`` handles it
    # before reaching here, and a branch returning an id as a "path" would make
    # every reader of ``Grant.scope_path`` check which kind it was looking at.
    raise ValidationError(f"scope kind {kind!r} has no path to resolve")


async def resolve_scope(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    ref: uuid.UUID | None,
) -> ScopeRef:
    """Check that a scope exists in this tenant, and return its path.

    This is the referential integrity ``role_grants.scope_ref`` gives up by not
    being a foreign key. A scope that does not resolve is refused here on write;
    one that stops resolving later (a department deleted) denies at decision
    time rather than matching everything, which is the direction that matters.
    """
    if kind not in perms.SCOPE_KINDS:
        raise ValidationError(
            f"unknown scope kind {kind!r}; legal kinds are {list(perms.SCOPE_KINDS)}"
        )

    if kind in {perms.SCOPE_TENANT, perms.SCOPE_SELF}:
        if ref is not None:
            raise ValidationError(f"a {kind!r} scope names no row; drop the reference")
        return ScopeRef(kind=kind, ref=None, path=None)

    if ref is None:
        raise ValidationError(f"a {kind!r} scope must name the row it is scoped to")

    with tenant_scope(tenant_id):
        if kind == perms.SCOPE_CONTRACTOR:
            exists = (
                await session.execute(
                    select(Contractor.id).where(
                        Contractor.tenant_id == tenant_id, Contractor.id == ref
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                raise NotFoundError(f"no contractor {ref} in this tenant")
            return ScopeRef(kind=kind, ref=ref, path=None)

        path = (
            await session.execute(_scope_statement(kind, tenant_id=tenant_id, ref=ref))
        ).scalar_one_or_none()
    if path is None:
        raise NotFoundError(f"no {kind} {ref} in this tenant")
    return ScopeRef(kind=kind, ref=ref, path=str(path))


def assert_scope_fits_permissions(*, permissions: Sequence[str], kind: str) -> None:
    """Refuse a narrow scope on a permission that has nothing to narrow.

    ``tenants:provision`` scoped to a ward is not a tighter grant, it is a
    meaningless one — and the dangerous reading is the charitable one, where a
    reviewer assumes it must be restricting something. The registry's
    ``resource_kind=None`` is what says a permission is tenant-global, so this
    reads the declaration rather than keeping a second list.
    """
    if kind == perms.SCOPE_TENANT:
        return
    unscopable = [name for name in permissions if not perms.get(name).is_scopable]
    if unscopable:
        raise ValidationError(
            f"permission(s) {unscopable} are tenant-global and cannot be scoped to a "
            f"{kind}. Grant the role tenant-wide, or split the role so the scopable "
            f"permissions can be scoped on their own"
        )


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


async def grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    scope_kind: str = perms.SCOPE_TENANT,
    scope_ref: uuid.UUID | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    reason: str | None = None,
    delegated_by_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> RoleGrant:
    """Assign a role to a user over a scope.

    Not idempotent, and the duplicate is a 409 rather than a silent no-op. Two
    identical grants differ in exactly one way that matters — their validity
    windows — so "you already granted this" is information the caller needs: the
    likely intent was to *extend* an existing grant, and silently accepting a
    second row would leave two to revoke, one of which nobody remembers.
    """
    moment = now or datetime.now(tz=UTC)
    role = await get_role(session, tenant_id=tenant_id, role_id=role_id)
    if not role.is_active:
        raise ValidationError(
            f"role {role.code!r} is deactivated; reactivate it before granting it"
        )

    resolved = await resolve_scope(session, tenant_id=tenant_id, kind=scope_kind, ref=scope_ref)
    assert_scope_fits_permissions(permissions=role.permissions, kind=resolved.kind)

    if valid_until is not None and valid_until <= moment:
        raise ValidationError("valid_until is already in the past")
    if valid_until is not None and valid_from is not None and valid_until <= valid_from:
        raise ValidationError("valid_until must be after valid_from")

    await _assert_user_in_tenant(session, tenant_id=tenant_id, user_id=user_id)

    with tenant_scope(tenant_id):
        clash = (
            await session.execute(
                select(RoleGrant.id).where(
                    RoleGrant.tenant_id == tenant_id,
                    RoleGrant.user_id == user_id,
                    RoleGrant.role_id == role_id,
                    RoleGrant.scope_kind == resolved.kind,
                    RoleGrant.scope_ref.is_(None)
                    if resolved.ref is None
                    else RoleGrant.scope_ref == resolved.ref,
                    RoleGrant.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(
                f"this user already holds {role.code!r} on that scope (grant {clash}). "
                f"Revoke it or amend its window rather than adding a second"
            )

        row = RoleGrant(
            tenant_id=tenant_id,
            user_id=user_id,
            role_id=role_id,
            scope_kind=resolved.kind,
            scope_ref=resolved.ref,
            valid_from=valid_from,
            valid_until=valid_until,
            reason=reason,
            delegated_by_user_id=delegated_by_user_id,
        )
        session.add(row)
        await session.flush()
    return row


def assert_within_delegator_authority(
    *,
    delegator_grants: Sequence[Grant],
    permissions: Sequence[str],
    scope_kind: str,
    scope_path: str | None,
    scope_ref: uuid.UUID | None,
) -> None:
    """Nobody delegates what they do not hold. See the module docstring.

    Pure, and separate from ``delegate`` for the same reason
    ``identity.scopes.evaluate`` is pure: this is the rule a property test has to
    be able to hammer with generated matrices, and it cannot do that against a
    function that needs a database.
    """
    missing: list[str] = []
    for permission in permissions:
        if not any(
            held.permits(permission)
            and _delegator_scope_contains(
                held=held, scope_kind=scope_kind, scope_path=scope_path, scope_ref=scope_ref
            )
            for held in delegator_grants
        ):
            missing.append(permission)

    if missing:
        raise ValidationError(
            f"cannot delegate permission(s) {sorted(missing)} on that scope: the "
            f"delegator does not hold them there. Delegation hands over a subset of "
            f"one's own authority, never more"
        )


def _delegator_scope_contains(
    *,
    held: Grant,
    scope_kind: str,
    scope_path: str | None,
    scope_ref: uuid.UUID | None,
) -> bool:
    """Whether a grant the delegator holds covers the scope being delegated.

    The ``is_descendant`` argument order is the part worth staring at: the scope
    being *given away* must be at or below the scope *held*. Reversed, a ward
    officer could delegate tenant-wide authority, and the reversal reads
    perfectly naturally in English either way round.
    """
    if held.scope_kind == perms.SCOPE_TENANT:
        return True
    if scope_kind == perms.SCOPE_TENANT:
        # Holding anything narrower than the tenant cannot cover the tenant.
        return False
    if held.scope_kind != scope_kind:
        # A department grant says nothing about a zone. Cross-tree containment
        # ("Public Works covers Ward 4") is a real relationship and it is
        # Phase 12's routing table, not an authorization inference.
        return False
    if scope_kind == perms.SCOPE_SELF:
        # "Only your own rows" is not delegable: the rows are defined by who the
        # holder is, so handing it over changes which rows it means.
        return False
    if scope_kind == perms.SCOPE_CONTRACTOR:
        return held.scope_id is not None and held.scope_id == scope_ref
    if held.scope_path is None or scope_path is None:
        return False
    return is_descendant(scope_path, held.scope_path)


async def delegate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    delegator_user_id: uuid.UUID,
    delegator_grants: Sequence[Grant],
    to_user_id: uuid.UUID,
    role_id: uuid.UUID,
    scope_kind: str,
    scope_ref: uuid.UUID | None,
    valid_until: datetime,
    reason: str,
    now: datetime | None = None,
) -> RoleGrant:
    """Hand a bounded subset of one's own authority to a colleague.

    ``delegator_grants`` is passed in rather than loaded here. The caller — the
    API layer — already resolved the delegator's principal in order to check
    ``grants:delegate``, and loading it a second time would make this function's
    answer depend on a race with its own authorization check.
    """
    moment = now or datetime.now(tz=UTC)
    if delegator_user_id == to_user_id:
        raise ValidationError("delegating to oneself changes nothing")
    if not reason or len(reason.strip()) < 10:
        raise ValidationError(
            "a delegation needs a reason — 'covering for Priya, 14-21 March'. An "
            "unexplained temporary grant is indistinguishable from a mistake six "
            "months later"
        )
    if valid_until <= moment:
        raise ValidationError("valid_until is already in the past")
    horizon = moment + timedelta(days=MAX_DELEGATION_DAYS)
    if valid_until > horizon:
        raise ValidationError(
            f"a delegation may run at most {MAX_DELEGATION_DAYS} days. Renew it, or ask "
            f"an administrator for a grant — an unbounded delegation is a standing "
            f"grant that arrived through the side door"
        )

    role = await get_role(session, tenant_id=tenant_id, role_id=role_id)
    resolved = await resolve_scope(session, tenant_id=tenant_id, kind=scope_kind, ref=scope_ref)
    assert_scope_fits_permissions(permissions=role.permissions, kind=resolved.kind)
    assert_within_delegator_authority(
        delegator_grants=delegator_grants,
        permissions=role.permissions,
        scope_kind=resolved.kind,
        scope_path=resolved.path,
        scope_ref=resolved.ref,
    )

    return await grant(
        session,
        tenant_id=tenant_id,
        user_id=to_user_id,
        role_id=role_id,
        scope_kind=resolved.kind,
        scope_ref=resolved.ref,
        valid_from=moment,
        valid_until=valid_until,
        reason=reason,
        delegated_by_user_id=delegator_user_id,
        now=moment,
    )


async def revoke_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    reason: str,
    now: datetime | None = None,
) -> None:
    """Revoke a grant. The row stays, revoked, for the audit trail.

    Deletion would make "what could this person do on 3 March" unanswerable,
    which is the question every access review and every incident asks.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        result = await session.execute(
            update(RoleGrant)
            .where(
                RoleGrant.tenant_id == tenant_id,
                RoleGrant.id == grant_id,
                RoleGrant.revoked_at.is_(None),
            )
            .values(revoked_at=moment, revoked_reason=reason)
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            exists = (
                await session.execute(
                    select(func.count())
                    .select_from(RoleGrant)
                    .where(RoleGrant.tenant_id == tenant_id, RoleGrant.id == grant_id)
                )
            ).scalar_one()
            if not exists:
                raise NotFoundError(f"no grant {grant_id} in this tenant")


async def load_live_grants(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[Grant, ...]:
    """Every grant that authorises *right now*, with scope paths resolved.

    Two statements, not one per grant. The join to ``roles`` is unavoidable
    (permissions live there, and must be read live rather than cached onto the
    grant — see ``scopes.Grant``), and the paths are then resolved in one
    statement per tree for the ids actually referenced. A naive implementation
    is N+1 on the hottest path in the system.

    A grant whose scope no longer resolves is **dropped**, not kept with a null
    path. Keeping it would hand the decision point a grant it must then
    special-case, and the correct reading of "scoped to a department that no
    longer exists" is that it authorises nothing.
    """
    moment = now or datetime.now(tz=UTC)
    with tenant_scope(tenant_id):
        rows = list(
            (
                await session.execute(
                    select(RoleGrant, Role)
                    .join(Role, Role.id == RoleGrant.role_id)
                    .where(
                        RoleGrant.tenant_id == tenant_id,
                        RoleGrant.user_id == user_id,
                        RoleGrant.revoked_at.is_(None),
                        Role.tenant_id == tenant_id,
                        Role.is_active.is_(True),
                    )
                )
            ).all()
        )

    live = [
        (row, role)
        for row, role in rows
        if (row.valid_from is None or row.valid_from <= moment)
        and (row.valid_until is None or row.valid_until > moment)
    ]
    if not live:
        return ()

    paths = await _resolve_paths(
        session,
        tenant_id=tenant_id,
        wanted={
            (row.scope_kind, row.scope_ref)
            for row, _ in live
            if row.scope_ref is not None
            and row.scope_kind in {perms.SCOPE_DEPARTMENT, perms.SCOPE_ZONE, perms.SCOPE_TAXONOMY}
        },
    )

    grants: list[Grant] = []
    for row, role in live:
        path: str | None = None
        if row.scope_kind in {perms.SCOPE_DEPARTMENT, perms.SCOPE_ZONE, perms.SCOPE_TAXONOMY}:
            path = paths.get((row.scope_kind, row.scope_ref))
            if path is None:
                continue
        grants.append(
            Grant(
                id=row.id,
                role_id=role.id,
                role_code=role.code,
                permissions=frozenset(role.permissions),
                scope_kind=row.scope_kind,
                scope_id=row.scope_ref,
                scope_path=path,
                delegated_by_user_id=row.delegated_by_user_id,
            )
        )
    return tuple(grants)


async def _resolve_paths(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    wanted: set[tuple[str, uuid.UUID | None]],
) -> dict[tuple[str, uuid.UUID | None], str]:
    """One statement per tree, for the ids actually referenced."""
    if not wanted:
        return {}

    resolved: dict[tuple[str, uuid.UUID | None], str] = {}
    by_kind: dict[str, list[uuid.UUID]] = {}
    for kind, ref in wanted:
        if ref is not None:
            by_kind.setdefault(kind, []).append(ref)

    with tenant_scope(tenant_id):
        if department_ids := by_kind.get(perms.SCOPE_DEPARTMENT):
            for row_id, path in (
                await session.execute(
                    select(Department.id, Department.path).where(
                        Department.tenant_id == tenant_id, Department.id.in_(department_ids)
                    )
                )
            ).tuples():
                resolved[(perms.SCOPE_DEPARTMENT, row_id)] = path
        if zone_ids := by_kind.get(perms.SCOPE_ZONE):
            for row_id, path in (
                await session.execute(
                    select(Zone.id, Zone.path).where(
                        Zone.tenant_id == tenant_id, Zone.id.in_(zone_ids)
                    )
                )
            ).tuples():
                resolved[(perms.SCOPE_ZONE, row_id)] = path
        if node_ids := by_kind.get(perms.SCOPE_TAXONOMY):
            for row_id, path in (
                await session.execute(
                    select(TaxonomyNode.id, TaxonomyNode.path).where(
                        TaxonomyNode.tenant_id == tenant_id, TaxonomyNode.id.in_(node_ids)
                    )
                )
            ).tuples():
                resolved[(perms.SCOPE_TAXONOMY, row_id)] = path
    return resolved


async def list_grants(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    include_revoked: bool = False,
) -> list[tuple[RoleGrant, Role]]:
    """Grants with their roles, for an access review."""
    with tenant_scope(tenant_id):
        statement = (
            select(RoleGrant, Role)
            .join(Role, Role.id == RoleGrant.role_id)
            .where(RoleGrant.tenant_id == tenant_id, Role.tenant_id == tenant_id)
            .order_by(RoleGrant.created_at.desc())
        )
        if user_id is not None:
            statement = statement.where(RoleGrant.user_id == user_id)
        if not include_revoked:
            statement = statement.where(RoleGrant.revoked_at.is_(None))
        return [(row[0], row[1]) for row in (await session.execute(statement)).all()]


async def count_grants_of_role(
    session: AsyncSession, *, tenant_id: uuid.UUID, role_id: uuid.UUID
) -> int:
    """How many live grants a role has — what an edit is about to change."""
    with tenant_scope(tenant_id):
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(RoleGrant)
                    .where(
                        RoleGrant.tenant_id == tenant_id,
                        RoleGrant.role_id == role_id,
                        RoleGrant.revoked_at.is_(None),
                    )
                )
            ).scalar_one()
        )


async def _assert_user_in_tenant(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    with tenant_scope(tenant_id):
        found = (
            await session.execute(
                select(User.id).where(User.tenant_id == tenant_id, User.id == user_id)
            )
        ).scalar_one_or_none()
    if found is None:
        raise NotFoundError(f"no user {user_id} in this tenant")


__all__ = [
    "MAX_DELEGATION_DAYS",
    "ScopeRef",
    "assert_scope_fits_permissions",
    "assert_within_delegator_authority",
    "count_grants_of_role",
    "create_role",
    "delegate",
    "get_role",
    "grant",
    "list_grants",
    "list_roles",
    "load_live_grants",
    "normalise_permissions",
    "resolve_scope",
    "revoke_grant",
    "update_role",
]
