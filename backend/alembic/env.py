"""Alembic environment.

Runs on the async driver so migrations exercise the same connection stack as
the application. The database URL comes from ``nemesis.config.Settings`` — never
from alembic.ini — so there is exactly one place credentials are configured.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# `nemesis.db.models` is imported for its side effect: it registers every model
# on `Base.metadata`. A model absent from that package is invisible to
# autogenerate, which then emits a migration that drops the table it never saw.
import nemesis.db.models  # noqa: F401
from alembic import context
from nemesis.config import get_settings
from nemesis.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Extension-owned tables must never appear in autogenerate diffs, or every
# migration will try to drop PostGIS's internal bookkeeping.
EXCLUDED_TABLES = {"spatial_ref_sys", "geography_columns", "geometry_columns"}


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "table" and name in EXCLUDED_TABLES)


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # Every migration runs inside a transaction; a failed migration leaves
        # no half-applied schema behind.
        transaction_per_migration=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_settings().database_url

    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
