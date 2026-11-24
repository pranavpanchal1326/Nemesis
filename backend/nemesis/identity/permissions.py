"""Every permission this system recognises, declared in one place.

**A permission is a string in code, not a row in a table, and that is the
opposite of the choice Phase 5 made for the taxonomy.** The reasoning is the
same reasoning applied to a different kind of noun. A taxonomy node is tenant
data: a campus genuinely needs "elevator fault", and no engineer can know that
in advance. A *permission* is the name of a code path — ``review:decide`` exists
because ``api/v1/review.py`` has a handler that records a judgement on a
citizen's report. A tenant cannot invent a permission, because inventing one
would mean inventing the endpoint it guards.

What a tenant composes freely is the **role**: any set of these permissions,
scoped to any part of its own department, zone, taxonomy or contractor trees.
That is critique-log defect #7's actual fix — the enum that needed deleting was
the *role* enum (``ward_officer``, ``department_head``), never the permission
vocabulary. ``db.models.identity.Role`` carries a permission array precisely so
"Ward Officer (South), nights only, no budget approval" is a row rather than a
release.

**Declaration is enforcement, not documentation.** Three mechanisms read this
module:

``scripts/check_endpoint_permissions.py``
    Fails the build if a route exists whose decorator declares neither a
    permission from this registry nor an explicit public exemption. That is the
    Phase 13 gate clause "adding an endpoint without a declared permission
    fails CI", and it is why the registry is a dict literal an AST walk can read
    without importing the application.
``identity.decisions``
    Refuses to evaluate a permission that is not declared here. An undeclared
    string would otherwise be a *deny* that looks like a bug, or a typo in a
    role that looks like a privilege nobody has — and the second is worse,
    because the role appears to work until the day it has to.
``identity.roles``
    Refuses to *store* an undeclared permission, so the typo is rejected when
    somebody writes it rather than discovered by an officer who cannot do their
    job at 23:00.

**``audited`` is not "is this important".** It selects which *allows* reach the
decision log. Denials are always written — a denial is rare, cheap, and the
single most useful record during an incident. Allows are the hot path:
``complaints:read`` allows would write one row per list request per officer per
refresh, which buys an unreadable table and write amplification nobody
budgeted. So ``audited=True`` marks the allows worth that cost: the ones that
change what the system means, move money, or read another person's data.

**``platform_only`` marks the permissions no customer role may hold.** Support
impersonation and the deployment's own integrity surface belong to the reserved
system tenant (``domain.constants.SYSTEM_TENANT_ID``). ``identity.roles``
refuses to put one of these in a customer tenant's role, which makes "a tenant
admin granted themselves impersonation" an impossible row rather than a thing
we hope nobody does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Permission:
    """One capability, and the facts the decision point needs about it."""

    #: ``resource:action``. The colon is load-bearing: the checker and the
    #: decision log both split on it, and a permission without an action half
    #: cannot be reasoned about ("may this role *read* or *write* policy?").
    name: str

    description: str

    #: Which resource kind this permission is evaluated against. ``None`` means
    #: the permission is tenant-global: there is no narrower thing to scope it
    #: to, so a department-scoped grant of it is meaningless and is rejected
    #: rather than silently widened.
    resource_kind: str | None

    #: Write every *allow*, not only every deny. See the module docstring.
    audited: bool = False

    #: May only be held by a role in the reserved system tenant.
    platform_only: bool = False

    @property
    def action(self) -> str:
        return self.name.split(":", 1)[1]

    @property
    def resource(self) -> str:
        return self.name.split(":", 1)[0]

    @property
    def is_scopable(self) -> bool:
        """Whether a department/zone/taxonomy/contractor-scoped grant is legal."""
        return self.resource_kind is not None


class UnknownPermissionError(LookupError):
    """A permission string that no code path declares.

    Its own exception type rather than ``ValueError``: ``identity.roles`` turns
    it into a 422 with the sorted vocabulary attached and ``decisions`` turns it
    into a hard failure, and neither call site should have to string-match an
    error message to tell it apart from any other bad input.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"unknown permission {name!r}. Declared permissions are "
            f"{sorted(PERMISSIONS)}. A permission names a code path, so a new one "
            f"arrives with the endpoint it guards — never from configuration."
        )


def _declare(*permissions: Permission) -> dict[str, Permission]:
    """Index the declarations, refusing a duplicate or a malformed name.

    A duplicate name is not a style problem. Two ``Permission`` objects with one
    name means one of them is unreachable, and which one wins depends on dict
    ordering — so the facts the decision point reads (``audited``,
    ``platform_only``) would depend on the order somebody typed the lines in.
    """
    index: dict[str, Permission] = {}
    for permission in permissions:
        if ":" not in permission.name:
            raise ValueError(
                f"permission {permission.name!r} must be 'resource:action'; the decision "
                f"log and the CI checker both split on the colon"
            )
        if permission.name in index:
            raise ValueError(f"permission {permission.name!r} declared twice")
        index[permission.name] = permission
    return index


