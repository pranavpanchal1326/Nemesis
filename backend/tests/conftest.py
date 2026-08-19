"""Test fixtures.

Integration tests run against a **real** Postgres with PostGIS and pgvector, on
a throwaway database created and dropped per session. Nothing here mocks the
datastore: the two things most likely to break in NEMESIS are geospatial
queries and vector similarity (§14), and neither is meaningfully testable
against SQLite or a mock.

If no Postgres is reachable, integration tests skip rather than fail, so
``pytest`` stays useful before ``docker compose up``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nemesis.config import Settings

_ADMIN_DSN_ENV = "NEMESIS_TEST_ADMIN_DSN"
_DEFAULT_ADMIN_DSN = "postgresql://nemesis:nemesis@localhost:5432/postgres"

_EXTENSIONS = ("postgis", "vector", "pgcrypto", "pg_trgm")


def _admin_dsn() -> str:
    return os.environ.get(_ADMIN_DSN_ENV, _DEFAULT_ADMIN_DSN)


def _postgres_reachable() -> bool:
    try:
        with psycopg.connect(_admin_dsn(), connect_timeout=3):
            return True
    except Exception:
        return False


postgres_required = pytest.mark.skipif(
    not _postgres_reachable(),
    reason="no Postgres reachable; run `docker compose up -d postgres`",
)


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """Create a uniquely-named database for this session and drop it after.

    Per-session isolation (rather than per-test truncation) is what makes the
    hash-chain concurrency tests in Phase 1 trustworthy: they need real
    row-level locking against a real chain tail, not a cleaned-between-tests
    approximation.
    """
    if not _postgres_reachable():
        pytest.skip("no Postgres reachable")

    db_name = f"nemesis_test_{uuid.uuid4().hex[:12]}"
    admin = _admin_dsn()

    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{db_name}"')

    created_dsn = admin.rsplit("/", 1)[0] + f"/{db_name}"
    try:
        with psycopg.connect(created_dsn, autocommit=True) as conn:
            for ext in _EXTENSIONS:
                conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
        yield created_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')


@pytest.fixture(scope="session")
def settings(test_database_url: str) -> Settings:
    return Settings(
        app_env="ci",
        log_level="WARNING",
        database_url=test_database_url,
        database_pool_size=5,
        database_max_overflow=0,
    )


@pytest.fixture(scope="session")
def migrated_database_url(test_database_url: str) -> str:
    """Apply every migration to the throwaway database, once per session.

    Through ``alembic upgrade head`` in a subprocess, never
    ``Base.metadata.create_all``. Two reasons, and the second is the one that
    bites:

    1. The engineering standards forbid ``create_all`` outright — a test suite
       that builds its schema differently from production is testing a schema
       that will never exist anywhere else.
    2. ``events`` is ``PARTITION BY RANGE``, and ``create_all`` produces a
       partitioned table with **no partitions**, which rejects every insert.
       The tests would fail for a reason that has nothing to do with the code.

    A subprocess rather than Alembic's Python API because ``env.py`` resolves
    its URL from the process-global ``Settings``. Mutating that in-process and
    clearing the cache would leave the application pointed at the test database
    for the rest of the session — exactly the defect the ``bound_session``
    fixture exists to correct.
    """
    environment = os.environ | {
        "NEMESIS_DATABASE_URL": test_database_url,
        # Alembic's env.py refuses a wildcard CORS policy in production-like
        # environments; the migration run is not the place to exercise that.
        "NEMESIS_APP_ENV": "ci",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parent.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return test_database_url


#: Truncated between tests. Order is irrelevant — CASCADE handles the foreign
#: keys — but `events` is listed first because truncating a partitioned parent
#: is the operation most likely to break on a Postgres upgrade, and a failure
#: there should be obvious rather than buried mid-list.
_TRUNCATED_TABLES = (
    "events",
    "event_chain_heads",
    "event_idempotency",
    "event_snapshots",
    "archived_partitions",
    "complaints",
    "complaint_clusters",
    "work_orders",
    "budget_allocations",
    "outbox_messages",
    "pipeline_dead_letters",
    "users",
    # Phase 5 control-plane tables. Listed before `departments` and
    # `contractors` only for readability — CASCADE makes the order irrelevant —
    # but listed at all is not optional: a table left out here leaks rows
    # between tests, and the symptom is a unique-constraint failure in whichever
    # test happens to run second.
    "contractor_certifications",
    "taxonomy_prompt_sets",
    "taxonomy_nodes",
    "shifts",
    "calendar_exceptions",
    "business_calendars",
    "translations",
    "zones",
    "departments",
    "contractors",
    # Phase 4 integration tables. `webhook_cursor` is deliberately absent: it is
    # a singleton row created by the migration, and truncating it would delete
    # the row the fan-out locks — which fails as `scalar_one()` finding no rows,
    # in whichever webhook test happened to run first.
    # Phase 7 simulation tables. Reached by CASCADE from `tenants` like every
    # other scoped table, and named anyway for the reason the Phase 5 block is:
    # this tuple is the readable answer to "what does a test start with", and a
    # table that is only ever cleaned as a side effect of another one's cascade
    # is a table nobody notices when the cascade path changes.
    "policy_certificates",
    "evaluation_labels",
    "evaluation_sets",
    "shadow_observations",
    "simulation_runs",
    "api_key_usage",
    "api_keys",
    "webhook_deliveries",
    "webhook_endpoints",
    "tenants",
)


@pytest.fixture
async def migrated_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    """An engine on the migrated database, with the tenancy guard installed.

    The guard is installed here rather than only in the application so the test
    suite runs under the same restriction production does. A test that could
    quietly issue an unscoped query would be free to assert behaviour no real
    caller can reach.
    """
    from nemesis.tenancy.guard import install_tenant_guard

    eng = create_async_engine(migrated_database_url, pool_pre_ping=True)
    install_tenant_guard(eng.sync_engine)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def tenants(migrated_engine: AsyncEngine) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """A clean database with two tenants.

    Two, always. One tenant cannot demonstrate isolation, and a suite that only
    ever has one is how a cross-tenant leak reaches production with every test
    green.
    """
    from sqlalchemy import text as sql_text

    from nemesis.domain.constants import SYSTEM_TENANT_ID, SYSTEM_TENANT_SLUG

    first, second = uuid.uuid4(), uuid.uuid4()
    async with migrated_engine.begin() as conn:
        await conn.execute(
            sql_text(f"TRUNCATE {', '.join(_TRUNCATED_TABLES)} RESTART IDENTITY CASCADE")
        )
        # Re-seeded after every truncate. The Phase 3 migration creates it, but
        # truncating `tenants` removes it — and a suite that quietly dropped it
        # would make `record_system_degradation` fail with a foreign key
        # violation in tests only, which is the exact defect that migration
        # exists to fix, reintroduced by the fixture that is supposed to prove
        # it is fixed.
        await conn.execute(
            sql_text(
                "INSERT INTO tenants (id, slug, name, plan, is_active) "
                "VALUES (CAST(:id AS uuid), :slug, 'NEMESIS platform', 'internal', false)"
            ).bindparams(id=SYSTEM_TENANT_ID, slug=SYSTEM_TENANT_SLUG)
        )
        # Rewind the webhook fan-out cursor. `outbox_messages` is truncated with
        # RESTART IDENTITY, so ids begin again at 1 — a cursor left at 50 from a
        # previous test would silently skip the next fifty rows, and the symptom
        # would be a webhook test asserting a delivery that was never enqueued,
        # for reasons entirely outside the test that failed.
        await conn.execute(sql_text("UPDATE webhook_cursor SET last_outbox_id = 0"))
        for tenant_uuid, slug in ((first, "pilot-city"), (second, "campus")):
            await conn.execute(
                sql_text(
                    "INSERT INTO tenants (id, slug, name) VALUES (:id, :slug, :name)"
                ).bindparams(id=tenant_uuid, slug=slug, name=slug.replace("-", " ").title())
            )
    yield first, second


@pytest.fixture
def tenant_id(tenants: tuple[uuid.UUID, uuid.UUID]) -> uuid.UUID:
    return tenants[0]


@pytest.fixture
def other_tenant_id(tenants: tuple[uuid.UUID, uuid.UUID]) -> uuid.UUID:
    return tenants[1]


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Function-scoped on purpose.

    asyncpg connections bind to the event loop that created them, and
    pytest-asyncio gives each test a fresh loop. A session-scoped engine
    therefore hands out connections owned by a closed loop. Creating an engine
    is cheap; the expensive part (the database itself) stays session-scoped.
    """
    eng = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def bound_session(settings: Settings) -> AsyncIterator[None]:
    """Point the module-global engine at the throwaway test database.

    `session_scope()` deliberately resolves its engine from process-global
    settings, which is correct in production and wrong in tests: without this
    fixture those tests silently exercise the *application* database. Binding
    explicitly — and disposing on the way out — keeps the isolation guarantee
    that the per-session database is supposed to provide.
    """
    from nemesis.db import session as session_module

    await session_module.dispose_engine()
    session_module._engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_module._sessionmaker = None
    try:
        yield
    finally:
        await session_module.dispose_engine()


