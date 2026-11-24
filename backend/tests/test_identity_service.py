"""Credentials, sessions, roles, grants and support access, against Postgres.

The pure half of Phase 13 is tested in ``test_identity_authorization.py``. This
is the half that needs a real database, and the three things it is really
testing are the three that a mock would have been happy to get wrong: a
refresh-token *family* being revoked in one statement, a lockout surviving
because it is a column rather than a cache entry, and a grant whose scope no
longer resolves denying rather than matching.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.control_plane import organisation
from nemesis.control_plane.schemas import DepartmentSpec
from nemesis.db.models.event import Event
from nemesis.db.models.identity import Role, UserSession
from nemesis.domain.constants import SYSTEM_TENANT_ID
from nemesis.identity import bootstrap, impersonation, passwords, roles, sessions, users
from nemesis.identity import permissions as perms
from nemesis.identity.errors import (
    AuthenticationError,
    ConflictError,
    CredentialLockedError,
    NotFoundError,
    ValidationError,
)
from nemesis.identity.scopes import Principal
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

SECRET = "test-secret-not-a-real-one"
PASSWORD = "a-perfectly-adequate-passphrase"


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A committed session with the tenant bound. See ``test_control_plane_taxonomy``."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            yield session
            await session.commit()


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_a_password_verifies_and_a_wrong_one_does_not() -> None:
    hashed = passwords.hash_password(PASSWORD)
    assert passwords.verify(
        password=PASSWORD,
        salt=hashed.salt,
        digest=hashed.digest,
        params=hashed.params,
    )
    assert not passwords.verify(
        password=PASSWORD + "x",
        salt=hashed.salt,
        digest=hashed.digest,
        params=hashed.params,
    )


def test_two_hashes_of_one_password_differ() -> None:
    """A per-credential salt, so a shared password is not a shared digest."""
    first = passwords.hash_password(PASSWORD)
    second = passwords.hash_password(PASSWORD)
    assert first.digest != second.digest
    assert first.salt != second.salt


def test_unicode_normalisation_makes_one_password_one_password() -> None:
    """Composed vs combining accents: one passphrase, two byte strings.

    Without NFKC a user who sets their password on one keyboard cannot sign in
    from another, and the support call is unreproducible by whoever takes it.
    """
    composed = "contraseña-con-acento"
    decomposed = "contraseña-con-acento"
    hashed = passwords.hash_password(composed)
    assert passwords.verify(
        password=decomposed,
        salt=hashed.salt,
        digest=hashed.digest,
        params=hashed.params,
    )


def test_a_credential_made_with_weaker_parameters_is_flagged_for_rehash() -> None:
    assert passwords.needs_rehash(algorithm="scrypt", params={"n": 2**10, "r": 8, "p": 1})
    assert not passwords.needs_rehash(
        algorithm="scrypt",
        params={"n": passwords.DEFAULT_N, "r": passwords.DEFAULT_R, "p": passwords.DEFAULT_P},
    )


def test_an_unknown_algorithm_fails_closed() -> None:
    """A credential written by a build that knew something we do not."""
    assert not passwords.verify(
        password=PASSWORD, salt="00", digest="00", algorithm="argon2id", params={}
    )


def test_the_platform_length_floor_cannot_be_lowered_by_a_tenant() -> None:
    with pytest.raises(ValidationError):
        passwords.assert_acceptable("short", min_length=1)
    passwords.assert_acceptable(PASSWORD, min_length=1)


def test_a_devanagari_passphrase_is_measured_in_characters() -> None:
    """A byte floor would accept a shorter Latin password than a longer Marathi one."""
    passwords.assert_acceptable("पुणेमहानगरपालिका", min_length=12)


# ---------------------------------------------------------------------------
# Users and credentials
# ---------------------------------------------------------------------------


async def test_a_user_signs_in_and_is_rejected_with_a_wrong_password(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        created = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Priya Deshmukh",
            email="priya@example.org",
            password=PASSWORD,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        authenticated = await users.authenticate(
            session, tenant_id=tenant_id, identifier="priya@example.org", password=PASSWORD
        )
        assert authenticated.user.id == created.id
        # An administrator-set password must be changed: a password somebody
        # else chose is a password somebody else knows.
        assert authenticated.must_change_password

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError) as caught:
            await users.authenticate(
                session, tenant_id=tenant_id, identifier="priya@example.org", password="wrong"
            )
        assert caught.value.reason == "bad_password"


async def test_an_unknown_user_and_a_wrong_password_look_the_same_to_a_caller(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Both are ``AuthenticationError``; only the logged reason differs."""
    async with scoped(migrated_engine, tenant_id) as session:
        await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Priya",
            email="p@example.org",
            password=PASSWORD,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError) as unknown:
            await users.authenticate(
                session,
                tenant_id=tenant_id,
                identifier="nobody@example.org",
                password=PASSWORD,
            )
        with pytest.raises(AuthenticationError) as wrong:
            await users.authenticate(
                session, tenant_id=tenant_id, identifier="p@example.org", password="nope"
            )
    assert unknown.value.reason == "unknown_user"
    assert wrong.value.reason == "bad_password"
    assert type(unknown.value) is type(wrong.value)


