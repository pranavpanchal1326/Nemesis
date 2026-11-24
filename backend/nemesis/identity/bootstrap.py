"""Installing the shipped roles, and creating the first operator.

**Every role name in this system lives in data, including the ones NEMESIS
ships.** ``control_plane/templates/roles.json`` is the library; this module
applies it. That is not a style choice — ``scripts/check_domain_literals.py``
reads the shipped templates and fails the build if a domain module contains one
of the role names in them, which is critique-log defect #1 enforced
mechanically. A Python constant ``SUPPORT_ROLE = "platform_support"`` would
break that check, and it would deserve to: the next person to need a seventh
role would add a seventh constant.

**The bootstrap problem, stated honestly.** Somebody has to create the first
account that can create accounts, and whoever does it is by definition not
authorized by the system they are bootstrapping. Three answers were available:

1. A shipped default account with a known password. Rejected outright — it is
   the single most exploited class of defect in deployed software, and a
   deployment that forgets to change it has no authentication at all.
2. An environment variable holding a bootstrap password. Rejected: it is a
   credential that lives in a process environment forever, and ``docs/SECRETS.md``
   would have to carry a rotation procedure for something used once.
3. **A CLI that runs against the database, prints a one-time password, and
   refuses to run twice.** Chosen. It requires shell access to the deployment,
   which is the same authority a database password gives, so it grants nothing
   that was not already held — and ``ensure_first_operator`` returning ``None``
   when an operator already exists means an attacker with API access cannot
   re-run it to mint themselves one.

The control-plane shared token (ADR-0020) is what this replaces. It stays in
place until a deployment has run the bootstrap, because removing it first would
leave a running pilot with no way to configure anything — the migration path is
in ADR-0066 and ``docs/runbooks/``.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.identity import Role
from nemesis.db.models.organisation import User
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.identity import roles as role_service
from nemesis.identity import users as user_service
from nemesis.identity.errors import ConflictError, ValidationError
from nemesis.observability.logging import get_logger
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: Beside this module rather than in ``control_plane/templates/``, where it
#: first lived: that directory is scanned by the provisioning template loader,
#: which validates every ``*.json`` in it against ``TenantTemplate`` — so a role
#: library sitting there made every template listing fail validation. The file is
#: still *data* rather than code, which is the property that matters (see the
#: module docstring); where it sits is a loader's business.
ROLE_LIBRARY_PATH: Final = Path(__file__).resolve().parent / "role_library.json"

#: Characters in a generated bootstrap password. 32 URL-safe characters is ~190
#: bits — far beyond anything the scrypt parameters need to defend, which is the
#: point: the password is typed once, into a password manager, and changed.
BOOTSTRAP_PASSWORD_CHARS: Final = 32


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """One role from the shipped library."""

    code: str
    name: str
    description: str
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FirstOperator:
    """The bootstrap account. ``password`` exists here and nowhere else."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    password: str
    role_code: str


@lru_cache(maxsize=1)
def role_library() -> dict[str, tuple[RoleSpec, ...]]:
    """Parse and validate the shipped role library, once per process.

    Validation happens here rather than at use: a malformed library is a broken
    build, and the moment to find out is the first call in the test suite, not
    the middle of a customer's provisioning run.
    """
    raw: dict[str, Any] = json.loads(ROLE_LIBRARY_PATH.read_text(encoding="utf-8-sig"))
    library: dict[str, tuple[RoleSpec, ...]] = {}
    for section in ("platform", "tenant"):
        entries = raw.get(section)
        if not isinstance(entries, list) or not entries:
            raise ValidationError(f"role library section {section!r} is missing or empty")
        specs: list[RoleSpec] = []
        for entry in entries:
            permissions = tuple(entry["permissions"])
            if not permissions:
                raise ValidationError(f"role {entry['code']!r} permits nothing")
            specs.append(
                RoleSpec(
                    code=str(entry["code"]),
                    name=str(entry["name"]),
                    description=str(entry.get("description", "")),
                    permissions=permissions,
                )
            )
        codes = [spec.code for spec in specs]
        if len(set(codes)) != len(codes):
            raise ValidationError(f"role library section {section!r} has duplicate codes")
        library[section] = tuple(specs)
    return library