#: Resource kinds a grant may be scoped by. Not a database enum, for the reason
#: ``pipeline_dead_letters.stage`` gives: the vocabulary belongs to the code that
#: resolves each scope, and a Postgres type would be a second thing to migrate
#: every time a phase adds one.
SCOPE_DEPARTMENT: Final = "department"
SCOPE_ZONE: Final = "zone"
SCOPE_TAXONOMY: Final = "taxonomy_node"
#: Scoped to one contractor organisation. The *value* is ``contractor_org`` and
#: not ``contractor`` on purpose: ``contractor`` is a role name a tenant ships
#: with (``roles.json``), and ``check_domain_literals.py`` rightly refuses a
#: tenant role name compiled into a domain module. The collision was a word, not
#: a concept — a scope over an organisation and a job title are different nouns —
#: and spelling it out is cheaper than an exemption that reads as "we needed the
#: word".
SCOPE_CONTRACTOR: Final = "contractor_org"
SCOPE_TENANT: Final = "tenant"
#: "Only the rows this user is personally attached to" — the §18.1 field-staff
#: line, which is a *relationship* and not a place. Kept in the same vocabulary
#: because it is the same column: a grant is one row whether it names a ward or
#: names the holder.
SCOPE_SELF: Final = "self"

SCOPE_KINDS: Final[tuple[str, ...]] = (
    SCOPE_TENANT,
    SCOPE_DEPARTMENT,
    SCOPE_ZONE,
    SCOPE_TAXONOMY,
    SCOPE_CONTRACTOR,
    SCOPE_SELF,
)

#: Resource kinds, as the decision point names the thing being acted on.
KIND_COMPLAINT: Final = "complaint"
KIND_REVIEW_ITEM: Final = "review_item"
KIND_POLICY: Final = "policy"
KIND_TAXONOMY: Final = "taxonomy"
KIND_ORGANISATION: Final = "organisation"
KIND_CONTRACTOR: Final = "contractor_org"
KIND_USER: Final = "user"
KIND_SIMULATION: Final = "simulation"
KIND_INTEGRATION: Final = "integration"
KIND_TENANT: Final = "tenant"