async def test_a_user_in_another_tenant_cannot_sign_in_here(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The isolation guarantee at the authentication layer.

    The same address in two tenants is legitimate, which is why the sign-in
    endpoint takes a tenant at all — and why a credential must never be
    resolvable across one.
    """
    async with scoped(migrated_engine, other_tenant_id) as session:
        await users.create_user(
            session,
            tenant_id=other_tenant_id,
            display_name="Someone Else",
            email="shared@example.org",
            password=PASSWORD,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError) as caught:
            await users.authenticate(
                session,
                tenant_id=tenant_id,
                identifier="shared@example.org",
                password=PASSWORD,
            )
    assert caught.value.reason == "unknown_user"


async def test_lockout_is_enforced_from_the_row(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A lockout in a cache is a lockout an attacker clears with a deploy."""
    async with scoped(migrated_engine, tenant_id) as session:
        await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Locked Out",
            email="lock@example.org",
            password=PASSWORD,
        )
        policy = await users.effective_policy(session, tenant_id=tenant_id)

    for _ in range(policy.max_failed_attempts):
        async with scoped(migrated_engine, tenant_id) as session:
            with pytest.raises(AuthenticationError):
                await users.authenticate(
                    session,
                    tenant_id=tenant_id,
                    identifier="lock@example.org",
                    password="wrong",
                )

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(CredentialLockedError) as caught:
            # The *correct* password, refused: the lockout is about the
            # credential, not about this attempt.
            await users.authenticate(
                session,
                tenant_id=tenant_id,
                identifier="lock@example.org",
                password=PASSWORD,
            )
    assert caught.value.retry_after_seconds > 0


