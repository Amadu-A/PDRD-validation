# services/api-gateway/alembic/env.py

"""Runtime окружение Alembic migrations API Gateway."""

import asyncio

from alembic import context
from pdrd_api_gateway.core.settings import get_settings
from pdrd_api_gateway.infrastructure.database.base import Base
from pdrd_api_gateway.infrastructure.database.engine import (
    build_database_url,
)
from pdrd_api_gateway.infrastructure.database.models import (
    AnalysisJobModel,
    OutboxMessageModel,
)
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

del AnalysisJobModel
del OutboxMessageModel

config = context.config

settings = get_settings()

database_url = build_database_url(
    settings.database,
).render_as_string(
    hide_password=False,
)

config.set_main_option(
    "sqlalchemy.url",
    database_url.replace(
        "%",
        "%%",
    ),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Выполняет migrations без активного database connection."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(
    connection: object,
) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(
            _run_migrations,
        )

    await connectable.dispose()


def run_migrations_online() -> None:
    """Выполняет migrations через async PostgreSQL connection."""
    asyncio.run(
        _run_migrations_online(),
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
