# services/experience-service/tests/unit/test_database_readiness.py

"""Регрессия готовности после миграции каталога без доступного PostgreSQL.

Подменяется только SQL-соединение: адаптер читает настоящую цепочку Alembic
и отправляет реальные SQL-выражения с обязательными таблицами.
"""

import asyncio
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from pdrd_experience_service.core.settings import Settings
from pdrd_experience_service.infrastructure.database import health as health_module
from pdrd_experience_service.infrastructure.database.health import (
    DatabaseReadinessProbe,
)
from sqlalchemy.exc import SQLAlchemyError

MIGRATION_DIRECTORY = Path(__file__).resolve().parents[2] / "alembic"
CURRENT_HEAD = ScriptDirectory(str(MIGRATION_DIRECTORY)).get_current_head()
TABLES = {
    "experience.review_sessions",
    "experience.review_events",
    "experience.confirmed_areas",
    "experience.area_confirmation_events",
    "experience.catalog_examples",
    "experience.catalog_events",
}


class DatabaseConnection:
    """Чтение текущих версий и видимых таблиц с заданным состоянием БД."""

    def __init__(self, *, versions, tables, error=None, delay=0):
        """Не открывает сеть и не выполняет миграции."""
        self.versions, self.tables = versions, tables
        self.error, self.delay = error, delay
        self.statements, self.checked_tables = [], set()
        self.closed = False

    async def __aenter__(self):
        """Позволяет проверить ошибку соединения и таймаут без ожидания сервера."""
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self

    async def __aexit__(self, *_):
        """Адаптер должен освобождать соединение и при несовместимой версии."""
        self.closed = True

    async def scalars(self, statement):
        """Отдаёт все применённые версии, включая несовместимые лишние heads."""
        self.statements.append(str(statement))
        return iter(self.versions)

    async def scalar(self, statement, parameters):
        """Проверяет существование именно переданных адаптером таблиц."""
        self.statements.append(str(statement))
        self.checked_tables = set(parameters.values())
        return self.checked_tables <= self.tables


class DatabaseEngine:
    """Минимальный инфраструктурный порт контекстного подключения SQLAlchemy."""

    def __init__(self, **kwargs):
        """Указывает текущее состояние временной БД в каждом сценарии."""
        self.connection = DatabaseConnection(**kwargs)

    def connect(self):
        """Возвращает один контролируемый контекст подключения."""
        return self.connection


async def test_current_migrated_catalog_is_ready_and_probe_is_read_only():
    """Новый head с шестью таблицами готов; повторяет ошибку серверного лога."""
    engine = DatabaseEngine(versions=(CURRENT_HEAD,), tables=TABLES)
    assert await DatabaseReadinessProbe(engine, timeout_seconds=1).is_ready()
    assert engine.connection.checked_tables == TABLES
    assert engine.connection.closed
    assert all(sql.startswith("SELECT ") for sql in engine.connection.statements)


@pytest.mark.parametrize(
    "versions",
    [(), ("20260928_0002",), ("unknown",), (CURRENT_HEAD, "unknown")],
)
async def test_old_missing_or_extra_revision_is_not_ready(versions):
    """Успешное подключение и таблицы не заменяют актуальную версию миграций."""
    engine = DatabaseEngine(versions=versions, tables=TABLES)
    assert not await DatabaseReadinessProbe(engine, timeout_seconds=1).is_ready()
    assert engine.connection.closed


@pytest.mark.parametrize("missing", sorted(TABLES))
async def test_every_required_table_including_catalog_audit_is_checked(missing):
    """Ошибочно выставленный head без любой обязательной таблицы не даёт readiness."""
    engine = DatabaseEngine(versions=(CURRENT_HEAD,), tables=TABLES - {missing})
    assert not await DatabaseReadinessProbe(engine, timeout_seconds=1).is_ready()


@pytest.mark.parametrize("error", [OSError("offline"), SQLAlchemyError("unavailable")])
async def test_connection_errors_return_unready_without_escaping(error):
    """Недоступность БД не превращает health endpoint в необработанное исключение."""
    engine = DatabaseEngine(versions=(), tables=set(), error=error)
    assert not await DatabaseReadinessProbe(engine, timeout_seconds=1).is_ready()


async def test_connection_timeout_is_bounded():
    """Проверка не ждёт медленное соединение дольше своего таймаута."""
    engine = DatabaseEngine(versions=(CURRENT_HEAD,), tables=TABLES, delay=1)
    assert not await DatabaseReadinessProbe(engine, timeout_seconds=0.01).is_ready()


async def test_explicit_migrations_path_supports_installed_wheel(monkeypatch, tmp_path):
    """Docker читает /app/alembic, даже когда пакет находится в site-packages."""
    monkeypatch.setattr(
        health_module,
        "__file__",
        str(
            tmp_path
            / "site-packages/pdrd_experience_service/infrastructure/database/health.py"
        ),
    )
    monkeypatch.chdir(tmp_path)
    engine = DatabaseEngine(versions=(CURRENT_HEAD,), tables=TABLES)
    probe = DatabaseReadinessProbe(
        engine, timeout_seconds=1, migration_directory=MIGRATION_DIRECTORY
    )
    assert await probe.is_ready()


def test_runtime_env_selects_supplied_migrations_directory(monkeypatch, tmp_path):
    """Переменная образа действительно попадает в типизированные настройки DI."""
    monkeypatch.setenv("EXPERIENCE_SERVICE_MIGRATION_DIRECTORY", str(tmp_path))
    assert Settings(_env_file=None).migration_directory == tmp_path