async def test_setting_a_password_clears_a_lockout(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Continuing to lock out somebody who proved they hold the account would
    punish the victim of the guessing attempt rather than the attacker."""
    async with scoped(migrated_engine, tenant_id) as session:
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Reset",
            email="reset@example.org",
            password=PASSWORD,
        )
        policy = await users.effective_policy(session, tenant_id=tenant_id)

    for _ in range(policy.max_failed_attempts):
        async with scoped(migrated_engine, tenant_id) as session:
            with pytest.raises(AuthenticationError):
                await users.authenticate(
                    session,
                    tenant_id=tenant_id,
                    identifier="reset@example.org",
                    password="wrong",
                )

    async with scoped(migrated_engine, tenant_id) as session:
        await users.set_password(
            session, tenant_id=tenant_id, user_id=user.id, password=PASSWORD + "-new"
        )

    async with scoped(migrated_engine, tenant_id) as session:
        authenticated = await users.authenticate(
            session,
            tenant_id=tenant_id,
            identifier="reset@example.org",
            password=PASSWORD + "-new",
        )
    assert not authenticated.must_change_password


async def test_a_deactivated_user_cannot_sign_in(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Gone",
            email="gone@example.org",
            password=PASSWORD,
        )
    async with scoped(migrated_engine, tenant_id) as session:
        await users.deactivate(session, tenant_id=tenant_id, user_id=user.id)

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError) as caught:
            await users.authenticate(
                session,
                tenant_id=tenant_id,
                identifier="gone@example.org",
                password=PASSWORD,
            )
    # The credential is deleted by deactivation, so this is where it stops —
    # before any password comparison. Either reason is a correct refusal; the
    # test asserts the *behaviour* and names the reason it expects.
    assert caught.value.reason in {"no_credential", "user_inactive"}


async def test_a_contractor_user_must_name_its_organisation(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError):
            await users.create_user(
                session,
                tenant_id=tenant_id,
                display_name="Crew",
                kind=users.KIND_CONTRACTOR,
            )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def test_a_session_resolves_and_a_revoked_one_does_not(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Revocation takes effect on the next request, not at token expiry."""
    from nemesis.identity import tokens

    async with scoped(migrated_engine, tenant_id) as session:
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Operator",
            email="op@example.org",
            password=PASSWORD,
        )
        issued = await sessions.issue(
            session,
            secret=SECRET,
            user=user,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            method=sessions.METHOD_PASSWORD,
        )

    claims = tokens.decode(issued.access_token, secret=SECRET)
    async with scoped(migrated_engine, tenant_id) as session:
        resolved = await sessions.resolve(session, claims=claims)
        assert resolved.user_id == user.id

    async with scoped(migrated_engine, tenant_id) as session:
        await sessions.revoke(
            session,
            tenant_id=tenant_id,
            session_id=issued.session_id,
            reason=sessions.REVOKED_SIGNED_OUT,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError) as caught:
            await sessions.resolve(session, claims=claims)
    assert caught.value.reason == "session_revoked"


