# services/auth-service/tests/unit/test_migration_url.py

"""Проверяет выбор URL миграции сессий без включения LDAPS и утечки секрета."""

import pytest
from pdrd_auth_service.core.settings import DatabaseSettings, Settings
from pdrd_auth_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)
from pydantic import SecretStr
from sqlalchemy.engine import make_url


def test_migration_uses_own_database_without_ad_ca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Миграция может подготовить схему до включения входа и доставки CA."""
    monkeypatch.delenv("AUTH_SERVICE_DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        enabled=False,
        database=DatabaseSettings(
            host="auth-test-postgres",
            name="pdrd_auth_test",
            user="auth_test",
            password=SecretStr("test-only"),
        ),
    )
    url = make_url(resolve_migration_url(settings=settings))
    assert (url.host, url.database, url.username) == (
        "auth-test-postgres",
        "pdrd_auth_test",
        "auth_test",
    )


def test_migration_rejects_placeholder_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Шаблон .env.example не даёт запустить миграцию рабочей БД."""
    monkeypatch.delenv("AUTH_SERVICE_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="пароль PostgreSQL"):
        resolve_migration_url(settings=Settings(_env_file=None))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://missing-password@postgres/pdrd",
        "sqlite+aiosqlite:///tmp/test.db",
        "invalid-url",
    ],
)
def test_migration_rejects_non_postgres_or_incomplete_url(url: str) -> None:
    """Явный URL должен указывать на PostgreSQL с полными реквизитами."""
    with pytest.raises(ValueError):
        resolve_migration_url(explicit_url=url)


def test_migration_rejects_ambiguous_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Одновременный URL и набор полей не выбираются по скрытому приоритету."""
    monkeypatch.setenv("AUTH_SERVICE_DATABASE__HOST", "auth-test-postgres")
    with pytest.raises(ValueError, match="одновременно"):
        resolve_migration_url(
            explicit_url="postgresql+asyncpg://auth_test:test@auth-test-postgres/pdrd_auth_test"
        )
