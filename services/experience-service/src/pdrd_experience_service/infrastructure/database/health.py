# services/experience-service/src/pdrd_experience_service/infrastructure/database/health.py

"""Проверяет PostgreSQL, актуальные миграции и все таблицы Experience Service.

Ожидаемые версии берутся из поставленной цепочки Alembic, а таблицы —
из собственных ORM-метаданных. Новая миграция не требует второй версии
в коде readiness. Проверка ничего не создаёт и не меняет в рабочей базе данных.
"""

import asyncio
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from pdrd_experience_service.infrastructure.database.catalog_models import (
    CatalogExampleModel,
)


class DatabaseReadinessProbe:
    """Проверяет доступность и обязательные таблицы текущей версии сервиса."""

    def __init__(
        self,
        engine: AsyncEngine,
        timeout_seconds: float,
        *,
        migration_directory: Path | None = None,
    ) -> None:
        """Получает engine через DI и задаёт максимальное время проверки."""
        self._engine = engine
        self._timeout_seconds = timeout_seconds
        # В checkout работает путь от src; Docker явно передаёт /app/alembic,
        # потому что установленный wheel находится отдельно в site-packages.
        migrations = migration_directory or (
            Path(__file__).resolve().parents[4] / "alembic"
        )
        self._expected_heads = frozenset(ScriptDirectory(str(migrations)).get_heads())
        if not self._expected_heads:
            raise RuntimeError("В поставке Experience отсутствуют миграции Alembic.")
        # Импорт catalog_models регистрирует также исходные Review/Area модели.
        self._tables = tuple(
            sorted(
                table.fullname
                for table in CatalogExampleModel.metadata.tables.values()
                if table.schema == "experience"
            )
        )

    async def is_ready(self) -> bool:
        """Возвращает False, если миграции не доведены до текущего head."""
        try:
            async with asyncio.timeout(
                self._timeout_seconds,
            ):
                async with self._engine.connect() as connection:
                    versions = await connection.scalars(
                        text(
                            "SELECT version_num "
                            "FROM experience.alembic_version_experience"
                        )
                    )

                    if frozenset(versions) != self._expected_heads:
                        return False

                    parameters = {
                        f"table_{index}": name
                        for index, name in enumerate(self._tables)
                    }
                    tables_exist = await connection.scalar(
                        text(
                            "SELECT "
                            + " AND ".join(
                                f"to_regclass(:{key}) IS NOT NULL" for key in parameters
                            )
                        ),
                        parameters,
                    )

                    return tables_exist is True

        except (
            TimeoutError,
            OSError,
            SQLAlchemyError,
        ):
            return False
