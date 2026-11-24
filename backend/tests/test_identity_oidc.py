"""The OIDC flow, against a provider that exists only in this file.

**Why a mock transport and not a real provider.** Every assertion here is about
*our* half of the protocol: what we send, what we refuse, and what we store. A
real identity provider would make this test a network test with a secret in CI,
and would still not let us forge the ID tokens that the interesting half of
these cases need.

**What is being proved, in order of how much it matters.**

*The channel is the trust basis, and the browser is never part of it.* ADR-0064
says the ID token is trusted because it was fetched over TLS from the issuer's
own token endpoint. The test that this is not a loophole is that there is no way
to hand the system an ID token: ``complete`` takes a ``code``, and the request
model forbids extra fields.

*The claims are all checked.* Issuer, audience, authorised party, nonce and
expiry each get a case, because each is a one-line check whose absence is
invisible until somebody exploits it.

*The email domain allow-list is enforced.* A tenant that configures Google
Workspace SSO must not thereby admit every Google account on earth — the single
most common OIDC misconfiguration there is.

*State is single use.* A replayed state is either a captured redirect or a
double submit, and neither gets a session.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nemesis.identity import bootstrap, oidc, roles, users
from nemesis.identity.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ProviderError,
    ValidationError,
)
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.integration]

ISSUER = "https://sso.example.org"
CLIENT_ID = "nemesis-pilot"
SECRET_SETTING = "NEMESIS_TEST_OIDC_SECRET"
REDIRECT = "https://console.example.org/auth/callback"


@asynccontextmanager
async def scoped(engine: AsyncEngine, tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        with tenant_scope(tenant_id):
            yield session
            await session.commit()


def discovery_document() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }


def id_token(claims: dict[str, Any]) -> str:
    """An unsigned-looking JWT. The signature is never verified — ADR-0064.

    Three segments with a nonsense signature, because that is exactly what this
    implementation accepts from the token endpoint and refuses from anywhere
    else. A test that signed it properly would be testing a verification step
    that deliberately does not exist.
    """

    def segment(payload: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'RS256'})}.{segment(claims)}.not-verified"


def base_claims(**overrides: Any) -> dict[str, Any]:
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "sso-subject-1",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "email": "priya@example.org",
        "email_verified": True,
        "name": "Priya Deshmukh",
    }
    claims.update(overrides)
    return claims


def provider_client(
    *,
    claims_for: Any = None,
    discovery: dict[str, Any] | None = None,
    discovery_status: int = 200,
    token_status: int = 200,
    token_body: dict[str, Any] | None = None,
) -> httpx.AsyncClient:
    """A client that answers as the provider, and records the token request.

    ``claims_for`` is called with the posted form so a case can assert on what
    we sent — the PKCE verifier and the client secret in particular, which are
    the two values a wrong implementation leaves out.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            if discovery_status != 200:
                return httpx.Response(discovery_status, json={"error": "nope"})
            return httpx.Response(200, json=discovery or discovery_document())
        if request.url.path.endswith("/token"):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            form = dict(
                pair.split("=", 1) for pair in request.content.decode().split("&") if "=" in pair
            )
            claims = claims_for(form) if callable(claims_for) else base_claims()
            body = token_body if token_body is not None else {"id_token": id_token(claims)}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "unexpected path"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def register(session: AsyncSession, *, tenant_id: uuid.UUID, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "tenant_id": tenant_id,
        "slug": "sso",
        "display_name": "Example SSO",
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret_setting": SECRET_SETTING,
    }
    arguments.update(overrides)
    return await oidc.register_provider(session, **arguments)


@pytest.fixture(autouse=True)
def provider_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client secret lives in the environment, never in the database."""
    monkeypatch.setenv(SECRET_SETTING, "a-client-secret-from-the-environment")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


async def test_a_provider_is_configured_without_its_secret(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A database dump of a tenant contains no credential of ours."""
    async with scoped(migrated_engine, tenant_id) as session:
        provider = await register(session, tenant_id=tenant_id)

    assert provider.client_secret_setting == SECRET_SETTING
    stored = {column.name: getattr(provider, column.name) for column in provider.__table__.columns}
    assert "a-client-secret-from-the-environment" not in json.dumps(stored, default=str)
    # ``openid`` is added by the service, so a misconfigured row cannot produce a
    # request that is not an OIDC request.
    assert "openid" in provider.scopes


