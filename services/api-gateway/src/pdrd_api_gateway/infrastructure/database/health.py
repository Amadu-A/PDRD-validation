# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/health.py

"""PostgreSQL adapter для readiness-проверки API Gateway."""

import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseReadinessProbe:
    """Проверяет возможность выполнить запрос к PostgreSQL."""

    def __init__(
        self,
        engine: AsyncEngine,
        timeout_seconds: float,
    ) -> None:
        """Сохраняет database engine и ограничение времени проверки."""
        self._engine = engine
        self._timeout_seconds = timeout_seconds

    async def is_ready(self) -> bool:
        """Выполняет минимальный запрос SELECT 1."""
        try:
            async with asyncio.timeout(
                self._timeout_seconds,
            ):
                async with self._engine.connect() as connection:
                    await connection.execute(
                        text("SELECT 1"),
                    )
        except (
            TimeoutError,
            OSError,
            SQLAlchemyError,
        ):
            return False

        return True
