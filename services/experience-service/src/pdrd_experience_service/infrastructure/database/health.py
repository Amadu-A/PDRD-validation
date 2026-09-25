# services/experience-service/src/pdrd_experience_service/infrastructure/database/health.py

"""Проверка доступности и готовности PostgreSQL Experience Service.

Назначение файла:
- проверить возможность подключиться к PostgreSQL;
- убедиться в наличии собственной схемы Experience;
- проверить существование таблиц Review и Alembic;
- возвращать безопасный результат без раскрытия DSN.

Отсутствие миграций считается состоянием NOT READY.
Проверка не создаёт схему и не модифицирует данные.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseReadinessProbe:
    """Infrastructure adapter проверки готовности PostgreSQL."""

    def __init__(
        self,
        engine: AsyncEngine,
        timeout_seconds: float,
    ) -> None:
        """Получает engine через DI и ограничение времени проверки."""
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def is_ready(self) -> bool:
        """Проверяет доступность базы и обязательных таблиц Experience."""
        try:
            async with asyncio.timeout(
                self._timeout_seconds,
            ):
                async with self._engine.connect() as connection:
                    version = await connection.scalar(
                        text(
                            "SELECT version_num "
                            "FROM experience.alembic_version_experience"
                        )
                    )

                    if version is None:
                        return False

                    tables_exist = await connection.scalar(
                        text(
                            "SELECT "
                            "to_regclass('experience.review_sessions') "
                            "IS NOT NULL "
                            "AND "
                            "to_regclass('experience.review_events') "
                            "IS NOT NULL"
                        )
                    )

                    return tables_exist is True

        except (
            TimeoutError,
            OSError,
            SQLAlchemyError,
        ):
            return False
