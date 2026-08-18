"""Managing API keys and webhook subscriptions.

**Guarded by the control-plane token, not by an API key.** A key that could mint
keys would be a privilege-escalation primitive: a leaked ``public:read``
credential could issue itself ``webhooks:manage`` and register an endpoint
receiving every event the tenant produces. Issuing credentials is a
control-plane operation, and it goes through the same shared secret Phase 5's
writes do — with the same honest caveat that it is not identity (ADR-0020) and
the same compensating control, which is that every mutation writes an event.

**Reads are tenant-scoped and token-free**, matching the control plane: seeing
which keys exist and how much they have been used is the same class of operation
as reading your own taxonomy. The *secrets* are not readable by anybody,
including with the token, because they are not stored.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import select

from nemesis.api.deps import ConfigDep, SessionDep, TenantDep
from nemesis.api.errors import (
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE,
    PROBLEM_BASE,
    ProblemDetailError,
)
from nemesis.config import Settings
from nemesis.db.models.integration import WebhookDelivery, WebhookEndpoint
from nemesis.events.store import EventStore
from nemesis.integrations import keys, webhooks
from nemesis.integrations.errors import (
    ConflictError,
    IntegrationError,
    NotFoundError,
    UnsafeTargetError,
)
from nemesis.observability.logging import get_correlation_id
from nemesis.tenancy.context import tenant_scope

router = APIRouter(prefix="/integrations", tags=["integrations"])

CONTROL_PLANE_TOKEN_HEADER: Final = "X-Control-Plane-Token"


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class MintKeyRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1)
    quota_per_hour: int = Field(default=3600, gt=0, le=1_000_000)
    #: Days until expiry. Expressed as a duration rather than a date because
    #: every caller computes the same "ninety days from now" and half of them
    #: get the timezone wrong.
    expires_in_days: int | None = Field(default=None, gt=0, le=3650)


class RevokeKeyRequest(ApiModel):
    #: Required, for the reason ``admin_action.justification`` is required: a
    #: revocation with no stated reason is an audit trail that answers "what"
    #: and refuses to answer "why", six months later when somebody asks whether
    #: the key was compromised or merely retired.
    reason: str = Field(min_length=1, max_length=500)


class CreateWebhookRequest(ApiModel):
    url: HttpUrl
    description: str = Field(min_length=1, max_length=500)
    event_types: list[str] = Field(min_length=1)


class SetWebhookActiveRequest(ApiModel):
    active: bool


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class MintedKeyResponse(ApiModel):
    """The one and only time the secret is transmitted."""

    id: str
    name: str
    key_prefix: str
    #: **Shown once.** Nothing in this system can reproduce it — see
    #: ``integrations.keys.MintedKey``.
    secret: str
    scopes: list[str]
    quota_per_hour: int
    expires_at: str | None
    warning: str = (
        "Store this secret now. It is hashed on arrival and cannot be retrieved, "
        "resent, or recovered — a lost key is replaced by minting a new one and "
        "revoking this."
    )


class KeyResponse(ApiModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    quota_per_hour: int
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None
    revoked_reason: str | None


class UsageRow(ApiModel):
    key_prefix: str
    usage_date: str
    endpoint: str
    request_count: int
    error_count: int
    throttled_count: int


class UsageResponse(ApiModel):
    since: str
    until: str
    rows: list[UsageRow]
    total_requests: int


class CreatedWebhookResponse(ApiModel):
    id: str
    url: str
    #: Shown once, like a key. The signing secret is derived, not stored, so
    #: "resend it to me" is a request nothing can satisfy.
    secret: str
    secret_version: int
    event_types: list[str]
    signature_header: str = webhooks.SIGNATURE_HEADER
    signature_scheme: str = (
        "HMAC-SHA256 over '<timestamp>.<raw body>', sent as "
        "'t=<unix>,v1=<hex>'. Reject anything outside your tolerance window; "
        "300 seconds is recommended. See /developers/webhooks."
    )


class WebhookResponse(ApiModel):
    id: str
    url: str
    description: str
    event_types: list[str]
    is_active: bool
    secret_version: int
    secret_fingerprint: str
    consecutive_failures: int
    disabled_at: str | None
    disabled_reason: str | None
    created_at: str


class DeliveryResponse(ApiModel):
    id: int
    event_type: str
    status: str
    attempts: int
    next_attempt_at: str | None
    delivered_at: str | None
    last_status_code: int | None
    last_error: str | None
    created_at: str


class DeliveryLogResponse(ApiModel):
    deliveries: list[DeliveryResponse]
    pending: int
    failed: int


# ---------------------------------------------------------------------------
# Write authorisation — the same shape as the control plane's
# ---------------------------------------------------------------------------


def _require_token(settings: Settings, supplied: str | None) -> None:
    expected = settings.control_plane_token.get_secret_value()
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise ProblemDetailError(
            status_code=HTTP_403_FORBIDDEN,
            title="Control-plane token required",
            detail=(
                f"Issuing credentials and registering webhook targets are "
                f"control-plane operations. Supply the {CONTROL_PLANE_TOKEN_HEADER} header."
            ),
            problem_type=f"{PROBLEM_BASE}/control-plane-token-required",
        )


TokenHeader = Annotated[str | None, Header(alias=CONTROL_PLANE_TOKEN_HEADER)]


def _translate(exc: IntegrationError) -> ProblemDetailError:
    """One error translation, for the reason the control plane gives."""
    if isinstance(exc, NotFoundError):
        return ProblemDetailError(
            status_code=HTTP_404_NOT_FOUND,
            title="Not found",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/not-found",
        )
    if isinstance(exc, ConflictError):
        return ProblemDetailError(
            status_code=HTTP_409_CONFLICT,
            title="Conflict",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/conflict",
        )
    if isinstance(exc, UnsafeTargetError):
        return ProblemDetailError(
            status_code=HTTP_422_UNPROCESSABLE,
            title="Unsafe webhook target",
            detail=str(exc),
            problem_type=f"{PROBLEM_BASE}/unsafe-webhook-target",
        )
    return ProblemDetailError(
        status_code=HTTP_422_UNPROCESSABLE,
        title="Invalid request",
        detail=str(exc),
        problem_type=f"{PROBLEM_BASE}/validation-error",
    )


async def _record(
    session: SessionDep,
    *,
    tenant_id: uuid.UUID,
    action: str,
    justification: str,
    changes: dict[str, Any],
) -> None:
    """Append an ``admin_action`` for a credential change.

    Every mutation here writes one, which is the compensating control for the
    token not being identity (ADR-0020): a compromise is investigated by reading
    the chain, because the token cannot say who used it. Credential issue and
    revocation are the two operations where that trail matters most.
    """
    with tenant_scope(tenant_id):
        await EventStore(session).append(
            tenant_id=tenant_id,
            entity_type="admin_action",
            entity_id=uuid.uuid4(),
            event_type="admin_action",
            payload={
                "action": action,
                "justification": justification,
                "changes": changes,
            },
            correlation_id=get_correlation_id(),
        )


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


@router.post("/keys", status_code=status.HTTP_201_CREATED, summary="Mint an API key")
async def mint_key(
    body: MintKeyRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenHeader = None,
) -> MintedKeyResponse:
    _require_token(settings, token)
    expires = (
        None
        if body.expires_in_days is None
        else datetime.now(tz=UTC) + timedelta(days=body.expires_in_days)
    )
    try:
        minted = await keys.mint(
            session,
            tenant_id=tenant.id,
            name=body.name,
            scopes=body.scopes,
            quota_per_hour=body.quota_per_hour,
            expires_at=expires,
        )
    except IntegrationError as exc:
        raise _translate(exc) from exc

    await _record(
        session,
        tenant_id=tenant.id,
        action="api_key_issued",
        justification=f"Key '{body.name}' issued for {sorted(set(body.scopes))}",
        # The prefix, never the secret and never the digest. An audit trail that
        # records the credential it is auditing is a second copy of the thing
        # that must not leak.
        changes={"key_prefix": minted.prefix, "scopes": sorted(set(body.scopes))},
    )

    return MintedKeyResponse(
        id=str(minted.id),
        name=minted.name,
        key_prefix=minted.prefix,
        secret=minted.secret,
        scopes=list(minted.scopes),
        quota_per_hour=minted.quota_per_hour,
        expires_at=None if minted.expires_at is None else minted.expires_at.isoformat(),
    )


@router.get("/keys", summary="List this tenant's API keys")
async def list_keys(tenant: TenantDep, session: SessionDep) -> list[KeyResponse]:
    rows = await keys.list_keys(session, tenant_id=tenant.id)
    return [
        KeyResponse(
            id=str(row.id),
            name=row.name,
            key_prefix=row.key_prefix,
            scopes=list(row.scopes),
            quota_per_hour=row.quota_per_hour,
            created_at=row.created_at.isoformat(),
            last_used_at=None if row.last_used_at is None else row.last_used_at.isoformat(),
            expires_at=None if row.expires_at is None else row.expires_at.isoformat(),
            revoked_at=None if row.revoked_at is None else row.revoked_at.isoformat(),
            revoked_reason=row.revoked_reason,
        )
        for row in rows
    ]


@router.post("/keys/{key_id}/revoke", summary="Revoke an API key")
async def revoke_key(
    key_id: uuid.UUID,
    body: RevokeKeyRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenHeader = None,
) -> dict[str, str]:
    _require_token(settings, token)
    try:
        await keys.revoke(session, tenant_id=tenant.id, key_id=key_id, reason=body.reason)
    except IntegrationError as exc:
        raise _translate(exc) from exc
    await _record(
        session,
        tenant_id=tenant.id,
        action="api_key_revoked",
        justification=body.reason,
        changes={"key_id": str(key_id)},
    )
    return {"status": "revoked"}


@router.get("/usage", summary="Per-key request accounting")
async def usage(
    tenant: TenantDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> UsageResponse:
    """The rollup, not a request log — see ``db.models.integration``."""
    until = datetime.now(tz=UTC).date()
    since = until - timedelta(days=days - 1)
    rows = await keys.usage_report(session, tenant_id=tenant.id, since=since, until=until)
    return UsageResponse(
        since=since.isoformat(),
        until=until.isoformat(),
        rows=[
            UsageRow(
                key_prefix=prefix,
                usage_date=day.isoformat(),
                endpoint=endpoint,
                request_count=requests,
                error_count=errors,
                throttled_count=throttled,
            )
            for _key_id, prefix, day, endpoint, requests, errors, throttled in rows
        ],
        total_requests=sum(row[4] for row in rows),
    )


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


@router.post(
    "/webhooks", status_code=status.HTTP_201_CREATED, summary="Register a webhook endpoint"
)
async def create_webhook(
    body: CreateWebhookRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenHeader = None,
) -> CreatedWebhookResponse:
    _require_token(settings, token)
    try:
        created = await webhooks.create_endpoint(
            session,
            tenant_id=tenant.id,
            url=str(body.url),
            description=body.description,
            event_types=body.event_types,
            root_key=settings.webhook_signing_key.get_secret_value(),
            allow_private=settings.webhooks.allow_private_network_targets,
        )
    except IntegrationError as exc:
        raise _translate(exc) from exc

    await _record(
        session,
        tenant_id=tenant.id,
        action="webhook_endpoint_created",
        justification=body.description,
        # The URL is recorded because it is the target of a durable data flow and
        # an auditor asking "where did our events go in March" has no other way
        # to answer it. The secret is not, and cannot be — it is derived.
        changes={"url": str(body.url), "event_types": sorted(set(body.event_types))},
    )

    return CreatedWebhookResponse(
        id=str(created.id),
        url=created.url,
        secret=created.secret,
        secret_version=created.secret_version,
        event_types=list(created.event_types),
    )


@router.get("/webhooks", summary="List this tenant's webhook endpoints")
async def list_webhooks(tenant: TenantDep, session: SessionDep) -> list[WebhookResponse]:
    rows = await webhooks.list_endpoints(session, tenant_id=tenant.id)
    return [_webhook_response(row) for row in rows]


@router.post("/webhooks/{endpoint_id}/rotate-secret", summary="Rotate a signing secret")
async def rotate_secret(
    endpoint_id: uuid.UUID,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenHeader = None,
) -> CreatedWebhookResponse:
    """The previous secret stops working immediately — see ``webhooks.rotate_secret``."""
    _require_token(settings, token)
    try:
        rotated = await webhooks.rotate_secret(
            session,
            tenant_id=tenant.id,
            endpoint_id=endpoint_id,
            root_key=settings.webhook_signing_key.get_secret_value(),
        )
    except IntegrationError as exc:
        raise _translate(exc) from exc
    await _record(
        session,
        tenant_id=tenant.id,
        action="webhook_secret_rotated",
        justification="Signing secret rotated on request",
        changes={"endpoint_id": str(endpoint_id), "secret_version": rotated.secret_version},
    )
    return CreatedWebhookResponse(
        id=str(rotated.id),
        url=rotated.url,
        secret=rotated.secret,
        secret_version=rotated.secret_version,
        event_types=list(rotated.event_types),
    )


@router.post("/webhooks/{endpoint_id}/active", summary="Enable or disable an endpoint")
async def set_webhook_active(
    endpoint_id: uuid.UUID,
    body: SetWebhookActiveRequest,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenHeader = None,
) -> dict[str, bool]:
    _require_token(settings, token)
    try:
        await webhooks.set_active(
            session, tenant_id=tenant.id, endpoint_id=endpoint_id, active=body.active
        )
    except IntegrationError as exc:
        raise _translate(exc) from exc
    return {"is_active": body.active}


@router.delete("/webhooks/{endpoint_id}", summary="Delete an endpoint and its history")
async def delete_webhook(
    endpoint_id: uuid.UUID,
    tenant: TenantDep,
    session: SessionDep,
    settings: ConfigDep,
    token: TokenHeader = None,
) -> dict[str, str]:
    _require_token(settings, token)
    try:
        await webhooks.delete_endpoint(session, tenant_id=tenant.id, endpoint_id=endpoint_id)
    except IntegrationError as exc:
        raise _translate(exc) from exc
    await _record(
        session,
        tenant_id=tenant.id,
        action="webhook_endpoint_deleted",
        justification="Endpoint removed on request",
        changes={"endpoint_id": str(endpoint_id)},
    )
    return {"status": "deleted"}


@router.get(
    "/webhooks/{endpoint_id}/deliveries",
    summary="The delivery log for one endpoint",
)
async def delivery_log(
    endpoint_id: uuid.UUID,
    tenant: TenantDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DeliveryLogResponse:
    """ "A delivery log tenants can inspect", made literal.

    Token-free, like every other read: a tenant seeing whether their own events
    were delivered is not a privileged operation, and making it one would mean
    the shared secret has to be distributed to whoever debugs the integration.
    """
    with tenant_scope(tenant.id):
        exists = (
            await session.execute(
                select(WebhookEndpoint.id).where(
                    WebhookEndpoint.tenant_id == tenant.id,
                    WebhookEndpoint.id == endpoint_id,
                )
            )
        ).one_or_none()
        if exists is None:
            raise _translate(NotFoundError(f"no webhook endpoint {endpoint_id} for this tenant"))

        rows = (
            (
                await session.execute(
                    select(WebhookDelivery)
                    .where(
                        WebhookDelivery.tenant_id == tenant.id,
                        WebhookDelivery.endpoint_id == endpoint_id,
                    )
                    .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    return DeliveryLogResponse(
        deliveries=[
            DeliveryResponse(
                id=row.id,
                event_type=row.event_type,
                status=row.status,
                attempts=row.attempts,
                next_attempt_at=(
                    None if row.next_attempt_at is None else row.next_attempt_at.isoformat()
                ),
                delivered_at=None if row.delivered_at is None else row.delivered_at.isoformat(),
                last_status_code=row.last_status_code,
                last_error=row.last_error,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ],
        pending=sum(1 for row in rows if row.status == "pending"),
        failed=sum(1 for row in rows if row.status == "failed"),
    )


def _webhook_response(row: WebhookEndpoint) -> WebhookResponse:
    return WebhookResponse(
        id=str(row.id),
        url=row.url,
        description=row.description,
        event_types=list(row.event_types),
        is_active=row.is_active,
        secret_version=row.secret_version,
        secret_fingerprint=row.secret_fingerprint,
        consecutive_failures=row.consecutive_failures,
        disabled_at=None if row.disabled_at is None else row.disabled_at.isoformat(),
        disabled_reason=row.disabled_reason,
        created_at=row.created_at.isoformat(),
    )
