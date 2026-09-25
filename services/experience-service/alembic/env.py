# services/experience-service/alembic/env.py

"""Experience-only Alembic runner using the project-specific PostgreSQL DB."""

import asyncio
import os

from alembic import context
from pdrd_experience_service.infrastructure.database.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

# Until transport/settings and Compose are connected, supply this URL explicitly.
# No migration is executed by importing the Experience Service package.
url = os.environ.get(
    "EXPERIENCE_SERVICE_DATABASE_URL",
    "",
)

if not url:
    raise RuntimeError("EXPERIENCE_SERVICE_DATABASE_URL must be explicitly configured.")

if not url.startswith("postgresql+asyncpg://"):
    raise RuntimeError("Experience migrations require postgresql+asyncpg URL.")

config.set_main_option(
    "sqlalchemy.url",
    url.replace("%", "%%"),
)


def _configure(
    **options: object,
) -> None:
    """Keep the version table separate from Gateway and Knowledge Service."""
    context.configure(
        target_metadata=Base.metadata,
        include_schemas=True,
        version_table="alembic_version_experience",
        version_table_schema="experience",
        compare_type=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Produce review migration SQL without connecting to PostgreSQL."""
    _configure(
        url=url,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
    )

    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS experience")

        context.run_migrations()


def _migrate(
    connection: Connection,
) -> None:
    """Ensure the schema exists before Alembic creates its version table."""
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS experience")

    _configure(
        connection=connection,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Create a short-lived connection exclusively for Alembic migrations."""
    engine = async_engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(
                _migrate,
            )
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_migrations_online())
