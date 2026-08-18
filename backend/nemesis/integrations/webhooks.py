"""Webhook subscriptions, signatures, and the SSRF guard.

**The signature scheme.** ``X-Nemesis-Signature: t=<unix>,v1=<hex>``, where the
hex is ``HMAC-SHA256(secret, "<t>.<body>")``.

Three properties, each a decision:

*The timestamp is inside the signed string, not beside it.* A signature over the
body alone is replayable forever — anyone who captures one delivery can resend
it at any point in the future and the receiver's verification passes. Binding the
timestamp into the MAC lets a receiver reject anything older than their tolerance
window, and makes the timestamp itself unforgeable.

*The scheme is versioned in the header* (``v1=``). When SHA-256 needs replacing
the header carries both for a transition period and receivers verify whichever
they support. A bare hex string would make that migration a flag day across every
tenant's infrastructure simultaneously.

*The secret is derived, never stored.* ``HMAC(deployment_key, endpoint_id ||
version)``. A database dump contains no signing material, rotation is an integer
increment, and there is no encryption key to manage — see the model's column
comment for why the two obvious alternatives are worse.

**The SSRF guard is the security control of this module.** A webhook URL is
attacker-supplied by construction: anyone who can reach the control plane can
point one at ``http://169.254.169.254/latest/meta-data/`` or at a Postgres
admin interface on the deployment's own network, and the dispatcher will fetch
it and record the status code in a log the same party can read. That is a
credential-exfiltration primitive with a delivery receipt.

The guard resolves the hostname and rejects private, loopback, link-local,
multicast, and reserved ranges — resolving rather than pattern-matching the
string, because ``http://spoof.example.com/`` resolving to ``127.0.0.1`` is the
whole attack, and a regex over the URL cannot see it. It is re-checked at
delivery time as well as at registration, because DNS answers change between
the two and a rebinding attack is exactly that gap.

``allow_private_network_targets`` relaxes **loopback and RFC 1918 only**. It is
not an off switch: link-local stays refused on every deployment, because that
range is the cloud credential endpoint and is never a webhook target anybody
wants. See ``_unsafe_reason`` — the distinction was found by the Phase 4 gate
registering a metadata URL against a local stack and getting a 201.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nemesis.db.models.integration import KEY_PREFIX_LENGTH, WebhookEndpoint
from nemesis.events.registry import registered_events
from nemesis.integrations.errors import (
    ConflictError,
    NotFoundError,
    UnsafeTargetError,
    ValidationError,
)
from nemesis.tenancy.context import tenant_scope

SIGNATURE_HEADER: Final = "X-Nemesis-Signature"
DELIVERY_ID_HEADER: Final = "X-Nemesis-Delivery"
EVENT_TYPE_HEADER: Final = "X-Nemesis-Event"
ATTEMPT_HEADER: Final = "X-Nemesis-Attempt"

#: The MAC version in the signature header. Bumping it means shipping both for
#: a transition window, never swapping in place.
SIGNATURE_VERSION: Final = "v1"

#: What a receiver should reject beyond. Published in the developer portal
#: rather than merely implemented, because a replay window nobody documents is
#: one every integrator picks differently.
RECOMMENDED_TOLERANCE_SECONDS: Final = 300

_ALLOWED_SCHEMES: Final = frozenset({"https", "http"})


def derive_secret(root_key: str, endpoint_id: uuid.UUID, version: int) -> str:
    """The signing secret for one endpoint at one rotation.

    Deterministic, so nothing is stored; keyed by the endpoint id, so one
    tenant's leaked secret says nothing about another's; keyed by the version,
    so a rotation genuinely produces an unrelated value rather than something
    derivable from the old one.
    """
    material = f"{endpoint_id}:{version}".encode()
    return hmac.new(root_key.encode("utf-8"), material, hashlib.sha256).hexdigest()


def fingerprint(secret: str) -> str:
    """A short, non-reversible label for a secret.

    Stored so a tenant can answer "is my handler on the current secret" after a
    rotation without either side transmitting the secret to find out.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:KEY_PREFIX_LENGTH]


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    """Build the ``X-Nemesis-Signature`` value for a body."""
    stamp = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode("utf-8"), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},{SIGNATURE_VERSION}={mac}"


def verify(
    secret: str,
    body: bytes,
    header: str,
    *,
    tolerance_seconds: int = RECOMMENDED_TOLERANCE_SECONDS,
    now: int | None = None,
) -> bool:
    """Verify a signature the way a receiver should.

    Shipped here rather than only documented because every integrator writes
    this function, most of them write ``==`` instead of ``compare_digest``, and
    a reference implementation in the vendor's own test suite is the cheapest
    way to make the published example one that has been executed. The developer
    portal renders it.
    """
    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    stamp = parts.get("t")
    presented = parts.get(SIGNATURE_VERSION)
    if stamp is None or presented is None:
        return False
    try:
        issued = int(stamp)
    except ValueError:
        return False

    current = int(time.time()) if now is None else now
    if abs(current - issued) > tolerance_seconds:
        return False

    expected = hmac.new(
        secret.encode("utf-8"), f"{issued}.".encode() + body, hashlib.sha256
    ).hexdigest()
    # compare_digest, not ==: the presented value is attacker-controlled, and a
    # short-circuiting comparison leaks the length of the matching prefix, which
    # is enough to forge a signature one byte at a time.
    return hmac.compare_digest(expected, presented)


