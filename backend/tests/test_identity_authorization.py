"""The decision function, property-tested against a generated role/scope matrix.

This is the Phase 13 gate clause *"authorization decisions are property-tested
against a generated role/scope matrix"*. It is possible only because
``identity.scopes`` is pure — no session, no clock — so Hypothesis can hammer it
with thousands of (role x scope x resource) combinations in the time one
database round trip would take.

**The invariants, and why each one is the one worth generating inputs for.**

*Default-deny.* A principal with no grants is refused everything, and a
principal whose grants do not mention a permission is refused that permission.
Stated as a property because the dangerous version of this bug is conditional —
it appears for one scope kind, or for a resource with one attribute missing.

*Containment, both directions.* A grant on an ancestor covers a descendant and a
grant on a descendant never covers an ancestor. The second half is the one a
``is_descendant`` call written the wrong way round breaks, and it reads
perfectly naturally in English either way.

*No prefix bleed.* A grant on ``roads`` must not cover ``roadside_waste``.
Generated sibling keys that share a prefix are the only reliable way to catch
the missing separator.

*Visibility agrees with the per-row decision.* ``evaluate`` answers "may I touch
this row" and ``visibility`` answers "which rows exist for me". If they can
disagree, the product has two access-control models and the list endpoint is the
one that leaks.

*Delegation never widens.* Nobody delegates authority they do not hold, on a
scope they do not cover.
"""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nemesis.identity import permissions as perms
from nemesis.identity.errors import ValidationError
from nemesis.identity.roles import assert_within_delegator_authority
from nemesis.identity.scopes import (
    REASON_IMPERSONATION_READ_ONLY,
    REASON_NO_GRANT,
    REASON_OUT_OF_SCOPE,
    Grant,
    Principal,
    Resource,
    evaluate,
    grant_covers,
    visibility,
)

#: Permission names that are scopable, so a resource can narrow them.
SCOPABLE = sorted(name for name, permission in perms.PERMISSIONS.items() if permission.is_scopable)

#: Path keys shaped like the tree keys the three hierarchies allow, including
#: pairs that share a prefix — which is what catches a missing separator.
path_keys = st.sampled_from(["pw", "pw_south", "roads", "roadside", "roadside_waste", "water", "w"])
paths = st.lists(path_keys, min_size=1, max_size=4).map("/".join)


def principal_with(*grants: Grant, **overrides: object) -> Principal:
    return Principal(
        user_id=overrides.get("user_id", uuid.uuid4()),  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        subject="officer@example.org",
        session_id=uuid.uuid4(),
        grants=grants,
        impersonator_subject=overrides.get("impersonator_subject"),  # type: ignore[arg-type]
        impersonation_allows_writes=bool(overrides.get("impersonation_allows_writes", False)),
    )


def department_grant(*, path: str, permission: str) -> Grant:
    return Grant(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        role_code="generated",
        permissions=frozenset({permission}),
        scope_kind=perms.SCOPE_DEPARTMENT,
        scope_id=uuid.uuid4(),
        scope_path=path,
    )


# ---------------------------------------------------------------------------
# Default-deny
# ---------------------------------------------------------------------------


@given(permission=st.sampled_from(SCOPABLE))
def test_a_principal_with_no_grants_is_refused_everything(permission: str) -> None:
    decision = evaluate(
        principal=principal_with(),
        permission=permission,
        resource=Resource(kind=perms.get(permission).resource_kind or "tenant"),
    )
    assert not decision.allowed
    assert decision.reason_code == REASON_NO_GRANT


@given(held=st.sampled_from(SCOPABLE), wanted=st.sampled_from(SCOPABLE), path=paths)
def test_a_grant_authorises_only_its_own_permissions(held: str, wanted: str, path: str) -> None:
    principal = principal_with(department_grant(path=path, permission=held))
    decision = evaluate(
        principal=principal,
        permission=wanted,
        resource=Resource(kind=perms.get(wanted).resource_kind or "tenant", department_path=path),
    )
    assert decision.allowed == (held == wanted)


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------