async def install_roles(session: AsyncSession, *, tenant_id: uuid.UUID, section: str) -> list[Role]:
    """Create the library's roles for a tenant, skipping any that exist.

    Idempotent, because it is called from provisioning *and* from the bootstrap
    CLI *and* from a migration-time backfill for tenants that predate this
    phase — and because a half-installed library is worse than either state.
    Skipping rather than updating is deliberate: a tenant that has edited a
    shipped role has made a decision, and a provisioning rerun must not quietly
    undo it.

    The roles are marked ``is_system`` so the API refuses to edit them in place.
    A customer who wants a different "Department head" copies it, which keeps
    the shipped definition identical across every deployment running this
    version — the thing that makes a support conversation about it possible.
    """
    specs = role_library()[section]
    with tenant_scope(tenant_id):
        existing = set(
            (await session.execute(select(Role.code).where(Role.tenant_id == tenant_id)))
            .scalars()
            .all()
        )

    created: list[Role] = []
    for spec in specs:
        if spec.code in existing:
            continue
        created.append(
            await role_service.create_role(
                session,
                tenant_id=tenant_id,
                code=spec.code,
                name=spec.name,
                permissions=list(spec.permissions),
                description=spec.description,
                is_system=True,
            )
        )
    if created:
        log.info(
            "roles.installed",
            tenant_id=str(tenant_id),
            section=section,
            codes=[role.code for role in created],
        )
    return created


async def ensure_first_operator(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
    role_code: str | None = None,
) -> FirstOperator | None:
    """Create the platform's first operator, or return ``None`` if one exists.

    ``None`` rather than an error, and that distinction carries the security
    property: this is reachable from a CLI that an operator may legitimately run
    twice (a script, a retry, a second engineer following the runbook), and the
    second run must be a no-op that reveals nothing. An exception would be a
    signal — "an operator already exists here" — to anybody who can trigger it.

    The role granted is the *first* platform role in the library unless named,
    which is the administrator rather than support: the account that bootstraps
    a deployment needs to provision tenants, and giving it impersonation as well
    would make the deployment's own first act the creation of an account that
    can read any customer's screen.
    """
    with tenant_scope(SYSTEM_TENANT_ID):
        operators = (
            await session.execute(
                select(func.count()).select_from(User).where(User.tenant_id == SYSTEM_TENANT_ID)
            )
        ).scalar_one()
    if operators:
        return None

    await install_roles(session, tenant_id=SYSTEM_TENANT_ID, section="platform")

    library = role_library()["platform"]
    wanted = role_code or next(
        spec.code for spec in library if "tenants:provision" in spec.permissions
    )
    with tenant_scope(SYSTEM_TENANT_ID):
        role = (
            await session.execute(
                select(Role).where(Role.tenant_id == SYSTEM_TENANT_ID, Role.code == wanted)
            )
        ).scalar_one_or_none()
    if role is None:
        raise ConflictError(
            f"the shipped role library has no platform role {wanted!r}; "
            f"available: {[spec.code for spec in library]}"
        )

    password = secrets.token_urlsafe(BOOTSTRAP_PASSWORD_CHARS)
    user = await user_service.create_user(
        session,
        tenant_id=SYSTEM_TENANT_ID,
        display_name=display_name,
        email=email,
        kind=user_service.KIND_OPERATOR,
        password=password,
    )
    await role_service.grant(
        session,
        tenant_id=SYSTEM_TENANT_ID,
        user_id=user.id,
        role_id=role.id,
        reason="deployment bootstrap",
    )
    log.warning(
        # Warning: the creation of the deployment's first privileged account is
        # a thing a human should see in the log, once, forever.
        "identity.bootstrapped",
        user_id=str(user.id),
        email=email,
        role=role.code,
    )
    return FirstOperator(
        user_id=user.id,
        tenant_id=SYSTEM_TENANT_ID,
        email=email,
        password=password,
        role_code=role.code,
    )


__all__ = [
    "BOOTSTRAP_PASSWORD_CHARS",
    "ROLE_LIBRARY_PATH",
    "FirstOperator",
    "RoleSpec",
    "ensure_first_operator",
    "install_roles",
    "role_library",
]
