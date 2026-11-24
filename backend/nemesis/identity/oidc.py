"""OIDC authorization-code flow with PKCE, and no asymmetric cryptography.

**The ID token's signature is not verified, and that is a decision with a
citation rather than an omission.** OpenID Connect Core §3.1.3.7 clause 6 says
that when the ID token is received directly from the token endpoint over a TLS
channel, with server-side validation of the TLS certificate, the client *may*
use the TLS mechanism to validate the issuer instead of verifying the
signature. That is exactly the situation here: this module never accepts an ID
token presented by a browser, only one it fetched itself, over HTTPS, from an
endpoint named by the issuer's own discovery document.

Why it matters enough to write down: verifying an RS256 signature in-process
means an RSA implementation, which means ``cryptography`` or ``pyjwt`` in the
API image, the worker image and the CLI — and the repository already has a
standing argument (``pyproject.toml``, Phase 8) about dependencies that land in
the wrong image. ADR-0064 records the trade and, more importantly, records the
obligation it creates: **the implicit and hybrid flows are not supported and
must never be added.** Those deliver an ID token through the browser, where the
channel proves nothing and a signature is the only thing standing between a
forged token and a session. ``complete`` takes a ``code``; there is no code path
that accepts an ``id_token`` parameter.

**What *is* validated, every time:** ``iss`` against the configured issuer,
``aud`` against our client id, ``exp`` and ``iat`` against the clock, ``nonce``
against the value we stored when we started the flow, the ``state`` parameter
against a row we wrote (single-use), and the verified email's domain against the
provider's allow-list. The last one is the control that stops the single most
common OIDC misconfiguration there is: a tenant configures Google Workspace SSO
and every Google account on earth can sign in.

**The client secret is never in the database.** ``identity_providers`` holds the
*name* of an environment variable; the value is read at the moment of the token
request. A database dump of a customer's tenant therefore contains no credential
that authenticates us to their identity provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.identity import IdentityProvider, OidcAuthRequest
from nemesis.db.models.organisation import User
from nemesis.identity import roles as role_service
from nemesis.identity import users as user_service
from nemesis.identity.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ProviderError,
    ValidationError,
)
from nemesis.observability.logging import get_correlation_id, get_logger
from nemesis.tenancy.context import tenant_scope

log = get_logger(__name__)

#: How long a started flow may take to come back. Five minutes covers a
#: password prompt, an MFA push and a consent screen; it does not cover a tab
#: left open overnight, which is a state parameter sitting in a browser history
#: waiting to be replayed.
AUTH_REQUEST_TTL: Final = timedelta(minutes=5)

#: Timeout on every call to a provider. Three seconds for discovery (cached, so
#: a slow fetch delays only the first sign-in after a cache miss) and ten for the
#: token exchange, which is interactive and has a human waiting.
DISCOVERY_TIMEOUT: Final = 3.0
TOKEN_TIMEOUT: Final = 10.0

#: Re-fetch the discovery document when it is older than this. A day: endpoints
#: change rarely, and a provider rotating one mid-day is a published breaking
#: change on their side rather than something to poll for.
DISCOVERY_MAX_AGE: Final = timedelta(days=1)

#: Clock tolerance on ``exp`` and ``iat``. Matches ``tokens.CLOCK_SKEW_LEEWAY``
#: — a provider's clock is no better synchronised than ours.
CLOCK_SKEW_LEEWAY: Final = timedelta(seconds=30)

REQUIRED_SCOPE: Final = "openid"


@dataclass(frozen=True, slots=True)
class StartedFlow:
    """Where to send the browser, and the row that will validate its return."""

    authorization_url: str
    state: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CompletedFlow:
    """A verified sign-in through a provider."""

    user: User
    provider_slug: str
    email: str | None
    was_provisioned: bool


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


async def register_provider(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    slug: str,
    display_name: str,
    issuer: str,
    client_id: str,
    client_secret_setting: str,
    scopes: list[str] | None = None,
    allowed_email_domains: list[str] | None = None,
    auto_provision: bool = False,
    auto_provision_role_id: uuid.UUID | None = None,
) -> IdentityProvider:
    """Configure a provider for one tenant.

    The issuer must be ``https``. A plaintext issuer would make the TLS channel
    this module's entire trust basis (see the module docstring) nonexistent,
    which would turn a documented, citable design decision into a vulnerability
    — so it is refused here rather than discouraged in prose.
    """
    if not issuer.startswith("https://"):
        raise ValidationError(
            "the issuer must be https. This implementation trusts the ID token "
            "because of the channel it arrived on (ADR-0064); over plaintext there "
            "is no channel to trust"
        )
    if not client_secret_setting.strip():
        raise ValidationError(
            "name the environment variable holding the client secret. The secret "
            "itself is never stored — see the model docstring"
        )
    if auto_provision_role_id is not None:
        await role_service.get_role(session, tenant_id=tenant_id, role_id=auto_provision_role_id)

    requested = sorted({*(scopes or ["email", "profile"]), REQUIRED_SCOPE})

    with tenant_scope(tenant_id):
        clash = (
            await session.execute(
                select(IdentityProvider.id).where(
                    IdentityProvider.tenant_id == tenant_id,
                    IdentityProvider.slug == slug,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(f"a provider with slug {slug!r} already exists")

        row = IdentityProvider(
            tenant_id=tenant_id,
            slug=slug,
            display_name=display_name,
            issuer=issuer.rstrip("/"),
            client_id=client_id,
            client_secret_setting=client_secret_setting.strip(),
            scopes=requested,
            allowed_email_domains=[
                domain.strip().lower().lstrip("@")
                for domain in (allowed_email_domains or [])
                if domain.strip()
            ],
            auto_provision=auto_provision,
            auto_provision_role_id=auto_provision_role_id,
        )
        session.add(row)
        await session.flush()
    return row


async def get_provider(
    session: AsyncSession, *, tenant_id: uuid.UUID, slug: str
) -> IdentityProvider:
    with tenant_scope(tenant_id):
        row = (
            await session.execute(
                select(IdentityProvider).where(
                    IdentityProvider.tenant_id == tenant_id,
                    IdentityProvider.slug == slug,
                    IdentityProvider.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"no active identity provider {slug!r} for this tenant")
    return row


async def list_providers(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[IdentityProvider]:
    with tenant_scope(tenant_id):
        return list(
            (
                await session.execute(
                    select(IdentityProvider)
                    .where(IdentityProvider.tenant_id == tenant_id)
                    .order_by(IdentityProvider.slug)
                )
            )
            .scalars()
            .all()
        )


async def discovery_document(
    session: AsyncSession,
    *,
    provider: IdentityProvider,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The provider's ``.well-known`` metadata, cached on the row.

    ADR-0060's rule, applied: when the fetch fails and a cached document exists,
    the cached one is used and a degradation is recorded. A sign-in that fails
    because a metadata endpoint was briefly slow would make the provider's
    availability our availability, on a document that changes about once a year.

    When there is no cached document and the fetch fails, there is nothing to
    degrade to — that is a ``ProviderError``, and the API renders it as 502 with
    the provider named, because the remedy belongs to whoever runs it.
    """
    moment = now or datetime.now(tz=UTC)
    cached = dict(provider.discovery) if provider.discovery else {}
    fresh = (
        provider.discovery_fetched_at is not None
        and provider.discovery_fetched_at + DISCOVERY_MAX_AGE > moment
        and "token_endpoint" in cached
    )
    if fresh:
        return cached

    url = f"{provider.issuer}/.well-known/openid-configuration"
    try:
        document = await _fetch_json(url, client=client, timeout=DISCOVERY_TIMEOUT)
    except ProviderError:
        if cached.get("token_endpoint"):
            from nemesis.pipeline.integrity import record_system_degradation

            await record_system_degradation(
                component=f"oidc:{provider.slug}",
                failure_mode="discovery_unreachable",
                fallback_taken="used cached discovery document",
                correlation_id=get_correlation_id(),
            )
            log.warning("oidc.discovery_stale", provider=provider.slug, issuer=provider.issuer)
            return cached
        raise

    issued_issuer = str(document.get("issuer", "")).rstrip("/")
    if issued_issuer != provider.issuer:
        # The document must agree with where we fetched it from. A mismatch is
        # either a misconfiguration or somebody else's metadata, and both make
        # every subsequent `iss` check meaningless.
        raise ProviderError(
            f"discovery document declares issuer {issued_issuer!r}, configured "
            f"issuer is {provider.issuer!r}"
        )
    for required in ("authorization_endpoint", "token_endpoint"):
        if not str(document.get(required, "")).startswith("https://"):
            raise ProviderError(f"discovery document has no https {required}; refusing to use it")

    with tenant_scope(provider.tenant_id):
        await session.execute(
            update(IdentityProvider)
            .where(
                IdentityProvider.tenant_id == provider.tenant_id,
                IdentityProvider.id == provider.id,
            )
            .values(discovery=document, discovery_fetched_at=moment)
        )
    return document


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` for PKCE S256.

    S256 and never ``plain``. A ``plain`` challenge is the verifier, so an
    attacker who can see the authorization request can complete the exchange —
    which defeats the only thing PKCE is for.
    """
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def begin(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    slug: str,
    redirect_uri: str,
    allowed_redirect_uris: tuple[str, ...],
    return_to: str | None = None,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> StartedFlow:
    """Start a flow: store state, nonce and verifier, return the provider URL.

    ``redirect_uri`` is checked against an exact allow-list from configuration,
    not a pattern. Prefix matching on redirect URIs is how an open redirect
    becomes a token theft, and the list is short because it is one per
    deployment.
    """
    moment = now or datetime.now(tz=UTC)
    if redirect_uri not in allowed_redirect_uris:
        raise ValidationError(
            "that redirect_uri is not configured for this deployment. The list is "
            "exact rather than a pattern: prefix-matched redirect URIs are how an "
            "open redirect becomes a stolen session"
        )
    if return_to is not None and not return_to.startswith("/"):
        raise ValidationError("return_to must be a path on this origin, not an absolute URL")

    provider = await get_provider(session, tenant_id=tenant_id, slug=slug)
    document = await discovery_document(session, provider=provider, client=client, now=moment)

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    with tenant_scope(tenant_id):
        session.add(
            OidcAuthRequest(
                tenant_id=tenant_id,
                provider_id=provider.id,
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                return_to=return_to,
                expires_at=moment + AUTH_REQUEST_TTL,
            )
        )
        await session.flush()

    query = urlencode(
        {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(provider.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return StartedFlow(
        authorization_url=f"{document['authorization_endpoint']}?{query}",
        state=state,
        expires_at=moment + AUTH_REQUEST_TTL,
    )


async def complete(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    state: str,
    code: str,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> CompletedFlow:
    """Finish a flow: exchange the code, validate the claims, resolve the user.

    There is no ``id_token`` parameter and there must never be one. See the
    module docstring: this implementation's trust basis is the channel the token
    arrived on, so a token handed to us by a browser is a token with no trust
    basis at all.
    """
    moment = now or datetime.now(tz=UTC)

    with tenant_scope(tenant_id):
        request_row = (
            await session.execute(
                select(OidcAuthRequest).where(
                    OidcAuthRequest.tenant_id == tenant_id,
                    OidcAuthRequest.state == state,
                )
            )
        ).scalar_one_or_none()

    if request_row is None:
        raise AuthenticationError("unknown sign-in state", reason="oidc_state_unknown")
    if request_row.consumed_at is not None:
        # Single use. A replayed state with a fresh code is an attacker holding
        # a captured redirect; a replayed state with the same code is a double
        # submit. Neither gets a session.
        raise AuthenticationError("sign-in state already used", reason="oidc_state_reused")
    if request_row.expires_at <= moment:
        raise AuthenticationError("sign-in took too long", reason="oidc_state_expired")

    with tenant_scope(tenant_id):
        await session.execute(
            update(OidcAuthRequest)
            .where(
                OidcAuthRequest.tenant_id == tenant_id,
                OidcAuthRequest.id == request_row.id,
            )
            .values(consumed_at=moment)
        )
        provider = (
            await session.execute(
                select(IdentityProvider).where(
                    IdentityProvider.tenant_id == tenant_id,
                    IdentityProvider.id == request_row.provider_id,
                )
            )
        ).scalar_one_or_none()
    if provider is None or not provider.is_active:
        raise AuthenticationError("provider no longer active", reason="oidc_provider_inactive")

    document = await discovery_document(session, provider=provider, client=client, now=moment)
    claims = await _exchange_code(
        provider=provider,
        document=document,
        code=code,
        code_verifier=request_row.code_verifier,
        redirect_uri=request_row.redirect_uri,
        client=client,
    )
    _validate_claims(claims=claims, provider=provider, nonce=request_row.nonce, now=moment)

    email = _verified_email(claims=claims, provider=provider)
    subject = f"{provider.issuer}#{claims['sub']}"

    existing = await user_service.find_by_subject(session, tenant_id=tenant_id, subject=subject)
    if existing is not None:
        if not existing.is_active:
            raise AuthenticationError("user deactivated", reason="user_inactive")
        return CompletedFlow(
            user=existing,
            provider_slug=provider.slug,
            email=email,
            was_provisioned=False,
        )

    if not provider.auto_provision:
        # Default-deny, at the identity layer. A tenant that has not asked for
        # auto-provisioning gets "we do not know you" rather than a user table
        # that fills up with everybody at the university.
        raise AuthenticationError("no user for that subject", reason="oidc_unknown_subject")

    created = await user_service.create_user(
        session,
        tenant_id=tenant_id,
        display_name=str(claims.get("name") or email or claims["sub"]),
        email=email,
        subject=subject,
        kind=user_service.KIND_OPERATOR,
        identity_provider_id=provider.id,
    )
    if provider.auto_provision_role_id is not None:
        await role_service.grant(
            session,
            tenant_id=tenant_id,
            user_id=created.id,
            role_id=provider.auto_provision_role_id,
            reason=f"auto-provisioned by {provider.slug}",
            now=moment,
        )
    log.info("oidc.provisioned", provider=provider.slug, user_id=str(created.id))
    return CompletedFlow(
        user=created, provider_slug=provider.slug, email=email, was_provisioned=True
    )


async def _exchange_code(
    *,
    provider: IdentityProvider,
    document: dict[str, Any],
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    """Back-channel token request, returning the ID token's claims."""
    secret = os.environ.get(provider.client_secret_setting, "")
    if not secret:
        raise ProviderError(
            f"the client secret for provider {provider.slug!r} is not set: "
            f"{provider.client_secret_setting} is empty in this process. The secret "
            f"is read from the environment and never stored in the database"
        )

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "client_secret": secret,
        "code_verifier": code_verifier,
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=TOKEN_TIMEOUT, follow_redirects=False)
    try:
        response = await http.post(str(document["token_endpoint"]), data=form)
    except httpx.HTTPError as exc:
        raise ProviderError(f"token endpoint unreachable: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code != 200:
        # The provider's body is not forwarded. It is somebody else's error
        # message, it sometimes contains the client id, and it reaches a browser
        # if forwarded — so it goes to the log and the caller gets a 502.
        log.warning(
            "oidc.token_exchange_failed",
            provider=provider.slug,
            status=response.status_code,
        )
        raise ProviderError(
            f"the identity provider refused the code exchange (HTTP {response.status_code})"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("token endpoint returned a non-JSON body") from exc

    id_token = body.get("id_token")
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        raise ProviderError("token response carried no usable id_token")
    return _unverified_claims(id_token)


def _unverified_claims(id_token: str) -> dict[str, Any]:
    """Decode an ID token's payload **without** verifying its signature.

    Named to say so. The safety of this is the TLS channel and the fact that
    this function is only ever reached from ``_exchange_code`` — see the module
    docstring and ADR-0064. A reader who finds this function being called from
    anywhere else has found a vulnerability, and the name is the fastest way for
    them to know it.
    """
    _, payload_segment, _ = id_token.split(".")
    padding = "=" * (-len(payload_segment) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload_segment + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProviderError("id_token payload is not decodable JSON") from exc
    if not isinstance(claims, dict) or not isinstance(claims.get("sub"), str):
        raise ProviderError("id_token carries no subject claim")
    return claims


def _validate_claims(
    *,
    claims: dict[str, Any],
    provider: IdentityProvider,
    nonce: str,
    now: datetime,
) -> None:
    """Every claim check, in one place, with no early exit on success."""
    issuer = str(claims.get("iss", "")).rstrip("/")
    if issuer != provider.issuer:
        raise AuthenticationError("id_token issuer mismatch", reason="oidc_issuer_mismatch")

    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if provider.client_id not in [str(value) for value in audiences if value is not None]:
        raise AuthenticationError("id_token audience mismatch", reason="oidc_audience_mismatch")

    # `azp` matters when the token has several audiences: it names which client
    # the token was actually issued to, and ignoring it accepts a token minted
    # for a different client that happens to list us.
    if len(audiences) > 1 and str(claims.get("azp", provider.client_id)) != provider.client_id:
        raise AuthenticationError("id_token authorised party mismatch", reason="oidc_azp_mismatch")

    if str(claims.get("nonce", "")) != nonce:
        raise AuthenticationError("id_token nonce mismatch", reason="oidc_nonce_mismatch")

    expires = claims.get("exp")
    if not isinstance(expires, int | float) or isinstance(expires, bool):
        raise AuthenticationError("id_token has no expiry", reason="oidc_malformed")
    if datetime.fromtimestamp(float(expires), tz=UTC) + CLOCK_SKEW_LEEWAY <= now:
        raise AuthenticationError("id_token expired", reason="oidc_expired")


def _verified_email(*, claims: dict[str, Any], provider: IdentityProvider) -> str | None:
    """The email claim, only if the provider says it is verified and in domain.

    An unverified email is not used *at all* — not stored, not matched against.
    A provider that lets a user set an arbitrary unverified address is a
    provider where accepting it means account takeover by typing somebody
    else's address into a profile page.
    """
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        if provider.allowed_email_domains:
            raise AuthenticationError(
                "provider returned no email to check against the allow-list",
                reason="oidc_no_email",
            )
        return None

    if claims.get("email_verified") is not True:
        if provider.allowed_email_domains:
            raise AuthenticationError(
                "provider returned an unverified email", reason="oidc_email_unverified"
            )
        return None

    normalised = email.strip().lower()
    domain = normalised.rsplit("@", 1)[-1]
    if provider.allowed_email_domains and domain not in provider.allowed_email_domains:
        # The control that stops "we configured Google SSO, so every Google
        # account can sign in". See the module docstring.
        raise AuthenticationError(
            f"{domain!r} is not an allowed domain for this provider",
            reason="oidc_domain_not_allowed",
        )
    return normalised


async def _fetch_json(
    url: str, *, client: httpx.AsyncClient | None, timeout: float
) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        response = await http.get(url)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{url} unreachable: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code != 200:
        raise ProviderError(f"{url} answered HTTP {response.status_code}")
    try:
        document = response.json()
    except ValueError as exc:
        raise ProviderError(f"{url} returned a non-JSON body") from exc
    if not isinstance(document, dict):
        raise ProviderError(f"{url} returned JSON that is not an object")
    return document


async def sweep_expired_requests(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete timed-out flows. Returns how many.

    Deleted rather than kept: an unconsumed authorization request holds a PKCE
    verifier and nothing of audit value — the sign-in it would have produced
    never happened, and there is no event to explain.
    """
    from sqlalchemy import delete

    from nemesis.tenancy.guard import TENANT_SCOPE_EXEMPT

    moment = now or datetime.now(tz=UTC)
    # tenant-scope-exempt: a deployment-wide sweep of short-lived rows. Scoping
    # it per tenant would make the job's cost proportional to the customer count
    # rather than to the number of abandoned sign-ins.
    result = await session.execute(
        # tenant-scope-exempt: see above.
        delete(OidcAuthRequest)
        .where(OidcAuthRequest.expires_at <= moment)
        .execution_options(**{TENANT_SCOPE_EXEMPT: True})
    )
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "AUTH_REQUEST_TTL",
    "DISCOVERY_MAX_AGE",
    "CompletedFlow",
    "StartedFlow",
    "begin",
    "complete",
    "discovery_document",
    "get_provider",
    "list_providers",
    "register_provider",
    "sweep_expired_requests",
]