PERMISSIONS: Final[dict[str, Permission]] = _declare(
    # --- citizen reports ---------------------------------------------------
    Permission(
        name="complaints:read",
        description="Read complaints, including citizen-supplied text and redacted media",
        resource_kind=KIND_COMPLAINT,
    ),
    Permission(
        name="complaints:read_reporter",
        description=(
            "Read the reporter's contact details on a complaint. Separate from "
            "complaints:read because §22 treats the citizen's phone number as a different "
            "disclosure from the pothole's photograph, and a triage officer needs the "
            "second without the first"
        ),
        resource_kind=KIND_COMPLAINT,
        audited=True,
    ),
    # --- trust & safety review queue ---------------------------------------
    Permission(
        name="review:read",
        description="Read the Phase 8 human review queue and the evidence behind each stop",
        resource_kind=KIND_REVIEW_ITEM,
    ),
    Permission(
        name="review:decide",
        description="Record the judgement on a flagged complaint",
        resource_kind=KIND_REVIEW_ITEM,
        audited=True,
    ),
    # --- control plane: what a complaint means -----------------------------
    Permission(
        name="taxonomy:read",
        description="Read the tenant's taxonomy, calendars, locales and organisation trees",
        resource_kind=KIND_TAXONOMY,
    ),
    Permission(
        name="taxonomy:write",
        description="Redefine the taxonomy — what a complaint is",
        resource_kind=KIND_TAXONOMY,
        audited=True,
    ),
    Permission(
        name="organisation:write",
        description="Create and amend departments, zones, shifts and contractor records",
        resource_kind=KIND_ORGANISATION,
        audited=True,
    ),
    Permission(
        name="publication:write",
        description="Change what this tenant publishes about itself on the public surface",
        # ``None``, not ``KIND_TENANT``: the subject of this permission *is*
        # the tenant, so there is nothing narrower to scope it to, and a
        # department-scoped grant of it would read to a reviewer as a
        # restriction while restricting nothing. ``roles.assert_scope_fits_permissions``
        # refuses that grant because of this line.
        resource_kind=None,
        audited=True,
    ),
    # --- policy & simulation -----------------------------------------------
    Permission(
        name="policy:read",
        description="Read policy documents, revisions and effective values",
        resource_kind=KIND_POLICY,
    ),
    Permission(
        name="policy:draft",
        description="Draft and amend a policy revision that is not yet live",
        resource_kind=KIND_POLICY,
    ),
    Permission(
        name="policy:activate",
        description="Make a policy revision live — severity weights, SLAs, routing, dedup bands",
        resource_kind=KIND_POLICY,
        audited=True,
    ),
    Permission(
        name="simulation:read",
        description="Read simulation and backtest results",
        resource_kind=KIND_SIMULATION,
    ),
    Permission(
        name="simulation:run",
        description="Run a backtest or shadow evaluation against real history",
        resource_kind=KIND_SIMULATION,
    ),
    # --- identity itself ---------------------------------------------------
    Permission(
        name="users:read",
        description="List the tenant's users and their grants",
        resource_kind=KIND_USER,
    ),
    Permission(
        name="users:write",
        description="Create, amend and deactivate users",
        resource_kind=KIND_USER,
        audited=True,
    ),
    Permission(
        name="roles:read",
        description="Read the tenant's roles and what each one permits",
        resource_kind=KIND_USER,
    ),
    Permission(
        name="roles:write",
        description="Compose roles from permissions",
        resource_kind=KIND_USER,
        audited=True,
    ),
    Permission(
        name="grants:write",
        description="Assign and revoke a role on a scope, for anyone in the tenant",
        resource_kind=KIND_USER,
        audited=True,
    ),
    Permission(
        name="grants:delegate",
        description=(
            "Hand a subset of one's own authority to somebody else for a bounded window "
            "— leave and shift cover. Never more than the delegator holds; identity.roles "
            "enforces that and a property test proves it"
        ),
        resource_kind=KIND_USER,
        audited=True,
    ),
    Permission(
        name="contractor_users:manage",
        description="Manage the sub-users of a contractor organisation",
        resource_kind=KIND_CONTRACTOR,
        audited=True,
    ),
    Permission(
        name="decisions:read",
        description="Read the authorization decision log for this tenant",
        resource_kind=KIND_USER,
    ),
    Permission(
        name="impersonation:read",
        description=(
            "Read the impersonation sessions taken against this tenant. Held by tenant "
            "roles, not by support: the gate clause is that impersonation is visible to "
            "the impersonated party"
        ),
        # Tenant-global for the reason ``publication:write`` gives.
        resource_kind=None,
    ),
    # --- machine credentials -----------------------------------------------
    Permission(
        name="integrations:manage",
        description="Mint and revoke API keys and webhook subscriptions",
        resource_kind=KIND_INTEGRATION,
        audited=True,
    ),
    # --- platform-only -----------------------------------------------------
    Permission(
        name="tenants:provision",
        description="Create a tenant",
        resource_kind=None,
        audited=True,
        platform_only=True,
    ),
    Permission(
        name="impersonation:start",
        description=(
            "Begin an audited support session acting as a tenant user. Requires a "
            "justification, is capped in duration, and writes an event into the "
            "impersonated tenant's own log"
        ),
        resource_kind=None,
        audited=True,
        platform_only=True,
    ),
    Permission(
        name="platform:read",
        description="Read the deployment's integrity findings and degradation history",
        resource_kind=None,
        platform_only=True,
    ),
)

#: Permissions a customer tenant may compose into a role.
TENANT_PERMISSIONS: Final[frozenset[str]] = frozenset(
    name for name, permission in PERMISSIONS.items() if not permission.platform_only
)

PLATFORM_PERMISSIONS: Final[frozenset[str]] = frozenset(
    name for name, permission in PERMISSIONS.items() if permission.platform_only
)


def get(name: str) -> Permission:
    """Resolve a declared permission, or fail loudly.

    A bare ``KeyError`` would be caught by a generic handler somewhere and
    become a 500 with no explanation. This says what went wrong and what the
    legal values are, because the reader is almost always somebody who typo'd a
    permission in a role they were composing.
    """
    try:
        return PERMISSIONS[name]
    except KeyError:
        raise UnknownPermissionError(name) from None


def exists(name: str) -> bool:
    return name in PERMISSIONS


__all__ = [
    "KIND_COMPLAINT",
    "KIND_CONTRACTOR",
    "KIND_INTEGRATION",
    "KIND_ORGANISATION",
    "KIND_POLICY",
    "KIND_REVIEW_ITEM",
    "KIND_SIMULATION",
    "KIND_TAXONOMY",
    "KIND_TENANT",
    "KIND_USER",
    "PERMISSIONS",
    "PLATFORM_PERMISSIONS",
    "SCOPE_CONTRACTOR",
    "SCOPE_DEPARTMENT",
    "SCOPE_KINDS",
    "SCOPE_SELF",
    "SCOPE_TAXONOMY",
    "SCOPE_TENANT",
    "SCOPE_ZONE",
    "TENANT_PERMISSIONS",
    "Permission",
    "UnknownPermissionError",
    "exists",
    "get",
]
