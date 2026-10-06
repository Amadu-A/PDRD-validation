# services/auth-service/alembic/env.py

"""Ограничивает Alembic только таблицами схемы auth."""

import asyncio

from alembic import context
from pdrd_auth_service.infrastructure.database import models as _models  # noqa: F401
from pdrd_auth_service.infrastructure.database.base import Base
from pdrd_auth_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
database_url = resolve_migration_url()
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def _include_name(name: str | None, type_: str, parent_names: dict[str, str]) -> bool:
    """Не допускает autogenerate над чужими схемами и таблицами."""
    if type_ == "schema":
        return name == "auth"
    if type_ == "table":
        return parent_names.get("schema_name") == "auth"
    return True


def _configure(**options: object) -> None:
    """Ведёт независимую таблицу версии миграции внутри auth."""
    context.configure(
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=_include_name,
        version_table="alembic_version_auth",
        version_table_schema="auth",
        compare_type=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Генерирует SQL без подключения к серверу."""
    _configure(
        url=database_url, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS auth")
        context.run_migrations()


def _migrate(connection: Connection) -> None:
    """Применяет только миграции auth в транзакции."""
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS auth")
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Закрывает пул после успешного применения или ошибки."""
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
