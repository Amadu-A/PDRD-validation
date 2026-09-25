# services/experience-service/alembic/env.py

"""Конфигурация миграций PostgreSQL для Experience Service.

Назначение файла:
- подключает Alembic к отдельной схеме experience;
- использует собственную таблицу версий миграций;
- поддерживает проверку миграций без подключения к базе;
- обеспечивает атомарное выполнение реальных миграций.

Важно:
создание схемы и применение миграций должны находиться
в одной явно управляемой транзакции.

Если миграция завершается успешно, изменения фиксируются.
Если возникает ошибка, PostgreSQL откатывает всю транзакцию.

Миграции других микросервисов этот файл не запускает.
"""

import asyncio
import os

from alembic import context
from pdrd_experience_service.infrastructure.database.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config


# До подключения единой runtime-конфигурации сервиса
# адрес PostgreSQL передаётся явно через переменную окружения.
#
# Импорт пакета Experience Service не выполняет миграции.
database_url = os.environ.get(
    "EXPERIENCE_SERVICE_DATABASE_URL",
    "",
)

if not database_url:
    raise RuntimeError("Не задан EXPERIENCE_SERVICE_DATABASE_URL.")

if not database_url.startswith("postgresql+asyncpg://"):
    raise RuntimeError(
        "Миграции Experience Service требуют PostgreSQL и драйвер asyncpg."
    )

config.set_main_option(
    "sqlalchemy.url",
    database_url.replace("%", "%%"),
)


def _configure(
    **options: object,
) -> None:
    """Настраивает независимую историю миграций Experience.

    Собственная таблица версий располагается в схеме experience.
    Таким образом, миграции этого сервиса не пересекаются
    с миграциями API Gateway и Knowledge Service.
    """
    context.configure(
        target_metadata=Base.metadata,
        include_schemas=True,
        version_table="alembic_version_experience",
        version_table_schema="experience",
        compare_type=True,
        **options,
    )


def run_migrations_offline() -> None:
    """Генерирует SQL миграций без подключения к PostgreSQL.

    Используется архитектурными тестами и предварительной
    проверкой изменений схемы.
    """
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
    """Применяет миграции внутри уже открытой транзакции.

    Создание схемы выполняется до создания таблицы
    alembic_version_experience.

    Фиксацией или откатом транзакции управляет вызывающий
    код через engine.begin().
    """
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS experience")

    _configure(
        connection=connection,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    """Выполняет миграции с обязательной фиксацией транзакции.

    Используем engine.begin(), а не engine.connect().

    Причина:
    предварительный CREATE SCHEMA открывает транзакцию.
    Если использовать connect() без явного commit(),
    при закрытии соединения все изменения могут откатиться,
    несмотря на успешное завершение Alembic.

    Контекст engine.begin():
    - фиксирует транзакцию при успешном завершении;
    - откатывает её при возникновении исключения.
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
