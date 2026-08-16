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
import uuid
from collections.abc import AsyncIterator, Iterator

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