async def test_refresh_rotation_replaces_the_token_and_keeps_the_family(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Operator",
            email="rot@example.org",
            password=PASSWORD,
        )
        first = await sessions.issue(
            session,
            secret=SECRET,
            user=user,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            method=sessions.METHOD_PASSWORD,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        second = await sessions.rotate(
            session,
            secret=SECRET,
            presented_refresh_token=first.refresh_token,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )

    assert second.refresh_token != first.refresh_token
    assert second.family_id == first.family_id


async def test_replaying_a_refresh_token_tears_down_the_whole_family(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The property that makes a stolen refresh token useless rather than eternal.

    A replay and a retry after a lost response are indistinguishable from here,
    so both get the strong answer — ``identity.sessions`` names the cost.
    """
    async with scoped(migrated_engine, tenant_id) as session:
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Operator",
            email="reuse@example.org",
            password=PASSWORD,
        )
        first = await sessions.issue(
            session,
            secret=SECRET,
            user=user,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            method=sessions.METHOD_PASSWORD,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        second = await sessions.rotate(
            session,
            secret=SECRET,
            presented_refresh_token=first.refresh_token,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError) as caught:
            await sessions.rotate(
                session,
                secret=SECRET,
                presented_refresh_token=first.refresh_token,
                access_ttl_seconds=900,
                refresh_ttl_seconds=3600,
            )
    assert caught.value.reason == "refresh_reuse_detected"

    # And the session minted by the legitimate rotation is dead too: the whole
    # family goes, because we cannot tell which half was the attacker.
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(AuthenticationError):
            await sessions.rotate(
                session,
                secret=SECRET,
                presented_refresh_token=second.refresh_token,
                access_ttl_seconds=900,
                refresh_ttl_seconds=3600,
            )

        rows = await sessions.list_for_user(session, tenant_id=tenant_id, user_id=user.id)
    assert all(row.revoked_at is not None for row in rows)
    assert any(row.revoked_reason == sessions.REVOKED_REUSE for row in rows)


async def test_revoking_for_a_user_can_spare_the_current_session(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A self-service password change must not sign you out of the tab you used."""
    async with scoped(migrated_engine, tenant_id) as session:
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Operator",
            email="spare@example.org",
            password=PASSWORD,
        )
        kept = await sessions.issue(
            session,
            secret=SECRET,
            user=user,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            method=sessions.METHOD_PASSWORD,
        )
        other = await sessions.issue(
            session,
            secret=SECRET,
            user=user,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            method=sessions.METHOD_PASSWORD,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        revoked = await sessions.revoke_for_user(
            session,
            tenant_id=tenant_id,
            user_id=user.id,
            reason=sessions.REVOKED_CREDENTIAL_CHANGED,
            except_session_id=kept.session_id,
        )
        assert revoked == 1

        rows = {
            row.id: row
            for row in await sessions.list_for_user(session, tenant_id=tenant_id, user_id=user.id)
        }
    assert rows[kept.session_id].revoked_at is None
    assert rows[other.session_id].revoked_at is not None


def test_a_client_address_is_truncated_before_it_is_stored() -> None:
    """§22 treats an officer's location as personal data too."""
    assert sessions.truncate_network("203.0.113.47") == "203.0.113.0/24"
    assert sessions.truncate_network("2001:db8:1234:5678::1") == "2001:db8:1234::/48"
    assert sessions.truncate_network("not-an-address") is None
    assert sessions.truncate_network(None) is None


# ---------------------------------------------------------------------------
# Roles and grants
# ---------------------------------------------------------------------------


async def test_a_role_cannot_hold_an_undeclared_permission(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError) as caught:
            await roles.create_role(
                session,
                tenant_id=tenant_id,
                code="typo",
                name="Typo",
                permissions=["review:decidr"],
            )
    assert "review:decidr" in str(caught.value)


async def test_a_customer_role_cannot_hold_a_platform_permission(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ "A tenant administrator granted themselves impersonation" is an
    impossible row rather than a thing we hope nobody does."""
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError):
            await roles.create_role(
                session,
                tenant_id=tenant_id,
                code="sneaky",
                name="Sneaky",
                permissions=["impersonation:start"],
            )


async def test_a_platform_role_may_hold_one(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, SYSTEM_TENANT_ID) as session:
        role = await roles.create_role(
            session,
            tenant_id=SYSTEM_TENANT_ID,
            code="support_test",
            name="Support",
            permissions=["impersonation:start", "platform:read"],
        )
    assert "impersonation:start" in role.permissions


async def test_a_duplicate_role_code_is_a_conflict(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="dup",
            name="First",
            permissions=["review:read"],
        )
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ConflictError):
            await roles.create_role(
                session,
                tenant_id=tenant_id,
                code="dup",
                name="Second",
                permissions=["review:read"],
            )


async def test_permissions_are_stored_sorted_and_deduplicated(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="tidy",
            name="Tidy",
            permissions=["review:read", "complaints:read", "review:read"],
        )
    assert role.permissions == ["complaints:read", "review:read"]


async def test_a_grant_on_a_department_resolves_its_path(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await organisation.import_departments(
            session,
            tenant_id=tenant_id,
            specs=[
                DepartmentSpec.model_validate({"code": "pw", "name": "Public Works"}),
                DepartmentSpec.model_validate(
                    {"code": "south", "name": "South Division", "parent_code": "pw"}
                ),
            ],
        )
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="head",
            name="Head",
            permissions=["review:read"],
        )
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Head",
            email="head@example.org",
        )
        departments = await organisation.list_departments(session, tenant_id=tenant_id)
        south = next(row for row in departments if row.code == "south")
        await roles.grant(
            session,
            tenant_id=tenant_id,
            user_id=user.id,
            role_id=role.id,
            scope_kind=perms.SCOPE_DEPARTMENT,
            scope_ref=south.id,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        live = await roles.load_live_grants(session, tenant_id=tenant_id, user_id=user.id)
    assert len(live) == 1
    assert live[0].scope_path == "pw/south"
    assert live[0].permits("review:read")


async def test_a_grant_naming_a_scope_in_another_tenant_is_not_found(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """The referential integrity ``scope_ref`` gives up by not being a foreign key."""
    async with scoped(migrated_engine, other_tenant_id) as session:
        await organisation.import_departments(
            session,
            tenant_id=other_tenant_id,
            specs=[DepartmentSpec.model_validate({"code": "theirs", "name": "Theirs"})],
        )
        theirs = (await organisation.list_departments(session, tenant_id=other_tenant_id))[0]

    async with scoped(migrated_engine, tenant_id) as session:
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="head",
            name="Head",
            permissions=["review:read"],
        )
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Head",
            email="h@example.org",
        )
        with pytest.raises(NotFoundError):
            await roles.grant(
                session,
                tenant_id=tenant_id,
                user_id=user.id,
                role_id=role.id,
                scope_kind=perms.SCOPE_DEPARTMENT,
                scope_ref=theirs.id,
            )


async def test_an_expired_grant_stops_authorising_without_a_sweep(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Expiry is a column read by every decision, not a cleanup job."""
    now = datetime.now(tz=UTC)
    async with scoped(migrated_engine, tenant_id) as session:
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="cover",
            name="Cover",
            permissions=["review:read"],
        )
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Cover",
            email="c@example.org",
        )
        await roles.grant(
            session,
            tenant_id=tenant_id,
            user_id=user.id,
            role_id=role.id,
            valid_until=now + timedelta(hours=1),
        )

    async with scoped(migrated_engine, tenant_id) as session:
        live_now = await roles.load_live_grants(
            session, tenant_id=tenant_id, user_id=user.id, now=now
        )
        live_later = await roles.load_live_grants(
            session, tenant_id=tenant_id, user_id=user.id, now=now + timedelta(hours=2)
        )
    assert len(live_now) == 1
    assert live_later == ()


async def test_a_revoked_grant_is_kept_and_stops_authorising(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ "What could this person do on 3 March" must stay answerable."""
    async with scoped(migrated_engine, tenant_id) as session:
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="rev",
            name="Rev",
            permissions=["review:read"],
        )
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Rev",
            email="r@example.org",
        )
        grant = await roles.grant(session, tenant_id=tenant_id, user_id=user.id, role_id=role.id)

    async with scoped(migrated_engine, tenant_id) as session:
        await roles.revoke_grant(
            session, tenant_id=tenant_id, grant_id=grant.id, reason="left the team"
        )
        live = await roles.load_live_grants(session, tenant_id=tenant_id, user_id=user.id)
        history = await roles.list_grants(
            session, tenant_id=tenant_id, user_id=user.id, include_revoked=True
        )
    assert live == ()
    assert len(history) == 1


async def test_a_role_edit_reaches_every_holder_immediately(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Why permissions are read live rather than copied onto the grant."""
    async with scoped(migrated_engine, tenant_id) as session:
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="live",
            name="Live",
            permissions=["review:read", "review:decide"],
        )
        user = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Holder",
            email="h2@example.org",
        )
        await roles.grant(session, tenant_id=tenant_id, user_id=user.id, role_id=role.id)

    async with scoped(migrated_engine, tenant_id) as session:
        await roles.update_role(
            session, tenant_id=tenant_id, role_id=role.id, permissions=["review:read"]
        )
        live = await roles.load_live_grants(session, tenant_id=tenant_id, user_id=user.id)
    assert live[0].permits("review:read")
    assert not live[0].permits("review:decide")


