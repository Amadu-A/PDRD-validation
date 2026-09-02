# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/health.py

"""PostgreSQL readiness adapter нормативного каталога."""

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

_CATALOG_READINESS_QUERY = text(
    """
    SELECT
        to_regclass(
            'public.alembic_version_knowledge'
        ) IS NOT NULL
        AND to_regclass(
            'knowledge.normative_sections'
        ) IS NOT NULL
        AND to_regclass(
            'knowledge.normative_categories'
        ) IS NOT NULL
        AND to_regclass(
            'knowledge.normative_documents'
        ) IS NOT NULL
    """
)


class DatabaseReadinessProbe:
    """Проверяет PostgreSQL schema нормативного каталога."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        timeout_seconds: float,
    ) -> None:
        """Сохраняет engine и timeout readiness probe."""
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def is_ready(
        self,
    ) -> bool:
        """Проверяет connection и наличие таблиц Knowledge Service."""
        try:
            async with asyncio.timeout(
                self._timeout_seconds,
            ):
                async with self._engine.connect() as connection:
                    result = await connection.scalar(
                        _CATALOG_READINESS_QUERY,
                    )

        except (
            TimeoutError,
            OSError,
            SQLAlchemyError,
        ):
            return False

        return result is True
