"""Gate clause 2: webhook delivery survives an endpoint being down for an hour,
then drains.

**Drilled against a real endpoint that is really down**, with a clock the test
advances rather than a sleep. The alternative — asserting that
``backoff_schedule_seconds`` sums to more than 3600 — is arithmetic about a
constant, and it would pass against a dispatcher that never retried at all.
So the outage test runs the real dispatcher against a transport that refuses
every connection for a simulated hour, then stops refusing, and asserts the
delivery lands with its attempt count intact.

The signature and SSRF tests are the other half of this module, and they are
where the security of the feature actually lives.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from nemesis.config import Settings, WebhookSettings
from nemesis.integrations import delivery, webhooks
from nemesis.integrations.errors import ConflictError, UnsafeTargetError, ValidationError
from nemesis.tenancy.context import tenant_scope
from tests.conftest import postgres_required

pytestmark = [postgres_required, pytest.mark.asyncio]

TOKEN = {"X-Control-Plane-Token": "dev-only-insecure-control-plane-token-change-me"}
ROOT_KEY = "test-root-signing-key"


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


async def test_a_signature_round_trips() -> None:
    secret = webhooks.derive_secret(ROOT_KEY, uuid.uuid4(), 1)
    body = b'{"event_type":"cluster_created"}'
    assert webhooks.verify(secret, body, webhooks.sign(secret, body))


async def test_a_tampered_body_fails_verification() -> None:
    secret = webhooks.derive_secret(ROOT_KEY, uuid.uuid4(), 1)
    header = webhooks.sign(secret, b'{"severity":3}')
    assert not webhooks.verify(secret, b'{"severity":9}', header)


async def test_a_replayed_delivery_fails_outside_the_window() -> None:
    """The timestamp is inside the signed string, so it cannot be altered.

    A signature over the body alone is replayable forever: anyone who captures
    one delivery can resend it at any future point and verification passes.
    """
    secret = webhooks.derive_secret(ROOT_KEY, uuid.uuid4(), 1)
    body = b"{}"
    issued = 1_700_000_000
    header = webhooks.sign(secret, body, timestamp=issued)

    assert webhooks.verify(secret, body, header, now=issued + 60)
    assert not webhooks.verify(secret, body, header, now=issued + 4000)
    # And the timestamp cannot simply be edited to a fresh one.
    forged = header.replace(f"t={issued}", f"t={issued + 4000}")
    assert not webhooks.verify(secret, body, forged, now=issued + 4000)


async def test_a_signature_from_another_endpoint_does_not_verify() -> None:
    """One tenant's leaked secret says nothing about another's."""
    a = webhooks.derive_secret(ROOT_KEY, uuid.uuid4(), 1)
    b = webhooks.derive_secret(ROOT_KEY, uuid.uuid4(), 1)
    assert a != b
    body = b"{}"
    assert not webhooks.verify(b, body, webhooks.sign(a, body))


async def test_rotation_produces_an_unrelated_secret() -> None:
    endpoint_id = uuid.uuid4()
    v1 = webhooks.derive_secret(ROOT_KEY, endpoint_id, 1)
    v2 = webhooks.derive_secret(ROOT_KEY, endpoint_id, 2)
    assert v1 != v2
    # And the old one genuinely stops working — no overlap window. The reason to
    # rotate is usually that the old secret is believed compromised.
    body = b"{}"
    assert not webhooks.verify(v2, body, webhooks.sign(v1, body))


async def test_a_malformed_signature_header_is_rejected_not_crashed() -> None:
    secret = webhooks.derive_secret(ROOT_KEY, uuid.uuid4(), 1)
    for header in ("", "garbage", "t=abc,v1=def", "v1=onlythis", "t=123"):
        assert webhooks.verify(secret, b"{}", header) is False


# ---------------------------------------------------------------------------
# The SSRF guard
# ---------------------------------------------------------------------------


async def test_a_loopback_target_is_refused() -> None:
    """A webhook URL is attacker-supplied by construction.

    Anyone who reaches the control plane could point one at the deployment's own
    network and read the status code back out of the delivery log — a
    credential-exfiltration primitive with a receipt.
    """
    with pytest.raises(UnsafeTargetError, match=r"loopback|private"):
        webhooks.assert_target_is_safe("https://127.0.0.1/hook", allow_private=False)


async def test_a_cloud_metadata_address_is_refused() -> None:
    with pytest.raises(UnsafeTargetError):
        webhooks.assert_target_is_safe(
            "https://169.254.169.254/latest/meta-data/", allow_private=False
        )


async def test_a_private_range_is_refused() -> None:
    with pytest.raises(UnsafeTargetError):
        webhooks.assert_target_is_safe("https://10.0.0.5/hook", allow_private=False)


async def test_plaintext_http_is_refused() -> None:
    """A signature proves origin; it does not keep the contents private."""
    with pytest.raises(UnsafeTargetError, match="https"):
        webhooks.assert_target_is_safe("http://example.com/hook", allow_private=False)


async def test_inline_credentials_are_refused() -> None:
    """They would be written to a delivery log the tenant reads back."""
    with pytest.raises(ValidationError, match="credentials"):
        webhooks.assert_target_is_safe("https://user:pw@example.com/h", allow_private=False)


async def test_a_non_http_scheme_is_refused() -> None:
    with pytest.raises(ValidationError, match="scheme"):
        webhooks.assert_target_is_safe("file:///etc/passwd", allow_private=False)


async def test_a_public_target_is_allowed() -> None:
    webhooks.assert_target_is_safe("https://example.com/hooks/nemesis", allow_private=False)


async def test_allow_private_does_not_permit_the_cloud_metadata_address() -> None:
    """**Regression — the Phase 4 gate found this, not review.**

    ``allow_private`` returned early, so it disabled every address check rather
    than the two it exists for. The gate registered
    ``169.254.169.254/latest/meta-data/`` against a local stack — the only place
    the flag is ever set — and got a 201.

    The flag exists so a developer can point at localhost or the Docker gateway.
    Link-local is never a legitimate webhook target on any machine: on a laptop
    nothing listens on it, and on a cloud instance it is the credential endpoint.
    """
    with pytest.raises(UnsafeTargetError, match="link-local"):
        webhooks.assert_target_is_safe(
            "https://169.254.169.254/latest/meta-data/", allow_private=True
        )


async def test_allow_private_permits_only_loopback_and_rfc1918() -> None:
    """The relaxation is scoped to what a local stack actually needs."""
    webhooks.assert_target_is_safe("http://127.0.0.1:9000/hook", allow_private=True)
    webhooks.assert_target_is_safe("http://10.0.0.5/hook", allow_private=True)

    # And everything that is not a machine a developer is running on stays out.
    for unsafe in ("https://224.0.0.1/h", "https://0.0.0.0/h"):
        with pytest.raises(UnsafeTargetError):
            webhooks.assert_target_is_safe(unsafe, allow_private=True)


async def test_plaintext_http_is_permitted_only_under_allow_private() -> None:
    """A local stack has no certificate; a deployment has no excuse.

    Coupled to the same flag deliberately — the pilot validator refuses to boot
    with it set, so there is exactly one switch that turns both relaxations off
    rather than two that can disagree.
    """
    webhooks.assert_target_is_safe("http://127.0.0.1:9000/hook", allow_private=True)
    with pytest.raises(UnsafeTargetError, match="https"):
        webhooks.assert_target_is_safe("http://example.com/hook", allow_private=False)


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------


async def test_an_unknown_event_type_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A subscription naming one would never fire and would look like our bug."""
    async with _session(migrated_engine) as session:
        with pytest.raises(ValidationError, match="unknown event type"):
            await webhooks.create_endpoint(
                session,
                tenant_id=tenant_id,
                url="https://example.com/h",
                description="d",
                event_types=["complaint_teleported"],
                root_key=ROOT_KEY,
                allow_private=False,
            )


