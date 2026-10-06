# services/user-service/alembic/env.py

"""Отдельная цепочка Alembic User Service в схеме users."""

import asyncio

from alembic import context
from pdrd_user_service.infrastructure.database import models as _models  # noqa: F401
from pdrd_user_service.infrastructure.database.base import Base
from pdrd_user_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
database_url = resolve_migration_url()
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def _include_name(name: str | None, type_: str, parent_names: dict[str, str]) -> bool:
    """Не допускает autogenerate операций над схемами других сервисов."""
    if type_ == "schema":
        return name == "users"
    if type_ == "table":
        return parent_names.get("schema_name") == "users"
    return True


def _configure(**options: object) -> None:
    """Ограничивает сравнение метаданных собственными таблицами users."""
    context.configure(
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=_include_name,
        version_table="alembic_version_users",
        version_table_schema="users",
        compare_type=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Генерирует SQL без подключения к БД."""
    _configure(
        url=database_url, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS users")
        context.run_migrations()


def _migrate(connection: Connection) -> None:
    """Выполняет миграции в одной транзакции соединения."""
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS users")
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Фиксирует успешную миграцию и откатывает ошибочную."""
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