@given(ancestor=paths, child=path_keys)
def test_a_grant_on_an_ancestor_covers_its_descendants(ancestor: str, child: str) -> None:
    grant = department_grant(path=ancestor, permission="complaints:read")
    resource = Resource(kind=perms.KIND_COMPLAINT, department_path=f"{ancestor}/{child}")
    assert grant_covers(grant=grant, resource=resource, principal_user_id=uuid.uuid4())


@given(ancestor=paths, child=path_keys)
def test_a_grant_on_a_descendant_never_covers_its_ancestor(ancestor: str, child: str) -> None:
    """The direction a reversed ``is_descendant`` call silently inverts."""
    grant = department_grant(path=f"{ancestor}/{child}", permission="complaints:read")
    resource = Resource(kind=perms.KIND_COMPLAINT, department_path=ancestor)
    assert not grant_covers(grant=grant, resource=resource, principal_user_id=uuid.uuid4())


@pytest.mark.parametrize(
    ("granted", "resource_path"),
    [
        ("roads", "roadside_waste"),
        ("roads", "roadside"),
        ("w", "water"),
        ("pw", "pw_south"),
    ],
)
def test_a_shared_prefix_is_not_containment(granted: str, resource_path: str) -> None:
    """``roads`` is not an ancestor of ``roadside_waste``.

    A bare ``startswith`` says it is, and the resulting privilege is one nobody
    granted and nobody can see in the UI.
    """
    grant = department_grant(path=granted, permission="complaints:read")
    resource = Resource(kind=perms.KIND_COMPLAINT, department_path=resource_path)
    assert not grant_covers(grant=grant, resource=resource, principal_user_id=uuid.uuid4())


@given(path=paths)
def test_a_resource_with_no_path_cannot_satisfy_a_scoped_grant(path: str) -> None:
    """Unprovable is denied. ``identity.scopes`` argues why this is not the same
    as "not in any department, therefore allowed"."""
    grant = department_grant(path=path, permission="complaints:read")
    resource = Resource(kind=perms.KIND_COMPLAINT, department_path=None)
    assert not grant_covers(grant=grant, resource=resource, principal_user_id=uuid.uuid4())


def test_an_unknown_scope_kind_grants_nothing() -> None:
    """A row written by a newer build, or by hand during an incident."""
    grant = Grant(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        role_code="from-the-future",
        permissions=frozenset({"complaints:read"}),
        scope_kind="orbital_platform",
        scope_path="anything",
    )
    assert not grant_covers(
        grant=grant,
        resource=Resource(kind=perms.KIND_COMPLAINT, department_path="anything"),
        principal_user_id=uuid.uuid4(),
    )


def test_a_tenant_scoped_grant_covers_every_resource() -> None:
    grant = Grant(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        role_code="admin",
        permissions=frozenset({"complaints:read"}),
        scope_kind=perms.SCOPE_TENANT,
    )
    assert grant_covers(
        grant=grant,
        resource=Resource(kind=perms.KIND_COMPLAINT),
        principal_user_id=uuid.uuid4(),
    )


def test_a_self_scoped_grant_covers_only_attached_rows() -> None:
    holder = uuid.uuid4()
    grant = Grant(
        id=uuid.uuid4(),
        role_id=uuid.uuid4(),
        role_code="field",
        permissions=frozenset({"complaints:read"}),
        scope_kind=perms.SCOPE_SELF,
    )
    attached = Resource(kind=perms.KIND_COMPLAINT, attached_user_ids=frozenset({holder}))
    somebody_else = Resource(kind=perms.KIND_COMPLAINT, attached_user_ids=frozenset({uuid.uuid4()}))

    assert grant_covers(grant=grant, resource=attached, principal_user_id=holder)
    assert not grant_covers(grant=grant, resource=somebody_else, principal_user_id=holder)