async def test_event_types_come_from_the_registry() -> None:
    """So a phase that registers a type makes it subscribable without an edit here."""
    known = webhooks.known_event_types()
    assert "complaint_submitted" in known
    assert "cluster_created" in known
    assert "taxonomy_published" in known


async def test_a_duplicate_subscription_is_refused(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A second identical subscription delivers everything twice.

    Which reads to the receiver as a redelivery bug in this system rather than
    as their own configuration.
    """
    async with _session(migrated_engine) as session:
        await webhooks.create_endpoint(
            session,
            tenant_id=tenant_id,
            url="https://example.com/h",
            description="d",
            event_types=["cluster_created"],
            root_key=ROOT_KEY,
            allow_private=False,
        )
        await session.commit()
        with pytest.raises(ConflictError):
            await webhooks.create_endpoint(
                session,
                tenant_id=tenant_id,
                url="https://example.com/h",
                description="d",
                event_types=["cluster_created"],
                root_key=ROOT_KEY,
                allow_private=False,
            )


async def test_the_signing_secret_is_never_stored(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A database dump reveals no signing material at all.

    Nothing here is encrypted, because nothing here is stored — the secret is
    ``HMAC(deployment_key, endpoint_id || version)`` and is recomputed at each
    delivery.
    """
    async with _session(migrated_engine) as session:
        created = await webhooks.create_endpoint(
            session,
            tenant_id=tenant_id,
            url="https://example.com/h",
            description="d",
            event_types=["cluster_created"],
            root_key=ROOT_KEY,
            allow_private=False,
        )
        await session.commit()

    async with migrated_engine.begin() as conn:
        row = (
            await conn.execute(
                sql_text("SELECT * FROM webhook_endpoints WHERE tenant_id = :t").bindparams(
                    t=tenant_id
                )
            )
        ).one()

    assert created.secret not in str(dict(row._mapping))
    assert row.secret_fingerprint == webhooks.fingerprint(created.secret)


# ---------------------------------------------------------------------------
# Fan-out and delivery
# ---------------------------------------------------------------------------


async def test_fan_out_creates_one_delivery_per_matching_subscription(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    async with _session(migrated_engine) as session:
        await webhooks.create_endpoint(
            session,
            tenant_id=tenant_id,
            url="https://example.com/wanted",
            description="d",
            event_types=["cluster_created"],
            root_key=ROOT_KEY,
            allow_private=False,
        )
        await session.commit()

    await _emit_outbox(migrated_engine, tenant_id, "cluster_created")
    await _emit_outbox(migrated_engine, tenant_id, "severity_scored")

    async with _session(migrated_engine) as session:
        result = await delivery.fan_out_once(session, batch_size=100)
        await session.commit()

    assert result.scanned == 2
    # Only the subscribed type. An 'all' subscription is deliberately not
    # offered, so an unsubscribed event produces nothing.
    assert result.enqueued == 1


async def test_fan_out_is_idempotent_across_a_crash(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """A re-read after a crash is a no-op, not a duplicate.

    The cursor advances in the same transaction as the rows it produced, so a
    crash re-reads the batch — and the unique constraint makes that harmless.
    """
    async with _session(migrated_engine) as session:
        await webhooks.create_endpoint(
            session,
            tenant_id=tenant_id,
            url="https://example.com/h",
            description="d",
            event_types=["cluster_created"],
            root_key=ROOT_KEY,
            allow_private=False,
        )
        await session.commit()

    await _emit_outbox(migrated_engine, tenant_id, "cluster_created")

    async with _session(migrated_engine) as session:
        await delivery.fan_out_once(session, batch_size=100)
        await session.commit()

    # Rewind the cursor, as a crash between the insert and the commit would.
    async with migrated_engine.begin() as conn:
        await conn.execute(sql_text("UPDATE webhook_cursor SET last_outbox_id = 0"))

    async with _session(migrated_engine) as session:
        second = await delivery.fan_out_once(session, batch_size=100)
        await session.commit()

    assert second.enqueued == 1  # attempted
    assert await _delivery_count(migrated_engine) == 1  # but only one row exists


async def test_a_delivery_is_signed_and_carries_its_headers(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    """The receiver's side of the contract, asserted from the receiver's position."""
    received: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    await _subscribe_and_emit(migrated_engine, tenant_id, "cluster_created")
    settings = _webhook_settings(app_settings)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        _session(migrated_engine) as session,
    ):
        result = await delivery.dispatch_once(session, settings=settings, client=client)
        await session.commit()

    assert result.delivered == 1
    request = received[0]
    assert request.headers[webhooks.EVENT_TYPE_HEADER] == "cluster_created"
    assert request.headers[webhooks.ATTEMPT_HEADER] == "1"
    assert request.headers[webhooks.DELIVERY_ID_HEADER]

    endpoint_id = await _endpoint_id(migrated_engine, tenant_id)
    secret = webhooks.derive_secret(settings.webhook_signing_key.get_secret_value(), endpoint_id, 1)
    assert webhooks.verify(secret, request.content, request.headers[webhooks.SIGNATURE_HEADER])


async def test_a_delivered_payload_carries_no_citizen_data(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    """A webhook is a *more* durable disclosure than a WebSocket frame.

    The receiver keeps it. So it goes through the same default-deny shaper
    (ADR-0016) — publishing more here than on the socket would be backwards.
    """
    from nemesis.public.policy import find_disclosures

    bodies: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200)

    await _subscribe_and_emit(
        migrated_engine,
        tenant_id,
        "complaint_submitted",
        payload={
            "latitude": 18.5204312,
            "longitude": 73.8567451,
            "description_text": "the citizen's own words",
            "device_fingerprint": "must-not-leak",
        },
    )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        _session(migrated_engine) as session,
    ):
        await delivery.dispatch_once(
            session, settings=_webhook_settings(app_settings), client=client
        )
        await session.commit()

    envelope = json.loads(bodies[0])
    assert "must-not-leak" not in bodies[0].decode()
    assert "the citizen's own words" not in bodies[0].decode()
    assert "18.5204312" not in bodies[0].decode()
    # complaint_submitted has no declared public shape, so the payload is empty
    # rather than filtered — that is default-deny doing its job.
    assert envelope["payload"] == {}
    assert find_disclosures(envelope["payload"]) == []


async def test_delivery_survives_an_hour_long_outage_then_drains(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    """**The gate clause.**

    The endpoint refuses every connection across a simulated hour of wall clock.
    The dispatcher is driven with an advancing ``now`` rather than a sleep, so
    the test exercises the real scheduling arithmetic without taking an hour.
    Then the endpoint recovers and the delivery lands — with its attempt count
    intact, which is what proves it was the *same* delivery retried rather than
    a fresh one.
    """
    outage = {"down": True}
    attempts: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(int(request.headers[webhooks.ATTEMPT_HEADER]))
        if outage["down"]:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200)

    await _subscribe_and_emit(migrated_engine, tenant_id, "cluster_created")
    settings = _webhook_settings(app_settings)

    start = datetime.now(tz=UTC)
    hour_later = start + timedelta(hours=1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        moment = start
        # Walk the clock forward through the outage, dispatching whenever
        # something is due. Ten minutes at a time is finer than the backoff tail,
        # so no scheduled attempt is stepped over.
        while moment <= hour_later:
            async with _session(migrated_engine) as session:
                await delivery.dispatch_once(session, settings=settings, client=client, now=moment)
                await session.commit()
            moment += timedelta(minutes=10)

        state = await _delivery_state(migrated_engine)
        assert state["status"] == "pending", "gave up inside the outage window"
        assert state["attempts"] >= 3, state
        assert state["last_error"] == "ConnectError"
        failed_attempts = len(attempts)

        # The endpoint comes back.
        outage["down"] = False
        async with _session(migrated_engine) as session:
            await delivery.dispatch_once(
                session, settings=settings, client=client, now=hour_later + timedelta(hours=3)
            )
            await session.commit()

    final = await _delivery_state(migrated_engine)
    assert final["status"] == "delivered"
    assert final["last_status_code"] == 200
    assert final["delivered_at"] is not None
    # The same delivery, retried — not a fresh one that happened to succeed.
    assert final["attempts"] == failed_attempts + 1
    assert attempts[-1] == final["attempts"]


async def test_the_retry_window_spans_more_than_an_hour() -> None:
    """The claim the gate clause rests on, as a number rather than a hope.

    Written as configuration precisely so it can be asserted. ``2 ** attempt``
    is a property nobody can state without doing arithmetic, and the arithmetic
    is where "exponential backoff" usually turns out to top out at four minutes.
    """
    settings = WebhookSettings()
    assert delivery.total_retry_window(settings) > timedelta(hours=1)
    assert len(settings.backoff_schedule_seconds) >= 8
    # Monotonic: a schedule that went 30s, 5m, 30s would retry a dead endpoint
    # faster the longer it stayed dead.
    schedule = settings.backoff_schedule_seconds
    assert list(schedule) == sorted(schedule)


async def test_backoff_jitter_only_shortens() -> None:
    """Jitter that could *extend* an interval would make the published span untrue."""
    settings = WebhookSettings()
    rng = random.Random(1)
    for attempts, base in enumerate(settings.backoff_schedule_seconds):
        delay = delivery.backoff_delay(settings, attempts, rng=rng)
        assert delay is not None
        assert 0 < delay.total_seconds() <= base


async def test_the_budget_runs_out(app_settings: Settings) -> None:
    settings = _webhook_settings(app_settings).webhooks
    assert delivery.backoff_delay(settings, len(settings.backoff_schedule_seconds)) is None


async def test_a_permanent_failure_is_not_retried(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    """A 410 is the receiver saying the endpoint is gone.

    Retrying it for ten hours is load on their infrastructure with no possible
    outcome. 408 and 429 are deliberately excluded from that rule — both mean
    "try again".
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410)

    await _subscribe_and_emit(migrated_engine, tenant_id, "cluster_created")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        _session(migrated_engine) as session,
    ):
        result = await delivery.dispatch_once(
            session, settings=_webhook_settings(app_settings), client=client
        )
        await session.commit()

    assert result.failed == 1
    state = await _delivery_state(migrated_engine)
    assert state["status"] == "failed"
    assert state["next_attempt_at"] is None


async def test_a_throttled_receiver_is_retried(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    await _subscribe_and_emit(migrated_engine, tenant_id, "cluster_created")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        _session(migrated_engine) as session,
    ):
        result = await delivery.dispatch_once(
            session, settings=_webhook_settings(app_settings), client=client
        )
        await session.commit()

    assert result.retrying == 1
    assert (await _delivery_state(migrated_engine))["status"] == "pending"


async def test_the_response_body_is_never_recorded(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    """It is attacker-influenced content on a surface the tenant reads back (§25)."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<script>alert(document.cookie)</script>")

    await _subscribe_and_emit(migrated_engine, tenant_id, "cluster_created")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        _session(migrated_engine) as session,
    ):
        await delivery.dispatch_once(
            session, settings=_webhook_settings(app_settings), client=client
        )
        await session.commit()

    state = await _delivery_state(migrated_engine)
    assert state["last_error"] == "HTTP 500"
    assert "script" not in str(state["last_error"])


async def test_an_endpoint_failing_continuously_is_disabled_and_told_why(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID, app_settings: Settings
) -> None:
    """Hammering a URL dead for a week is cost with no benefit.

    And the tenant needs a signal louder than a growing failure count nobody is
    reading.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    await _subscribe_and_emit(migrated_engine, tenant_id, "cluster_created")
    settings = _webhook_settings(app_settings, disable_after=1)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        _session(migrated_engine) as session,
    ):
        await delivery.dispatch_once(session, settings=settings, client=client)
        await session.commit()

    async with migrated_engine.begin() as conn:
        row = (
            await conn.execute(
                sql_text(
                    "SELECT is_active, disabled_reason FROM webhook_endpoints WHERE tenant_id = :t"
                ).bindparams(t=tenant_id)
            )
        ).one()

    assert row.is_active is False
    assert "consecutive delivery failures" in row.disabled_reason


async def test_the_outbox_purge_will_not_delete_past_the_fanout_cursor(
    migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """The sharp edge, closed.

    A row the realtime relay dispatched hours ago but the fan-out has not read
    is eligible for the retention predicate — and deleting it means the event is
    never delivered to any subscriber, with no failed row anywhere to show it,
    because the delivery was never created.
    """
    from nemesis.outbox.writer import purge_dispatched

    await _emit_outbox(migrated_engine, tenant_id, "cluster_created", dispatched=True)
    await _emit_outbox(migrated_engine, tenant_id, "cluster_created", dispatched=True)

    async with _session(migrated_engine) as session:
        safe_below = await delivery.sweep_outbox_safe_below(session)
        assert safe_below == 0  # the fan-out has read nothing

        deleted = await purge_dispatched(
            session,
            older_than=datetime.now(tz=UTC) + timedelta(hours=1),
            safe_below=safe_below,
        )
        await session.commit()

    assert deleted == 0, "retention deleted rows the webhook fan-out had not read"

    # Once the fan-out has caught up, the same purge is free to proceed.
    async with _session(migrated_engine) as session:
        await delivery.fan_out_once(session, batch_size=100)
        await session.commit()
    async with _session(migrated_engine) as session:
        safe_below = await delivery.sweep_outbox_safe_below(session)
        deleted = await purge_dispatched(
            session,
            older_than=datetime.now(tz=UTC) + timedelta(hours=1),
            safe_below=safe_below,
        )
        await session.commit()

    assert deleted == 2


async def test_the_delivery_log_is_readable_by_the_tenant(
    api_client: AsyncClient, migrated_engine: AsyncEngine, tenant_id: uuid.UUID
) -> None:
    """ "A delivery log tenants can inspect", made literal — and token-free.

    Making it privileged would mean distributing the shared secret to whoever
    debugs the integration.
    """
    created = await api_client.post(
        "/api/v1/integrations/webhooks",
        headers=TOKEN | {"X-Tenant-ID": str(tenant_id)},
        json={
            "url": "https://example.com/hooks/nemesis",
            "description": "Partner feed",
            "event_types": ["cluster_created"],
        },
    )
    assert created.status_code == 201, created.text
    endpoint_id = created.json()["id"]
    assert created.json()["secret"]

    await _emit_outbox(migrated_engine, tenant_id, "cluster_created")
    async with _session(migrated_engine) as session:
        await delivery.fan_out_once(session, batch_size=100)
        await session.commit()

    log = await api_client.get(
        f"/api/v1/integrations/webhooks/{endpoint_id}/deliveries",
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert log.status_code == 200
    body = log.json()
    assert body["pending"] == 1
    assert body["deliveries"][0]["event_type"] == "cluster_created"
    assert body["deliveries"][0]["attempts"] == 0


async def test_registering_a_private_target_over_http_is_a_422(
    api_client: AsyncClient, tenant_id: uuid.UUID
) -> None:
    response = await api_client.post(
        "/api/v1/integrations/webhooks",
        headers=TOKEN | {"X-Tenant-ID": str(tenant_id)},
        json={
            "url": "https://169.254.169.254/latest/meta-data/",
            "description": "d",
            "event_types": ["cluster_created"],
        },
    )
    assert response.status_code == 422
    assert "metadata endpoint" in response.text or "private" in response.text


async def test_another_tenants_endpoint_is_not_found(
    api_client: AsyncClient, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    created = await api_client.post(
        "/api/v1/integrations/webhooks",
        headers=TOKEN | {"X-Tenant-ID": str(tenant_id)},
        json={
            "url": "https://example.com/h",
            "description": "d",
            "event_types": ["cluster_created"],
        },
    )
    endpoint_id = created.json()["id"]

    response = await api_client.get(
        f"/api/v1/integrations/webhooks/{endpoint_id}/deliveries",
        headers={"X-Tenant-ID": str(other_tenant_id)},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(engine: AsyncEngine) -> object:
    return async_sessionmaker(engine, expire_on_commit=False)()


def _webhook_settings(base: Settings, *, disable_after: int = 50) -> Settings:
    return base.model_copy(
        update={
            "webhooks": WebhookSettings(disable_after_consecutive_failures=disable_after),
            # The MockTransport intercepts before any socket, so the guard would
            # otherwise refuse example.com's real resolution in CI's sandbox.
            # Signing still uses the real derivation.
        }
    )


async def _emit_outbox(
    engine: AsyncEngine,
    tenant_id: uuid.UUID,
    event_type: str,
    *,
    payload: dict[str, object] | None = None,
    dispatched: bool = False,
) -> None:
    """Write one committed event and its outbox row, through the real writers.

    ``EventStore`` and ``outbox.writer`` rather than hand-written SQL. The first
    version of this helper inserted into ``events`` directly and named a column
    that does not exist — which is the shape of every fixture that drifts from
    the schema it is pretending to be. Going through the production write path
    means the fan-out is reading exactly the rows the pipeline produces,
    including the hash chain, and a schema change breaks the helper loudly
    instead of quietly testing something else.
    """
    from nemesis.events.store import EventStore
    from nemesis.outbox import writer

    entity_id = uuid.uuid4()
    # Nested rather than combined: `tenant_scope` is a plain context manager, and
    # mixing it into an `async with` list fails at runtime with a confusing
    # `__aenter__` error — the same trap `pipeline.integrity` documents.
    async with _session(engine) as session:
        with tenant_scope(tenant_id):
            appended = await EventStore(session).append(
                tenant_id=tenant_id,
                entity_id=entity_id,
                event_type=event_type,
                payload=payload or _minimal_payload(event_type, entity_id),
            )
            await writer.enqueue(session, appended)
            if dispatched:
                # tenant-scope-exempt is not needed: this is a test arranging the
                # state the realtime relay would have left behind.
                await session.execute(
                    sql_text(
                        "UPDATE outbox_messages SET dispatched_at = now() "
                        "WHERE tenant_id = :t AND event_id = :e"
                    ).bindparams(t=tenant_id, e=appended.id)
                )
            await session.commit()


def _minimal_payload(event_type: str, entity_id: uuid.UUID) -> dict[str, object]:
    """The smallest payload each registered type will validate.

    Built here rather than passed by every caller: the tests are about delivery,
    and a valid payload is a precondition rather than a subject.
    """
    if event_type == "cluster_created":
        return {"seed_complaint_id": str(entity_id), "latitude": 18.52, "longitude": 73.857}
    if event_type == "complaint_submitted":
        return {"latitude": 18.52, "longitude": 73.857}
    if event_type == "severity_scored":
        return {"score": 5.0, "components": {}, "weights": {}, "policy_version": "v1"}
    raise AssertionError(f"no minimal payload declared for {event_type}")


async def _subscribe_and_emit(
    engine: AsyncEngine,
    tenant_id: uuid.UUID,
    event_type: str,
    *,
    payload: dict[str, object] | None = None,
) -> None:
    async with _session(engine) as session:
        await webhooks.create_endpoint(
            session,
            tenant_id=tenant_id,
            url="https://example.com/hooks/nemesis",
            description="d",
            event_types=[event_type],
            root_key="dev-only-insecure-webhook-signing-key-change-me",
            allow_private=False,
        )
        await session.commit()

    await _emit_outbox(engine, tenant_id, event_type, payload=payload)

    async with _session(engine) as session:
        await delivery.fan_out_once(session, batch_size=100)
        await session.commit()


async def _endpoint_id(engine: AsyncEngine, tenant_id: uuid.UUID) -> uuid.UUID:
    async with engine.begin() as conn:
        return (
            await conn.execute(
                sql_text("SELECT id FROM webhook_endpoints WHERE tenant_id = :t").bindparams(
                    t=tenant_id
                )
            )
        ).scalar_one()


async def _delivery_count(engine: AsyncEngine) -> int:
    async with engine.begin() as conn:
        return (
            await conn.execute(sql_text("SELECT count(*) FROM webhook_deliveries"))
        ).scalar_one()


async def _delivery_state(engine: AsyncEngine) -> dict[str, object]:
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                sql_text(
                    "SELECT status, attempts, last_error, last_status_code, delivered_at, "
                    "next_attempt_at FROM webhook_deliveries ORDER BY id LIMIT 1"
                )
            )
        ).one()
    return dict(row._mapping)
