# services/user-service/src/pdrd_user_service/infrastructure/database/health.py

"""Readiness PostgreSQL: актуальная миграция и полный набор таблиц users."""

import asyncio
from pathlib import Path

from alembic.script import ScriptDirectory
from pdrd_user_service.infrastructure.database import models as _models  # noqa: F401
from pdrd_user_service.infrastructure.database.base import Base
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


def _default_migration_directory() -> Path:
    """Находит Alembic рядом с Docker WORKDIR или в исходном checkout."""
    workdir = Path.cwd()
    if (workdir / "alembic.ini").is_file() and (
        workdir / "alembic" / "versions"
    ).is_dir():
        return workdir / "alembic"
    return Path(__file__).resolve().parents[4] / "alembic"


class DatabaseReadinessProbe:
    """Проверяет схему без создания или изменения таблиц."""

    def __init__(
        self,
        engine: AsyncEngine,
        timeout_seconds: float,
        *,
        migration_directory: Path | None = None,
    ) -> None:
        """Фиксирует ожидаемые головы Alembic и имена таблиц."""
        self._engine = engine
        self._timeout_seconds = timeout_seconds
        migrations = migration_directory or _default_migration_directory()
        self._expected_heads = frozenset(ScriptDirectory(str(migrations)).get_heads())
        if not self._expected_heads:
            raise RuntimeError("В поставке User Service нет миграций Alembic.")
        self._tables = tuple(
            sorted(
                table.fullname
                for table in Base.metadata.tables.values()
                if table.schema == "users"
            )
        )

    async def is_ready(self) -> bool:
        """Возвращает False, если БД недоступна или миграция отстаёт."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._engine.connect() as connection:
                    versions = await connection.scalars(
                        text("SELECT version_num FROM users.alembic_version_users")
                    )
                    if frozenset(versions) != self._expected_heads:
                        return False
                    parameters = {
                        f"table_{index}": name
                        for index, name in enumerate(self._tables)
                    }
                    present = await connection.scalar(
                        text(
                            "SELECT "
                            + " AND ".join(
                                f"to_regclass(:{key}) IS NOT NULL" for key in parameters
                            )
                        ),
                        parameters,
                    )
                    return present is True
        except (TimeoutError, OSError, SQLAlchemyError):
            return False