# ---------------------------------------------------------------------------
# The two models agree
# ---------------------------------------------------------------------------


@given(granted=paths, resource_path=paths)
@settings(max_examples=300)
def test_visibility_agrees_with_the_per_row_decision(granted: str, resource_path: str) -> None:
    """If these can disagree, the list endpoint is the one that leaks.

    ``visibility`` is what reaches the SQL; ``evaluate`` is what guards a single
    row. A resource inside a visible path must be allowed by ``evaluate``, and
    one outside every visible path must not be.
    """
    principal = principal_with(department_grant(path=granted, permission="complaints:read"))
    allowed = visibility(principal=principal, permission="complaints:read")
    decision = evaluate(
        principal=principal,
        permission="complaints:read",
        resource=Resource(kind=perms.KIND_COMPLAINT, department_path=resource_path),
    )

    covered = any(
        resource_path == path or resource_path.startswith(f"{path}/")
        for path in allowed.department_paths
    )
    assert decision.allowed == covered


def test_an_empty_visibility_is_not_the_same_as_unrestricted() -> None:
    """The boolean whose inversion would expose every row in the tenant."""
    nothing = visibility(principal=principal_with(), permission="complaints:read")
    assert nothing.permits_nothing
    assert not nothing.unrestricted

    everything = visibility(
        principal=principal_with(
            Grant(
                id=uuid.uuid4(),
                role_id=uuid.uuid4(),
                role_code="admin",
                permissions=frozenset({"complaints:read"}),
                scope_kind=perms.SCOPE_TENANT,
            )
        ),
        permission="complaints:read",
    )
    assert everything.unrestricted
    assert not everything.permits_nothing


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


def test_a_read_only_impersonation_cannot_write_even_where_the_user_could() -> None:
    principal = principal_with(
        Grant(
            id=uuid.uuid4(),
            role_id=uuid.uuid4(),
            role_code="reviewer",
            permissions=frozenset({"review:decide"}),
            scope_kind=perms.SCOPE_TENANT,
        ),
        impersonator_subject="support@nemesis",
        impersonation_allows_writes=False,
    )
    decision = evaluate(
        principal=principal,
        permission="review:decide",
        resource=Resource(kind=perms.KIND_REVIEW_ITEM),
        requires_write=True,
    )
    assert not decision.allowed
    assert decision.reason_code == REASON_IMPERSONATION_READ_ONLY


def test_a_read_only_impersonation_may_still_read() -> None:
    principal = principal_with(
        Grant(
            id=uuid.uuid4(),
            role_id=uuid.uuid4(),
            role_code="reviewer",
            permissions=frozenset({"review:read"}),
            scope_kind=perms.SCOPE_TENANT,
        ),
        impersonator_subject="support@nemesis",
    )
    assert evaluate(
        principal=principal,
        permission="review:read",
        resource=Resource(kind=perms.KIND_REVIEW_ITEM),
    ).allowed


def test_a_write_authorised_impersonation_may_write() -> None:
    principal = principal_with(
        Grant(
            id=uuid.uuid4(),
            role_id=uuid.uuid4(),
            role_code="reviewer",
            permissions=frozenset({"review:decide"}),
            scope_kind=perms.SCOPE_TENANT,
        ),
        impersonator_subject="support@nemesis",
        impersonation_allows_writes=True,
    )
    assert evaluate(
        principal=principal,
        permission="review:decide",
        resource=Resource(kind=perms.KIND_REVIEW_ITEM),
        requires_write=True,
    ).allowed


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


