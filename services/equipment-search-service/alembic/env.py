# services/equipment-search-service/alembic/env.py

"""Миграции только собственной схемы Equipment Search Service."""

import asyncio

from alembic import context
from pdrd_equipment_search_service.core.settings import Settings
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    Settings().database.url().replace("%", "%%"),
)


def _configure(**options: object) -> None:
    """Изолирует таблицу версий в схеме equipment_search."""
    context.configure(
        target_metadata=None,
        version_table="alembic_version_equipment_search",
        version_table_schema="equipment_search",
        include_schemas=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Генерирует SQL без подключения к PostgreSQL."""
    _configure(
        url=Settings().database.url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS equipment_search")
        context.run_migrations()


def _migrate(connection: Connection) -> None:
    """Применяет миграции и создание схемы в одной транзакции."""
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS equipment_search")
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Подключается асинхронно и фиксирует только успешную миграцию."""
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_migrations_online())
