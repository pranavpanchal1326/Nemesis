"""Who is asking, what they are asking about, and whether one contains the other.

**This module is pure.** No session, no clock, no settings — a principal, a
resource, and a set of grants in, a decision out. That is deliberate and it is
what makes the Phase 13 gate clause "authorization decisions are property-tested
against a generated role/scope matrix" achievable: Hypothesis can generate ten
thousand (role x scope x resource) combinations and assert invariants over them
in milliseconds, which is impossible against a function that needs a database to
answer.

The impure halves live next door and each owns exactly one concern:
``identity.decisions`` resolves grants and writes the log, ``identity.sessions``
builds the principal from a token.

**Containment is prefix containment over the materialised paths Phase 5 already
maintains**, via ``control_plane.hierarchy.is_descendant`` — the same function
the three trees use for every other ancestor question. Reimplementing it here
would be the drift that module's docstring warns about, and the failure mode is
specific and bad: a ``startswith`` without the separator makes a grant on
``roads`` cover ``roadside_waste``, which is a privilege nobody granted and
nobody can see.

**Default-deny is implemented as "a missing attribute denies", not as a final
``return False``.** The two are not the same. If a resource arrives with
``department_path=None`` and a grant is scoped to a department, the honest
answer is "this decision cannot be made", and the only safe rendering of that is
deny — a grant must *prove* containment. Treating the unknown as "not in any
department, therefore no grant applies" happens to give the same answer here,
but it would give the opposite answer the moment somebody adds a negative rule,
so the reasoning is written down rather than relied on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Final

from nemesis.control_plane.hierarchy import is_descendant
from nemesis.identity.permissions import (
    SCOPE_CONTRACTOR,
    SCOPE_DEPARTMENT,
    SCOPE_SELF,
    SCOPE_TAXONOMY,
    SCOPE_TENANT,
    SCOPE_ZONE,
)

#: Why a decision came out the way it did, as a short stable string. These reach
#: the decision log, the 403 body, and a Prometheus label, so they are a closed
#: vocabulary rather than prose: a free-text reason would make
#: ``authorization_decisions`` unqueryable and the metric unbounded in
#: cardinality, which Phase 1a's own checker would reject.
REASON_GRANTED: Final = "granted"
REASON_NO_PRINCIPAL: Final = "no_principal"
REASON_NO_GRANT: Final = "no_grant_for_permission"
REASON_OUT_OF_SCOPE: Final = "out_of_scope"
REASON_SCOPE_UNPROVABLE: Final = "scope_unprovable"
REASON_WRONG_TENANT: Final = "wrong_tenant"
REASON_PLATFORM_ONLY: Final = "platform_permission_in_tenant_role"
REASON_IMPERSONATION_READ_ONLY: Final = "impersonation_is_read_only"
REASON_SESSION_INVALID: Final = "session_invalid"

REASON_CODES: Final[tuple[str, ...]] = (
    REASON_GRANTED,
    REASON_NO_PRINCIPAL,
    REASON_NO_GRANT,
    REASON_OUT_OF_SCOPE,
    REASON_SCOPE_UNPROVABLE,
    REASON_WRONG_TENANT,
    REASON_PLATFORM_ONLY,
    REASON_IMPERSONATION_READ_ONLY,
    REASON_SESSION_INVALID,
)

#: Actions that mutate. Used only to decide whether an impersonated session may
#: proceed (ADR-0067: support reads by default and writes only when the
#: justification said so). Derived from the action half of a permission name
#: rather than from a per-permission flag, so a new read permission is read-only
#: without anybody remembering to mark it.
READ_ACTIONS: Final[frozenset[str]] = frozenset({"read", "read_reporter", "list", "export"})


@dataclass(frozen=True, slots=True)
class Grant:
    """One role, held by one user, over one scope, already time-filtered.

    ``permissions`` is the role's permission set copied onto the grant rather
    than a role id to dereference. The copy is made when the grant is *resolved*
    (``identity.decisions.load_grants``), never stored — a stored copy would be
    a cache of the role that goes stale the moment the role is edited, and the
    staleness would present as a permission somebody still has after it was
    removed.
    """

    id: uuid.UUID
    role_id: uuid.UUID
    role_code: str
    permissions: frozenset[str]
    scope_kind: str
    #: ``None`` for ``tenant`` and ``self`` scopes, which have no row to name.
    scope_id: uuid.UUID | None = None
    #: The materialised path of ``scope_id``, resolved at decision time. Never a
    #: stored column: reparenting a department must not leave grants pointing at
    #: where it used to be.
    scope_path: str | None = None
    #: Set when this grant was delegated rather than assigned. Carried into the
    #: decision log so "who gave them this" is answerable without a join.
    delegated_by_user_id: uuid.UUID | None = None

    def permits(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as every downstream layer needs them.

    **A principal is not a user row.** It is the user *plus* the session that
    authenticated them plus, when support is acting, the operator behind the
    session. The three are inseparable for audit: a decision recorded against
    ``user_id`` alone cannot distinguish the officer doing their job from a
    support engineer acting as that officer, and the gate clause requires the
    tenant to be able to tell those apart.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    subject: str
    session_id: uuid.UUID
    grants: tuple[Grant, ...] = ()
    display_name: str | None = None
    #: The contractor organisation this user belongs to, if any. Separate from a
    #: contractor-scoped grant: the grant says what they may do, this says who
    #: they are, and Phase 17's fraud surface needs the second even for a user
    #: whose grants have all expired.
    contractor_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None

    #: Support impersonation. ``None`` on an ordinary session.
    impersonator_subject: str | None = None
    impersonation_id: uuid.UUID | None = None
    impersonation_allows_writes: bool = False

    #: True for a principal in the reserved system tenant. Platform-only
    #: permissions are evaluable only for these.
    is_platform: bool = False

    @property
    def is_impersonated(self) -> bool:
        return self.impersonator_subject is not None

    @property
    def permissions(self) -> frozenset[str]:
        """Every permission held on *some* scope.

        Useful for a UI that greys out a button, and useless for a decision —
        holding ``review:decide`` somewhere says nothing about this review item.
        Named ``permissions`` rather than ``can`` so no call site mistakes it for
        an authorization check; ``decisions.authorize`` is the only check.
        """
        return (
            frozenset().union(*(grant.permissions for grant in self.grants))
            if self.grants
            else frozenset()
        )


@dataclass(frozen=True, slots=True)
class Resource:
    """The thing being acted on, described by the attributes a grant can test.

    Assembled by the handler from the row it already loaded. Every field is
    optional because the attributes that exist depend on the kind — a policy
    document has no zone — and a grant scoped to an attribute the resource does
    not carry denies rather than matches (see the module docstring).

    ``TENANT_WIDE`` is the sentinel for "this permission is about the tenant
    itself": provisioning, publication, reading the decision log. It is not the
    same as a resource whose attributes are unknown, and conflating the two is
    how a tenant-global permission ends up grantable on one ward.
    """

    kind: str
    id: uuid.UUID | None = None
    department_path: str | None = None
    zone_path: str | None = None
    taxonomy_path: str | None = None
    contractor_id: uuid.UUID | None = None
    #: Who this row is personally attached to — the assignee of a work order,
    #: the author of a draft. What ``self`` scope tests.
    attached_user_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


#: The resource for a permission with no narrower subject. ``kind`` matches the
#: ``resource_kind=None`` declaration in the registry.
TENANT_WIDE: Final = Resource(kind="tenant")


@dataclass(frozen=True, slots=True)
class Decision:
    """The answer, and enough of the reasoning to log and to render.

    ``matched_grant`` is the grant that allowed it — not "a" grant that could
    have. When two grants both permit, the first match wins and is recorded,
    because an audit answering "under what authority" needs one answer and
    "either of these two" is not one.
    """

    allowed: bool
    permission: str
    reason_code: str
    matched_grant: Grant | None = None

    @property
    def scope_description(self) -> str:
        """Human-readable scope of the matching grant, for the log and the UI."""
        if self.matched_grant is None:
            return "none"
        grant = self.matched_grant
        if grant.scope_kind == SCOPE_TENANT:
            return "tenant"
        if grant.scope_kind == SCOPE_SELF:
            return "self"
        return f"{grant.scope_kind}:{grant.scope_path or grant.scope_id}"


def grant_covers(
    *,
    grant: Grant,
    resource: Resource,
    principal_user_id: uuid.UUID,
) -> bool:
    """Whether ``grant``'s scope contains ``resource``.

    One function, one ``if`` per scope kind, and an unknown kind returns
    ``False``. The unknown branch is not dead code waiting for a bug: scope kinds
    are strings in a column (see ``permissions.SCOPE_KINDS`` for why they are not
    a database enum), so a row written by a future phase, a hand-fixed incident,
    or a restored backup from a newer schema can carry a kind this build does not
    know — and the safe reading of "I do not understand this grant" is that it
    grants nothing.
    """
    if grant.scope_kind == SCOPE_TENANT:
        return True

    if grant.scope_kind == SCOPE_SELF:
        return principal_user_id in resource.attached_user_ids

    if grant.scope_kind == SCOPE_CONTRACTOR:
        return (
            grant.scope_id is not None
            and resource.contractor_id is not None
            and grant.scope_id == resource.contractor_id
        )

    paths = {
        SCOPE_DEPARTMENT: resource.department_path,
        SCOPE_ZONE: resource.zone_path,
        SCOPE_TAXONOMY: resource.taxonomy_path,
    }
    if grant.scope_kind in paths:
        resource_path = paths[grant.scope_kind]
        if grant.scope_path is None or resource_path is None:
            # Unprovable, therefore denied. See the module docstring.
            return False
        return is_descendant(resource_path, grant.scope_path)

    return False


def evaluate(
    *,
    principal: Principal,
    permission: str,
    resource: Resource,
    requires_write: bool = False,
) -> Decision:
    """The pure decision. ``identity.decisions.authorize`` wraps it with I/O.

    Order of checks is chosen so the *reason* is the most useful one available.
    An impersonated support session attempting a write with no write
    authorisation is reported as ``impersonation_is_read_only`` even if the
    principal also lacks the grant, because the first fact is the one the
    operator needs and the second is a consequence of acting as somebody else.
    """
    if principal.is_impersonated and requires_write and not principal.impersonation_allows_writes:
        return Decision(
            allowed=False,
            permission=permission,
            reason_code=REASON_IMPERSONATION_READ_ONLY,
        )

    holding = [grant for grant in principal.grants if grant.permits(permission)]
    if not holding:
        return Decision(allowed=False, permission=permission, reason_code=REASON_NO_GRANT)

    for grant in holding:
        if grant_covers(grant=grant, resource=resource, principal_user_id=principal.user_id):
            return Decision(
                allowed=True,
                permission=permission,
                reason_code=REASON_GRANTED,
                matched_grant=grant,
            )

    # Held, but not here. Distinguished from "not held at all" because the two
    # have different remedies — widen an existing grant, or issue one — and
    # because an operator reading the decision log needs to know the difference
    # between a misconfigured scope and a missing role.
    unprovable = any(
        grant.scope_kind in {SCOPE_DEPARTMENT, SCOPE_ZONE, SCOPE_TAXONOMY}
        and _resource_path_for(resource, grant.scope_kind) is None
        for grant in holding
    )
    return Decision(
        allowed=False,
        permission=permission,
        reason_code=REASON_SCOPE_UNPROVABLE if unprovable else REASON_OUT_OF_SCOPE,
    )


def _resource_path_for(resource: Resource, scope_kind: str) -> str | None:
    if scope_kind == SCOPE_DEPARTMENT:
        return resource.department_path
    if scope_kind == SCOPE_ZONE:
        return resource.zone_path
    if scope_kind == SCOPE_TAXONOMY:
        return resource.taxonomy_path
    return None


@dataclass(frozen=True, slots=True)
class Visibility:
    """What a *list* query may return, for one principal and one permission.

    **The gate clause is why this type exists.** "Department head A provably
    cannot read department B's rows through any endpoint, **including error
    messages and pagination counts**" cannot be met by checking a decision per
    row: the count is computed in SQL before any row is seen, so the filter has
    to reach the query. ``evaluate`` answers "may I touch this one"; this
    answers "which ones exist for me", and both have to agree or the product has
    two access-control models.

    ``unrestricted`` is tenant-wide visibility. It is a separate flag rather
    than an empty constraint list because empty means *nothing is visible*, and
    those two readings of "no constraints" differ by the whole dataset. A bug
    that confused them would be invisible in a test with one tenant-wide grant.
    """

    permission: str
    unrestricted: bool = False
    department_paths: frozenset[str] = field(default_factory=frozenset)
    zone_paths: frozenset[str] = field(default_factory=frozenset)
    taxonomy_paths: frozenset[str] = field(default_factory=frozenset)
    contractor_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    #: True when a ``self``-scoped grant participates: the query must also admit
    #: rows attached to this user, as a union with any path constraints.
    includes_self: bool = False

    @property
    def permits_nothing(self) -> bool:
        return not (
            self.unrestricted
            or self.department_paths
            or self.zone_paths
            or self.taxonomy_paths
            or self.contractor_ids
            or self.includes_self
        )


def visibility(*, principal: Principal, permission: str) -> Visibility:
    """Collapse a principal's grants for one permission into a query filter.

    Paths are *not* minimised — a grant on ``pw`` and a grant on ``pw/south``
    both appear, even though the second is redundant. Minimising would be a few
    lines and would make the SQL marginally smaller; it would also mean the
    filter no longer corresponds one-to-one with the grants an operator can see
    in the UI, and "why does the query mention a ward I revoked" is a question
    worth never having to answer. The redundant prefix costs one more ``OR`` on
    an index-backed prefix scan.
    """
    result = Visibility(permission=permission)
    departments: set[str] = set()
    zones: set[str] = set()
    taxonomies: set[str] = set()
    contractors: set[uuid.UUID] = set()
    includes_self = False

    for grant in principal.grants:
        if not grant.permits(permission):
            continue
        if grant.scope_kind == SCOPE_TENANT:
            return Visibility(permission=permission, unrestricted=True)
        if grant.scope_kind == SCOPE_SELF:
            includes_self = True
        elif grant.scope_kind == SCOPE_CONTRACTOR and grant.scope_id is not None:
            contractors.add(grant.scope_id)
        elif grant.scope_kind == SCOPE_DEPARTMENT and grant.scope_path is not None:
            departments.add(grant.scope_path)
        elif grant.scope_kind == SCOPE_ZONE and grant.scope_path is not None:
            zones.add(grant.scope_path)
        elif grant.scope_kind == SCOPE_TAXONOMY and grant.scope_path is not None:
            taxonomies.add(grant.scope_path)

    return Visibility(
        permission=result.permission,
        unrestricted=False,
        department_paths=frozenset(departments),
        zone_paths=frozenset(zones),
        taxonomy_paths=frozenset(taxonomies),
        contractor_ids=frozenset(contractors),
        includes_self=includes_self,
    )


def is_write_action(permission: str) -> bool:
    """Whether this permission's action mutates. See ``READ_ACTIONS``."""
    _, _, action = permission.partition(":")
    return action not in READ_ACTIONS


__all__ = [
    "READ_ACTIONS",
    "REASON_CODES",
    "REASON_GRANTED",
    "REASON_IMPERSONATION_READ_ONLY",
    "REASON_NO_GRANT",
    "REASON_NO_PRINCIPAL",
    "REASON_OUT_OF_SCOPE",
    "REASON_PLATFORM_ONLY",
    "REASON_SCOPE_UNPROVABLE",
    "REASON_SESSION_INVALID",
    "REASON_WRONG_TENANT",
    "TENANT_WIDE",
    "Decision",
    "Grant",
    "Principal",
    "Resource",
    "Visibility",
    "evaluate",
    "grant_covers",
    "is_write_action",
    "visibility",
]