@given(delegator_path=paths, delegated_path=paths)
@settings(max_examples=300)
def test_delegation_never_exceeds_the_delegator(delegator_path: str, delegated_path: str) -> None:
    held = [department_grant(path=delegator_path, permission="review:decide")]
    covered = delegated_path == delegator_path or delegated_path.startswith(f"{delegator_path}/")

    if covered:
        assert_within_delegator_authority(
            delegator_grants=held,
            permissions=["review:decide"],
            scope_kind=perms.SCOPE_DEPARTMENT,
            scope_path=delegated_path,
            scope_ref=uuid.uuid4(),
        )
    else:
        with pytest.raises(ValidationError):
            assert_within_delegator_authority(
                delegator_grants=held,
                permissions=["review:decide"],
                scope_kind=perms.SCOPE_DEPARTMENT,
                scope_path=delegated_path,
                scope_ref=uuid.uuid4(),
            )


def test_every_permission_must_be_held_not_merely_some() -> None:
    """One shared permission must not carry nine others across."""
    held = [department_grant(path="pw", permission="review:read")]
    with pytest.raises(ValidationError) as caught:
        assert_within_delegator_authority(
            delegator_grants=held,
            permissions=["review:read", "review:decide"],
            scope_kind=perms.SCOPE_DEPARTMENT,
            scope_path="pw/south",
            scope_ref=uuid.uuid4(),
        )
    assert "review:decide" in str(caught.value)


def test_a_scoped_holder_cannot_delegate_tenant_wide() -> None:
    held = [department_grant(path="pw", permission="review:decide")]
    with pytest.raises(ValidationError):
        assert_within_delegator_authority(
            delegator_grants=held,
            permissions=["review:decide"],
            scope_kind=perms.SCOPE_TENANT,
            scope_path=None,
            scope_ref=None,
        )


def test_a_department_grant_does_not_cover_a_zone_delegation() -> None:
    """Cross-tree containment is Phase 12 routing, not an authorization inference.

    "Public Works covers Ward 4" is a real relationship and it is a *routing
    table*. Inferring it here would mean a department head could delegate over a
    ward on the strength of a relationship nobody recorded.
    """
    held = [department_grant(path="pw", permission="review:decide")]
    with pytest.raises(ValidationError):
        assert_within_delegator_authority(
            delegator_grants=held,
            permissions=["review:decide"],
            scope_kind=perms.SCOPE_ZONE,
            scope_path="ward-4",
            scope_ref=uuid.uuid4(),
        )


def test_self_scope_is_not_delegable() -> None:
    """Its meaning changes when it changes hands, which is not delegation."""
    held = [
        Grant(
            id=uuid.uuid4(),
            role_id=uuid.uuid4(),
            role_code="field",
            permissions=frozenset({"complaints:read"}),
            scope_kind=perms.SCOPE_SELF,
        )
    ]
    with pytest.raises(ValidationError):
        assert_within_delegator_authority(
            delegator_grants=held,
            permissions=["complaints:read"],
            scope_kind=perms.SCOPE_SELF,
            scope_path=None,
            scope_ref=None,
        )


def test_out_of_scope_is_reported_distinctly_from_not_held() -> None:
    """Two findings with two different remedies: widen a grant, or issue one."""
    principal = principal_with(department_grant(path="pw/south", permission="review:read"))
    out_of_scope = evaluate(
        principal=principal,
        permission="review:read",
        resource=Resource(kind=perms.KIND_REVIEW_ITEM, department_path="water"),
    )
    not_held = evaluate(
        principal=principal,
        permission="review:decide",
        resource=Resource(kind=perms.KIND_REVIEW_ITEM, department_path="pw/south"),
    )
    assert out_of_scope.reason_code == REASON_OUT_OF_SCOPE
    assert not_held.reason_code == REASON_NO_GRANT


def test_the_matching_grant_is_recorded_on_the_decision() -> None:
    """An audit answering "under what authority" needs one answer, not two."""
    grant = department_grant(path="pw", permission="review:read")
    decision = evaluate(
        principal=principal_with(grant),
        permission="review:read",
        resource=Resource(kind=perms.KIND_REVIEW_ITEM, department_path="pw/south"),
    )
    assert decision.matched_grant is not None
    assert decision.matched_grant.id == grant.id
    assert decision.scope_description == "department:pw"
