# services/experience-service/alembic/env.py

"""Конфигурация миграций PostgreSQL для Experience Service.

Назначение файла:
- использовать согласованные настройки подключения;
- изолировать таблицу версий миграций в схеме experience;
- поддерживать offline-генерацию SQL;
- применять реальные миграции одной атомарной транзакцией.

Создание схемы и изменение таблиц происходят в одной
транзакции. При успешном завершении выполняется commit,
при ошибке — rollback.

Миграции других микросервисов этот модуль не запускает.
"""

import asyncio

from alembic import context
from pdrd_experience_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)
from pdrd_experience_service.infrastructure.database.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

database_url = resolve_migration_url()

config.set_main_option(
    "sqlalchemy.url",
    database_url.replace("%", "%%"),
)


def _configure(
    **options: object,
) -> None:
    """Настраивает отдельную цепочку миграций Experience Service."""
    context.configure(
        target_metadata=Base.metadata,
        include_schemas=True,
        version_table="alembic_version_experience",
        version_table_schema="experience",
        compare_type=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Генерирует SQL без установки соединения с PostgreSQL."""
    _configure(
        url=database_url,
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
    """Выполняет миграции внутри транзакции вызывающего кода."""
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS experience")

    _configure(
        connection=connection,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Применяет миграции с гарантированной фиксацией изменений.

    Использование engine.begin() обязательно: создание схемы
    предварительно открывает транзакцию SQLAlchemy.

    Контекст engine.begin() фиксирует успешную миграцию
    и откатывает незавершённую при возникновении исключения.
    """
    engine = async_engine_from_config(
        config.get_section(
            config.config_ini_section,
            {},
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                _migrate,
            )
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_migrations_online())
