# services/knowledge-service/alembic/env.py

"""Runtime окружение Alembic migrations Knowledge Service."""

import asyncio

from alembic import context
from pdrd_knowledge_service.core.settings import get_settings
from pdrd_knowledge_service.infrastructure.database.base import Base
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_database_url,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeCategoryModel,
    NormativeDocumentModel,
    NormativeSectionModel,
)
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

del NormativeCategoryModel
del NormativeDocumentModel
del NormativeSectionModel

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

VERSION_TABLE = "alembic_version_knowledge"


def _configure_context(
    **kwargs: object,
) -> None:
    """Применяет общие параметры Knowledge migrations."""
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
        version_table=VERSION_TABLE,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Выполняет migrations без активного PostgreSQL connection."""
    _configure_context(
        url=database_url,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(
    connection: object,
) -> None:
    """Выполняет migrations через переданный sync facade connection."""
    _configure_context(
        connection=connection,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Создаёт async connection и выполняет migrations."""
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
    """Запускает async migration runner."""
    asyncio.run(
        _run_migrations_online(),
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