@pytest.fixture
async def app_settings(settings: Settings) -> Settings:
    """Settings for an application under test, with the throwaway database.

    Rate limiting is left **on**. Turning it off for convenience would mean the
    limiter's own tests are the only thing that ever exercises it, and every
    other endpoint test would pass against a code path production does not run.
    """
    return settings.model_copy(update={"upload_dir": Path(tempfile.mkdtemp(prefix="nemesis-up-"))})


@pytest.fixture
async def api_client(
    app_settings: Settings,
    migrated_database_url: str,
    tenants: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[AsyncClient]:
    """A client whose handlers talk to the *test* database.

    Distinct from the `client` fixture, which builds the app from process-global
    settings and is right for the probe and error-contract tests. A domain
    endpoint opens `session_scope()`, which resolves the module-global engine —
    so without binding that engine here, every domain test would silently
    exercise the application database, which is Phase 0 defect #7 in a new
    place.
    """
    from nemesis.api.ratelimit import close_limiter
    from nemesis.db import session as session_module
    from nemesis.db.session import dispose_engine
    from nemesis.flags import close_flags
    from nemesis.main import create_app
    from nemesis.realtime.service import close_realtime
    from nemesis.tenancy.guard import install_tenant_guard

    await dispose_engine()
    engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)
    install_tenant_guard(engine.sync_engine)
    session_module._engine = engine
    session_module._sessionmaker = None

    app = create_app(app_settings)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        await close_realtime()
        await close_limiter()
        await close_flags()
        await dispose_engine()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the ASGI app without opening a socket.

    Tears down the process-global resources the app creates lazily — the
    SQLAlchemy engine (on the first `/ready`) and the feature flag store's Redis
    client. `ASGITransport` does not run lifespan events, so the shutdown hook
    that normally disposes them never fires here.

    Leaving them was a latent flake rather than a leak that stayed quiet: the
    connections were finalised by the garbage collector at an arbitrary later
    point, and `filterwarnings = ["error"]` turned the resulting
    `ResourceWarning` into a failure attributed to whichever unrelated test
    happened to be running at that moment. Intermittent, and it blames the wrong
    test every time.
    """
    from nemesis.db.session import dispose_engine
    from nemesis.flags import close_flags
    from nemesis.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()
        await close_flags()