@dataclass(frozen=True, slots=True)
class CreatedEndpoint:
    """A new subscription. ``secret`` is returned once and never again."""

    id: uuid.UUID
    url: str
    secret: str
    secret_version: int
    event_types: tuple[str, ...]


def assert_target_is_safe(url: str, *, allow_private: bool) -> None:
    """Refuse a URL that would make this deployment an SSRF proxy.

    Resolves the hostname rather than inspecting the string. ``http://
    evil.example/`` that resolves to ``10.0.0.5`` is indistinguishable from any
    other public hostname until you look up the address, and looking at the
    string is the check that feels like security while providing none.

    A hostname that does not resolve is *accepted* at registration time and
    checked again before every delivery. Refusing it would break the ordinary
    case of a tenant registering an endpoint before their DNS has propagated,
    and the delivery-time check is the one that actually protects anything —
    which is also why a rebinding attack, where the answer changes between the
    two, does not get through.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValidationError(
            f"webhook URL scheme must be one of {sorted(_ALLOWED_SCHEMES)}, got '{parsed.scheme}'"
        )
    if parsed.scheme == "http" and not allow_private:
        raise UnsafeTargetError(
            "webhook URLs must use https. A signed payload delivered over plaintext "
            "is readable by anything on the path, and the signature proves it came "
            "from us — it does not keep the contents private."
        )
    if not parsed.hostname:
        raise ValidationError("webhook URL has no host")
    if parsed.username or parsed.password:
        raise ValidationError(
            "webhook URL carries inline credentials; they would be written to the "
            "delivery log, which the tenant can read back"
        )

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        # Unresolvable now, re-checked at delivery. See the docstring.
        return

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        reason = _unsafe_reason(address, allow_private=allow_private)
        if reason is not None:
            raise UnsafeTargetError(
                f"'{parsed.hostname}' resolves to {address}, which is {reason}. "
                f"Delivering there would let a webhook subscription reach this "
                f"deployment's own internal network and its cloud metadata "
                f"endpoint, and report back what it found."
            )


def _unsafe_reason(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool
) -> str | None:
    """Why this address may not be delivered to, or ``None`` if it may.

    **``allow_private`` is not an off switch, and that distinction was found by
    the Phase 4 gate rather than by review.** The first version returned early
    when the flag was set, which meant a local stack — the only place the flag is
    ever set — would happily register and deliver to
    ``169.254.169.254/latest/meta-data/``. The gate registered exactly that URL
    and got a 201.

    The flag exists so a developer can point a webhook at ``localhost`` or the
    Docker gateway. That is the whole legitimate use, and it is satisfied by
    relaxing **loopback and RFC 1918 private ranges only**. Link-local is never a
    legitimate webhook target on any machine: on a laptop it is a
    self-assigned address nothing listens on, and on a cloud instance it is the
    credential endpoint. Multicast, reserved, and unspecified are not addresses
    an HTTP endpoint has at all.

    So the relaxation is scoped to the two families a developer actually needs,
    and the family that matters stays refused unconditionally.
    """
    if address.is_link_local:
        return "a link-local address — on a cloud instance this is the credential metadata endpoint"
    if address.is_multicast:
        return "a multicast address, which no HTTP endpoint has"
    if address.is_reserved:
        return "a reserved address"
    if address.is_unspecified:
        return "the unspecified address"
    if allow_private:
        # Loopback and RFC 1918 only, and only because a local stack asked.
        return None
    if address.is_loopback:
        return "a loopback address"
    if address.is_private:
        return "a private address"
    return None


def known_event_types() -> frozenset[str]:
    """Every event type a subscription may name.

    Read from the event registry rather than from a list, so a phase that
    registers a new type makes it subscribable without touching this module —
    and so a typo in a subscription is refused at creation instead of producing
    a subscription that silently never fires.
    """
    return frozenset(entry.event_type for entry in registered_events())


async def create_endpoint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    url: str,
    description: str,
    event_types: list[str],
    root_key: str,
    allow_private: bool,
) -> CreatedEndpoint:
    if not event_types:
        raise ValidationError(
            "a subscription with no event types delivers nothing; 'all' is "
            "deliberately not offered — see the model's column comment"
        )
    unknown = sorted(set(event_types) - known_event_types())
    if unknown:
        raise ValidationError(
            f"unknown event type(s) {unknown}; a subscription naming one would never "
            f"fire and would look like a delivery bug on our side"
        )
    assert_target_is_safe(url, allow_private=allow_private)

    with tenant_scope(tenant_id):
        clash = (
            await session.execute(
                select(func.count())
                .select_from(WebhookEndpoint)
                .where(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.url == url)
            )
        ).scalar_one()
        if clash:
            raise ConflictError(
                f"a subscription for {url} already exists; a second one would deliver "
                f"every event twice, which reads to the receiver as our bug"
            )

        # The id is generated here rather than by the column's server default.
        # The secret derives from it, so the alternative is insert-then-update —
        # and that second statement is an ORM flush emitting
        # `UPDATE ... WHERE id = ?` with no tenant predicate, which the tenancy
        # guard refuses and is right to (ADR-0014): it is indistinguishable from
        # the unscoped write the guard exists to catch. Assigning the id up front
        # makes the whole creation one INSERT, which the NOT NULL tenant column
        # already covers.
        endpoint_id = uuid.uuid4()
        secret = derive_secret(root_key, endpoint_id, 1)
        endpoint = WebhookEndpoint(
            id=endpoint_id,
            tenant_id=tenant_id,
            url=url,
            description=description,
            event_types=sorted(set(event_types)),
            secret_version=1,
            secret_fingerprint=fingerprint(secret),
        )
        session.add(endpoint)
        await session.flush()

    return CreatedEndpoint(
        id=endpoint.id,
        url=url,
        secret=secret,
        secret_version=endpoint.secret_version,
        event_types=tuple(endpoint.event_types),
    )


async def rotate_secret(
    session: AsyncSession, *, tenant_id: uuid.UUID, endpoint_id: uuid.UUID, root_key: str
) -> CreatedEndpoint:
    """Issue a new signing secret for an endpoint.

    **The old secret stops working immediately, and that is stated rather than
    softened.** An overlap window — accepting both for an hour — is friendlier
    and defeats the purpose: the reason to rotate is usually that the old secret
    is believed compromised, and a rotation that leaves the compromised value
    valid for another hour has not rotated anything. Tenants coordinate a
    rotation with a deploy; the developer portal says so.
    """
    with tenant_scope(tenant_id):
        endpoint = (
            await session.execute(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.tenant_id == tenant_id,
                    WebhookEndpoint.id == endpoint_id,
                )
            )
        ).scalar_one_or_none()
        if endpoint is None:
            raise NotFoundError(f"no webhook endpoint {endpoint_id} for this tenant")

        version = endpoint.secret_version + 1
        secret = derive_secret(root_key, endpoint.id, version)
        # An explicit, tenant-scoped UPDATE rather than an attribute assignment,
        # for the reason `create_endpoint` gives: an ORM flush emits a
        # primary-key-only predicate that the tenancy guard cannot distinguish
        # from an unscoped write.
        await session.execute(
            update(WebhookEndpoint)
            .where(
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.id == endpoint_id,
            )
            .values(secret_version=version, secret_fingerprint=fingerprint(secret))
        )
        result = CreatedEndpoint(
            id=endpoint.id,
            url=endpoint.url,
            secret=secret,
            secret_version=version,
            event_types=tuple(endpoint.event_types),
        )

    return result


async def list_endpoints(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[WebhookEndpoint]:
    with tenant_scope(tenant_id):
        return list(
            (
                await session.execute(
                    select(WebhookEndpoint)
                    .where(WebhookEndpoint.tenant_id == tenant_id)
                    .order_by(WebhookEndpoint.created_at.desc())
                )
            )
            .scalars()
            .all()
        )


async def set_active(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    active: bool,
) -> None:
    """Enable or disable a subscription.

    Re-enabling clears ``consecutive_failures`` and the disabled reason: the
    tenant is asserting they have fixed the endpoint, and leaving the counter at
    fifty would disable it again on the first transient blip after recovery.
    """
    values: dict[str, object] = {"is_active": active}
    if active:
        values |= {"consecutive_failures": 0, "disabled_at": None, "disabled_reason": None}

    with tenant_scope(tenant_id):
        result = await session.execute(
            update(WebhookEndpoint)
            .where(
                WebhookEndpoint.tenant_id == tenant_id,
                WebhookEndpoint.id == endpoint_id,
            )
            .values(**values)
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            raise NotFoundError(f"no webhook endpoint {endpoint_id} for this tenant")


async def delete_endpoint(
    session: AsyncSession, *, tenant_id: uuid.UUID, endpoint_id: uuid.UUID
) -> None:
    """Remove a subscription and its delivery history.

    The delivery rows cascade, which is the one place in this system where
    history is genuinely deleted rather than tombstoned — and it is correct
    here: the *events* remain in the append-only log untouched, and these rows
    record only whether an HTTP request to a URL that no longer exists
    succeeded. Keeping them would preserve a tenant's endpoint URL after they
    asked for it to be gone.
    """
    with tenant_scope(tenant_id):
        endpoint = (
            await session.execute(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.tenant_id == tenant_id,
                    WebhookEndpoint.id == endpoint_id,
                )
            )
        ).scalar_one_or_none()
        if endpoint is None:
            raise NotFoundError(f"no webhook endpoint {endpoint_id} for this tenant")
        await session.delete(endpoint)
