# services/experience-service/src/pdrd_experience_service/infrastructure/database/health.py

"""Проверяет PostgreSQL и наличие схемы подтверждений Experience Service.

Readiness становится положительным только после миграции 20260928_0002.
Проверка ничего не создаёт и не меняет в рабочей базе данных.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseReadinessProbe:
    """Проверяет доступность и обязательные таблицы текущей версии сервиса."""

    def __init__(
        self,
        engine: AsyncEngine,
        timeout_seconds: float,
    ) -> None:
        """Получает engine через DI и задаёт максимальное время проверки."""
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def is_ready(self) -> bool:
        """Возвращает False, если миграции не доведены до текущего head."""
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

                    if version != "20260928_0002":
                        return False

                    tables_exist = await connection.scalar(
                        text(
                            "SELECT "
                            "to_regclass('experience.review_sessions') "
                            "IS NOT NULL "
                            "AND to_regclass('experience.review_events') "
                            "IS NOT NULL "
                            "AND to_regclass('experience.confirmed_areas') "
                            "IS NOT NULL "
                            "AND "
                            "to_regclass('experience.area_confirmation_events') "
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