async def test_a_tenant_global_permission_cannot_be_scoped_to_a_department(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, SYSTEM_TENANT_ID) as session:
        role = await roles.create_role(
            session,
            tenant_id=SYSTEM_TENANT_ID,
            code="prov",
            name="Provisioner",
            permissions=["tenants:provision"],
        )
    with pytest.raises(ValidationError):
        roles.assert_scope_fits_permissions(
            permissions=role.permissions, kind=perms.SCOPE_DEPARTMENT
        )


async def test_a_delegation_is_capped_and_attributed(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    now = datetime.now(tz=UTC)
    async with scoped(migrated_engine, tenant_id) as session:
        role = await roles.create_role(
            session,
            tenant_id=tenant_id,
            code="rev2",
            name="Reviewer",
            permissions=["review:read"],
        )
        delegator = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Priya",
            email="pd@example.org",
        )
        colleague = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Amit",
            email="ak@example.org",
        )
        await roles.grant(session, tenant_id=tenant_id, user_id=delegator.id, role_id=role.id)
        held = await roles.load_live_grants(session, tenant_id=tenant_id, user_id=delegator.id)

    async with scoped(migrated_engine, tenant_id) as session:
        delegated = await roles.delegate(
            session,
            tenant_id=tenant_id,
            delegator_user_id=delegator.id,
            delegator_grants=held,
            to_user_id=colleague.id,
            role_id=role.id,
            scope_kind=perms.SCOPE_TENANT,
            scope_ref=None,
            valid_until=now + timedelta(days=7),
            reason="covering for Priya, 14-21 March",
        )
    assert delegated.delegated_by_user_id == delegator.id
    assert delegated.valid_until is not None

    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError):
            await roles.delegate(
                session,
                tenant_id=tenant_id,
                delegator_user_id=delegator.id,
                delegator_grants=held,
                to_user_id=colleague.id,
                role_id=role.id,
                scope_kind=perms.SCOPE_TENANT,
                scope_ref=None,
                valid_until=now + timedelta(days=365),
                reason="indefinitely, for convenience",
            )