async def test_a_plaintext_issuer_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Over plaintext there is no channel to trust, so ADR-0064 collapses."""
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ValidationError):
            await register(session, tenant_id=tenant_id, issuer="http://sso.example.org")


async def test_a_duplicate_slug_is_a_conflict(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id)
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(ConflictError):
            await register(session, tenant_id=tenant_id, issuer=f"{ISSUER}/second")


async def test_an_unknown_provider_is_not_found(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        with pytest.raises(NotFoundError):
            await oidc.get_provider(session, tenant_id=tenant_id, slug="nope")


async def test_a_providers_discovery_document_is_cached_on_the_row(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Fetching it per sign-in would make their availability our availability."""
    async with scoped(migrated_engine, tenant_id) as session:
        provider = await register(session, tenant_id=tenant_id)

    async with scoped(migrated_engine, tenant_id) as session, provider_client() as client:
        document = await oidc.discovery_document(session, provider=provider, client=client)
    assert document["token_endpoint"] == f"{ISSUER}/token"

    async with scoped(migrated_engine, tenant_id) as session:
        refreshed = await oidc.get_provider(session, tenant_id=tenant_id, slug="sso")
    assert refreshed.discovery_fetched_at is not None
    assert refreshed.discovery["token_endpoint"] == f"{ISSUER}/token"

    # Second read, with a client that would fail if it were called at all.
    async with (
        scoped(migrated_engine, tenant_id) as session,
        provider_client(discovery_status=500) as client,
    ):
        again = await oidc.discovery_document(session, provider=refreshed, client=client)
    assert again["token_endpoint"] == f"{ISSUER}/token"


async def test_a_discovery_document_that_disagrees_about_its_issuer_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A mismatch makes every later ``iss`` check meaningless."""
    async with scoped(migrated_engine, tenant_id) as session:
        provider = await register(session, tenant_id=tenant_id)
        async with provider_client(
            discovery=discovery_document() | {"issuer": "https://somebody.else"}
        ) as client:
            with pytest.raises(ProviderError):
                await oidc.discovery_document(session, provider=provider, client=client)


async def test_an_unreachable_provider_with_no_cache_is_a_provider_error(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ADR-0060: an unreachable API is an answer. With nothing cached, the
    answer is 502 and the remedy belongs to whoever runs the provider."""
    async with scoped(migrated_engine, tenant_id) as session:
        provider = await register(session, tenant_id=tenant_id)
        async with provider_client(discovery_status=503) as client:
            with pytest.raises(ProviderError):
                await oidc.discovery_document(session, provider=provider, client=client)


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------


async def test_the_authorization_url_carries_pkce_and_a_nonce(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id)

    async with scoped(migrated_engine, tenant_id) as session, provider_client() as client:
        started = await oidc.begin(
            session,
            tenant_id=tenant_id,
            slug="sso",
            redirect_uri=REDIRECT,
            allowed_redirect_uris=(REDIRECT,),
            client=client,
        )

    assert started.authorization_url.startswith(f"{ISSUER}/authorize?")
    assert "code_challenge_method=S256" in started.authorization_url
    assert "response_type=code" in started.authorization_url
    assert f"state={started.state}" in started.authorization_url
    assert "nonce=" in started.authorization_url
    # The verifier never leaves the server.
    assert "code_verifier" not in started.authorization_url


async def test_a_redirect_uri_that_is_not_configured_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Exact matching, not a prefix: a prefix-matched redirect is how an open
    redirect becomes a stolen session."""
    async with scoped(migrated_engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id)
        async with provider_client() as client:
            with pytest.raises(ValidationError):
                await oidc.begin(
                    session,
                    tenant_id=tenant_id,
                    slug="sso",
                    redirect_uri="https://console.example.org.evil.test/callback",
                    allowed_redirect_uris=(REDIRECT,),
                    client=client,
                )


async def test_an_absolute_return_to_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id)
        async with provider_client() as client:
            with pytest.raises(ValidationError):
                await oidc.begin(
                    session,
                    tenant_id=tenant_id,
                    slug="sso",
                    redirect_uri=REDIRECT,
                    allowed_redirect_uris=(REDIRECT,),
                    return_to="https://elsewhere.example/",
                    client=client,
                )


async def _begin(engine: AsyncEngine, tenant_id: uuid.UUID, **register_kwargs: Any) -> str:
    async with scoped(engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id, **register_kwargs)
    async with scoped(engine, tenant_id) as session, provider_client() as client:
        started = await oidc.begin(
            session,
            tenant_id=tenant_id,
            slug="sso",
            redirect_uri=REDIRECT,
            allowed_redirect_uris=(REDIRECT,),
            client=client,
        )
    return started.state


async def test_a_known_subject_signs_in_and_the_verifier_is_presented(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The happy path, plus an assertion about what we posted.

    A PKCE flow that forgets to send the verifier works against providers that
    do not enforce it and fails against the ones that do — which is the worst
    kind of bug, because it passes in development.
    """
    state = await _begin(migrated_engine, tenant_id)
    subject = f"{ISSUER}#sso-subject-1"

    async with scoped(migrated_engine, tenant_id) as session:
        await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Priya",
            email="priya@example.org",
            subject=subject,
        )

    posted: dict[str, str] = {}

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)

        def with_nonce(form: dict[str, str]) -> dict[str, Any]:
            posted.update(form)
            return base_claims(nonce=nonce)

        async with provider_client(claims_for=with_nonce) as client:
            completed = await oidc.complete(
                session,
                tenant_id=tenant_id,
                state=state,
                code="an-authorization-code",
                client=client,
            )

    assert completed.user.subject == subject
    assert completed.was_provisioned is False
    assert posted["grant_type"] == "authorization_code"
    assert posted["code_verifier"]
    assert posted["client_secret"]


async def _read_nonce(session: AsyncSession, tenant_id: uuid.UUID, state: str) -> str:
    """The nonce we sent, so a test can mint an ID token that carries it.

    Scoped to the tenant even though ``state`` is globally unique: the tenancy
    guard refuses an unscoped read of a tenant-scoped table, and a test suite
    that could issue one would be free to assert behaviour no real caller can
    reach. (It caught this helper on the first run, which is the guard doing
    exactly its job.)
    """
    from sqlalchemy import select

    from nemesis.db.models.identity import OidcAuthRequest

    return str(
        (
            await session.execute(
                select(OidcAuthRequest.nonce).where(
                    OidcAuthRequest.tenant_id == tenant_id,
                    OidcAuthRequest.state == state,
                )
            )
        ).scalar_one()
    )


async def test_an_unknown_subject_is_refused_unless_auto_provisioning_is_on(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Default-deny at the identity layer: a tenant that has not asked for
    auto-provisioning gets "we do not know you", not a user table that fills up
    with everybody at the university."""
    state = await _begin(migrated_engine, tenant_id)

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        async with provider_client(claims_for=lambda _f: base_claims(nonce=nonce)) as client:
            with pytest.raises(AuthenticationError) as caught:
                await oidc.complete(
                    session, tenant_id=tenant_id, state=state, code="code", client=client
                )
    assert caught.value.reason == "oidc_unknown_subject"


async def test_auto_provisioning_creates_a_user_with_the_configured_role(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        installed = await bootstrap.install_roles(session, tenant_id=tenant_id, section="tenant")
        reviewer = next(role for role in installed if role.code == "reviewer")

    state = await _begin(
        migrated_engine,
        tenant_id,
        auto_provision=True,
        auto_provision_role_id=reviewer.id,
    )

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        async with provider_client(claims_for=lambda _f: base_claims(nonce=nonce)) as client:
            completed = await oidc.complete(
                session, tenant_id=tenant_id, state=state, code="code", client=client
            )

    assert completed.was_provisioned
    assert completed.user.email == "priya@example.org"

    async with scoped(migrated_engine, tenant_id) as session:
        grants = await roles.load_live_grants(
            session, tenant_id=tenant_id, user_id=completed.user.id
        )
    assert any(grant.role_code == "reviewer" for grant in grants)


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"iss": "https://somebody.else"}, "oidc_issuer_mismatch"),
        ({"aud": "another-client"}, "oidc_audience_mismatch"),
        ({"exp": int(time.time()) - 600}, "oidc_expired"),
        ({"nonce": "not-the-nonce-we-sent"}, "oidc_nonce_mismatch"),
    ],
)
async def test_every_claim_is_checked(
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
    override: dict[str, Any],
    reason: str,
) -> None:
    state = await _begin(migrated_engine, tenant_id)

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        claims = base_claims(nonce=nonce) | override
        async with provider_client(claims_for=lambda _f: claims) as client:
            with pytest.raises(AuthenticationError) as caught:
                await oidc.complete(
                    session, tenant_id=tenant_id, state=state, code="code", client=client
                )
    assert caught.value.reason == reason


async def test_a_token_for_a_different_client_in_a_multi_audience_token_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """``azp`` matters exactly when several audiences are listed."""
    state = await _begin(migrated_engine, tenant_id)

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        claims = base_claims(nonce=nonce, aud=[CLIENT_ID, "another-client"], azp="another-client")
        async with provider_client(claims_for=lambda _f: claims) as client:
            with pytest.raises(AuthenticationError) as caught:
                await oidc.complete(
                    session, tenant_id=tenant_id, state=state, code="code", client=client
                )
    assert caught.value.reason == "oidc_azp_mismatch"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"email": "priya@not-allowed.test"}, "oidc_domain_not_allowed"),
        ({"email_verified": False}, "oidc_email_unverified"),
        ({"email": None}, "oidc_no_email"),
    ],
)
async def test_the_email_allow_list_is_enforced(
    migrated_engine: AsyncEngine,
    tenant_id: uuid.UUID,
    override: dict[str, Any],
    reason: str,
) -> None:
    """The control that stops "we configured Google SSO, so every Google
    account can sign in"."""
    state = await _begin(migrated_engine, tenant_id, allowed_email_domains=["example.org"])

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        claims = base_claims(nonce=nonce) | override
        async with provider_client(claims_for=lambda _f: claims) as client:
            with pytest.raises(AuthenticationError) as caught:
                await oidc.complete(
                    session, tenant_id=tenant_id, state=state, code="code", client=client
                )
    assert caught.value.reason == reason


async def test_a_state_is_single_use(migrated_engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    state = await _begin(migrated_engine, tenant_id, auto_provision=True)

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        async with provider_client(claims_for=lambda _f: base_claims(nonce=nonce)) as client:
            await oidc.complete(
                session, tenant_id=tenant_id, state=state, code="code", client=client
            )

    async with (
        scoped(migrated_engine, tenant_id) as session,
        provider_client(claims_for=lambda _f: base_claims(nonce=nonce)) as client,
    ):
        with pytest.raises(AuthenticationError) as caught:
            await oidc.complete(
                session, tenant_id=tenant_id, state=state, code="code", client=client
            )
    assert caught.value.reason == "oidc_state_reused"


async def test_an_unknown_state_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id)
        async with provider_client() as client:
            with pytest.raises(AuthenticationError) as caught:
                await oidc.complete(
                    session,
                    tenant_id=tenant_id,
                    state="a-state-we-never-issued",
                    code="code",
                    client=client,
                )
    assert caught.value.reason == "oidc_state_unknown"


async def test_a_refused_code_exchange_does_not_leak_the_providers_body(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """Somebody else's error message sometimes carries our client id, and it
    would reach a browser if forwarded."""
    state = await _begin(migrated_engine, tenant_id)

    async with (
        scoped(migrated_engine, tenant_id) as session,
        provider_client(token_status=400) as client,
    ):
        with pytest.raises(ProviderError) as caught:
            await oidc.complete(
                session, tenant_id=tenant_id, state=state, code="code", client=client
            )
    assert "invalid_grant" not in str(caught.value)


async def test_a_token_response_with_no_id_token_is_a_provider_error(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    state = await _begin(migrated_engine, tenant_id)
    async with (
        scoped(migrated_engine, tenant_id) as session,
        provider_client(token_body={"access_token": "only-this"}) as client,
    ):
        with pytest.raises(ProviderError):
            await oidc.complete(
                session, tenant_id=tenant_id, state=state, code="code", client=client
            )


async def test_a_missing_client_secret_is_a_provider_error_not_a_crash(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secret is read from the process at exchange time, so an unset
    variable is a configuration error with a name in it."""
    state = await _begin(migrated_engine, tenant_id)
    monkeypatch.delenv(SECRET_SETTING, raising=False)

    async with scoped(migrated_engine, tenant_id) as session, provider_client() as client:
        with pytest.raises(ProviderError) as caught:
            await oidc.complete(
                session, tenant_id=tenant_id, state=state, code="code", client=client
            )
    assert SECRET_SETTING in str(caught.value)


async def test_a_deactivated_user_cannot_sign_in_through_a_provider(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    state = await _begin(migrated_engine, tenant_id)
    subject = f"{ISSUER}#sso-subject-1"

    async with scoped(migrated_engine, tenant_id) as session:
        created = await users.create_user(
            session,
            tenant_id=tenant_id,
            display_name="Priya",
            email="priya@example.org",
            subject=subject,
        )
    async with scoped(migrated_engine, tenant_id) as session:
        await users.deactivate(session, tenant_id=tenant_id, user_id=created.id)

    async with scoped(migrated_engine, tenant_id) as session:
        nonce = await _read_nonce(session, tenant_id, state)
        async with provider_client(claims_for=lambda _f: base_claims(nonce=nonce)) as client:
            with pytest.raises(AuthenticationError) as caught:
                await oidc.complete(
                    session, tenant_id=tenant_id, state=state, code="code", client=client
                )
    assert caught.value.reason == "user_inactive"


async def test_expired_flows_are_swept(migrated_engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    """An abandoned flow holds a PKCE verifier and nothing of audit value."""
    from datetime import UTC, datetime, timedelta

    state = await _begin(migrated_engine, tenant_id)
    assert state

    maker = async_sessionmaker(migrated_engine, expire_on_commit=False)
    async with maker() as session:
        swept = await oidc.sweep_expired_requests(
            session, now=datetime.now(tz=UTC) + timedelta(hours=1)
        )
        await session.commit()
    assert swept == 1


async def test_the_provider_listing_carries_no_configuration(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with scoped(migrated_engine, tenant_id) as session:
        await register(session, tenant_id=tenant_id)
        listed = await oidc.list_providers(session, tenant_id=tenant_id)
    assert [provider.slug for provider in listed] == ["sso"]