# ---------------------------------------------------------------------------
# The shipped role library
# ---------------------------------------------------------------------------


def test_every_shipped_role_holds_only_declared_permissions() -> None:
    """The library is data, so a typo in it is a data error — caught here.

    Without this test the first sign that ``roles.json`` names a permission the
    build does not have is a provisioning run failing at a customer.
    """
    library = bootstrap.role_library()
    for section, specs in library.items():
        for spec in specs:
            for permission in spec.permissions:
                assert perms.exists(permission), f"{section}/{spec.code}: {permission}"


def test_no_tenant_role_in_the_library_holds_a_platform_permission() -> None:
    for spec in bootstrap.role_library()["tenant"]:
        assert not (set(spec.permissions) & perms.PLATFORM_PERMISSIONS), spec.code


async def test_installing_the_library_is_idempotent(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        first = await bootstrap.install_roles(session, tenant_id=tenant_id, section="tenant")
    async with scoped(migrated_engine, tenant_id) as session:
        second = await bootstrap.install_roles(session, tenant_id=tenant_id, section="tenant")
        installed = await roles.list_roles(session, tenant_id=tenant_id)
    assert first
    assert second == []
    assert len(installed) == len(first)
    assert all(role.is_system for role in installed)


async def test_a_shipped_role_cannot_be_edited_in_place(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Identical across every deployment running this version, or a support
    conversation about it is impossible."""
    async with scoped(migrated_engine, tenant_id) as session:
        installed = await bootstrap.install_roles(session, tenant_id=tenant_id, section="tenant")
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError):
            await roles.update_role(
                session,
                tenant_id=tenant_id,
                role_id=installed[0].id,
                permissions=["review:read"],
            )


async def test_the_first_operator_is_created_once_and_only_once(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A second run must be a no-op that reveals nothing. ``identity.bootstrap``
    explains why it returns ``None`` rather than raising."""
    async with scoped(migrated_engine, SYSTEM_TENANT_ID) as session:
        first = await bootstrap.ensure_first_operator(
            session, email="root@nemesis.example", display_name="Deployment root"
        )
    assert first is not None
    assert len(first.password) > 20

    async with scoped(migrated_engine, SYSTEM_TENANT_ID) as session:
        again = await bootstrap.ensure_first_operator(
            session, email="root@nemesis.example", display_name="Deployment root"
        )
    assert again is None

    async with scoped(migrated_engine, SYSTEM_TENANT_ID) as session:
        authenticated = await users.authenticate(
            session,
            tenant_id=SYSTEM_TENANT_ID,
            identifier="root@nemesis.example",
            password=first.password,
        )
        grants = await roles.load_live_grants(
            session, tenant_id=SYSTEM_TENANT_ID, user_id=authenticated.user.id
        )
    assert any("tenants:provision" in grant.permissions for grant in grants)


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


async def _platform_operator(
    engine: AsyncEngine,
) -> Principal:
    async with scoped(engine, SYSTEM_TENANT_ID) as session:
        await bootstrap.install_roles(session, tenant_id=SYSTEM_TENANT_ID, section="platform")
        operator = await users.create_user(
            session,
            tenant_id=SYSTEM_TENANT_ID,
            display_name="Support",
            email="support@nemesis.example",
        )
        role = (
            await session.execute(
                select(Role).where(
                    Role.tenant_id == SYSTEM_TENANT_ID, Role.code == "platform_support"
                )
            )
        ).scalar_one()
        await roles.grant(
            session,
            tenant_id=SYSTEM_TENANT_ID,
            user_id=operator.id,
            role_id=role.id,
        )
        grants = await roles.load_live_grants(
            session, tenant_id=SYSTEM_TENANT_ID, user_id=operator.id
        )
    return Principal(
        user_id=operator.id,
        tenant_id=SYSTEM_TENANT_ID,
        subject=operator.subject,
        session_id=uuid.uuid4(),
        grants=grants,
        is_platform=True,
    )


async def test_an_impersonation_is_an_event_in_the_impersonated_tenants_chain(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The gate clause: visible to the impersonated tenant.

    An event on the operator's own chain would be visible to everybody except
    the party with the right to see it, so the assertion is specifically that
    ``events.tenant_id`` is the *customer's*.
    """
    operator = await _platform_operator(migrated_engine)

    async with scoped(migrated_engine, tenant_id) as session:
        target = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Officer",
            email="officer@example.org",
        )

    async with scoped(migrated_engine, tenant_id) as session:
        started = await impersonation.start(
            session,
            secret=SECRET,
            operator=operator,
            target_tenant_id=tenant_id,
            target_user_id=target.id,
            justification="ticket NEM-4821: officer reports the review queue renders empty",
            access_ttl_seconds=900,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        rows = list(
            (
                await session.execute(
                    select(Event.tenant_id, Event.event_type).where(
                        Event.tenant_id == tenant_id,
                        Event.event_type == "impersonation_started",
                    )
                )
            ).tuples()
        )
        visible = await impersonation.list_for_tenant(session, tenant_id=tenant_id)

    assert rows == [(tenant_id, "impersonation_started")]
    assert len(visible) == 1
    assert visible[0].operator_subject == operator.subject
    assert not visible[0].allows_writes
    assert started.ends_at > datetime.now(tz=UTC)


async def test_an_impersonation_session_is_read_only_unless_asked_for(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    operator = await _platform_operator(migrated_engine)
    async with scoped(migrated_engine, tenant_id) as session:
        target = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Officer",
            email="officer2@example.org",
        )
    async with scoped(migrated_engine, tenant_id) as session:
        started = await impersonation.start(
            session,
            secret=SECRET,
            operator=operator,
            target_tenant_id=tenant_id,
            target_user_id=target.id,
            justification="ticket NEM-4822: reproducing a reported rendering fault",
            access_ttl_seconds=900,
            allows_writes=True,
        )
    async with scoped(migrated_engine, tenant_id) as session:
        row = await impersonation.get(
            session, tenant_id=tenant_id, impersonation_id=started.impersonation_id
        )
    assert row.allows_writes


async def test_a_thin_justification_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    operator = await _platform_operator(migrated_engine)
    async with scoped(migrated_engine, tenant_id) as session:
        target = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Officer",
            email="officer3@example.org",
        )
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError):
            await impersonation.start(
                session,
                secret=SECRET,
                operator=operator,
                target_tenant_id=tenant_id,
                target_user_id=target.id,
                justification="debugging",
                access_ttl_seconds=900,
            )


async def test_ending_an_impersonation_revokes_its_sessions_and_records_counters(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    operator = await _platform_operator(migrated_engine)
    async with scoped(migrated_engine, tenant_id) as session:
        target = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Officer",
            email="officer4@example.org",
        )
    async with scoped(migrated_engine, tenant_id) as session:
        started = await impersonation.start(
            session,
            secret=SECRET,
            operator=operator,
            target_tenant_id=tenant_id,
            target_user_id=target.id,
            justification="ticket NEM-4823: checking what the officer sees on the queue",
            access_ttl_seconds=900,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        await impersonation.end(
            session,
            tenant_id=tenant_id,
            impersonation_id=started.impersonation_id,
            reason=impersonation.ENDED_BY_OPERATOR,
        )

    async with scoped(migrated_engine, tenant_id) as session:
        row = await impersonation.get(
            session, tenant_id=tenant_id, impersonation_id=started.impersonation_id
        )
        still_live = await impersonation.is_live(
            session, tenant_id=tenant_id, impersonation_id=started.impersonation_id
        )
        session_rows = list(
            (
                await session.execute(
                    select(UserSession).where(
                        UserSession.tenant_id == tenant_id,
                        UserSession.impersonation_id == started.impersonation_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        ended_events = list(
            (
                await session.execute(
                    select(Event.event_type).where(
                        Event.tenant_id == tenant_id,
                        Event.event_type == "impersonation_ended",
                    )
                )
            )
            .scalars()
            .all()
        )

    assert row.ended_at is not None
    assert still_live is None
    assert session_rows and all(r.revoked_at is not None for r in session_rows)
    assert ended_events == ["impersonation_ended"]


async def test_an_overdue_impersonation_is_swept(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The cap is enforced by something that runs, not only by a column."""
    operator = await _platform_operator(migrated_engine)
    async with scoped(migrated_engine, tenant_id) as session:
        target = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Officer",
            email="officer5@example.org",
        )
    async with scoped(migrated_engine, tenant_id) as session:
        started = await impersonation.start(
            session,
            secret=SECRET,
            operator=operator,
            target_tenant_id=tenant_id,
            target_user_id=target.id,
            justification="ticket NEM-4824: long-running investigation into queue ordering",
            access_ttl_seconds=900,
            duration=timedelta(minutes=1),
        )

    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        swept = await impersonation.expire_overdue(
            session, now=datetime.now(tz=UTC) + timedelta(minutes=5)
        )
        await session.commit()
    assert swept == 1

    async with scoped(migrated_engine, tenant_id) as session:
        row = await impersonation.get(
            session, tenant_id=tenant_id, impersonation_id=started.impersonation_id
        )
    assert row.ended_reason == impersonation.ENDED_EXPIRED


async def test_the_platform_tenant_cannot_be_impersonated(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Support engineers may not become each other.

    Takes ``tenant_id`` despite never using it: that fixture is what truncates
    the tables between tests, and a test that skips it inherits the previous
    one's rows. The symptom is a unique-constraint failure attributed to
    whichever test happens to run second.
    """
    operator = await _platform_operator(migrated_engine)
    async with scoped(migrated_engine, SYSTEM_TENANT_ID) as session:
        with pytest.raises(ValidationError):
            await impersonation.start(
                session,
                secret=SECRET,
                operator=operator,
                target_tenant_id=SYSTEM_TENANT_ID,
                target_user_id=operator.user_id,
                justification="ticket NEM-4825: an attempt to act as another engineer",
                access_ttl_seconds=900,
            )
